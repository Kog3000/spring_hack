"""SQLAlchemy ORM-модели.

Шесть таблиц:
- users          : абитуриенты с согласием на ПД
- organizers     : сотрудники приёмной комиссии (email + пароль)
- admins         : технические администраторы (email + пароль, доступ ко всему)
- events         : мероприятия
- registrations  : записи (один user — много events, QR-токен на запись)
- notifications  : журнал push-рассылок участникам мероприятия
- audit_log      : журнал всех значимых действий (требование кейса о логировании)
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    JSON,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Базовый класс для всех моделей."""


# ============================================================
# Enum-типы
# ============================================================


class EventFormat(str, enum.Enum):
    online = "online"
    offline = "offline"


class RegistrationStatus(str, enum.Enum):
    confirmed = "confirmed"
    cancelled = "cancelled"
    attended = "attended"


class NotificationType(str, enum.Enum):
    reschedule = "reschedule"        # перенос времени
    location = "location"            # изменение аудитории/ссылки
    reminder_24h = "reminder_24h"    # напоминание за сутки
    reminder_1h = "reminder_1h"      # напоминание за час
    info = "info"                    # произвольное информационное


class AuditAction(str, enum.Enum):
    # действия абитуриента
    consent_given = "consent_given"
    registration_created = "registration_created"
    registration_cancelled = "registration_cancelled"
    notifications_toggled = "notifications_toggled"
    # действия организатора
    organizer_login = "organizer_login"
    event_created = "event_created"
    event_updated = "event_updated"
    registration_closed = "registration_closed"
    attended_marked = "attended_marked"
    notification_sent = "notification_sent"
    controllers_assigned = "controllers_assigned"
    controller_revoked = "controller_revoked"
    # действия контролёра
    controller_login = "controller_login"
    # действия админа
    admin_login = "admin_login"
    admin_action = "admin_action"
    user_created = "user_created"
    user_deactivated = "user_deactivated"


# ============================================================
# users — абитуриенты
# ============================================================


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    max_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    consent_version: Mapped[str] = mapped_column(String(20), nullable=False)
    consent_given_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    registrations: Mapped[list["Registration"]] = relationship(back_populates="user")


# ============================================================
# organizers — сотрудники приёмной комиссии
# ============================================================


class Organizer(Base):
    __tablename__ = "organizers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    events: Mapped[list["Event"]] = relationship(back_populates="organizer")


# ============================================================
# admins — технические администраторы
# ============================================================


class Admin(Base):
    """Технический администратор. Доступ ко всем мероприятиям и журналу аудита.

    Поля профиля минимальны (email + display name): этой роли не нужны и не
    собираются никакие ПД абитуриентов сверх того, что необходимо для работы
    сервиса. См. требование кейса о минимизации.
    """

    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ============================================================
# controllers — контролёры на входе мероприятия
# ============================================================


class Controller(Base):
    """Контролёр на входе. Имеет доступ только к QR-сканеру мероприятий,
    к которым его привязал организатор. Не видит ни список участников
    целиком, ни статистику, ни журнал аудита.
    """

    __tablename__ = "controllers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ============================================================
# event_controllers — кому из контролёров доступно мероприятие
# ============================================================


class EventController(Base):
    """Связь N:M между мероприятиями и контролёрами.
    Один контролёр может быть привязан к нескольким мероприятиям,
    одно мероприятие может иметь нескольких контролёров.
    """

    __tablename__ = "event_controllers"
    __table_args__ = (
        UniqueConstraint("event_id", "controller_id", name="uq_event_controller"),
        Index("ix_event_controllers_controller", "controller_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    controller_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("controllers.id", ondelete="CASCADE"), nullable=False
    )
    granted_by_organizer_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("organizers.id", ondelete="SET NULL")
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ============================================================
# events — мероприятия
# ============================================================


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint("duration_minutes > 0", name="ck_event_duration_positive"),
        CheckConstraint("max_participants > 0", name="ck_event_capacity_positive"),
        Index("ix_events_date", "event_date"),
        Index("ix_events_organizer", "organizer_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organizer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizers.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    event_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    format: Mapped[EventFormat] = mapped_column(
        SAEnum(EventFormat, name="event_format"), nullable=False
    )
    location: Mapped[str] = mapped_column(String(500), nullable=False)
    max_participants: Mapped[int] = mapped_column(Integer, nullable=False)
    registration_closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    organizer: Mapped["Organizer"] = relationship(back_populates="events")
    registrations: Mapped[list["Registration"]] = relationship(back_populates="event")
    notifications: Mapped[list["Notification"]] = relationship(back_populates="event")


# ============================================================
# registrations — записи на мероприятия
# ============================================================


class Registration(Base):
    __tablename__ = "registrations"
    __table_args__ = (
        Index("ix_reg_event_status", "event_id", "status"),
        Index("ix_reg_user", "user_id"),
        # Защита от повторной активной записи: один user — одна confirmed-запись на event
        Index(
            "ix_reg_no_dup",
            "user_id",
            "event_id",
            unique=True,
            postgresql_where=(("status = 'confirmed'")),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    event_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("events.id", ondelete="RESTRICT"), nullable=False
    )
    registration_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    qr_token: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    status: Mapped[RegistrationStatus] = mapped_column(
        SAEnum(RegistrationStatus, name="registration_status"),
        nullable=False,
        default=RegistrationStatus.confirmed,
    )
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    attended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="registrations")
    event: Mapped["Event"] = relationship(back_populates="registrations")


# ============================================================
# notifications — журнал push-уведомлений
# ============================================================


class Notification(Base):
    """Журнал push-уведомлений, отправленных участникам мероприятия.

    Хранит факт рассылки для отчётности. Содержимое — короткое, без ПД.
    Уведомления привязаны к одному мероприятию (нельзя массово рассылать
    всем пользователям бота — это специально не предусмотрено).
    """

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("events.id", ondelete="RESTRICT"), nullable=False
    )
    organizer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizers.id", ondelete="RESTRICT"), nullable=False
    )
    type: Mapped[NotificationType] = mapped_column(
        SAEnum(NotificationType, name="notification_type"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    delivered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    event: Mapped["Event"] = relationship(back_populates="notifications")


# ============================================================
# audit_log — журнал значимых действий
# ============================================================


class AuditLog(Base):
    """Журнал значимых действий. Минимизирует ПД: хранит только идентификаторы и тип.

    Используется для расследования инцидентов и общей наблюдаемости. Поле payload —
    JSON со служебной информацией (не должно содержать ПД, только id и константы).
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_created", "created_at"),
        Index("ix_audit_action", "action"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    action: Mapped[AuditAction] = mapped_column(
        SAEnum(AuditAction, name="audit_action"), nullable=False
    )
    actor_user_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    actor_organizer_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    actor_admin_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    target_event_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    target_registration_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    payload: Mapped[Optional[dict]] = mapped_column(JSON)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45))  # IPv6 max длина
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
