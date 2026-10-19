from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, LargeBinary, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, new_uuid, str_enum, utcnow
from app.models.enums import (
    ChallengePurpose,
    ChallengeStatus,
    EnrollmentSessionStatus,
    TemplateStatus,
    VerificationDecision,
)


class BiometricConsent(Base):
    __tablename__ = "biometric_consents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    consent_version: Mapped[str] = mapped_column(String(40), nullable=False)
    consent_text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(256))

    __table_args__ = (Index("ix_biometric_consents_user", "user_id", "granted_at"),)


class BiometricTemplate(Base):
    """An encrypted face template (AES-256-GCM). The plaintext embedding never touches the DB.

    The ciphertext's associated data binds it to (template id, user id, model), so a ciphertext copied
    onto another user's row fails authentication on decryption.
    """

    __tablename__ = "biometric_templates"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[TemplateStatus] = mapped_column(str_enum(TemplateStatus, "template_status"), nullable=False)
    model_name: Mapped[str] = mapped_column(String(80), nullable=False)
    embedding_dim: Mapped[int] = mapped_column(Integer, nullable=False)
    key_id: Mapped[str] = mapped_column(String(40), nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary(12), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    quality_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    enrollment_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("enrollment_sessions.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "uq_biometric_templates_one_active",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )


class EnrollmentSession(Base):
    __tablename__ = "enrollment_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    consent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("biometric_consents.id", ondelete="RESTRICT"), nullable=False
    )
    challenge_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("liveness_challenges.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    status: Mapped[EnrollmentSessionStatus] = mapped_column(
        str_enum(EnrollmentSessionStatus, "enrollment_session_status"), nullable=False
    )
    failure_reasons: Mapped[list[str] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_enrollment_sessions_user_created", "user_id", "created_at"),)


class LivenessChallenge(Base):
    """A server-issued, single-use challenge bound to user, auth session, purpose and expiry."""

    __tablename__ = "liveness_challenges"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    auth_session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("auth_sessions.id", ondelete="SET NULL"))
    purpose: Mapped[ChallengePurpose] = mapped_column(str_enum(ChallengePurpose, "challenge_purpose"), nullable=False)
    steps: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status: Mapped[ChallengeStatus] = mapped_column(str_enum(ChallengeStatus, "challenge_status"), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("verification_attempts.id", ondelete="SET NULL", use_alter=True)
    )

    __table_args__ = (Index("ix_liveness_challenges_user_issued", "user_id", "issued_at"),)


class VerificationAttempt(Base):
    """Auditable server-side record of one biometric decision. Stores scores and reason codes,
    never images or embeddings."""

    __tablename__ = "verification_attempts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    challenge_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("liveness_challenges.id", ondelete="SET NULL"))
    purpose: Mapped[ChallengePurpose] = mapped_column(
        str_enum(ChallengePurpose, "verification_purpose"), nullable=False
    )
    decision: Mapped[VerificationDecision] = mapped_column(
        str_enum(VerificationDecision, "verification_decision"), nullable=False
    )
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    frame_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    usable_frame_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pad_score: Mapped[float | None] = mapped_column(Float)
    match_score: Mapped[float | None] = mapped_column(Float)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    provider: Mapped[str] = mapped_column(String(120), nullable=False)
    processing_ms: Mapped[int | None] = mapped_column(Integer)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        Index("ix_verification_attempts_user_created", "user_id", "created_at"),
        Index("ix_verification_attempts_created", "created_at"),
    )
