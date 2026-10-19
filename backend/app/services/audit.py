"""Audit trail.

``add`` writes inside the caller's transaction (the event commits or rolls back with the action).
``emit`` writes in an independent, immediately committed transaction — used for denials and failures
that must be recorded even though the request itself fails.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import SENSITIVE_KEYS
from app.db.session import get_session_factory
from app.models import AuditEvent
from app.models.enums import AuditCategory, AuditOutcome, AuditSeverity
from app.services.context import RequestContext

logger = logging.getLogger("app.audit")


def _sanitize(details: dict[str, Any] | None) -> dict[str, Any]:
    if not details:
        return {}
    clean: dict[str, Any] = {}
    for key, value in details.items():
        if key.lower() in SENSITIVE_KEYS:
            continue
        if isinstance(value, dict):
            clean[key] = _sanitize(value)
        elif isinstance(value, uuid.UUID):
            clean[key] = str(value)
        else:
            clean[key] = value
    return clean


def build_event(
    *,
    category: AuditCategory,
    action: str,
    ctx: RequestContext | None,
    outcome: AuditOutcome = AuditOutcome.SUCCESS,
    severity: AuditSeverity = AuditSeverity.INFO,
    actor_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: uuid.UUID | str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    return AuditEvent(
        category=category,
        action=action,
        outcome=outcome,
        severity=severity,
        actor_user_id=actor_id,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        ip_address=ctx.ip_address if ctx else None,
        user_agent=ctx.user_agent if ctx else None,
        request_id=ctx.request_id if ctx else None,
        details=_sanitize(details),
    )


def add(db: Session, **kwargs: Any) -> AuditEvent:
    event = build_event(**kwargs)
    db.add(event)
    return event


def emit(**kwargs: Any) -> None:
    """Persist an audit event in its own transaction. Failures are logged, never swallowed silently."""
    event = build_event(**kwargs)
    factory = get_session_factory()
    with factory() as db:
        try:
            db.add(event)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("failed to persist audit event", extra={"action": event.action})
            raise
