"""Data-retention purge. Run periodically: ``python -m app.cli purge`` (idempotent).

Audit events are append-only for the application role; the purge is the only code path that deletes
them, and it records its own audit event summarising what was removed.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.base import utcnow
from app.models import (
    AuditEvent,
    BiometricTemplate,
    IdempotencyKey,
    LivenessChallenge,
    LoginAttempt,
    User,
    VerificationAttempt,
)
from app.models.enums import AuditCategory, BiometricStatus, ChallengeStatus
from app.services import audit, system_settings
from app.services.context import SYSTEM_CONTEXT


def _count(result: object) -> int:
    return int(getattr(result, "rowcount", 0) or 0)


def purge(db: Session, settings: Settings) -> dict[str, int]:
    now = utcnow()
    values = system_settings.get_all(db, settings)
    out: dict[str, int] = {}

    out["challenges_expired"] = _count(
        db.execute(
            update(LivenessChallenge)
            .where(
                LivenessChallenge.status.in_(
                    [ChallengeStatus.ISSUED, ChallengeStatus.PASSED, ChallengeStatus.PROCESSING]
                ),
                LivenessChallenge.expires_at < now - timedelta(minutes=10),
            )
            .values(status=ChallengeStatus.EXPIRED)
        )
    )
    out["idempotency_keys"] = _count(
        db.execute(delete(IdempotencyKey).where(IdempotencyKey.created_at < now - timedelta(days=2)))
    )
    out["login_attempts"] = _count(
        db.execute(
            delete(LoginAttempt).where(
                LoginAttempt.created_at < now - timedelta(days=settings.retention_login_attempts_days)
            )
        )
    )
    out["verification_attempts"] = _count(
        db.execute(
            delete(VerificationAttempt).where(
                VerificationAttempt.created_at
                < now - timedelta(days=int(values["retention.verification_attempts_days"]))
            )
        )
    )

    # Templates of deactivated users after the grace period.
    cutoff = now - timedelta(days=int(values["retention.inactive_biometric_days"]))
    stale = list(
        db.scalars(
            select(User.id).where(
                User.is_active.is_(False), User.deactivated_at.is_not(None), User.deactivated_at < cutoff
            )
        ).all()
    )
    out["templates_of_inactive_users"] = (
        _count(db.execute(delete(BiometricTemplate).where(BiometricTemplate.user_id.in_(stale)))) if stale else 0
    )
    if stale:
        db.execute(
            update(User)
            .where(User.id.in_(stale), User.biometric_status != BiometricStatus.DELETED)
            .values(biometric_status=BiometricStatus.DELETED, biometric_status_changed_at=now)
        )

    out["audit_events"] = _count(
        db.execute(
            delete(AuditEvent).where(AuditEvent.occurred_at < now - timedelta(days=int(values["retention.audit_days"])))
        )
    )
    audit.add(db, category=AuditCategory.PRIVACY, action="retention.purge", ctx=SYSTEM_CONTEXT, details=out)
    db.commit()
    return out
