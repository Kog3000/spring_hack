"""Создаёт все таблицы в БД. Запускается один раз перед первым стартом.

Usage:
    python -m migrations.init_db
"""
from app.db import init_db
from app.logging_setup import setup_logging, get_logger


def main():
    setup_logging()
    log = get_logger(__name__)
    log.info("Создание таблиц...")
    init_db()
    log.info("Готово.")


if __name__ == "__main__":
    main()
