"""Обработчик webhook-событий от MAX.

Архитектура событий MAX (см. https://habr.com/ru/articles/1005282/):
- update_type='message_created': пользователь прислал сообщение
- update_type='bot_started': пользователь впервые нажал «Запустить» бота
- update_type='message_callback': нажата callback-кнопка inline-клавиатуры

В отличие от Telegram, у MAX user_id нажавшего callback лежит в callback.user.user_id,
а не в sender. У message_created — в message.sender.user_id или sender.user_id.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..config import Config
from ..logging_setup import get_logger, mask_id
from ..models import (
    AuditAction,
    EventFormat,
    RegistrationStatus,
)
from ..repositories.repos import (
    EventRepo,
    RegistrationRepo,
    UserRepo,
)
from ..services import audit
from ..services.codes import (
    generate_qr_token,
    generate_registration_code,
    render_qr_png,
)
from ..services.max_client import MaxBotClient
from . import messages as M

log = get_logger(__name__)


# ============================================================
# Извлечение полей из update
# ============================================================


def _extract_user(update: dict) -> tuple[Optional[int], Optional[str]]:
    """Возвращает (max_user_id, username) из любого типа update."""
    # callback-нажатие
    cb = update.get("callback") or {}
    user = cb.get("user")
    if user:
        return user.get("user_id"), _user_display_name(user)

    # обычное сообщение
    msg = update.get("message") or {}
    sender = msg.get("sender") or update.get("sender") or {}
    if sender.get("user_id"):
        return sender["user_id"], _user_display_name(sender)

    # bot_started
    u = update.get("user") or {}
    if u.get("user_id"):
        return u.get("user_id"), _user_display_name(u)

    return None, None


def _user_display_name(user: dict) -> str:
    """Из объекта user MAX составляет отображаемое имя."""
    name = user.get("name") or user.get("first_name") or ""
    last = user.get("last_name") or ""
    if name and last:
        return f"{name} {last}".strip()
    return name or user.get("username") or f"user_{user.get('user_id', '?')}"


def _extract_text(update: dict) -> str:
    msg = update.get("message") or {}
    body = msg.get("body") or {}
    return body.get("text") or ""


def _extract_callback_id(update: dict) -> Optional[str]:
    cb = update.get("callback") or {}
    return cb.get("callback_id")


def _extract_callback_payload(update: dict) -> Optional[str]:
    cb = update.get("callback") or {}
    return cb.get("payload")


# ============================================================
# Главный диспетчер
# ============================================================


def dispatch(update: dict, session: Session, client: MaxBotClient) -> None:
    """Главная точка входа для webhook. Маршрутизирует событие в нужный хендлер."""
    update_type = update.get("update_type") or update.get("type") or ""
    max_user_id, username = _extract_user(update)

    log.info(
        "WEBHOOK type=%s user=%s",
        update_type,
        mask_id(max_user_id),
    )

    if max_user_id is None:
        log.warning("WEBHOOK без user_id, игнорируем")
        return

    if update_type in ("bot_started", "message_created"):
        text = _extract_text(update).strip().lower()
        if text == "/start" or update_type == "bot_started":
            _handle_start(session, client, max_user_id, username or "user")
        elif text in ("/help", "помощь"):
            client.send_message(max_user_id, M.WELCOME, buttons=_menu_buttons())
        elif text in ("мои записи", "/my"):
            _handle_my_registrations(session, client, max_user_id)
        else:
            client.send_message(max_user_id, M.UNKNOWN_COMMAND, buttons=_menu_buttons())
        return

    if update_type == "message_callback":
        callback_id = _extract_callback_id(update)
        payload = _extract_callback_payload(update) or ""
        if callback_id is None:
            log.warning("callback без callback_id")
            return
        _handle_callback(session, client, max_user_id, username or "user", callback_id, payload)
        return

    log.info("WEBHOOK неизвестный тип %s", update_type)


# ============================================================
# Сценарии абитуриента
# ============================================================


def _menu_buttons() -> list[list[dict]]:
    """Главное меню."""
    return [
        [{"type": "callback", "text": "📅 Каталог мероприятий", "payload": "catalog"}],
        [{"type": "callback", "text": "🎫 Мои записи", "payload": "my_regs"}],
    ]


def _handle_start(
    session: Session, client: MaxBotClient, max_user_id: int, username: str
) -> None:
    """Первый запуск: показать дисклеймер и запросить согласие."""
    user = UserRepo.get_by_max_id(session, max_user_id)
    if user and user.consent_version == Config.CONSENT_VERSION:
        # Согласие уже есть — сразу меню
        client.send_message(
            max_user_id,
            f"С возвращением, {user.username}!",
            buttons=_menu_buttons(),
        )
        return

    # Согласие фиксируется только после нажатия «Согласен» — здесь только показываем дисклеймер
    client.send_message(max_user_id, M.WELCOME)
    client.send_message(
        max_user_id,
        M.CONSENT_PROMPT,
        buttons=[
            [
                {"type": "callback", "text": "✅ Согласен", "payload": f"consent:yes:{username}"},
                {"type": "callback", "text": "❌ Отказаться", "payload": "consent:no"},
            ]
        ],
    )


def _handle_callback(
    session: Session,
    client: MaxBotClient,
    max_user_id: int,
    username: str,
    callback_id: str,
    payload: str,
) -> None:
    """Маршрутизатор callback-кнопок по prefix."""
    parts = payload.split(":")
    action = parts[0] if parts else ""

    if action == "consent":
        _cb_consent(session, client, max_user_id, username, callback_id, parts)
    elif action == "catalog":
        _cb_catalog(session, client, max_user_id, callback_id)
    elif action == "event":  # event:<event_id> — карточка
        _cb_event_card(session, client, max_user_id, callback_id, parts)
    elif action == "reg":  # reg:<event_id> — подтверждение записи
        _cb_register(session, client, max_user_id, callback_id, parts)
    elif action == "my_regs":
        _cb_my_regs(session, client, max_user_id, callback_id)
    elif action == "cancel":  # cancel:<reg_id>
        _cb_cancel(session, client, max_user_id, callback_id, parts)
    elif action == "notif":  # notif:<reg_id>:on|off
        _cb_notif(session, client, max_user_id, callback_id, parts)
    elif action == "qr":  # qr:<reg_id> — повторно показать QR
        _cb_show_qr(session, client, max_user_id, callback_id, parts)
    else:
        client.answer_callback(callback_id, notification="Неизвестная команда")


def _cb_consent(
    session: Session,
    client: MaxBotClient,
    max_user_id: int,
    username: str,
    callback_id: str,
    parts: list[str],
) -> None:
    decision = parts[1] if len(parts) > 1 else ""
    if decision == "yes":
        # При согласии username берём из payload, чтобы он совпадал с тем, что показывали
        name_to_save = ":".join(parts[2:]) or username
        user = UserRepo.upsert_with_consent(
            session,
            max_user_id=max_user_id,
            username=name_to_save,
            consent_version=Config.CONSENT_VERSION,
        )
        audit.write(
            session,
            AuditAction.consent_given,
            user_id=user.id,
            payload={"version": Config.CONSENT_VERSION},
        )
        client.answer_callback(
            callback_id,
            text=M.CONSENT_ACCEPTED,
            buttons=_menu_buttons(),
        )
    else:
        client.answer_callback(callback_id, text=M.CONSENT_REFUSED)


def _cb_catalog(
    session: Session, client: MaxBotClient, max_user_id: int, callback_id: str
) -> None:
    user = UserRepo.get_by_max_id(session, max_user_id)
    if user is None:
        client.answer_callback(callback_id, notification="Сначала дайте согласие в /start")
        return

    items = EventRepo.list_upcoming(session, limit=10)
    if not items:
        client.answer_callback(callback_id, text=M.CATALOG_EMPTY)
        return

    # Каждое мероприятие — отдельная кнопка-карточка
    buttons = []
    for ev, free in items:
        label = f"{_format_date_short(ev.event_date)} · {ev.title}"
        if ev.registration_closed or free <= 0:
            label = "🔒 " + label
        buttons.append([{"type": "callback", "text": label, "payload": f"event:{ev.id}"}])
    buttons.append([{"type": "callback", "text": "🎫 Мои записи", "payload": "my_regs"}])

    client.answer_callback(callback_id, text=M.CATALOG_HEADER, buttons=buttons)


def _cb_event_card(
    session: Session,
    client: MaxBotClient,
    max_user_id: int,
    callback_id: str,
    parts: list[str],
) -> None:
    try:
        event_id = int(parts[1])
    except (IndexError, ValueError):
        client.answer_callback(callback_id, notification="Не удалось открыть мероприятие")
        return

    ev = EventRepo.get(session, event_id)
    if ev is None:
        client.answer_callback(callback_id, notification="Мероприятие не найдено")
        return

    free = ev.max_participants - RegistrationRepo.count_confirmed(session, event_id)
    text = (
        f"📌 {ev.title}\n\n"
        f"{ev.description}\n\n"
        f"🗓 {_format_date_full(ev.event_date)}\n"
        f"⏱ Длительность: {ev.duration_minutes} мин\n"
        f"📍 {ev.location}\n"
        f"💺 Свободных мест: {free} из {ev.max_participants}\n"
        f"📡 Формат: {'онлайн' if ev.format == EventFormat.online else 'очно'}"
    )

    buttons: list[list[dict]] = []
    if ev.registration_closed:
        text += "\n\n🔒 Регистрация закрыта."
    elif free <= 0:
        text += "\n\n🔒 Мест больше нет."
    else:
        buttons.append(
            [{"type": "callback", "text": "✍️ Записаться", "payload": f"reg:{event_id}"}]
        )
    buttons.append([{"type": "callback", "text": "← Назад к каталогу", "payload": "catalog"}])

    client.answer_callback(callback_id, text=text, buttons=buttons)


def _cb_register(
    session: Session,
    client: MaxBotClient,
    max_user_id: int,
    callback_id: str,
    parts: list[str],
) -> None:
    try:
        event_id = int(parts[1])
    except (IndexError, ValueError):
        client.answer_callback(callback_id, notification="Ошибка")
        return

    user = UserRepo.get_by_max_id(session, max_user_id)
    if user is None:
        client.answer_callback(callback_id, notification="Сначала дайте согласие")
        return

    ev = EventRepo.get(session, event_id)
    if ev is None:
        client.answer_callback(callback_id, notification="Мероприятие не найдено")
        return

    if ev.registration_closed:
        client.answer_callback(callback_id, text=M.REG_CLOSED)
        return

    # Проверяем активную запись
    existing = RegistrationRepo.get_active_for_user(session, user.id, event_id)
    if existing:
        client.answer_callback(callback_id, text=M.REG_ALREADY)
        return

    # Проверяем места
    free = ev.max_participants - RegistrationRepo.count_confirmed(session, event_id)
    if free <= 0:
        client.answer_callback(callback_id, text=M.REG_FULL)
        return

    # Создаём запись
    code = generate_registration_code()
    # qr_token зависит от id — генерируем в два этапа: сначала временно, потом обновим
    placeholder = generate_qr_token(0)
    reg = RegistrationRepo.create(
        session,
        user_id=user.id,
        event_id=event_id,
        registration_code=code,
        qr_token=placeholder,
    )
    if reg is None:
        client.answer_callback(callback_id, text=M.REG_ALREADY)
        return

    # Перегенерируем qr_token с реальным id
    reg.qr_token = generate_qr_token(reg.id)
    session.flush()

    audit.write(
        session,
        AuditAction.registration_created,
        user_id=user.id,
        event_id=event_id,
        registration_id=reg.id,
    )

    # Подтверждаем callback, чтобы убрать индикатор загрузки в MAX
    client.answer_callback(callback_id, notification="Записываем...")

    # 1) Первое сообщение — QR-картинка с названием мероприятия и кодом
    qr_caption = M.REG_SUCCESS_TEMPLATE.format(
        title=ev.title,
        date=_format_date_full(ev.event_date),
        location=ev.location,
        code=code,
    )
    image_token = None
    try:
        png_bytes = render_qr_png(reg.qr_token)
        image_token = client.upload_image(png_bytes, filename=f"qr_{reg.id}.png")
        if not image_token:
            log.warning("Не удалось загрузить QR-картинку для reg=%s", reg.id)
    except Exception as e:
        log.warning("QR render/upload failed for reg=%s: %s", reg.id, e)

    if image_token:
        client.send_message(max_user_id, qr_caption, image_token=image_token)
    else:
        # Если картинка не загрузилась — отправляем только текст с кодом
        client.send_message(max_user_id, qr_caption + "\n\n" + M.REG_QR_FAILED)

    # 2) Второе сообщение — меню для дальнейших действий
    client.send_message(
        max_user_id,
        M.REG_NEXT_PROMPT,
        buttons=[
            [{"type": "callback", "text": "📅 Каталог мероприятий", "payload": "catalog"}],
            [{"type": "callback", "text": "🎫 Мои записи", "payload": "my_regs"}],
        ],
    )


def _cb_my_regs(
    session: Session, client: MaxBotClient, max_user_id: int, callback_id: str
) -> None:
    user = UserRepo.get_by_max_id(session, max_user_id)
    if user is None:
        client.answer_callback(callback_id, notification="Сначала дайте согласие")
        return

    regs = [
        r
        for r in RegistrationRepo.list_for_user(session, user.id)
        if r.status == RegistrationStatus.confirmed
    ]
    if not regs:
        client.answer_callback(
            callback_id,
            text=M.NO_REGS,
            buttons=[
                [{"type": "callback", "text": "📅 К каталогу", "payload": "catalog"}]
            ],
        )
        return

    lines = ["🎫 Ваши активные записи:\n"]
    buttons: list[list[dict]] = []
    for r in regs:
        ev = r.event
        lines.append(
            f"• {ev.title} — {_format_date_short(ev.event_date)} — код {r.registration_code}"
        )
        # Сокращаем название если оно слишком длинное, чтобы кнопка не разваливалась
        short_title = ev.title if len(ev.title) <= 25 else ev.title[:22] + "…"
        buttons.append([
            {
                "type": "callback",
                "text": f"🎫 QR · {r.registration_code}",
                "payload": f"qr:{r.id}",
            },
        ])
        buttons.append([
            {
                "type": "callback",
                "text": f"❌ Отменить «{short_title}»",
                "payload": f"cancel:{r.id}",
            },
            {
                "type": "callback",
                "text": "🔕 Уведомления" if r.notifications_enabled else "🔔 Уведомления",
                "payload": f"notif:{r.id}:{'off' if r.notifications_enabled else 'on'}",
            },
        ])
    buttons.append([{"type": "callback", "text": "📅 К каталогу", "payload": "catalog"}])
    client.answer_callback(callback_id, text="\n".join(lines), buttons=buttons)


def _cb_cancel(
    session: Session,
    client: MaxBotClient,
    max_user_id: int,
    callback_id: str,
    parts: list[str],
) -> None:
    try:
        reg_id = int(parts[1])
    except (IndexError, ValueError):
        client.answer_callback(callback_id, notification="Ошибка")
        return

    user = UserRepo.get_by_max_id(session, max_user_id)
    if user is None:
        return

    reg = RegistrationRepo.get(session, reg_id)
    if reg is None or reg.user_id != user.id:
        client.answer_callback(callback_id, notification="Запись не найдена")
        return

    # Поздняя отмена не разрешена: мероприятие уже началось
    if reg.event.event_date <= datetime.now(timezone.utc):
        client.answer_callback(callback_id, text=M.CANCEL_LATE_DENIED)
        return

    ok = RegistrationRepo.cancel(session, reg_id, user.id)
    if not ok:
        client.answer_callback(callback_id, notification="Не удалось отменить")
        return

    audit.write(
        session,
        AuditAction.registration_cancelled,
        user_id=user.id,
        event_id=reg.event_id,
        registration_id=reg.id,
    )
    client.answer_callback(callback_id, text=M.CANCEL_OK, buttons=_menu_buttons())


def _cb_notif(
    session: Session,
    client: MaxBotClient,
    max_user_id: int,
    callback_id: str,
    parts: list[str],
) -> None:
    try:
        reg_id = int(parts[1])
        new_state = parts[2] == "on"
    except (IndexError, ValueError):
        client.answer_callback(callback_id, notification="Ошибка")
        return

    user = UserRepo.get_by_max_id(session, max_user_id)
    if user is None:
        return

    ok = RegistrationRepo.toggle_notifications(session, reg_id, user.id, new_state)
    if not ok:
        client.answer_callback(callback_id, notification="Не удалось")
        return

    audit.write(
        session,
        AuditAction.notifications_toggled,
        user_id=user.id,
        registration_id=reg_id,
        payload={"enabled": new_state},
    )
    client.answer_callback(
        callback_id, notification=M.NOTIFY_ON if new_state else M.NOTIFY_OFF
    )


def _cb_show_qr(
    session: Session,
    client: MaxBotClient,
    max_user_id: int,
    callback_id: str,
    parts: list[str],
) -> None:
    """Повторно отправляет QR-картинку для существующей записи."""
    try:
        reg_id = int(parts[1])
    except (IndexError, ValueError):
        client.answer_callback(callback_id, notification="Ошибка")
        return

    user = UserRepo.get_by_max_id(session, max_user_id)
    if user is None:
        return

    reg = RegistrationRepo.get(session, reg_id)
    if reg is None or reg.user_id != user.id:
        client.answer_callback(callback_id, notification="Запись не найдена")
        return
    if reg.status != RegistrationStatus.confirmed:
        client.answer_callback(callback_id, notification="Запись неактивна")
        return

    client.answer_callback(callback_id, notification="Отправляю QR...")

    ev = reg.event
    caption = M.REG_SUCCESS_TEMPLATE.format(
        title=ev.title,
        date=_format_date_full(ev.event_date),
        location=ev.location,
        code=reg.registration_code,
    )
    image_token = None
    try:
        png_bytes = render_qr_png(reg.qr_token)
        image_token = client.upload_image(png_bytes, filename=f"qr_{reg.id}.png")
    except Exception as e:
        log.warning("show_qr render/upload failed for reg=%s: %s", reg.id, e)

    if image_token:
        client.send_message(max_user_id, caption, image_token=image_token)
    else:
        client.send_message(max_user_id, caption + "\n\n" + M.REG_QR_FAILED)


def _handle_my_registrations(
    session: Session, client: MaxBotClient, max_user_id: int
) -> None:
    """Команда «Мои записи» текстом, не из callback."""
    user = UserRepo.get_by_max_id(session, max_user_id)
    if user is None:
        client.send_message(max_user_id, "Сначала напишите /start.")
        return
    fake_callback_id = "synth"  # MAX не вернёт ответа на этот id, но мы и не зависим от ответа
    try:
        _cb_my_regs(session, client, max_user_id, fake_callback_id)
    except Exception:
        # На синтетический callback_id /answers вернёт ошибку — отправим обычным сообщением
        regs = RegistrationRepo.list_for_user(session, user.id)
        active = [r for r in regs if r.status == RegistrationStatus.confirmed]
        if not active:
            client.send_message(max_user_id, M.NO_REGS, buttons=_menu_buttons())
        else:
            lines = ["🎫 Ваши активные записи:\n"]
            for r in active:
                lines.append(
                    f"• {r.event.title} — {_format_date_short(r.event.event_date)} — код {r.registration_code}"
                )
            client.send_message(max_user_id, "\n".join(lines), buttons=_menu_buttons())


# ============================================================
# Утилиты
# ============================================================


def _format_date_short(dt: datetime) -> str:
    return dt.astimezone().strftime("%d.%m %H:%M")


def _format_date_full(dt: datetime) -> str:
    return dt.astimezone().strftime("%d.%m.%Y в %H:%M")
