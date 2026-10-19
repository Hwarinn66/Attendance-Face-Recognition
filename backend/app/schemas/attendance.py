from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import Field, model_validator

from app.models.enums import CheckInStatus, CheckOutStatus, CorrectionStatus
from app.schemas.common import ORMModel, StrictInput


class OccurrenceOut(ORMModel):
    shift_id: uuid.UUID
    shift_name: str
    workplace_id: uuid.UUID
    timezone: str
    shift_date: date
    start_at: datetime
    end_at: datetime
    check_in_opens_at: datetime
    check_in_closes_at: datetime
    check_out_opens_at: datetime
    check_out_closes_at: datetime
    record_id: uuid.UUID | None = None


class AttendanceRecordOut(ORMModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user_name: str | None = None
    shift_id: uuid.UUID
    shift_name: str | None = None
    shift_date: date
    scheduled_start_at: datetime
    scheduled_end_at: datetime
    check_in_at: datetime
    check_in_status: CheckInStatus
    late_minutes: int
    check_out_at: datetime | None
    check_out_status: CheckOutStatus | None
    early_departure_minutes: int
    overtime_minutes: int
    worked_minutes: int | None
    check_in_distance_m: float | None
    is_corrected: bool
    source: str
    missing_checkout: bool = False


class AttendanceStatusOut(ORMModel):
    server_time: datetime
    biometric_status: str
    can_check_in: bool
    can_check_out: bool
    check_in_blocker: str | None
    check_out_blocker: str | None
    open_record: AttendanceRecordOut | None
    today: list[OccurrenceOut]
    next_occurrence: OccurrenceOut | None
    geofence_required: bool


class LocationIn(StrictInput):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0, le=100_000)


class AttendanceActionIn(StrictInput):
    challenge_id: uuid.UUID
    location: LocationIn | None = None


class CorrectionIn(StrictInput):
    user_id: uuid.UUID | None = None  # defaults to the requester; HR may file for others
    shift_id: uuid.UUID
    shift_date: date
    requested_check_in_at: datetime | None = None
    requested_check_out_at: datetime | None = None
    reason: str = Field(min_length=5, max_length=2000)
    apply_immediately: bool = False

    @model_validator(mode="after")
    def _validate(self) -> CorrectionIn:
        if self.requested_check_in_at is None and self.requested_check_out_at is None:
            raise ValueError("specify a check-in and/or check-out time")
        for v in (self.requested_check_in_at, self.requested_check_out_at):
            if v is not None and v.tzinfo is None:
                raise ValueError("timestamps must include a UTC offset")
        if (
            self.requested_check_in_at
            and self.requested_check_out_at
            and self.requested_check_out_at < self.requested_check_in_at
        ):
            raise ValueError("check-out must not precede check-in")
        return self


class RecordDeleteIn(StrictInput):
    reason: str = Field(min_length=5, max_length=1000)


class CorrectionReviewIn(StrictInput):
    approve: bool
    note: str | None = Field(default=None, max_length=2000)


class CorrectionOut(ORMModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user_name: str | None = None
    attendance_record_id: uuid.UUID | None
    shift_id: uuid.UUID
    shift_date: date
    requested_check_in_at: datetime | None
    requested_check_out_at: datetime | None
    reason: str
    status: CorrectionStatus
    requested_by_id: uuid.UUID
    reviewed_by_id: uuid.UUID | None
    reviewed_at: datetime | None
    review_note: str | None
    created_at: datetime


class ExceptionOut(ORMModel):
    kind: str  # late | early_departure | missing_checkout | absent
    user_id: uuid.UUID
    user_name: str
    shift_id: uuid.UUID
    shift_name: str
    shift_date: date
    minutes: int | None
    record_id: uuid.UUID | None
