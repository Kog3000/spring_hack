"""Репозитории — единственное место, где код обращается к БД напрямую.

Хендлеры бота и веб-приложение используют ТОЛЬКО эти функции, ничего больше.
Это даёт единую точку для аудита и фильтрации.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    Admin,
    Controller,
    Event,
    EventController,
    Notification,
    NotificationType,
    Organizer,
    Registration,
    RegistrationStatus,
    User,
)


# ============================================================
# UserRepo
# ============================================================


class UserRepo:
    @staticmethod
    def get_by_max_id(session: Session, max_user_id: int) -> Optional[User]:
        return session.scalar(select(User).where(User.max_user_id == max_user_id))

    @staticmethod
    def upsert_with_consent(
        session: Session,
        *,
        max_user_id: int,
        username: str,
        consent_version: str,
    ) -> User:
        """Создаёт пользователя при первом согласии или обновляет username при повторных входах."""
        user = UserRepo.get_by_max_id(session, max_user_id)
        if user is None:
            user = User(
                max_user_id=max_user_id,
                username=username,
                consent_version=consent_version,
                consent_given_at=datetime.now(timezone.utc),
            )
            session.add(user)
            session.flush()
        else:
            user.username = username
            if user.consent_version != consent_version:
                user.consent_version = consent_version
                user.consent_given_at = datetime.now(timezone.utc)
        return user


# ============================================================
# OrganizerRepo
# ============================================================


class OrganizerRepo:
    @staticmethod
    def get_by_email(session: Session, email: str) -> Optional[Organizer]:
        return session.scalar(select(Organizer).where(Organizer.email == email.lower()))

    @staticmethod
    def create(session: Session, *, email: str, password_hash: str, full_name: str) -> Organizer:
        org = Organizer(email=email.lower(), password_hash=password_hash, full_name=full_name)
        session.add(org)
        session.flush()
        return org

    @staticmethod
    def get(session: Session, organizer_id: int) -> Optional[Organizer]:
        return session.get(Organizer, organizer_id)

    @staticmethod
    def list_all(session: Session) -> list[Organizer]:
        return list(
            session.scalars(select(Organizer).order_by(Organizer.created_at.desc()))
        )

    @staticmethod
    def deactivate(session: Session, organizer_id: int) -> bool:
        o = session.get(Organizer, organizer_id)
        if o is None:
            return False
        o.is_active = False
        return True


# ============================================================
# AdminRepo
# ============================================================


class AdminRepo:
    @staticmethod
    def get_by_email(session: Session, email: str) -> Optional[Admin]:
        return session.scalar(select(Admin).where(Admin.email == email.lower()))

    @staticmethod
    def create(session: Session, *, email: str, password_hash: str, display_name: str) -> Admin:
        adm = Admin(email=email.lower(), password_hash=password_hash, display_name=display_name)
        session.add(adm)
        session.flush()
        return adm

    @staticmethod
    def get(session: Session, admin_id: int) -> Optional[Admin]:
        return session.get(Admin, admin_id)

    @staticmethod
    def list_all(session: Session) -> list[Admin]:
        return list(session.scalars(select(Admin).order_by(Admin.created_at.desc())))


# ============================================================
# ControllerRepo
# ============================================================


class ControllerRepo:
    @staticmethod
    def get(session: Session, controller_id: int) -> Optional[Controller]:
        return session.get(Controller, controller_id)

    @staticmethod
    def get_by_email(session: Session, email: str) -> Optional[Controller]:
        return session.scalar(select(Controller).where(Controller.email == email.lower()))

    @staticmethod
    def create(
        session: Session, *, email: str, password_hash: str, full_name: str
    ) -> Controller:
        c = Controller(email=email.lower(), password_hash=password_hash, full_name=full_name)
        session.add(c)
        session.flush()
        return c

    @staticmethod
    def list_all(session: Session) -> list[Controller]:
        return list(
            session.scalars(select(Controller).order_by(Controller.created_at.desc()))
        )

    @staticmethod
    def list_for_event(session: Session, event_id: int) -> list[Controller]:
        """Список контролёров, привязанных к мероприятию."""
        stmt = (
            select(Controller)
            .join(EventController, EventController.controller_id == Controller.id)
            .where(EventController.event_id == event_id)
            .order_by(Controller.full_name)
        )
        return list(session.scalars(stmt))

    @staticmethod
    def list_events_for_controller(session: Session, controller_id: int) -> list[Event]:
        """Список мероприятий, к которым привязан контролёр."""
        stmt = (
            select(Event)
            .join(EventController, EventController.event_id == Event.id)
            .where(EventController.controller_id == controller_id)
            .order_by(Event.event_date.desc())
        )
        return list(session.scalars(stmt))

    @staticmethod
    def has_access(session: Session, controller_id: int, event_id: int) -> bool:
        """Проверка, что контролёр имеет доступ к этому мероприятию."""
        row = session.scalar(
            select(EventController).where(
                EventController.controller_id == controller_id,
                EventController.event_id == event_id,
            )
        )
        return row is not None

    @staticmethod
    def assign_to_event(
        session: Session,
        *,
        event_id: int,
        controller_id: int,
        granted_by_organizer_id: Optional[int],
    ) -> bool:
        """Привязать контролёра к мероприятию. Возвращает True если создано, False если уже было."""
        existing = session.scalar(
            select(EventController).where(
                EventController.event_id == event_id,
                EventController.controller_id == controller_id,
            )
        )
        if existing is not None:
            return False
        link = EventController(
            event_id=event_id,
            controller_id=controller_id,
            granted_by_organizer_id=granted_by_organizer_id,
        )
        session.add(link)
        session.flush()
        return True

    @staticmethod
    def revoke_from_event(session: Session, event_id: int, controller_id: int) -> bool:
        link = session.scalar(
            select(EventController).where(
                EventController.event_id == event_id,
                EventController.controller_id == controller_id,
            )
        )
        if link is None:
            return False
        session.delete(link)
        return True

    @staticmethod
    def deactivate(session: Session, controller_id: int) -> bool:
        c = session.get(Controller, controller_id)
        if c is None:
            return False
        c.is_active = False
        return True


# ============================================================
# EventRepo
# ============================================================


class EventRepo:
    @staticmethod
    def get(session: Session, event_id: int) -> Optional[Event]:
        return session.get(Event, event_id)

    @staticmethod
    def list_upcoming(session: Session, limit: int = 20) -> list[tuple[Event, int]]:
        """Возвращает список (event, free_slots) для ближайших мероприятий."""
        now = datetime.now(timezone.utc)
        stmt = (
            select(
                Event,
                Event.max_participants
                - func.count(Registration.id).filter(
                    Registration.status == RegistrationStatus.confirmed
                ),
            )
            .outerjoin(Registration, Registration.event_id == Event.id)
            .where(Event.event_date > now)
            .group_by(Event.id)
            .order_by(Event.event_date)
            .limit(limit)
        )
        return list(session.execute(stmt))

    @staticmethod
    def list_by_organizer(session: Session, organizer_id: int) -> list[Event]:
        return list(
            session.scalars(
                select(Event)
                .where(Event.organizer_id == organizer_id)
                .order_by(Event.event_date.desc())
            )
        )

    @staticmethod
    def list_all_for_admin(session: Session) -> list[Event]:
        """Технический администратор видит все мероприятия для диагностики."""
        return list(session.scalars(select(Event).order_by(Event.event_date.desc())))

    @staticmethod
    def close_registration(session: Session, event_id: int, organizer_id: int) -> bool:
        ev = session.get(Event, event_id)
        if ev is None or ev.organizer_id != organizer_id:
            return False
        ev.registration_closed = True
        return True

    @staticmethod
    def create(
        session: Session,
        *,
        organizer_id: int,
        title: str,
        description: str,
        event_date: datetime,
        duration_minutes: int,
        format_: "EventFormat",
        location: str,
        max_participants: int,
    ) -> Event:
        ev = Event(
            organizer_id=organizer_id,
            title=title,
            description=description,
            event_date=event_date,
            duration_minutes=duration_minutes,
            format=format_,
            location=location,
            max_participants=max_participants,
        )
        session.add(ev)
        session.flush()
        return ev

    @staticmethod
    def update(
        session: Session,
        event_id: int,
        *,
        title: str,
        description: str,
        event_date: datetime,
        duration_minutes: int,
        format_: "EventFormat",
        location: str,
        max_participants: int,
    ) -> Optional[Event]:
        ev = session.get(Event, event_id)
        if ev is None:
            return None
        ev.title = title
        ev.description = description
        ev.event_date = event_date
        ev.duration_minutes = duration_minutes
        ev.format = format_
        ev.location = location
        ev.max_participants = max_participants
        return ev

    @staticmethod
    def open_registration(session: Session, event_id: int) -> bool:
        """Открывает ранее закрытую регистрацию."""
        ev = session.get(Event, event_id)
        if ev is None:
            return False
        ev.registration_closed = False
        return True

    @staticmethod
    def delete(session: Session, event_id: int) -> bool:
        """Удаляет мероприятие. Получится только если на нём нет записей —
        иначе сработает foreign key на registrations."""
        ev = session.get(Event, event_id)
        if ev is None:
            return False
        session.delete(ev)
        return True


# ============================================================
# RegistrationRepo
# ============================================================


class RegistrationRepo:
    @staticmethod
    def get(session: Session, reg_id: int) -> Optional[Registration]:
        return session.get(Registration, reg_id)

    @staticmethod
    def get_by_code(session: Session, code: str) -> Optional[Registration]:
        return session.scalar(
            select(Registration).where(Registration.registration_code == code)
        )

    @staticmethod
    def get_by_qr_token(session: Session, token: str) -> Optional[Registration]:
        return session.scalar(
            select(Registration).where(Registration.qr_token == token)
        )

    @staticmethod
    def get_active_for_user(
        session: Session, user_id: int, event_id: int
    ) -> Optional[Registration]:
        return session.scalar(
            select(Registration).where(
                Registration.user_id == user_id,
                Registration.event_id == event_id,
                Registration.status == RegistrationStatus.confirmed,
            )
        )

    @staticmethod
    def list_for_user(session: Session, user_id: int) -> list[Registration]:
        return list(
            session.scalars(
                select(Registration)
                .where(Registration.user_id == user_id)
                .order_by(Registration.created_at.desc())
            )
        )

    @staticmethod
    def list_for_event(session: Session, event_id: int) -> list[Registration]:
        return list(
            session.scalars(
                select(Registration)
                .where(Registration.event_id == event_id)
                .order_by(Registration.created_at)
            )
        )

    @staticmethod
    def count_confirmed(session: Session, event_id: int) -> int:
        return session.scalar(
            select(func.count(Registration.id)).where(
                Registration.event_id == event_id,
                Registration.status == RegistrationStatus.confirmed,
            )
        ) or 0

    @staticmethod
    def create(
        session: Session,
        *,
        user_id: int,
        event_id: int,
        registration_code: str,
        qr_token: str,
    ) -> Optional[Registration]:
        """Создаёт запись. Возвращает None при попытке дубля (partial unique index)."""
        reg = Registration(
            user_id=user_id,
            event_id=event_id,
            registration_code=registration_code,
            qr_token=qr_token,
            status=RegistrationStatus.confirmed,
        )
        session.add(reg)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            return None
        return reg

    @staticmethod
    def cancel(session: Session, reg_id: int, user_id: int) -> bool:
        reg = session.get(Registration, reg_id)
        if reg is None or reg.user_id != user_id:
            return False
        if reg.status != RegistrationStatus.confirmed:
            return False
        reg.status = RegistrationStatus.cancelled
        reg.cancelled_at = datetime.now(timezone.utc)
        return True

    @staticmethod
    def mark_attended(session: Session, reg_id: int) -> bool:
        reg = session.get(Registration, reg_id)
        if reg is None or reg.status != RegistrationStatus.confirmed:
            return False
        reg.status = RegistrationStatus.attended
        reg.attended_at = datetime.now(timezone.utc)
        return True

    @staticmethod
    def toggle_notifications(
        session: Session, reg_id: int, user_id: int, enabled: bool
    ) -> bool:
        reg = session.get(Registration, reg_id)
        if reg is None or reg.user_id != user_id:
            return False
        reg.notifications_enabled = enabled
        return True


# ============================================================
# NotificationRepo
# ============================================================


class NotificationRepo:
    @staticmethod
    def create(
        session: Session,
        *,
        event_id: int,
        organizer_id: int,
        type_: NotificationType,
        body: str,
    ) -> Notification:
        n = Notification(
            event_id=event_id, organizer_id=organizer_id, type=type_, body=body
        )
        session.add(n)
        session.flush()
        return n

    @staticmethod
    def update_stats(
        session: Session, notification_id: int, delivered: int, failed: int
    ) -> None:
        n = session.get(Notification, notification_id)
        if n is None:
            return
        n.delivered_count = delivered
        n.failed_count = failed

    @staticmethod
    def list_for_event(session: Session, event_id: int) -> list[Notification]:
        return list(
            session.scalars(
                select(Notification)
                .where(Notification.event_id == event_id)
                .order_by(Notification.sent_at.desc())
            )
        )
