"""Создаёт три тестовых мероприятия для демо.

Usage:
    python -m migrations.seed_events [--organizer-email organizer@mirea.ru]
"""
import argparse
from datetime import datetime, timedelta, timezone

from app.db import session_scope, init_db
from app.logging_setup import get_logger, setup_logging
from app.models import Event, EventFormat
from app.repositories.repos import OrganizerRepo


def main():
    setup_logging()
    log = get_logger(__name__)

    parser = argparse.ArgumentParser()
    parser.add_argument("--organizer-email", default="organizer@mirea.ru")
    args = parser.parse_args()

    init_db()

    now = datetime.now(timezone.utc)
    events = [
        dict(
            title="День открытых дверей ИИИ",
            description=(
                "Полная экскурсия по институту: лаборатории робототехники, "
                "встреча с деканом, ответы на вопросы по поступлению. "
                "Что взять с собой: паспорт для прохода в корпус."
            ),
            event_date=now + timedelta(days=7),
            duration_minutes=120,
            format=EventFormat.offline,
            location="Москва, проспект Вернадского, 78, ауд. А-101",
            max_participants=50,
        ),
        dict(
            title="Пробное занятие по Python",
            description=(
                "Знакомство с Python для будущих первокурсников. "
                "Установка окружения, первая программа, основы синтаксиса. "
                "Опыт программирования не требуется."
            ),
            event_date=now + timedelta(days=10),
            duration_minutes=90,
            format=EventFormat.offline,
            location="Москва, проспект Вернадского, 78, ауд. В-301",
            max_participants=25,
        ),
        dict(
            title="Онлайн-консультация по поступлению",
            description=(
                "Разбор правил приёма, баллов ЕГЭ, перечня документов. "
                "Можно задать любые вопросы голосом или в чате."
            ),
            event_date=now + timedelta(days=3),
            duration_minutes=60,
            format=EventFormat.online,
            location="https://jazz.sber.ru/abc-def-ghi",
            max_participants=100,
        ),
    ]

    with session_scope() as s:
        org = OrganizerRepo.get_by_email(s, args.organizer_email)
        if org is None:
            raise SystemExit(
                f"Не найден организатор {args.organizer_email}. "
                "Сначала запустите: python -m migrations.create_users"
            )
        for e in events:
            ev = Event(organizer_id=org.id, **e)
            s.add(ev)
            log.info("Создано: %s", e["title"])


if __name__ == "__main__":
    main()
