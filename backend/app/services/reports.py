"""Reports computed from persisted attendance data and schedules (never synthesised)."""

from __future__ import annotations

import csv
import io
import uuid
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models import AttendanceCorrection, AttendanceRecord, AuditEvent, User
from app.models.enums import AuditSeverity, BiometricStatus, CorrectionStatus
from app.services.attendance.service import expected_occurrences, is_missing_checkout
from app.services.users import visibility_clause

MAX_RANGE_DAYS = 370


@dataclass(slots=True)
class SummaryRow:
    user_id: uuid.UUID
    user_name: str
    employee_code: str | None
    department: str | None
    scheduled: int = 0
    present: int = 0
    on_time: int = 0
    late: int = 0
    early_departures: int = 0
    missing_checkouts: int = 0
    absent: int = 0
    late_minutes: int = 0
    worked_minutes: int = 0
    overtime_minutes: int = 0


def _visible_users(db: Session, actor: User, workplace_id: uuid.UUID | None) -> list[User]:
    stmt = select(User).where(visibility_clause(actor), User.is_active.is_(True))
    if workplace_id is not None:
        stmt = stmt.where(User.workplace_id == workplace_id)
    return list(db.scalars(stmt.order_by(User.full_name)).all())


def summary(
    db: Session, actor: User, start: date, end: date, workplace_id: uuid.UUID | None = None
) -> list[SummaryRow]:
    now = utcnow()
    users = _visible_users(db, actor, workplace_id)
    rows = {u.id: SummaryRow(u.id, u.full_name, u.employee_code, u.department) for u in users}
    if not users:
        return []
    records = db.scalars(
        select(AttendanceRecord).where(
            AttendanceRecord.user_id.in_(rows), AttendanceRecord.shift_date >= start, AttendanceRecord.shift_date <= end
        )
    ).all()
    have = set()
    for r in records:
        row = rows[r.user_id]
        have.add((r.user_id, r.shift_id, r.shift_date))
        row.present += 1
        if r.late_minutes > 0:
            row.late += 1
            row.late_minutes += r.late_minutes
        else:
            row.on_time += 1
        if r.early_departure_minutes > 0:
            row.early_departures += 1
        if is_missing_checkout(r, now):
            row.missing_checkouts += 1
        row.worked_minutes += r.worked_minutes or 0
        row.overtime_minutes += r.overtime_minutes
    for uid, occs in expected_occurrences(db, users, start, end).items():
        for o in occs:
            if o.check_in_closes_at < now:
                rows[uid].scheduled += 1
                if (uid, o.shift_id, o.shift_date) not in have:
                    rows[uid].absent += 1
    # Records outside a currently-assigned schedule (e.g. corrections) still count as scheduled.
    for row in rows.values():
        row.scheduled = max(row.scheduled, row.present + row.absent)
    return list(rows.values())


@dataclass(slots=True)
class DailyPoint:
    day: date
    scheduled: int
    present: int
    late: int
    absent: int


