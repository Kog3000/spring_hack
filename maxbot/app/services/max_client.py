"""Клиент MAX Bot API.

Документация: https://dev.max.ru/docs-api
Базовый URL: https://platform-api.max.ru

Авторизация: заголовок `Authorization: <token>` (без префикса Bearer).
Все методы возвращают разобранный JSON или выбрасывают исключение при ошибке.
"""
from __future__ import annotations

import time
from typing import Any, Optional

import requests

from ..config import Config
from ..logging_setup import get_logger, mask_id

log = get_logger(__name__)


class MaxAPIError(RuntimeError):
    """Любая ошибка при обращении к MAX Bot API."""


class MaxBotClient:
    """Тонкая обёртка над HTTP API. Только методы, которые нужны боту."""

    def __init__(self, token: Optional[str] = None, base_url: Optional[str] = None):
        self.token = token or Config.MAX_BOT_TOKEN
        self.base_url = (base_url or Config.MAX_API_BASE).rstrip("/")
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": self.token,
                "Content-Type": "application/json",
            }
        )

    # ------------------------------------------------------------------
    # Низкоуровневые методы
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs) -> dict:
        """Делает HTTP-запрос с авто-ретраями на сетевых ошибках.

        Пытаемся до 3 раз с увеличивающимися паузами, чтобы пережить нестабильное
        соединение к MAX API. Timeout по умолчанию 60 сек на каждый запрос.
        """
        url = f"{self.base_url}{path}"
        timeout = kwargs.pop("timeout", 60)
        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                resp = self._session.request(method, url, timeout=timeout, **kwargs)
                break
            except (requests.Timeout, requests.ConnectionError) as e:
                last_err = e
                wait = 2 * (attempt + 1)
                log.warning(
                    "MAX API сеть (попытка %s/3): %s %s — %s. Ждём %s с",
                    attempt + 1, method, path, e, wait,
                )
                time.sleep(wait)
            except requests.RequestException as e:
                log.error("MAX API сеть: %s %s — %s", method, path, e)
                raise MaxAPIError(f"network error: {e}") from e
        else:
            log.error("MAX API сеть после 3 попыток: %s %s — %s", method, path, last_err)
            raise MaxAPIError(f"network error: {last_err}") from last_err

        if resp.status_code >= 400:
            log.error("MAX API ошибка %s: %s %s", resp.status_code, method, path)
            raise MaxAPIError(f"HTTP {resp.status_code}: {resp.text[:200]}")

        try:
            return resp.json()
        except ValueError as e:
            raise MaxAPIError(f"невалидный JSON в ответе: {e}") from e

    # ------------------------------------------------------------------
    # Высокоуровневые методы
    # ------------------------------------------------------------------

    def get_me(self) -> dict:
        """Возвращает информацию о боте по токену. Используется для проверки на старте."""
        return self._request("GET", "/me")

    def get_subscriptions(self) -> dict:
        """Список Webhook-подписок бота."""
        return self._request("GET", "/subscriptions")

    def subscribe_webhook(self, url: str, secret: str) -> dict:
        """Подписывает бота на события через webhook."""
        body = {
            "url": url,
            "update_types": ["message_created", "bot_started", "message_callback"],
            "secret": secret,
        }
        return self._request("POST", "/subscriptions", json=body)

    def unsubscribe_webhook(self, url: str) -> dict:
        return self._request("DELETE", f"/subscriptions?url={url}")

    # ------------------------------------------------------------------
    # Загрузка картинок (для QR)
    # ------------------------------------------------------------------

    def upload_image(self, image_bytes: bytes, filename: str = "qr.png") -> Optional[str]:
        """Загружает картинку в MAX и возвращает token для использования в сообщении.

        1) POST /uploads?type=image — получаем upload URL
        2) Multipart POST на upload URL — заливаем файл
        3) Token может прийти в ответе любого из двух шагов под одним из ключей:
           token, photo_token, file_token, id, file_id и т.п.

        Подробно логируем, чтобы видеть формат ответа MAX в журнале.
        """
        import json as _json

        def _extract_token(data: dict) -> Optional[str]:
            """Ищет токен в JSON-ответе MAX по любому из возможных ключей."""
            if not isinstance(data, dict):
                return None
            # Прямые ключи
            for key in ("token", "photo_token", "file_token", "id", "file_id", "fileId"):
                val = data.get(key)
                if val and isinstance(val, str):
                    return val
            # Вложенные структуры: photo / photos / attachment / payload
            # MAX возвращает photos как dict: {"<photo_id>": {"token": "..."}}
            for nest in ("photo", "photos", "attachment", "payload"):
                inner = data.get(nest)
                if isinstance(inner, dict):
                    # Сначала пробуем извлечь токен прямо из inner
                    t = _extract_token(inner)
                    if t:
                        return t
                    # Затем — пробежаться по значениям (для photos {id: {token}})
                    for v in inner.values():
                        if isinstance(v, dict):
                            t = _extract_token(v)
                            if t:
                                return t
                elif isinstance(inner, list) and inner:
                    for item in inner:
                        if isinstance(item, dict):
                            t = _extract_token(item)
                            if t:
                                return t
            return None

        try:
            # ШАГ 1
            upload_url_resp = self._session.post(
                f"{self.base_url}/uploads?type=image",
                timeout=15,
            )
            log.info("MAX upload step1: HTTP %s, body=%s",
                     upload_url_resp.status_code, upload_url_resp.text[:400])
            if upload_url_resp.status_code >= 400:
                return None
            upload_data = upload_url_resp.json()
            upload_url = upload_data.get("url")
            if not upload_url:
                log.error("MAX upload_image: нет url в step1")
                return None

            # Если токен пришёл уже на шаге 1 — можно сразу взять
            token_from_step1 = _extract_token(upload_data)

            # ШАГ 2
            files = {"data": (filename, image_bytes, "image/png")}
            up_resp = requests.post(upload_url, files=files, timeout=30)
            body_preview = up_resp.text[:400] if up_resp.content else ""
            log.info("MAX upload step2: HTTP %s, body=%s",
                     up_resp.status_code, body_preview)
            if up_resp.status_code >= 400:
                return None

            up_data = {}
            if up_resp.content:
                try:
                    up_data = up_resp.json()
                except (_json.JSONDecodeError, ValueError):
                    up_data = {}

            token = _extract_token(up_data) or token_from_step1
            if not token:
                log.error(
                    "MAX upload_image: token не найден. step1_keys=%s step2_keys=%s",
                    list(upload_data.keys()),
                    list(up_data.keys()) if isinstance(up_data, dict) else "non-dict",
                )
                return None

            log.info("MAX upload_image OK: token получен")
            return token
        except Exception as e:
            log.error("MAX upload_image exception: %s", e)
            return None

    def send_message(
        self,
        user_id: int,
        text: str,
        buttons: Optional[list[list[dict]]] = None,
        image_token: Optional[str] = None,
    ) -> dict:
        """Отправить сообщение пользователю.

        buttons — двумерный массив [[{type, text, payload|url}]]
        image_token — токен ранее загруженной через upload_image() картинки
        """
        body: dict[str, Any] = {"text": text}
        attachments: list[dict] = []
        if image_token:
            attachments.append({"type": "image", "payload": {"token": image_token}})
        if buttons:
            attachments.append(
                {"type": "inline_keyboard", "payload": {"buttons": buttons}}
            )
        if attachments:
            body["attachments"] = attachments
        log.info(
            "MAX send_message → %s, кнопок=%s, image=%s",
            mask_id(user_id),
            len(buttons or []),
            bool(image_token),
        )
        # Если картинка не «прогрелась», MAX может вернуть attachment.not_ready —
        # повторяем 2 раза с паузой.
        last_err: Optional[MaxAPIError] = None
        for attempt in range(3):
            try:
                return self._request("POST", f"/messages?user_id={user_id}", json=body)
            except MaxAPIError as e:
                if "not_ready" in str(e) or "not.ready" in str(e):
                    last_err = e
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise
        if last_err:
            raise last_err
        return {}

    def answer_callback(
        self,
        callback_id: str,
        text: Optional[str] = None,
        notification: Optional[str] = None,
        buttons: Optional[list[list[dict]]] = None,
    ) -> dict:
        body: dict[str, Any] = {}
        if notification:
            body["notification"] = notification
        if text is not None:
            msg: dict[str, Any] = {"text": text}
            if buttons:
                msg["attachments"] = [
                    {"type": "inline_keyboard", "payload": {"buttons": buttons}}
                ]
            body["message"] = msg
        return self._request("POST", f"/answers?callback_id={callback_id}", json=body)
