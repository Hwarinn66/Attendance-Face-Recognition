"""Pure attendance rule engine (no I/O).

Concepts
--------
* A *shift policy* is a recurring template (local start/end wall-clock times + window offsets).
* A *shift occurrence* is that template materialised for one ``shift_date`` — the local calendar date
  on which the shift **starts**. Overnight shifts (end <= start) end on the following local date.
* All instants are timezone-aware UTC ``datetime``s. Local wall-clock times are converted with
  ``zoneinfo`` using PEP 495 semantics: a wall time inside a DST gap (non-existent) resolves with the
  pre-transition offset, i.e. it lands *after* the gap; an ambiguous wall time (DST fold) resolves to
  its first occurrence. Durations are therefore real elapsed time, so DST nights are 1 h shorter/longer.

Boundaries (all inclusive):
* check-in allowed  iff  start - opens_before <= t <= start + closes_after
* on time           iff  t <= start + grace;  otherwise late_minutes = ceil((t - start) / 1 min)
* check-out allowed iff  end - opens_before <= t <= end + closes_after  and  t >= check_in_at
* early departure   iff  t < end - early_grace;  early_minutes = ceil((end - t) / 1 min)
* overtime          iff  t > end + overtime_threshold;  overtime_minutes = floor((t - end) / 1 min)
* worked_minutes = max(0, floor((out - in) / 1 min) - break) — the unpaid break is only deducted
  when the elapsed time exceeds it.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.models.enums import CheckInStatus, CheckOutStatus

ALL_WEEKDAYS_MASK = 0b1111111


class RuleError(ValueError):
    """Raised when a policy is internally inconsistent."""


@dataclass(frozen=True, slots=True)
class ShiftPolicy:
    shift_id: uuid.UUID
    workplace_id: uuid.UUID
    timezone: str
    start_time: time
    end_time: time
    check_in_opens_before_min: int = 60
    check_in_closes_after_min: int = 60
    grace_min: int = 15
    check_out_opens_before_min: int = 60
    check_out_closes_after_min: int = 120
    early_departure_grace_min: int = 0
    overtime_threshold_min: int = 30
    break_min: int = 0
    workdays_mask: int = 0b0011111

    @property
    def is_overnight(self) -> bool:
        return self.end_time <= self.start_time

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def works_on(self, shift_date: date) -> bool:
        return bool(self.workdays_mask & (1 << shift_date.weekday()))


@dataclass(frozen=True, slots=True)
class ShiftOccurrence:
    shift_id: uuid.UUID
    workplace_id: uuid.UUID
    shift_date: date
    start_at: datetime
    end_at: datetime
    check_in_opens_at: datetime
    check_in_closes_at: datetime
    check_out_opens_at: datetime
    check_out_closes_at: datetime

    def check_in_open(self, at: datetime) -> bool:
        return self.check_in_opens_at <= at <= self.check_in_closes_at

    def check_out_open(self, at: datetime) -> bool:
        return self.check_out_opens_at <= at <= self.check_out_closes_at


@dataclass(frozen=True, slots=True)
class AssignedPolicy:
    """A policy plus the date range during which the employee is assigned to it."""

    policy: ShiftPolicy
    effective_from: date
    effective_to: date | None

    def active_on(self, shift_date: date) -> bool:
        return self.effective_from <= shift_date and (self.effective_to is None or shift_date <= self.effective_to)


@dataclass(frozen=True, slots=True)
class CheckInEvaluation:
    status: CheckInStatus
    late_minutes: int


@dataclass(frozen=True, slots=True)
class CheckOutEvaluation:
    status: CheckOutStatus
    early_departure_minutes: int
    overtime_minutes: int
    worked_minutes: int


def local_to_utc(local_date: date, wall: time, tz: ZoneInfo) -> datetime:
    # fold=0: gap -> pre-transition offset (lands after the gap); fold -> first occurrence.
    return datetime.combine(local_date, wall, tzinfo=tz).astimezone(UTC)


def validate_policy(policy: ShiftPolicy) -> None:
    """Reject policies whose windows overlap or invert (would make check-in/out ambiguous)."""
    ZoneInfo(policy.timezone)  # raises ZoneInfoNotFoundError for unknown zones
    if policy.start_time == policy.end_time:
        raise RuleError("shift start and end must differ")
    if not 1 <= policy.workdays_mask <= ALL_WEEKDAYS_MASK:
        raise RuleError("at least one workday is required")
    # Evaluate on a reference date without DST effects (nominal durations).
    ref = date(2001, 1, 1)
    start = datetime.combine(ref, policy.start_time)
    end = datetime.combine(ref + timedelta(days=1 if policy.is_overnight else 0), policy.end_time)
    length = end - start
    if length > timedelta(hours=20):
        raise RuleError("shift length must not exceed 20 hours")
    check_in_close = start + timedelta(minutes=policy.check_in_closes_after_min)
    check_out_open = end - timedelta(minutes=policy.check_out_opens_before_min)
    if check_in_close >= check_out_open:
        raise RuleError("the check-in window must close before the check-out window opens")
    if policy.grace_min > policy.check_in_closes_after_min:
        raise RuleError("grace period cannot extend beyond the check-in window")
    if (end + timedelta(minutes=policy.check_out_closes_after_min)) - (
        start - timedelta(minutes=policy.check_in_opens_before_min)
    ) > timedelta(hours=24):
        raise RuleError("a shift's full window (check-in open to check-out close) must fit within 24 hours")


def occurrence_for(policy: ShiftPolicy, shift_date: date) -> ShiftOccurrence:
    tz = policy.tz
    start_at = local_to_utc(shift_date, policy.start_time, tz)
    end_date = shift_date + timedelta(days=1) if policy.is_overnight else shift_date
    end_at = local_to_utc(end_date, policy.end_time, tz)
    return ShiftOccurrence(
        shift_id=policy.shift_id,
        workplace_id=policy.workplace_id,
        shift_date=shift_date,
        start_at=start_at,
        end_at=end_at,
        check_in_opens_at=start_at - timedelta(minutes=policy.check_in_opens_before_min),
        check_in_closes_at=start_at + timedelta(minutes=policy.check_in_closes_after_min),
        check_out_opens_at=end_at - timedelta(minutes=policy.check_out_opens_before_min),
        check_out_closes_at=end_at + timedelta(minutes=policy.check_out_closes_after_min),
    )


def scheduled_occurrences(
    assignments: Iterable[AssignedPolicy],
    shift_date: date,
    holidays: set[tuple[uuid.UUID | None, date]],
) -> list[ShiftOccurrence]:
    """Occurrences an employee is scheduled for on ``shift_date`` (local start date).

    ``holidays`` holds ``(workplace_id, date)`` pairs; ``(None, date)`` applies to all workplaces.
    """
    result: list[ShiftOccurrence] = []
    for assigned in assignments:
        p = assigned.policy
        if not assigned.active_on(shift_date) or not p.works_on(shift_date):
            continue
        if (None, shift_date) in holidays or (p.workplace_id, shift_date) in holidays:
            continue
        result.append(occurrence_for(p, shift_date))
    return sorted(result, key=lambda o: o.start_at)


def candidate_dates(at: datetime, timezones: Iterable[str]) -> list[date]:
    """Local dates whose occurrences could have windows containing ``at`` (yesterday..tomorrow)."""
    dates: set[date] = set()
    for tz_name in set(timezones):
        local = at.astimezone(ZoneInfo(tz_name)).date()
        dates.update({local - timedelta(days=1), local, local + timedelta(days=1)})
    return sorted(dates)


def occurrences_around(
    assignments: Sequence[AssignedPolicy],
    at: datetime,
    holidays: set[tuple[uuid.UUID | None, date]],
) -> list[ShiftOccurrence]:
    dates = candidate_dates(at, (a.policy.timezone for a in assignments))
    occurrences: list[ShiftOccurrence] = []
    for d in dates:
        occurrences.extend(scheduled_occurrences(assignments, d, holidays))
    return sorted(occurrences, key=lambda o: o.start_at)


def select_check_in_occurrence(
    occurrences: Sequence[ShiftOccurrence],
    at: datetime,
    already_checked_in: set[tuple[uuid.UUID, date]],
) -> ShiftOccurrence | None:
    """The open occurrence (window contains ``at``) without an existing record, earliest start first."""
    for occ in sorted(occurrences, key=lambda o: o.start_at):
        if occ.check_in_open(at) and (occ.shift_id, occ.shift_date) not in already_checked_in:
            return occ
    return None


def evaluate_check_in(policy: ShiftPolicy, occ: ShiftOccurrence, at: datetime) -> CheckInEvaluation:
    """Live check-in: enforces the window, then computes metrics."""
    _require_aware(at)
    if not occ.check_in_open(at):
        raise RuleError("check-in time is outside the permitted window")
    return check_in_metrics(policy, occ, at)


def check_in_metrics(policy: ShiftPolicy, occ: ShiftOccurrence, at: datetime) -> CheckInEvaluation:
    """Metrics only (no window enforcement) — used by authorised corrections."""
    _require_aware(at)
    if at <= occ.start_at + timedelta(minutes=policy.grace_min):
        return CheckInEvaluation(CheckInStatus.ON_TIME, 0)
    late = math.ceil((at - occ.start_at).total_seconds() / 60)
    return CheckInEvaluation(CheckInStatus.LATE, late)


def evaluate_check_out(
    policy: ShiftPolicy, occ: ShiftOccurrence, check_in_at: datetime, at: datetime
) -> CheckOutEvaluation:
    """Live check-out: enforces the window and ordering, then computes metrics."""
    _require_aware(at)
    _require_aware(check_in_at)
    if at < check_in_at:
        raise RuleError("check-out cannot precede check-in")
    if not occ.check_out_open(at):
        raise RuleError("check-out time is outside the permitted window")
    return check_out_metrics(policy, occ, check_in_at, at)


def check_out_metrics(
    policy: ShiftPolicy, occ: ShiftOccurrence, check_in_at: datetime, at: datetime
) -> CheckOutEvaluation:
    """Metrics only (no window enforcement) — used by authorised corrections."""
    _require_aware(at)
    _require_aware(check_in_at)
    if at < check_in_at:
        raise RuleError("check-out cannot precede check-in")
    early = 0
    overtime = 0
    status = CheckOutStatus.ON_TIME
    if at < occ.end_at - timedelta(minutes=policy.early_departure_grace_min):
        early = math.ceil((occ.end_at - at).total_seconds() / 60)
        status = CheckOutStatus.EARLY
    elif at > occ.end_at + timedelta(minutes=policy.overtime_threshold_min):
        overtime = math.floor((at - occ.end_at).total_seconds() / 60)
        status = CheckOutStatus.OVERTIME
    return CheckOutEvaluation(status, early, overtime, worked_minutes(check_in_at, at, policy.break_min))


def worked_minutes(check_in_at: datetime, check_out_at: datetime, break_min: int) -> int:
    elapsed = math.floor((check_out_at - check_in_at).total_seconds() / 60)
    if elapsed > break_min:
        return elapsed - break_min
    return max(0, elapsed)


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RuleError("timestamps must be timezone-aware")


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
