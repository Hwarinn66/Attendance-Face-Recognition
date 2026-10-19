from __future__ import annotations

import enum


class Role(enum.StrEnum):
    EMPLOYEE = "employee"
    ADMIN = "admin"


class BiometricStatus(enum.StrEnum):
    NOT_ENROLLED = "not_enrolled"
    IN_PROGRESS = "in_progress"
    VERIFICATION_FAILED = "verification_failed"
    PENDING_RETRY = "pending_retry"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class TemplateStatus(enum.StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"


class EnrollmentSessionStatus(enum.StrEnum):
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"


class ChallengePurpose(enum.StrEnum):
    ENROLLMENT = "enrollment"
    CHECK_IN = "check_in"
    CHECK_OUT = "check_out"


class ChallengeStatus(enum.StrEnum):
    ISSUED = "issued"
    PROCESSING = "processing"
    PASSED = "passed"
    FAILED = "failed"
    CONSUMED = "consumed"
    EXPIRED = "expired"


class VerificationDecision(enum.StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ERROR = "error"


class CheckInStatus(enum.StrEnum):
    ON_TIME = "on_time"
    LATE = "late"


class CheckOutStatus(enum.StrEnum):
    ON_TIME = "on_time"
    EARLY = "early"
    OVERTIME = "overtime"


class CorrectionStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class AuditOutcome(enum.StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"


class AuditSeverity(enum.StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AuditCategory(enum.StrEnum):
    AUTH = "auth"
    SECURITY = "security"
    ADMIN = "admin"
    ATTENDANCE = "attendance"
    BIOMETRIC = "biometric"
    PRIVACY = "privacy"
