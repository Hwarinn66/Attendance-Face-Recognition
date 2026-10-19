from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from app.models.enums import BiometricStatus, ChallengePurpose, ChallengeStatus, VerificationDecision
from app.schemas.common import ORMModel, StrictInput


class ConsentTextOut(ORMModel):
    version: str
    text: str


class ConsentIn(StrictInput):
    consent_version: str = Field(min_length=1, max_length=40)
    accepted: bool


class ConsentOut(ORMModel):
    id: uuid.UUID
    consent_version: str
    granted_at: datetime
    withdrawn_at: datetime | None


class TemplateMeta(ORMModel):
    id: uuid.UUID
    model_name: str
    created_at: datetime
    sample_count: int


class BiometricStatusOut(ORMModel):
    status: BiometricStatus
    consent: ConsentOut | None
    consent_current: bool
    template: TemplateMeta | None
    can_start_enrollment: bool
    attempts_remaining_today: int
    verification_available: bool
    pad_enforced: bool
    test_fixture: bool


class ChallengeOut(ORMModel):
    id: uuid.UUID
    purpose: ChallengePurpose
    steps: list[str]
    expires_at: datetime
    issued_at: datetime
    min_frames: int
    max_frames: int
    recommended_fps: int
    step_seconds: float


class EnrollmentStartOut(ORMModel):
    enrollment_id: uuid.UUID
    challenge: ChallengeOut


class ChallengeCreateIn(StrictInput):
    purpose: ChallengePurpose


class VerificationOut(ORMModel):
    challenge_id: uuid.UUID
    accepted: bool
    category: str | None
    message: str
    hints: list[str]
    biometric_status: BiometricStatus | None = None
    valid_until: datetime | None = None


class VerificationAttemptOut(ORMModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user_name: str | None = None
    purpose: ChallengePurpose
    decision: VerificationDecision
    reason_codes: list[str]
    frame_count: int
    usable_frame_count: int
    pad_score: float | None
    match_score: float | None
    provider: str
    processing_ms: int | None
    created_at: datetime
    details: dict[str, Any]


class ChallengeStateOut(ORMModel):
    id: uuid.UUID
    status: ChallengeStatus


class QualityCheckOut(ORMModel):
    ok: bool
    face_count: int
    hints: list[str]
