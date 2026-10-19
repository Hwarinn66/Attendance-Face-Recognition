"""ORM models. Importing this package registers every table on ``Base.metadata``."""

from app.models.attendance import AttendanceCorrection, AttendanceRecord, IdempotencyKey
from app.models.audit import AuditEvent
from app.models.biometrics import (
    BiometricConsent,
    BiometricTemplate,
    EnrollmentSession,
    LivenessChallenge,
    VerificationAttempt,
)
from app.models.identity import AuthSession, LoginAttempt, User
from app.models.organization import Holiday, Shift, ShiftAssignment, SystemSetting, Workplace

__all__ = [
    "AttendanceCorrection",
    "AttendanceRecord",
    "AuditEvent",
    "AuthSession",
    "BiometricConsent",
    "BiometricTemplate",
    "EnrollmentSession",
    "Holiday",
    "IdempotencyKey",
    "LivenessChallenge",
    "LoginAttempt",
    "Shift",
    "ShiftAssignment",
    "SystemSetting",
    "User",
    "VerificationAttempt",
    "Workplace",
]
