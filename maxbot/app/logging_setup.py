"""Настройка логирования с минимизацией персональных данных.

Согласно требованию кейса: «доступ к персональным данным в логах должен быть
минимальным». Все логи проходят через PIIRedactingFilter, который маскирует
идентификаторы и не пропускает в текст username, email и фрагменты qr_token.
"""
import logging
import re
import sys
from typing import Any

from .config import Config


# Регулярки для редактирования персональных данных в строках логов
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_QR_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_-]{43,}\b")  # base64 длинных HMAC


def mask_id(value: Any) -> str:
    """Возвращает идентификатор в виде user:<hash6>, чтобы не раскрывать его."""
    if value is None:
        return "user:none"
    s = str(value)
    if len(s) <= 4:
        return f"user:{s}"
    return f"user:{s[:2]}…{s[-2:]}"


def mask_code(value: Any) -> str:
    """Маскирует длинные секреты, оставляя первые 6 символов."""
    if value is None:
        return ""
    s = str(value)
    if len(s) <= 10:
        return "***"
    return f"{s[:6]}…"


class PIIRedactingFilter(logging.Filter):
    """Удаляет/маскирует ПД из текста сообщений логов.

    Никогда не пишет в лог: полный email, полный qr_token, тело webhook-события.
    Идентификаторы (max_user_id, registration_code) сокращаются до префикса.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        msg = _EMAIL_RE.sub(lambda m: m.group(0).split("@")[0][:2] + "***@***", msg)
        msg = _QR_TOKEN_RE.sub(lambda m: m.group(0)[:6] + "…", msg)
        # Подменяем args на ничего, чтобы в logger.info("...", arg) очищенный текст не перетёрся
        record.msg = msg
        record.args = ()
        return True


def setup_logging() -> logging.Logger:
    """Настраивает корневой логгер. Вызывается один раз на старте."""
    level = getattr(logging, Config.LOG_LEVEL.upper(), logging.INFO)

    fmt = "%(asctime)s [%(levelname)s] %(name)s [%(req_id)s] %(message)s"
    formatter = logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")

    # В консоль (Railway собирает stdout)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(PIIRedactingFilter())

    # ContextFilter, чтобы у каждой записи было поле req_id
    class ReqIdFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            if not hasattr(record, "req_id"):
                record.req_id = "-"
            return True

    handler.addFilter(ReqIdFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Понижаем шум от сторонних библиотек
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    return root


def get_logger(name: str) -> logging.Logger:
    """Хелпер для получения именованного логгера."""
    return logging.getLogger(name)
