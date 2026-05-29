from datetime import datetime, timedelta, timezone

from app.models import EventFormat, RegistrationStatus
from app.repositories.repos import ControllerRepo, EventRepo, RegistrationRepo, UserRepo


def test_user_upsert_creates_and_updates_existing_user(db_session):
    user = UserRepo.upsert_with_consent(
        db_session,
        max_user_id=1,
        username="Первое имя",
        consent_version="v1.0",
    )
    db_session.commit()

    same_user = UserRepo.upsert_with_consent(
        db_session,
        max_user_id=1,
        username="Новое имя",
        consent_version="v1.0",
    )
    db_session.commit()

    assert same_user.id == user.id
    assert same_user.username == "Новое имя"
    assert UserRepo.get_by_max_id(db_session, 1).id == user.id


def test_event_list_upcoming_counts_free_slots(db_session, organizer, user):
    event = EventRepo.create(
        db_session,
        organizer_id=organizer.id,
        title="Будущее мероприятие",
        description="Описание",
        event_date=datetime.now(timezone.utc) + timedelta(days=1),
        duration_minutes=60,
        format_=EventFormat.online,
        location="https://example.test/meet",
        max_participants=3,
    )
    RegistrationRepo.create(
        db_session,
        user_id=user.id,
        event_id=event.id,
        registration_code="ABC-DEFG-HJ",
        qr_token="token-1",
    )
    db_session.commit()

    rows = EventRepo.list_upcoming(db_session)

    assert len(rows) == 1
    listed_event, free_slots = rows[0]
    assert listed_event.id == event.id
    assert free_slots == 2


def test_registration_lifecycle_cancel_and_mark_attended(db_session, user, event):
    reg = RegistrationRepo.create(
        db_session,
        user_id=user.id,
        event_id=event.id,
        registration_code="LMN-2345-67",
        qr_token="qr-token-1",
    )
    db_session.commit()

    assert RegistrationRepo.count_confirmed(db_session, event.id) == 1
    assert RegistrationRepo.cancel(db_session, reg.id, user.id) is True
    assert reg.status == RegistrationStatus.cancelled
    assert reg.cancelled_at is not None
    db_session.flush()
    assert RegistrationRepo.count_confirmed(db_session, event.id) == 0
    assert RegistrationRepo.mark_attended(db_session, reg.id) is False


def test_registration_mark_attended_from_confirmed(db_session, user, event):
    reg = RegistrationRepo.create(
        db_session,
        user_id=user.id,
        event_id=event.id,
        registration_code="QWE-2345-RT",
        qr_token="qr-token-2",
    )
    db_session.commit()

    assert RegistrationRepo.mark_attended(db_session, reg.id) is True
    assert reg.status == RegistrationStatus.attended
    assert reg.attended_at is not None


def test_registration_toggle_notifications_requires_owner(db_session, user, event):
    reg = RegistrationRepo.create(
        db_session,
        user_id=user.id,
        event_id=event.id,
        registration_code="ASD-2345-FG",
        qr_token="qr-token-3",
    )
    db_session.commit()

    assert RegistrationRepo.toggle_notifications(db_session, reg.id, user.id, False) is True
    assert reg.notifications_enabled is False
    assert RegistrationRepo.toggle_notifications(db_session, reg.id, 999, True) is False


def test_controller_assignment_is_idempotent_and_revocable(db_session, organizer, event):
    controller = ControllerRepo.create(
        db_session,
        email="Controller@Test.RU",
        password_hash="hash",
        full_name="Контролёр",
    )
    db_session.commit()

    assert ControllerRepo.assign_to_event(
        db_session,
        event_id=event.id,
        controller_id=controller.id,
        granted_by_organizer_id=organizer.id,
    ) is True
    assert ControllerRepo.assign_to_event(
        db_session,
        event_id=event.id,
        controller_id=controller.id,
        granted_by_organizer_id=organizer.id,
    ) is False
    assert ControllerRepo.has_access(db_session, controller.id, event.id) is True
    assert ControllerRepo.revoke_from_event(db_session, event.id, controller.id) is True
    db_session.flush()
    assert ControllerRepo.has_access(db_session, controller.id, event.id) is False
