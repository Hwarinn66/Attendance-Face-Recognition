from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.models.enums import CheckInStatus, CheckOutStatus
from app.services.attendance.rules import (
    AssignedPolicy,
    RuleError,
    ShiftPolicy,
    evaluate_check_in,
    evaluate_check_out,
    haversine_m,
    occurrence_for,
    occurrences_around,
    scheduled_occurrences,
    select_check_in_occurrence,
    validate_policy,
    worked_minutes,
)

JKT = "Asia/Jakarta"  # UTC+7, no DST
NY = "America/New_York"
WP = uuid.UUID(int=1)


def policy(**overrides: object) -> ShiftPolicy:
    base: dict[str, object] = {
        "shift_id": uuid.UUID(int=10),
        "workplace_id": WP,
        "timezone": JKT,
        "start_time": time(8, 0),
        "end_time": time(17, 0),
        "check_in_opens_before_min": 60,  # 07:00
        "check_in_closes_after_min": 60,  # 09:00
        "grace_min": 15,
        "check_out_opens_before_min": 60,  # 16:00
        "check_out_closes_after_min": 120,  # 19:00
        "early_departure_grace_min": 0,
        "overtime_threshold_min": 30,
        "break_min": 60,
        "workdays_mask": 0b0011111,
    }
    base.update(overrides)
    return ShiftPolicy(**base)  # type: ignore[arg-type]


def local(d: date, h: int, m: int = 0, s: int = 0, tz: str = JKT) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, s, tzinfo=ZoneInfo(tz)).astimezone(UTC)


MONDAY = date(2026, 10, 12)


class TestOccurrence:
    def test_example_configuration_windows(self) -> None:
        occ = occurrence_for(policy(), MONDAY)
        assert occ.start_at == local(MONDAY, 8)
        assert occ.end_at == local(MONDAY, 17)
        assert occ.check_in_opens_at == local(MONDAY, 7)
        assert occ.check_in_closes_at == local(MONDAY, 9)
        assert occ.check_out_opens_at == local(MONDAY, 16)
        assert occ.check_out_closes_at == local(MONDAY, 19)
        assert occ.start_at.tzinfo == UTC

    def test_overnight_shift_ends_next_day(self) -> None:
        p = policy(start_time=time(22, 0), end_time=time(6, 0))
        occ = occurrence_for(p, MONDAY)
        assert occ.start_at == local(MONDAY, 22)
        assert occ.end_at == local(MONDAY + timedelta(days=1), 6)
        assert occ.end_at - occ.start_at == timedelta(hours=8)

    def test_dst_spring_forward_overnight_is_one_hour_shorter(self) -> None:
        # 2026-03-08 02:00 EST -> 03:00 EDT
        p = policy(timezone=NY, start_time=time(22, 0), end_time=time(6, 0), break_min=0)
        occ = occurrence_for(p, date(2026, 3, 7))
        assert occ.end_at - occ.start_at == timedelta(hours=7)

    def test_dst_fall_back_overnight_is_one_hour_longer(self) -> None:
        # 2026-11-01 02:00 EDT -> 01:00 EST
        p = policy(timezone=NY, start_time=time(22, 0), end_time=time(6, 0))
        occ = occurrence_for(p, date(2026, 10, 31))
        assert occ.end_at - occ.start_at == timedelta(hours=9)

    def test_nonexistent_local_start_lands_after_gap(self) -> None:
        p = policy(timezone=NY, start_time=time(2, 30), end_time=time(10, 0))
        occ = occurrence_for(p, date(2026, 3, 8))
        # 02:30 does not exist; resolves with the pre-transition offset (-05:00) => 07:30Z == 03:30 EDT
        assert occ.start_at == datetime(2026, 3, 8, 7, 30, tzinfo=UTC)

    def test_ambiguous_local_time_uses_first_occurrence(self) -> None:
        p = policy(timezone=NY, start_time=time(1, 30), end_time=time(9, 0))
        occ = occurrence_for(p, date(2026, 11, 1))
        assert occ.start_at == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)  # 01:30 EDT (-04:00)


