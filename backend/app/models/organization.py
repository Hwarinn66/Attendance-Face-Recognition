from __future__ import annotations

import uuid
from datetime import date, datetime, time
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
    SmallInteger,
    String,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid


class Workplace(TimestampMixin, Base):
    __tablename__ = "workplaces"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    address: Mapped[str | None] = mapped_column(String(300))
    geofence_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    geofence_radius_m: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        CheckConstraint(
            "NOT geofence_enabled OR (latitude IS NOT NULL AND longitude IS NOT NULL "
            "AND geofence_radius_m IS NOT NULL)",
            name="geofence_complete",
        ),
        CheckConstraint("latitude IS NULL OR (latitude BETWEEN -90 AND 90)", name="latitude_range"),
        CheckConstraint("longitude IS NULL OR (longitude BETWEEN -180 AND 180)", name="longitude_range"),
        CheckConstraint("geofence_radius_m IS NULL OR geofence_radius_m BETWEEN 10 AND 50000", name="radius_range"),
    )


class Shift(TimestampMixin, Base):
    """A recurring shift template. Windows are offsets relative to the official start/end so that
    overnight shifts and DST transitions are handled uniformly by the attendance engine."""

    __tablename__ = "shifts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    workplace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workplaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    check_in_opens_before_min: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=60)
    check_in_closes_after_min: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=60)
    grace_min: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=15)
    check_out_opens_before_min: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=60)
    check_out_closes_after_min: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=120)
    early_departure_grace_min: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    overtime_threshold_min: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=30)
    break_min: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=60)
    # Bitmask of ISO weekdays the shift starts on: Monday = bit 0 ... Sunday = bit 6.
    workdays_mask: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0b0011111)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    workplace = relationship("Workplace", lazy="joined")

    __table_args__ = (
        UniqueConstraint("workplace_id", "name"),
        CheckConstraint("start_time <> end_time", name="non_zero_length"),
        CheckConstraint("workdays_mask BETWEEN 1 AND 127", name="workdays_range"),
        CheckConstraint(
            "check_in_opens_before_min BETWEEN 0 AND 720 AND check_in_closes_after_min BETWEEN 0 AND 720 "
            "AND check_out_opens_before_min BETWEEN 0 AND 720 AND check_out_closes_after_min BETWEEN 0 AND 720",
            name="window_ranges",
        ),
        CheckConstraint(
            "grace_min BETWEEN 0 AND 240 AND early_departure_grace_min BETWEEN 0 AND 240 "
            "AND overtime_threshold_min BETWEEN 0 AND 720 AND break_min BETWEEN 0 AND 480",
            name="policy_ranges",
        ),
    )


class ShiftAssignment(TimestampMixin, Base):
    __tablename__ = "shift_assignments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    shift_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shifts.id", ondelete="RESTRICT"), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    shift = relationship("Shift", lazy="joined")

    __table_args__ = (
        Index("ix_shift_assignments_user_from", "user_id", "effective_from"),
        CheckConstraint("effective_to IS NULL OR effective_to >= effective_from", name="date_order"),
    )


class Holiday(TimestampMixin, Base):
    """A non-working day. ``workplace_id`` NULL means the holiday applies to every workplace."""

    __tablename__ = "holidays"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    workplace_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workplaces.id", ondelete="CASCADE"))
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    __table_args__ = (
        Index(
            "uq_holidays_workplace_date",
            "workplace_id",
            "holiday_date",
            unique=True,
            postgresql_where=text("workplace_id IS NOT NULL"),
        ),
        Index(
            "uq_holidays_global_date",
            "holiday_date",
            unique=True,
            postgresql_where=text("workplace_id IS NULL"),
        ),
    )


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
