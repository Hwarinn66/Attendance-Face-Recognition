from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from app.models.enums import AuditCategory, AuditOutcome, AuditSeverity
from app.schemas.common import ORMModel, StrictInput


class AuditEventOut(ORMModel):
    id: int
    occurred_at: datetime
    category: AuditCategory
    action: str
    outcome: AuditOutcome
    severity: AuditSeverity
    actor_user_id: uuid.UUID | None
    actor_email: str | None = None
    target_type: str | None
    target_id: str | None
    ip_address: str | None
    request_id: str | None
    details: dict[str, Any]


class SettingsOut(ORMModel):
    values: dict[str, Any]
    descriptions: dict[str, str]


class SettingsUpdate(StrictInput):
    values: dict[str, Any]


class SummaryRow(ORMModel):
    user_id: uuid.UUID
    user_name: str
    employee_code: str | None
    department: str | None
    scheduled: int
    present: int
    on_time: int
    late: int
    early_departures: int
    missing_checkouts: int
    absent: int
    late_minutes: int
    worked_minutes: int
    overtime_minutes: int


class DailyPoint(ORMModel):
    day: date
    scheduled: int
    present: int
    late: int
    absent: int


class OverviewOut(ORMModel):
    date: date
    scheduled: int
    checked_in: int
    late: int
    checked_out: int
    absent_so_far: int
    pending_corrections: int
    enrollment: dict[str, int]
    trend: list[DailyPoint]
    security_events_24h: int