class TestCheckIn:
    @pytest.mark.parametrize(
        ("hh", "mm", "ss", "status", "late"),
        [
            (7, 0, 0, CheckInStatus.ON_TIME, 0),  # window opens (inclusive)
            (8, 0, 0, CheckInStatus.ON_TIME, 0),
            (8, 15, 0, CheckInStatus.ON_TIME, 0),  # end of grace (inclusive)
            (8, 15, 1, CheckInStatus.LATE, 16),  # 15m01s after start -> ceil = 16
            (8, 40, 0, CheckInStatus.LATE, 40),
            (9, 0, 0, CheckInStatus.LATE, 60),  # window closes (inclusive)
        ],
    )
    def test_boundaries(self, hh: int, mm: int, ss: int, status: CheckInStatus, late: int) -> None:
        p = policy()
        occ = occurrence_for(p, MONDAY)
        result = evaluate_check_in(p, occ, local(MONDAY, hh, mm, ss))
        assert (result.status, result.late_minutes) == (status, late)

    @pytest.mark.parametrize(("hh", "mm", "ss"), [(6, 59, 59), (9, 0, 1)])
    def test_outside_window_rejected(self, hh: int, mm: int, ss: int) -> None:
        p = policy()
        occ = occurrence_for(p, MONDAY)
        with pytest.raises(RuleError):
            evaluate_check_in(p, occ, local(MONDAY, hh, mm, ss))

    def test_naive_timestamp_rejected(self) -> None:
        p = policy()
        occ = occurrence_for(p, MONDAY)
        with pytest.raises(RuleError):
            evaluate_check_in(p, occ, datetime(2026, 10, 12, 8, 0))


class TestCheckOut:
    def setup_method(self) -> None:
        self.p = policy()
        self.occ = occurrence_for(self.p, MONDAY)
        self.cin = local(MONDAY, 8, 0)

    @pytest.mark.parametrize(
        ("hh", "mm", "status", "early", "overtime"),
        [
            (16, 0, CheckOutStatus.EARLY, 60, 0),  # window opens (inclusive)
            (16, 59, CheckOutStatus.EARLY, 1, 0),
            (17, 0, CheckOutStatus.ON_TIME, 0, 0),
            (17, 30, CheckOutStatus.ON_TIME, 0, 0),  # exactly at overtime threshold: not overtime
            (17, 31, CheckOutStatus.OVERTIME, 0, 31),
            (19, 0, CheckOutStatus.OVERTIME, 0, 120),  # window closes (inclusive)
        ],
    )
    def test_boundaries(self, hh: int, mm: int, status: CheckOutStatus, early: int, overtime: int) -> None:
        r = evaluate_check_out(self.p, self.occ, self.cin, local(MONDAY, hh, mm))
        assert (r.status, r.early_departure_minutes, r.overtime_minutes) == (status, early, overtime)

    def test_worked_minutes_deduct_break(self) -> None:
        r = evaluate_check_out(self.p, self.occ, self.cin, local(MONDAY, 17, 0))
        assert r.worked_minutes == 9 * 60 - 60

    def test_early_departure_grace(self) -> None:
        p = policy(early_departure_grace_min=10)
        occ = occurrence_for(p, MONDAY)
        assert evaluate_check_out(p, occ, self.cin, local(MONDAY, 16, 50)).status == CheckOutStatus.ON_TIME
        r = evaluate_check_out(p, occ, self.cin, local(MONDAY, 16, 49))
        assert (r.status, r.early_departure_minutes) == (CheckOutStatus.EARLY, 11)

    @pytest.mark.parametrize(("hh", "mm"), [(15, 59), (19, 1)])
    def test_outside_window_rejected(self, hh: int, mm: int) -> None:
        with pytest.raises(RuleError):
            evaluate_check_out(self.p, self.occ, self.cin, local(MONDAY, hh, mm))

    def test_checkout_before_checkin_rejected(self) -> None:
        late_cin = local(MONDAY, 16, 30)
        with pytest.raises(RuleError):
            evaluate_check_out(self.p, self.occ, late_cin, local(MONDAY, 16, 10))

    def test_overnight_checkout_next_morning(self) -> None:
        p = policy(start_time=time(22, 0), end_time=time(6, 0), break_min=30)
        occ = occurrence_for(p, MONDAY)
        cin = local(MONDAY, 21, 50)
        r = evaluate_check_out(p, occ, cin, local(MONDAY + timedelta(days=1), 6, 5))
        assert r.status == CheckOutStatus.ON_TIME
        assert r.worked_minutes == 8 * 60 + 15 - 30

    def test_dst_overnight_worked_minutes_use_elapsed_time(self) -> None:
        p = policy(timezone=NY, start_time=time(22, 0), end_time=time(6, 0), break_min=0)
        occ = occurrence_for(p, date(2026, 3, 7))
        r = evaluate_check_out(p, occ, occ.start_at, occ.end_at)
        assert r.worked_minutes == 7 * 60


