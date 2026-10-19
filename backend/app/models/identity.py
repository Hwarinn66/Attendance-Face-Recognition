from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid, str_enum, utcnow
from app.models.enums import BiometricStatus, Role


class User(TimestampMixin, Base):
    """An account. Employee profile fields live here (1:1, no separate table needed)."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    # Optional sign-in ID as an alternative to the email address (stored lower case, like email).
    username: Mapped[str | None] = mapped_column(String(64), unique=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(str_enum(Role, "role"), nullable=False, default=Role.EMPLOYEE)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    employee_code: Mapped[str | None] = mapped_column(String(50), unique=True)
    department: Mapped[str | None] = mapped_column(String(120))
    position: Mapped[str | None] = mapped_column(String(120))
    workplace_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workplaces.id", ondelete="SET NULL"), index=True)
    manager_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)

    biometric_status: Mapped[BiometricStatus] = mapped_column(
        str_enum(BiometricStatus, "biometric_status"),
        nullable=False,
        default=BiometricStatus.NOT_ENROLLED,
        server_default=BiometricStatus.NOT_ENROLLED.value,
    )
    biometric_status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    workplace = relationship("Workplace", lazy="joined")
    manager = relationship("User", remote_side="User.id", lazy="select")

    __table_args__ = (
        # Emails are normalised to lower case by the service layer; enforce it so UNIQUE is case-insensitive.
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        # No "@" so a sign-in identifier is unambiguously either an email or a username.
        CheckConstraint("username = lower(username) AND position('@' in username) = 0", name="username_format"),
    )


class AuthSession(Base):
    """Server-side session. Only the SHA-256 of the bearer token and of the CSRF token are stored."""

    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(256))

    user = relationship("User", lazy="joined")

    __table_args__ = (Index("ix_auth_sessions_user_active", "user_id", "revoked_at"),)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        Index("ix_login_attempts_email_created", "email", "created_at"),
        Index("ix_login_attempts_ip_created", "ip_address", "created_at"),
    )
