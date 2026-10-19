"""Seed the Playwright E2E database (TEST FIXTURE ONLY).

Refuses to run unless APP_ENV=test and the database name ends in ``_e2e``. Truncates every table, then
creates users and shifts positioned around *now* so that a check-in window and a check-out window are
open while the browser tests run. Writes credentials (random per run) to frontend/e2e/.fixtures/seed.json.
"""

from __future__ import annotations

import json
import secrets
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import make_url, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import init_engine, session_scope
from app.models import AttendanceRecord, Shift, ShiftAssignment, User, Workplace
from app.models.enums import CheckInStatus, Role
from app.services.attendance.rules import occurrence_for
from app.services.scheduling import policy_from_shift

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "frontend" / "e2e" / ".fixtures" / "seed.json"
TABLES = (
    "attendance_corrections, attendance_records, biometric_templates, enrollment_sessions, verification_attempts, "
    "liveness_challenges, biometric_consents, idempotency_keys, shift_assignments, system_settings, auth_sessions, "
    "login_attempts, audit_events, users, holidays, shifts, workplaces"
)


def main() -> None:
    settings = get_settings()
    db_name = make_url(settings.database_url.get_secret_value()).database or ""
    if settings.app_env != "test" or not db_name.endswith("_e2e"):
        sys.exit("seed_e2e refuses to run: requires APP_ENV=test and a database name ending in '_e2e'")
    init_engine(settings)
    password = "E2e-" + secrets.token_urlsafe(12) + "-9x"
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    with session_scope() as db:
        db.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
        wp = Workplace(name="E2E Office", timezone="UTC")
        db.add(wp)
        db.flush()
        start_now = now - timedelta(minutes=5)
        end_soon = now + timedelta(minutes=20)
        current = Shift(
            workplace_id=wp.id,
            name="Current shift",
            start_time=start_now.time(),
            end_time=(start_now + timedelta(hours=4)).time(),
            workdays_mask=0b1111111,
            break_min=30,
        )
        ending = Shift(
            workplace_id=wp.id,
            name="Ending shift",
            start_time=(end_soon - timedelta(hours=4)).time(),
            end_time=end_soon.time(),
            workdays_mask=0b1111111,
            break_min=30,
        )
        db.add_all([current, ending])
        db.flush()
        users = {
            "admin": User(email="admin@e2e.example.com", full_name="E2E Admin", role=Role.ADMIN),
            "employee": User(email="employee@e2e.example.com", full_name="Erin Employee", role=Role.EMPLOYEE),
            "leaver": User(email="leaver@e2e.example.com", full_name="Lee Leaver", role=Role.EMPLOYEE),
            "spoofer": User(email="spoofer@e2e.example.com", full_name="Sam Spoofer", role=Role.EMPLOYEE),
        }
        for u in users.values():
            u.password_hash = hash_password(password)
            u.workplace_id = wp.id
            db.add(u)
        db.flush()
        for key in ("employee", "spoofer"):
            db.add(ShiftAssignment(user_id=users[key].id, shift_id=current.id, effective_from=date(2026, 1, 1)))
        db.add(ShiftAssignment(user_id=users["leaver"].id, shift_id=ending.id, effective_from=date(2026, 1, 1)))
        db.flush()
        db.refresh(ending)
        shift_date = (end_soon - timedelta(hours=4)).date()
        occ = occurrence_for(policy_from_shift(ending), shift_date)
        db.add(
            AttendanceRecord(
                user_id=users["leaver"].id,
                shift_id=ending.id,
                workplace_id=wp.id,
                shift_date=shift_date,
                scheduled_start_at=occ.start_at,
                scheduled_end_at=occ.end_at,
                check_out_closes_at=occ.check_out_closes_at,
                check_in_at=occ.start_at,
                check_in_status=CheckInStatus.ON_TIME,
                late_minutes=0,
                source="correction",
            )
        )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({"password": password, "users": {k: u.email for k, u in users.items()}}, indent=2), encoding="utf-8"
    )
    print(f"seeded E2E database; credentials in {OUT}")


if __name__ == "__main__":
    main()
