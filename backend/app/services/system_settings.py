"""Runtime-adjustable settings (admin UI). Each key is typed and bounded; unknown keys are rejected."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ValidationFailed
from app.db.base import utcnow
from app.models import SystemSetting

DEFAULT_CONSENT_TEXT = (
    "To record attendance with face verification, this system processes images of your face captured by your "
    "device camera. From these images a mathematical face template is computed, encrypted and stored. Raw images "
    "are processed in memory only and are not stored. Your template is used solely to verify your identity when you "
    "check in or check out. You can withdraw consent and delete your template at any time from your profile; "
    "attendance by other means must then be arranged with HR. Templates are deleted automatically when your account "
    "is deactivated, after the retention period. Verification decisions (not images) are logged for security audit."
)


@dataclass(frozen=True, slots=True)
class SettingSpec:
    kind: type
    description: str
    minimum: int | None = None
    maximum: int | None = None


SPECS: dict[str, SettingSpec] = {
    "consent.version": SettingSpec(str, "Version identifier of the biometric consent text"),
    "consent.text": SettingSpec(str, "Biometric consent text shown before enrollment"),
    "enrollment.max_attempts_per_day": SettingSpec(int, "Failed enrollment attempts allowed per 24 h", 1, 20),
    "retention.verification_attempts_days": SettingSpec(int, "Days to keep verification attempt records", 7, 3650),
    "retention.audit_days": SettingSpec(int, "Days to keep audit events", 90, 3650),
    "retention.inactive_biometric_days": SettingSpec(
        int, "Days after deactivation before templates are purged", 0, 365
    ),
}


def defaults(settings: Settings) -> dict[str, Any]:
    return {
        "consent.version": settings.consent_version,
        "consent.text": DEFAULT_CONSENT_TEXT,
        "enrollment.max_attempts_per_day": settings.enrollment_max_attempts_per_day,
        "retention.verification_attempts_days": settings.retention_verification_attempts_days,
        "retention.audit_days": settings.retention_audit_days,
        "retention.inactive_biometric_days": settings.retention_inactive_biometric_days,
    }


def get_all(db: Session, settings: Settings) -> dict[str, Any]:
    values = defaults(settings)
    for row in db.scalars(select(SystemSetting)):
        if row.key in SPECS:
            values[row.key] = row.value
    return values


def get(db: Session, settings: Settings, key: str) -> Any:
    return get_all(db, settings)[key]


def validate(key: str, value: Any) -> Any:
    spec = SPECS.get(key)
    if spec is None:
        raise ValidationFailed(f"unknown setting '{key}'")
    if spec.kind is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValidationFailed(f"'{key}' must be an integer")
        if (spec.minimum is not None and value < spec.minimum) or (spec.maximum is not None and value > spec.maximum):
            raise ValidationFailed(f"'{key}' must be between {spec.minimum} and {spec.maximum}")
    elif spec.kind is str:
        if not isinstance(value, str) or not value.strip() or len(value) > 5000:
            raise ValidationFailed(f"'{key}' must be a non-empty string (max 5000 chars)")
        value = value.strip()
    return value


def update(db: Session, changes: dict[str, Any], actor_id: uuid.UUID) -> dict[str, tuple[Any, Any]]:
    current = {row.key: row for row in db.scalars(select(SystemSetting).where(SystemSetting.key.in_(changes)))}
    applied: dict[str, tuple[Any, Any]] = {}
    for key, raw in changes.items():
        value = validate(key, raw)
        row = current.get(key)
        old = row.value if row else None
        if row is None:
            db.add(SystemSetting(key=key, value=value, updated_at=utcnow(), updated_by_id=actor_id))
        else:
            row.value = value
            row.updated_at = utcnow()
            row.updated_by_id = actor_id
        applied[key] = (old, value)
    return applied


def consent_text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
