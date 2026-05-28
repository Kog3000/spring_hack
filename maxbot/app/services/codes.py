"""Генерация кода записи и QR-токена.

registration_code — короткий человекочитаемый код вида LMN-1A2B-3C, виден пользователю.
qr_token — длинная HMAC-подпись, подделать без секрета нельзя. Вшита в QR.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import secrets
import string

import qrcode

from ..config import Config


_ALPHABET = string.ascii_uppercase + string.digits  # без I/O/0/1 — слишком похожи


def generate_registration_code() -> str:
    """Случайный код вида LMN-1A2B-3C. 10 значимых символов из безопасного алфавита."""
    safe = "".join(c for c in _ALPHABET if c not in "IO01")
    part1 = "".join(secrets.choice(safe) for _ in range(3))
    part2 = "".join(secrets.choice(safe) for _ in range(4))
    part3 = "".join(secrets.choice(safe) for _ in range(2))
    return f"{part1}-{part2}-{part3}"


def generate_qr_token(registration_id: int) -> str:
    """HMAC-подпись над id записи + случайной соль. Без секрета подделать нельзя."""
    salt = secrets.token_urlsafe(8)
    payload = f"{registration_id}:{salt}"
    sig = hmac.new(
        Config.QR_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{payload}:{sig}"


def verify_qr_token(token: str) -> int | None:
    """Проверяет подпись и возвращает registration_id или None."""
    try:
        parts = token.split(":")
        if len(parts) != 3:
            return None
        reg_id_str, salt, sig = parts
        payload = f"{reg_id_str}:{salt}"
        expected = hmac.new(
            Config.QR_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        return int(reg_id_str)
    except (ValueError, AttributeError):
        return None


def render_qr_png(token: str) -> bytes:
    """Рендерит QR-код в PNG."""
    img = qrcode.make(token, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
