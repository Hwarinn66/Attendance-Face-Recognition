from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid, str_enum, utcnow
from app.models.enums import CheckInStatus, CheckOutStatus, CorrectionStatus


class AttendanceRecord(TimestampMixin, Base):
    """One record per employee, shift and shift date. The UNIQUE constraint is the final guard
    against duplicate check-ins under concurrency."""

    __tablename__ = "attendance_records"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    shift_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shifts.id", ondelete="RESTRICT"), nullable=False)
    workplace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workplaces.id", ondelete="RESTRICT"), nullable=False)
    shift_date: Mapped[date] = mapped_column(Date, nullable=False)
    scheduled_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scheduled_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    check_out_closes_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    check_in_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    check_in_status: Mapped[CheckInStatus] = mapped_column(str_enum(CheckInStatus, "check_in_status"), nullable=False)
    late_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    check_in_verification_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("verification_attempts.id", ondelete="SET NULL")
    )
    check_in_latitude: Mapped[float | None] = mapped_column(Float)
    check_in_longitude: Mapped[float | None] = mapped_column(Float)
    check_in_accuracy_m: Mapped[float | None] = mapped_column(Float)
    check_in_distance_m: Mapped[float | None] = mapped_column(Float)

    check_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    check_out_status: Mapped[CheckOutStatus | None] = mapped_column(str_enum(CheckOutStatus, "check_out_status"))
    early_departure_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    overtime_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    worked_minutes: Mapped[int | None] = mapped_column(Integer)
    check_out_verification_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("verification_attempts.id", ondelete="SET NULL")
    )
    check_out_latitude: Mapped[float | None] = mapped_column(Float)
    check_out_longitude: Mapped[float | None] = mapped_column(Float)
    check_out_distance_m: Mapped[float | None] = mapped_column(Float)

    is_corrected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="face")

    user = relationship("User", lazy="joined", foreign_keys=[user_id])
    shift = relationship("Shift", lazy="joined")

    __table_args__ = (
        UniqueConstraint("user_id", "shift_id", "shift_date"),
        Index("ix_attendance_records_user_date", "user_id", "shift_date"),
        Index("ix_attendance_records_shift_date", "shift_date"),
        CheckConstraint("check_out_at IS NULL OR check_out_at >= check_in_at", name="checkout_after_checkin"),
        CheckConstraint(
            "late_minutes >= 0 AND early_departure_minutes >= 0 AND overtime_minutes >= 0", name="non_negative_minutes"
        ),
        CheckConstraint("source IN ('face', 'correction')", name="source_values"),
    )


class AttendanceCorrection(TimestampMixin, Base):
    """A requested or applied change to attendance data. Applied corrections keep the previous
    values so that no record is ever altered silently."""

    __tablename__ = "attendance_corrections"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    attendance_record_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("attendance_records.id", ondelete="SET NULL")
    )
    shift_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shifts.id", ondelete="RESTRICT"), nullable=False)
    shift_date: Mapped[date] = mapped_column(Date, nullable=False)
    requested_check_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_check_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[CorrectionStatus] = mapped_column(
        str_enum(CorrectionStatus, "correction_status"), nullable=False, default=CorrectionStatus.PENDING
    )
    requested_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
    previous_values: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    user = relationship("User", lazy="joined", foreign_keys=[user_id])
    shift = relationship("Shift", lazy="joined")

    __table_args__ = (
        Index("ix_attendance_corrections_status", "status", "created_at"),
        Index("ix_attendance_corrections_user", "user_id", "shift_date"),
        CheckConstraint("requested_check_in_at IS NOT NULL OR requested_check_out_at IS NOT NULL", name="has_change"),
    )


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    scope: Mapped[str] = mapped_column(String(50), nullable=False)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (UniqueConstraint("user_id", "scope", "key"),)
