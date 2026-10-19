from __future__ import annotations

import uuid
from datetime import date, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator

from app.schemas.common import ORMModel, StrictInput


def _check_tz(v: str) -> str:
    try:
        ZoneInfo(v)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("unknown IANA time zone") from exc
    return v


class WorkplaceIn(StrictInput):
    name: str = Field(min_length=1, max_length=120)
    timezone: str = Field(min_length=1, max_length=64)
    address: str | None = Field(default=None, max_length=300)
    geofence_enabled: bool = False
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    geofence_radius_m: int | None = Field(default=None, ge=10, le=50_000)
    is_active: bool = True

    _tz = field_validator("timezone")(_check_tz)

    @model_validator(mode="after")
    def _geofence(self) -> WorkplaceIn:
        if self.geofence_enabled and None in (self.latitude, self.longitude, self.geofence_radius_m):
            raise ValueError("geofence requires latitude, longitude and radius")
        return self


class WorkplaceOut(ORMModel):
    id: uuid.UUID
    name: str
    timezone: str
    address: str | None
    geofence_enabled: bool
    latitude: float | None
    longitude: float | None
    geofence_radius_m: int | None
    is_active: bool


class ShiftIn(StrictInput):
    workplace_id: uuid.UUID
    name: str = Field(min_length=1, max_length=120)
    start_time: time
    end_time: time
    check_in_opens_before_min: int = Field(default=60, ge=0, le=720)
    check_in_closes_after_min: int = Field(default=60, ge=0, le=720)
    grace_min: int = Field(default=15, ge=0, le=240)
    check_out_opens_before_min: int = Field(default=60, ge=0, le=720)
    check_out_closes_after_min: int = Field(default=120, ge=0, le=720)
    early_departure_grace_min: int = Field(default=0, ge=0, le=240)
    overtime_threshold_min: int = Field(default=30, ge=0, le=720)
    break_min: int = Field(default=60, ge=0, le=480)
    workdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4], min_length=1, max_length=7)
    is_active: bool = True

    @field_validator("start_time", "end_time")
    @classmethod
    def _minute_precision(cls, v: time) -> time:
        if v.second or v.microsecond or v.tzinfo:
            raise ValueError("use HH:MM local wall-clock time without seconds or offset")
        return v

    @field_validator("workdays")
    @classmethod
    def _days(cls, v: list[int]) -> list[int]:
        if any(d < 0 or d > 6 for d in v) or len(set(v)) != len(v):
            raise ValueError("workdays are distinct integers 0 (Mon) .. 6 (Sun)")
        return sorted(v)

    @property
    def workdays_mask(self) -> int:
        return sum(1 << d for d in self.workdays)


class ShiftOut(ORMModel):
    id: uuid.UUID
    workplace_id: uuid.UUID
    name: str
    start_time: time
    end_time: time
    check_in_opens_before_min: int
    check_in_closes_after_min: int
    grace_min: int
    check_out_opens_before_min: int
    check_out_closes_after_min: int
    early_departure_grace_min: int
    overtime_threshold_min: int
    break_min: int
    workdays: list[int]
    is_active: bool
    is_overnight: bool


class AssignmentIn(StrictInput):
    user_id: uuid.UUID
    shift_id: uuid.UUID
    effective_from: date
    effective_to: date | None = None

    @model_validator(mode="after")
    def _order(self) -> AssignmentIn:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not precede effective_from")
        return self


class AssignmentUpdate(StrictInput):
    effective_to: date | None


class AssignmentOut(ORMModel):
    id: uuid.UUID
    user_id: uuid.UUID
    shift_id: uuid.UUID
    shift_name: str
    effective_from: date
    effective_to: date | None


class HolidayIn(StrictInput):
    holiday_date: date
    name: str = Field(min_length=1, max_length=120)
    workplace_id: uuid.UUID | None = None


class HolidayOut(ORMModel):
    id: uuid.UUID
    holiday_date: date
    name: str
    workplace_id: uuid.UUID | None