class TestScheduling:
    def test_weekend_excluded(self) -> None:
        saturday = date(2026, 10, 17)
        assigned = [AssignedPolicy(policy(), date(2026, 1, 1), None)]
        assert scheduled_occurrences(assigned, saturday, set()) == []
        assert len(scheduled_occurrences(assigned, MONDAY, set())) == 1

    def test_holiday_global_and_workplace(self) -> None:
        assigned = [AssignedPolicy(policy(), date(2026, 1, 1), None)]
        assert scheduled_occurrences(assigned, MONDAY, {(None, MONDAY)}) == []
        assert scheduled_occurrences(assigned, MONDAY, {(WP, MONDAY)}) == []
        assert len(scheduled_occurrences(assigned, MONDAY, {(uuid.UUID(int=99), MONDAY)})) == 1

    def test_assignment_effective_range(self) -> None:
        assigned = [AssignedPolicy(policy(), MONDAY + timedelta(days=1), MONDAY + timedelta(days=2))]
        assert scheduled_occurrences(assigned, MONDAY, set()) == []
        assert len(scheduled_occurrences(assigned, MONDAY + timedelta(days=2), set())) == 1
        assert scheduled_occurrences(assigned, MONDAY + timedelta(days=3), set()) == []

    def test_overnight_found_from_previous_local_date(self) -> None:
        p = policy(start_time=time(22, 0), end_time=time(6, 0), workdays_mask=0b1111111)
        assigned = [AssignedPolicy(p, date(2026, 1, 1), None)]
        at = local(MONDAY + timedelta(days=1), 5, 30)  # Tuesday 05:30 belongs to Monday's shift
        occs = occurrences_around(assigned, at, set())
        open_for_checkout = [o for o in occs if o.check_out_open(at)]
        assert [o.shift_date for o in open_for_checkout] == [MONDAY]

    def test_select_skips_already_checked_in_and_picks_earliest(self) -> None:
        morning = policy(shift_id=uuid.UUID(int=1))
        overlapping = policy(shift_id=uuid.UUID(int=2), start_time=time(8, 30), end_time=time(17, 30))
        assigned = [
            AssignedPolicy(morning, date(2026, 1, 1), None),
            AssignedPolicy(overlapping, date(2026, 1, 1), None),
        ]
        at = local(MONDAY, 8, 10)
        occs = occurrences_around(assigned, at, set())
        first = select_check_in_occurrence(occs, at, set())
        assert first is not None and first.shift_id == uuid.UUID(int=1)
        second = select_check_in_occurrence(occs, at, {(uuid.UUID(int=1), MONDAY)})
        assert second is not None and second.shift_id == uuid.UUID(int=2)
        assert select_check_in_occurrence(occs, at, {(uuid.UUID(int=1), MONDAY), (uuid.UUID(int=2), MONDAY)}) is None


class TestPolicyValidation:
    def test_valid_example(self) -> None:
        validate_policy(policy())

    def test_overlapping_windows_rejected(self) -> None:
        with pytest.raises(RuleError):
            validate_policy(policy(start_time=time(8, 0), end_time=time(9, 30)))

    def test_grace_beyond_window_rejected(self) -> None:
        with pytest.raises(RuleError):
            validate_policy(policy(grace_min=90))

    def test_unknown_timezone_rejected(self) -> None:
        with pytest.raises(Exception):  # noqa: B017 - ZoneInfoNotFoundError
            validate_policy(policy(timezone="Mars/Olympus"))

    def test_full_window_must_fit_in_24h(self) -> None:
        with pytest.raises(RuleError):
            validate_policy(
                policy(
                    start_time=time(6, 0),
                    end_time=time(2, 0),
                    check_in_opens_before_min=240,
                    check_out_closes_after_min=240,
                )
            )


def test_worked_minutes_short_shift_does_not_go_negative() -> None:
    t0 = datetime(2026, 1, 1, 8, tzinfo=UTC)
    assert worked_minutes(t0, t0 + timedelta(minutes=30), 60) == 30
    assert worked_minutes(t0, t0, 60) == 0


def test_haversine_known_distance() -> None:
    # Monas (Jakarta) to Bundaran HI ~ 2.0 km
    d = haversine_m(-6.175392, 106.827153, -6.194924, 106.823024)
    assert 2100 < d < 2300
