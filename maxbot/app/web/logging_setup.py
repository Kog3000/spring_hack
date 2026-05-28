"""Веб-приложение организатора и технического администратора.

Маршруты:
  /admin/login           — общий логин для обеих ролей (роль определяется по email)
  /admin/logout          — выход
  /admin                 — дашборд: список мероприятий
  /admin/events/<id>     — карточка мероприятия: участники, кнопки, экспорт
  /admin/events/<id>/scan — страница QR-скана с камерой браузера
  /admin/scan/verify     — API проверки QR (POST с qr_token)
  /admin/events/<id>/close — закрыть регистрацию
  /admin/events/<id>/notify — отправить push-уведомление участникам
  /admin/events/<id>/export — выгрузка XLSX
  /admin/audit           — журнал аудита (только tech_admin)
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from functools import wraps
from typing import Optional

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    session as flask_session,
    url_for,
    jsonify,
)
from sqlalchemy import select

from ..config import Config
from ..db import session_scope
from ..logging_setup import get_logger, mask_id
from ..models import (
    AuditAction,
    AuditLog,
    Notification,
    NotificationType,
    Registration,
    RegistrationStatus,
)
from ..repositories.repos import (
    AdminRepo,
    EventRepo,
    NotificationRepo,
    OrganizerRepo,
    RegistrationRepo,
    UserRepo,
)
from ..services import audit
from ..services.codes import verify_qr_token
from ..services.max_client import MaxBotClient
from ..services.passwords import verify_password

log = get_logger(__name__)

admin_bp = Blueprint(
    "admin",
    __name__,
    url_prefix="/admin",
    template_folder="templates",
    static_folder="static",
)


# ============================================================
# Аутентификация
# ============================================================


def _current_user():
    """Возвращает кортеж (role, id) или None. role ∈ {'organizer', 'admin'}."""
    role = flask_session.get("role")
    uid = flask_session.get("uid")
    if not role or not uid:
        return None
    return role, uid


def login_required(roles: Optional[list[str]] = None):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            cu = _current_user()
            if cu is None:
                return redirect(url_for("admin.login", next=request.path))
            role, _ = cu
            if roles and role not in roles:
                abort(403)
            g.current_role = role
            return fn(*args, **kwargs)

        return wrapper

    return deco


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""

    if not email or not password:
        flash("Заполните email и пароль", "error")
        return render_template("login.html"), 400

    with session_scope() as s:
        # Пытаемся найти сначала среди админов, потом среди организаторов
        adm = AdminRepo.get_by_email(s, email)
        if adm and adm.is_active and verify_password(password, adm.password_hash):
            flask_session["role"] = "admin"
            flask_session["uid"] = adm.id
            flask_session["name"] = adm.display_name
            audit.write(
                s,
                AuditAction.admin_login,
                admin_id=adm.id,
                ip_address=request.remote_addr,
            )
            log.info("ADMIN login uid=%s", mask_id(adm.id))
            return redirect(request.args.get("next") or url_for("admin.dashboard"))

        org = OrganizerRepo.get_by_email(s, email)
        if org and org.is_active and verify_password(password, org.password_hash):
            flask_session["role"] = "organizer"
            flask_session["uid"] = org.id
            flask_session["name"] = org.full_name
            audit.write(
                s,
                AuditAction.organizer_login,
                organizer_id=org.id,
                ip_address=request.remote_addr,
            )
            log.info("ORGANIZER login uid=%s", mask_id(org.id))
            return redirect(request.args.get("next") or url_for("admin.dashboard"))

    flash("Неверный email или пароль", "error")
    return render_template("login.html"), 401


@admin_bp.route("/logout")
def logout():
    flask_session.clear()
    return redirect(url_for("admin.login"))


# ============================================================
# Дашборд
# ============================================================


@admin_bp.route("/")
@login_required()
def dashboard():
    role, uid = _current_user()
    with session_scope() as s:
        if role == "admin":
            events = EventRepo.list_all_for_admin(s)
        else:
            events = EventRepo.list_by_organizer(s, uid)
        # Свободные места
        rows = []
        for ev in events:
            taken = RegistrationRepo.count_confirmed(s, ev.id)
            rows.append(
                {
                    "id": ev.id,
                    "title": ev.title,
                    "date": ev.event_date,
                    "format": ev.format.value,
                    "location": ev.location,
                    "taken": taken,
                    "total": ev.max_participants,
                    "closed": ev.registration_closed,
                }
            )
    return render_template(
        "dashboard.html", events=rows, role=role, name=flask_session.get("name")
    )


# ============================================================
# Карточка мероприятия
# ============================================================


@admin_bp.route("/events/<int:event_id>")
@login_required()
def event_detail(event_id: int):
    role, uid = _current_user()
    with session_scope() as s:
        ev = EventRepo.get(s, event_id)
        if ev is None:
            abort(404)
        if role == "organizer" and ev.organizer_id != uid:
            abort(403)

        regs = RegistrationRepo.list_for_event(s, event_id)
        # Отдаём чистые dict, чтобы избежать DetachedInstanceError после закрытия сессии
        reg_rows = [
            {
                "id": r.id,
                "code": r.registration_code,
                "username": r.user.username,
                "status": r.status.value,
                "created_at": r.created_at,
                "attended_at": r.attended_at,
                "cancelled_at": r.cancelled_at,
            }
            for r in regs
        ]
        notifs = NotificationRepo.list_for_event(s, event_id)
        notif_rows = [
            {
                "id": n.id,
                "type": n.type.value,
                "body": n.body,
                "sent_at": n.sent_at,
                "delivered": n.delivered_count,
                "failed": n.failed_count,
            }
            for n in notifs
        ]
        taken = sum(1 for r in regs if r.status == RegistrationStatus.confirmed)
        attended = sum(1 for r in regs if r.status == RegistrationStatus.attended)

        event_data = {
            "id": ev.id,
            "title": ev.title,
            "description": ev.description,
            "date": ev.event_date,
            "duration_minutes": ev.duration_minutes,
            "format": ev.format.value,
            "location": ev.location,
            "max": ev.max_participants,
            "taken": taken,
            "attended": attended,
            "closed": ev.registration_closed,
        }
    return render_template(
        "event_detail.html",
        event=event_data,
        regs=reg_rows,
        notifs=notif_rows,
        role=role,
    )


@admin_bp.route("/events/<int:event_id>/close", methods=["POST"])
@login_required()
def event_close(event_id: int):
    role, uid = _current_user()
    with session_scope() as s:
        ev = EventRepo.get(s, event_id)
        if ev is None:
            abort(404)
        if role == "organizer" and ev.organizer_id != uid:
            abort(403)
        ev.registration_closed = True
        audit.write(
            s,
            AuditAction.registration_closed,
            organizer_id=uid if role == "organizer" else None,
            admin_id=uid if role == "admin" else None,
            event_id=event_id,
            ip_address=request.remote_addr,
        )
    flash("Регистрация закрыта", "success")
    return redirect(url_for("admin.event_detail", event_id=event_id))


@admin_bp.route("/events/<int:event_id>/open", methods=["POST"])
@login_required()
def event_open(event_id: int):
    """Открыть ранее закрытую регистрацию."""
    role, uid = _current_user()
    with session_scope() as s:
        ev = EventRepo.get(s, event_id)
        if ev is None:
            abort(404)
        if role == "organizer" and ev.organizer_id != uid:
            abort(403)
        EventRepo.open_registration(s, event_id)
        audit.write(
            s,
            AuditAction.event_updated,
            organizer_id=uid if role == "organizer" else None,
            admin_id=uid if role == "admin" else None,
            event_id=event_id,
            payload={"action": "open_registration"},
            ip_address=request.remote_addr,
        )
    flash("Регистрация открыта", "success")
    return redirect(url_for("admin.event_detail", event_id=event_id))


def _parse_event_form() -> Optional[dict]:
    """Разбирает форму создания/редактирования. Возвращает dict или None при ошибке."""
    try:
        title = (request.form.get("title") or "").strip()
        description = (request.form.get("description") or "").strip()
        date_str = (request.form.get("event_date") or "").strip()
        duration = int(request.form.get("duration_minutes") or "0")
        format_val = (request.form.get("format") or "offline").strip()
        location = (request.form.get("location") or "").strip()
        max_p = int(request.form.get("max_participants") or "0")

        if not title or not description or not date_str or not location:
            flash("Заполните все обязательные поля", "error")
            return None
        if duration <= 0:
            flash("Длительность должна быть положительной", "error")
            return None
        if max_p <= 0:
            flash("Лимит участников должен быть положительным", "error")
            return None
        try:
            from ..models import EventFormat as _EF  # локально, чтобы не циклить
            fmt = _EF(format_val)
        except ValueError:
            flash("Неверный формат мероприятия", "error")
            return None

        # datetime-local приходит как "2026-06-01T11:00"
        try:
            naive = datetime.strptime(date_str, "%Y-%m-%dT%H:%M")
        except ValueError:
            flash("Неверный формат даты", "error")
            return None
        # Считаем что время указано в локальной зоне сервера; делаем aware UTC.
        # Для MVP в большинстве случаев сервер в Moscow — этого достаточно.
        event_dt = naive.replace(tzinfo=timezone.utc)

        return {
            "title": title,
            "description": description,
            "event_date": event_dt,
            "duration_minutes": duration,
            "format_": fmt,
            "location": location,
            "max_participants": max_p,
        }
    except Exception as e:
        flash(f"Ошибка обработки формы: {e}", "error")
        return None


@admin_bp.route("/events/new", methods=["GET", "POST"])
@login_required()
def event_new():
    role, uid = _current_user()
    if request.method == "GET":
        return render_template("event_form.html", mode="new", event=None)

    data = _parse_event_form()
    if data is None:
        return render_template("event_form.html", mode="new", event=None), 400

    with session_scope() as s:
        if role == "organizer":
            organizer_id = uid
        else:
            # Админ создаёт мероприятие от имени первого активного организатора.
            # В будущем можно добавить выбор организатора в форме.
            from ..models import Organizer as _Org
            first_org = s.scalar(select(_Org).where(_Org.is_active.is_(True)).limit(1))
            if first_org is None:
                flash(
                    "Нет ни одного активного организатора. Создайте его через "
                    "python -m migrations.create_users",
                    "error",
                )
                return redirect(url_for("admin.dashboard"))
            organizer_id = first_org.id

        ev = EventRepo.create(s, organizer_id=organizer_id, **data)
        audit.write(
            s,
            AuditAction.event_created,
            organizer_id=uid if role == "organizer" else None,
            admin_id=uid if role == "admin" else None,
            event_id=ev.id,
            payload={"title": data["title"]},
            ip_address=request.remote_addr,
        )
        flash(f"Мероприятие «{data['title']}» создано", "success")
        return redirect(url_for("admin.event_detail", event_id=ev.id))


@admin_bp.route("/events/<int:event_id>/edit", methods=["GET", "POST"])
@login_required()
def event_edit(event_id: int):
    role, uid = _current_user()
    with session_scope() as s:
        ev = EventRepo.get(s, event_id)
        if ev is None:
            abort(404)
        if role == "organizer" and ev.organizer_id != uid:
            abort(403)

        if request.method == "GET":
            event_data = {
                "id": ev.id,
                "title": ev.title,
                "description": ev.description,
                "event_date": ev.event_date,
                "duration_minutes": ev.duration_minutes,
                "format": ev.format.value,
                "location": ev.location,
                "max_participants": ev.max_participants,
            }
            return render_template("event_form.html", mode="edit", event=event_data)

    # POST
    data = _parse_event_form()
    if data is None:
        # Перерисуем форму с введёнными данными — простой вариант: вернуть с GET
        return redirect(url_for("admin.event_edit", event_id=event_id))

    with session_scope() as s:
        ev = EventRepo.get(s, event_id)
        if ev is None:
            abort(404)
        if role == "organizer" and ev.organizer_id != uid:
            abort(403)
        EventRepo.update(s, event_id, **data)
        audit.write(
            s,
            AuditAction.event_updated,
            organizer_id=uid if role == "organizer" else None,
            admin_id=uid if role == "admin" else None,
            event_id=event_id,
            payload={"title": data["title"]},
            ip_address=request.remote_addr,
        )
        flash("Изменения сохранены", "success")
        return redirect(url_for("admin.event_detail", event_id=event_id))


@admin_bp.route("/events/<int:event_id>/delete", methods=["POST"])
@login_required()
def event_delete(event_id: int):
    """Удаляет мероприятие если на нём нет участников. Если есть — не даёт."""
    role, uid = _current_user()
    with session_scope() as s:
        ev = EventRepo.get(s, event_id)
        if ev is None:
            abort(404)
        if role == "organizer" and ev.organizer_id != uid:
            abort(403)
        regs_count = RegistrationRepo.count_confirmed(s, event_id)
        if regs_count > 0:
            flash(
                f"Нельзя удалить: уже записано {regs_count} участников. "
                "Сначала закройте регистрацию или отмените записи.",
                "error",
            )
            return redirect(url_for("admin.event_detail", event_id=event_id))
        EventRepo.delete(s, event_id)
        audit.write(
            s,
            AuditAction.event_updated,
            organizer_id=uid if role == "organizer" else None,
            admin_id=uid if role == "admin" else None,
            event_id=event_id,
            payload={"action": "deleted"},
            ip_address=request.remote_addr,
        )
    flash("Мероприятие удалено", "success")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/events/<int:event_id>/notify", methods=["POST"])
@login_required(roles=["organizer", "admin"])
def event_notify(event_id: int):
    role, uid = _current_user()
    body_text = (request.form.get("body") or "").strip()
    type_val = request.form.get("type") or "info"
    if not body_text:
        flash("Пустое сообщение", "error")
        return redirect(url_for("admin.event_detail", event_id=event_id))

    try:
        ntype = NotificationType(type_val)
    except ValueError:
        flash("Некорректный тип уведомления", "error")
        return redirect(url_for("admin.event_detail", event_id=event_id))

    with session_scope() as s:
        ev = EventRepo.get(s, event_id)
        if ev is None:
            abort(404)
        if role == "organizer" and ev.organizer_id != uid:
            abort(403)

        regs = [
            r
            for r in RegistrationRepo.list_for_event(s, event_id)
            if r.status == RegistrationStatus.confirmed and r.notifications_enabled
        ]

        n = NotificationRepo.create(
            s,
            event_id=event_id,
            organizer_id=ev.organizer_id,
            type_=ntype,
            body=body_text,
        )
        # Отправляем через MAX
        client = MaxBotClient()
        delivered = 0
        failed = 0
        text = f"🔔 «{ev.title}»\n\n{body_text}"
        for r in regs:
            try:
                client.send_message(r.user.max_user_id, text)
                delivered += 1
            except Exception as e:
                failed += 1
                log.warning("notify fail user=%s: %s", mask_id(r.user.max_user_id), e)
        NotificationRepo.update_stats(s, n.id, delivered=delivered, failed=failed)

        audit.write(
            s,
            AuditAction.notification_sent,
            organizer_id=uid if role == "organizer" else None,
            admin_id=uid if role == "admin" else None,
            event_id=event_id,
            payload={
                "type": ntype.value,
                "delivered": delivered,
                "failed": failed,
            },
            ip_address=request.remote_addr,
        )

    flash(f"Отправлено: {delivered}, ошибок: {failed}", "success")
    return redirect(url_for("admin.event_detail", event_id=event_id))


# ============================================================
# QR-сканер
# ============================================================


@admin_bp.route("/events/<int:event_id>/scan")
@login_required()
def event_scan(event_id: int):
    role, uid = _current_user()
    with session_scope() as s:
        ev = EventRepo.get(s, event_id)
        if ev is None:
            abort(404)
        if role == "organizer" and ev.organizer_id != uid:
            abort(403)
        event_data = {"id": ev.id, "title": ev.title}
    return render_template("scan.html", event=event_data)


@admin_bp.route("/scan/verify", methods=["POST"])
@login_required()
def scan_verify():
    """API проверки QR. Принимает {token} в JSON, возвращает {ok, reason, participant}."""
    role, uid = _current_user()
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    event_id = data.get("event_id")

    if not token or not event_id:
        return jsonify({"ok": False, "reason": "Нет токена"}), 400

    reg_id = verify_qr_token(token)
    if reg_id is None:
        log.warning("SCAN bad signature event=%s", event_id)
        return jsonify({"ok": False, "reason": "Подпись QR не прошла"})

    with session_scope() as s:
        reg = RegistrationRepo.get(s, reg_id)
        if reg is None:
            return jsonify({"ok": False, "reason": "Запись не найдена"})
        if reg.event_id != int(event_id):
            return jsonify({"ok": False, "reason": "QR от другого мероприятия"})
        if role == "organizer" and reg.event.organizer_id != uid:
            return jsonify({"ok": False, "reason": "Нет прав на мероприятие"}), 403
        if reg.status == RegistrationStatus.cancelled:
            return jsonify({"ok": False, "reason": "Запись отменена"})
        if reg.status == RegistrationStatus.attended:
            return jsonify(
                {
                    "ok": False,
                    "reason": "Уже отмечен пришедшим",
                    "participant": reg.user.username,
                    "code": reg.registration_code,
                }
            )

        ok = RegistrationRepo.mark_attended(s, reg.id)
        if not ok:
            return jsonify({"ok": False, "reason": "Не удалось отметить"})

        audit.write(
            s,
            AuditAction.attended_marked,
            organizer_id=uid if role == "organizer" else None,
            admin_id=uid if role == "admin" else None,
            event_id=reg.event_id,
            registration_id=reg.id,
            ip_address=request.remote_addr,
        )
        return jsonify(
            {
                "ok": True,
                "reason": "Допущен",
                "participant": reg.user.username,
                "code": reg.registration_code,
            }
        )


# ============================================================
# Экспорт
# ============================================================


@admin_bp.route("/events/<int:event_id>/export")
@login_required()
def event_export(event_id: int):
    from openpyxl import Workbook  # импорт здесь, чтобы не грузить при импорте модуля

    role, uid = _current_user()
    with session_scope() as s:
        ev = EventRepo.get(s, event_id)
        if ev is None:
            abort(404)
        if role == "organizer" and ev.organizer_id != uid:
            abort(403)

        regs = RegistrationRepo.list_for_event(s, event_id)
        wb = Workbook()
        ws = wb.active
        ws.title = "Участники"
        ws.append(["Код записи", "Имя профиля MAX", "Статус", "Записан", "Пришёл"])
        for r in regs:
            ws.append(
                [
                    r.registration_code,
                    r.user.username,
                    r.status.value,
                    r.created_at.strftime("%Y-%m-%d %H:%M"),
                    r.attended_at.strftime("%Y-%m-%d %H:%M") if r.attended_at else "",
                ]
            )

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

    filename = f"event_{event_id}_participants.xlsx"
    return send_file(
        buf,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ============================================================
# Журнал аудита (только tech admin)
# ============================================================


@admin_bp.route("/audit")
@login_required(roles=["admin"])
def audit_log():
    with session_scope() as s:
        stmt = (
            select(AuditLog)
            .order_by(AuditLog.created_at.desc())
            .limit(200)
        )
        entries = list(s.scalars(stmt))
        rows = [
            {
                "id": e.id,
                "created_at": e.created_at,
                "action": e.action.value,
                "actor_user_id": e.actor_user_id,
                "actor_organizer_id": e.actor_organizer_id,
                "actor_admin_id": e.actor_admin_id,
                "target_event_id": e.target_event_id,
                "target_registration_id": e.target_registration_id,
                "payload": e.payload,
                "ip_address": e.ip_address,
            }
            for e in entries
        ]
    return render_template("audit.html", rows=rows)