def overview(db: Session, actor: User, day: date, trend_days: int = 14) -> dict[str, object]:
    now = utcnow()
    users = _visible_users(db, actor, None)
    ids = [u.id for u in users]
    start = day - timedelta(days=trend_days - 1)
    occs = expected_occurrences(db, users, start, day)
    records = (
        db.scalars(
            select(AttendanceRecord).where(
                AttendanceRecord.user_id.in_(ids),
                AttendanceRecord.shift_date >= start,
                AttendanceRecord.shift_date <= day,
            )
        ).all()
        if ids
        else []
    )
    by_day: dict[date, list[AttendanceRecord]] = defaultdict(list)
    have = set()
    for r in records:
        by_day[r.shift_date].append(r)
        have.add((r.user_id, r.shift_id, r.shift_date))
    trend: list[DailyPoint] = []
    for i in range(trend_days):
        d = start + timedelta(days=i)
        scheduled = [(uid, o) for uid, os in occs.items() for o in os if o.shift_date == d]
        absent = sum(1 for uid, o in scheduled if o.check_in_closes_at < now and (uid, o.shift_id, d) not in have)
        trend.append(DailyPoint(d, len(scheduled), len(by_day[d]), sum(r.late_minutes > 0 for r in by_day[d]), absent))
    today = trend[-1]
    enrollment = {s.value: 0 for s in BiometricStatus}
    for u in users:
        enrollment[u.biometric_status.value] += 1
    pending = int(
        db.scalar(
            select(func.count())
            .select_from(AttendanceCorrection)
            .join(User, User.id == AttendanceCorrection.user_id)
            .where(AttendanceCorrection.status == CorrectionStatus.PENDING, visibility_clause(actor))
        )
        or 0
    )
    security = int(
        db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.occurred_at >= now - timedelta(hours=24),
                AuditEvent.severity.in_([AuditSeverity.WARNING, AuditSeverity.CRITICAL]),
            )
        )
        or 0
    )
    return {
        "date": day,
        "scheduled": today.scheduled,
        "checked_in": today.present,
        "late": today.late,
        "checked_out": sum(r.check_out_at is not None for r in by_day[day]),
        "absent_so_far": today.absent,
        "pending_corrections": pending,
        "enrollment": enrollment,
        "trend": trend,
        "security_events_24h": security,
    }


# --- CSV ------------------------------------------------------------------------------------------------

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value: object) -> str:
    """Neutralise spreadsheet formula injection (CWE-1236)."""
    text = "" if value is None else str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def _csv(header: list[str], rows: Iterable[list[object]]) -> Iterator[str]:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    yield buf.getvalue()
    for row in rows:
        buf.seek(0)
        buf.truncate()
        writer.writerow([csv_safe(v) for v in row])
        yield buf.getvalue()


def summary_csv(rows: list[SummaryRow]) -> Iterator[str]:
    header = [
        "employee_code",
        "name",
        "department",
        "scheduled",
        "present",
        "on_time",
        "late",
        "absent",
        "early_departures",
        "missing_checkouts",
        "late_minutes",
        "worked_minutes",
        "overtime_minutes",
    ]
    return _csv(
        header,
        (
            [
                r.employee_code,
                r.user_name,
                r.department,
                r.scheduled,
                r.present,
                r.on_time,
                r.late,
                r.absent,
                r.early_departures,
                r.missing_checkouts,
                r.late_minutes,
                r.worked_minutes,
                r.overtime_minutes,
            ]
            for r in rows
        ),
    )


def _local(ts: datetime | None, tz: str) -> str:
    return ts.astimezone(ZoneInfo(tz)).isoformat(timespec="seconds") if ts else ""


def records_csv(db: Session, actor: User, start: date, end: date) -> Iterator[str]:
    stmt = (
        select(AttendanceRecord)
        .join(User, User.id == AttendanceRecord.user_id)
        .where(visibility_clause(actor), AttendanceRecord.shift_date >= start, AttendanceRecord.shift_date <= end)
        .order_by(AttendanceRecord.shift_date, User.full_name)
    )
    now = utcnow()

    def rows() -> Iterator[list[object]]:
        for r in db.scalars(stmt):
            tz = r.shift.workplace.timezone
            yield [
                r.user.employee_code,
                r.user.full_name,
                r.shift.name,
                r.shift_date,
                _local(r.check_in_at, tz),
                r.check_in_status.value,
                r.late_minutes,
                _local(r.check_out_at, tz),
                r.check_out_status.value if r.check_out_status else "",
                r.early_departure_minutes,
                r.overtime_minutes,
                r.worked_minutes if r.worked_minutes is not None else "",
                "yes" if is_missing_checkout(r, now) else "no",
                "yes" if r.is_corrected else "no",
                tz,
            ]

    header = [
        "employee_code",
        "name",
        "shift",
        "shift_date",
        "check_in_local",
        "check_in_status",
        "late_minutes",
        "check_out_local",
        "check_out_status",
        "early_departure_minutes",
        "overtime_minutes",
        "worked_minutes",
        "missing_checkout",
        "corrected",
        "timezone",
    ]
    return _csv(header, rows())
