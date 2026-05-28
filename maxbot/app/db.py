"""Подключение к БД и фабрика сессий."""
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import Config
from .models import Base


def _normalize_db_url(url: str) -> str:
    """Railway отдаёт URL вида postgres://...; SQLAlchemy 2.x ждёт postgresql://..."""
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


engine = create_engine(
    _normalize_db_url(Config.DATABASE_URL),
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    """Создать все таблицы. Использовать при первом запуске или для тестов."""
    Base.metadata.create_all(bind=engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Контекст-менеджер для транзакционной работы с БД."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
