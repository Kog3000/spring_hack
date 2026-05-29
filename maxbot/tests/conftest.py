import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sqlalchemy import BigInteger
from sqlalchemy.ext.compiler import compiles


@compiles(BigInteger, "sqlite")
def _compile_big_integer_sqlite(type_, compiler, **kw):
    return "INTEGER"

# Чтобы импорты вида `from app...` работали при запуске pytest из папки maxbot
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Без этих переменных app.main.create_app() падает на Config.validate()
os.environ.setdefault("MAX_BOT_TOKEN", "test-token")
os.environ.setdefault("PUBLIC_BASE_URL", "https://example.test")
os.environ.setdefault("WEBHOOK_SECRET", "test-webhook-secret")
os.environ.setdefault("QR_SECRET", "test-qr-secret")
os.environ.setdefault("FLASK_SECRET_KEY", "test-flask-secret")
os.environ.setdefault("DATABASE_URL", "postgresql://maxbot:maxbot@localhost:5432/maxbot_test")
os.environ.setdefault("LOG_LEVEL", "CRITICAL")

from app.models import Base, EventFormat  # noqa: E402
from app.repositories.repos import EventRepo, OrganizerRepo, UserRepo  # noqa: E402
from app.services.passwords import hash_password  # noqa: E402


@pytest.fixture()
def engine():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def db_session(engine):
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def organizer(db_session):
    org = OrganizerRepo.create(
        db_session,
        email="Organizer@Test.RU",
        password_hash=hash_password("password123"),
        full_name="Тестовый организатор",
    )
    db_session.commit()
    return org


@pytest.fixture()
def user(db_session):
    user = UserRepo.upsert_with_consent(
        db_session,
        max_user_id=100500,
        username="Тестовый пользователь",
        consent_version="v1.0",
    )
    db_session.commit()
    return user


@pytest.fixture()
def event(db_session, organizer):
    from datetime import datetime, timedelta, timezone

    ev = EventRepo.create(
        db_session,
        organizer_id=organizer.id,
        title="День открытых дверей",
        description="Описание мероприятия",
        event_date=datetime.now(timezone.utc) + timedelta(days=3),
        duration_minutes=90,
        format_=EventFormat.offline,
        location="Аудитория 101",
        max_participants=2,
    )
    db_session.commit()
    return ev
