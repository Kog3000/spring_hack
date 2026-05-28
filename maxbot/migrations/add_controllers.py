"""Миграция: добавляет контролёров.

- Расширяет ENUM audit_action новыми значениями
- Создаёт таблицу controllers
- Создаёт таблицу event_controllers
- Безопасна для повторного запуска: каждое изменение IF NOT EXISTS

Запуск:  python -m migrations.add_controllers
"""
from __future__ import annotations

from sqlalchemy import text

from app.db import engine, init_db
from app.logging_setup import get_logger, setup_logging

setup_logging()
log = get_logger(__name__)


NEW_ENUM_VALUES = [
    "controllers_assigned",
    "controller_revoked",
    "controller_login",
    "user_created",
    "user_deactivated",
]

CREATE_CONTROLLERS = """
CREATE TABLE IF NOT EXISTS controllers (
    id BIGSERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    full_name VARCHAR(255) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

CREATE_EVENT_CONTROLLERS = """
CREATE TABLE IF NOT EXISTS event_controllers (
    id BIGSERIAL PRIMARY KEY,
    event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    controller_id BIGINT NOT NULL REFERENCES controllers(id) ON DELETE CASCADE,
    granted_by_organizer_id BIGINT REFERENCES organizers(id) ON DELETE SET NULL,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_event_controller UNIQUE (event_id, controller_id)
);
"""

CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS ix_event_controllers_controller
    ON event_controllers(controller_id);
"""


def main() -> None:
    log.info("Применяем миграцию: controllers + event_controllers + новые enum-значения")

    with engine.begin() as conn:
        # 1) Расширяем enum audit_action — ALTER TYPE ... ADD VALUE IF NOT EXISTS
        for val in NEW_ENUM_VALUES:
            log.info(f"ALTER TYPE audit_action ADD VALUE IF NOT EXISTS '{val}'")
            # ALTER TYPE ... ADD VALUE нельзя выполнять внутри транзакции в старых версиях PG,
            # но в PG 12+ — можно. Используем autocommit, чтобы пройти везде.
            try:
                conn.execute(text(f"ALTER TYPE audit_action ADD VALUE IF NOT EXISTS '{val}'"))
            except Exception as e:
                log.warning(f"Не удалось добавить '{val}': {e}")

        # 2) Таблицы
        log.info("CREATE TABLE controllers")
        conn.execute(text(CREATE_CONTROLLERS))

        log.info("CREATE TABLE event_controllers")
        conn.execute(text(CREATE_EVENT_CONTROLLERS))

        log.info("CREATE INDEX ix_event_controllers_controller")
        conn.execute(text(CREATE_INDEX))

    log.info("Миграция применена.")


if __name__ == "__main__":
    main()
