"""Запись событий в audit_log.

Удобный API: audit.log(action=..., user_id=..., payload={...}).
Никогда не пишет в payload персональные данные — только id, типы, флаги.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from ..logging_setup import get_logger, mask_id
from ..models import AuditAction, AuditLog

log = get_logger(__name__)


def write(
    session: Session,
    action: AuditAction,
    *,
    user_id: Optional[int] = None,
    organizer_id: Optional[int] = None,
    admin_id: Optional[int] = None,
    event_id: Optional[int] = None,
    registration_id: Optional[int] = None,
    payload: Optional[dict[str, Any]] = None,
    ip_address: Optional[str] = None,
) -> None:
    """Записывает событие в audit_log и дублирует в stdout-лог."""
    entry = AuditLog(
        action=action,
        actor_user_id=user_id,
        actor_organizer_id=organizer_id,
        actor_admin_id=admin_id,
        target_event_id=event_id,
        target_registration_id=registration_id,
        payload=payload,
        ip_address=ip_address,
    )
    session.add(entry)
    session.flush()

    log.info(
        "AUDIT %s user=%s org=%s admin=%s event=%s reg=%s",
        action.value,
        mask_id(user_id),
        mask_id(organizer_id),
        mask_id(admin_id),
        event_id or "-",
        registration_id or "-",
    )
