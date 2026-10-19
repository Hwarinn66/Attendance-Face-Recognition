from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import func, select

from app.api.deps import AuthContext, ContextDep, DbDep, SettingsDep, require
from app.api.v1.attendance import record_out
from app.core.errors import ValidationFailed
from app.core.permissions import Permission
from app.db.base import utcnow
from app.models import (
    AttendanceRecord,
    AuditEvent,
    BiometricConsent,
    BiometricTemplate,
    User,
    VerificationAttempt,
)
from app.models.enums import AuditCategory, AuditSeverity
from app.schemas.admin import AuditEventOut, OverviewOut, SettingsOut, SettingsUpdate, SummaryRow
from app.schemas.common import Page
from app.services import audit, reports, system_settings

router = APIRouter(tags=["administration"])

AuditRead = Annotated[AuthContext, Depends(require(Permission.AUDIT_READ))]
SettingsManage = Annotated[AuthContext, Depends(require(Permission.SETTINGS_MANAGE))]
ReportsRead = Annotated[AuthContext, Depends(require(Permission.REPORTS_READ))]
SelfService = Annotated[AuthContext, Depends(require(Permission.SELF_SERVICE))]


def _range(start: date | None, end: date | None) -> tuple[date, date]:
    end = end or utcnow().date()
    start = start or end.replace(day=1)
    if start > end or (end - start).days > reports.MAX_RANGE_DAYS:
        raise ValidationFailed("invalid date range")
    return start, end


# --- audit ----------------------------------------------------------------------------------------------
@router.get("/audit/events", response_model=Page[AuditEventOut])
def audit_events(
    auth: AuditRead,
    db: DbDep,
    category: AuditCategory | None = None,
    severity: AuditSeverity | None = None,
    action: Annotated[str | None, Query(max_length=80)] = None,
    actor_id: uuid.UUID | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    security_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[AuditEventOut]:
    stmt = select(AuditEvent, User.email).outerjoin(User, User.id == AuditEvent.actor_user_id)
    if category is not None:
        stmt = stmt.where(AuditEvent.category == category)
    if severity is not None:
        stmt = stmt.where(AuditEvent.severity == severity)
    if security_only:
        stmt = stmt.where(
            (AuditEvent.category == AuditCategory.SECURITY)
            | AuditEvent.severity.in_([AuditSeverity.WARNING, AuditSeverity.CRITICAL])
        )
    if action:
        stmt = stmt.where(AuditEvent.action.startswith(action))
    if actor_id is not None:
        stmt = stmt.where(AuditEvent.actor_user_id == actor_id)
    if start is not None:
        stmt = stmt.where(AuditEvent.occurred_at >= start)
    if end is not None:
        stmt = stmt.where(AuditEvent.occurred_at <= end)
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = db.execute(stmt.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc()).limit(limit).offset(offset))
    items = [
        AuditEventOut.model_validate({**AuditEventOut.model_validate(e).model_dump(), "actor_email": email})
        for e, email in rows.all()
    ]
    return Page(items=items, total=total, limit=limit, offset=offset)


# --- settings -------------------------------------------------------------------------------------------
@router.get("/settings", response_model=SettingsOut)
def get_settings_view(auth: SettingsManage, db: DbDep, settings: SettingsDep) -> SettingsOut:
    return SettingsOut(
        values=system_settings.get_all(db, settings),
        descriptions={k: v.description for k, v in system_settings.SPECS.items()},
    )


@router.patch("/settings", response_model=SettingsOut)
def update_settings(
    body: SettingsUpdate, auth: SettingsManage, db: DbDep, settings: SettingsDep, ctx: ContextDep
) -> SettingsOut:
    current = system_settings.get_all(db, settings)
    if (
        "consent.text" in body.values
        and body.values["consent.text"] != current["consent.text"]
        and body.values.get("consent.version", current["consent.version"]) == current["consent.version"]
    ):
        raise ValidationFailed(
            "Changing the consent text requires a new consent version", code="consent_version_required"
        )
    applied = system_settings.update(db, body.values, auth.user.id)
    audit.add(
        db,
        category=AuditCategory.ADMIN,
        action="settings.updated",
        actor_id=auth.user.id,
        ctx=ctx,
        severity=AuditSeverity.WARNING,
        details={
            "changes": {
                k: {"from": o if k != "consent.text" else "(text)", "to": n if k != "consent.text" else "(text)"}
                for k, (o, n) in applied.items()
            }
        },
    )
    db.commit()
    return get_settings_view(auth, db, settings)


# --- reports --------------------------------------------------------------------------------------------------
@router.get("/reports/overview", response_model=OverviewOut)
def overview(auth: ReportsRead, db: DbDep, day: date | None = None) -> OverviewOut:
    return OverviewOut.model_validate(reports.overview(db, auth.user, day or utcnow().date()))


@router.get("/reports/summary", response_model=list[SummaryRow])
def summary(
    auth: ReportsRead,
    db: DbDep,
    start: date | None = None,
    end: date | None = None,
    workplace_id: uuid.UUID | None = None,
) -> list[SummaryRow]:
    s, e = _range(start, end)
    return [SummaryRow.model_validate(r) for r in reports.summary(db, auth.user, s, e, workplace_id)]


def _csv_response(chunks: Any, filename: str) -> StreamingResponse:
    return StreamingResponse(
        chunks,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/reports/summary.csv")
def summary_csv(
    auth: ReportsRead,
    db: DbDep,
    ctx: ContextDep,
    start: date | None = None,
    end: date | None = None,
    workplace_id: uuid.UUID | None = None,
) -> StreamingResponse:
    s, e = _range(start, end)
    rows = reports.summary(db, auth.user, s, e, workplace_id)
    audit.add(
        db,
        category=AuditCategory.ADMIN,
        action="report.exported",
        actor_id=auth.user.id,
        ctx=ctx,
        details={"report": "summary", "start": str(s), "end": str(e), "rows": len(rows)},
    )
    db.commit()
    return _csv_response(reports.summary_csv(rows), f"attendance-summary-{s}-{e}.csv")


@router.get("/reports/attendance.csv")
def attendance_csv(
    auth: ReportsRead, db: DbDep, ctx: ContextDep, start: date | None = None, end: date | None = None
) -> StreamingResponse:
    s, e = _range(start, end)
    audit.add(
        db,
        category=AuditCategory.ADMIN,
        action="report.exported",
        actor_id=auth.user.id,
        ctx=ctx,
        details={"report": "attendance", "start": str(s), "end": str(e)},
    )
    db.commit()
    return _csv_response(reports.records_csv(db, auth.user, s, e), f"attendance-{s}-{e}.csv")


# --- privacy: personal data export --------------------------------------------------------------------
@router.get("/me/export")
def export_my_data(auth: SelfService, db: DbDep, ctx: ContextDep) -> Response:
    """Data-subject access export. Contains template *metadata* only: the encrypted template itself is not
    meaningful outside this system and is never released."""
    u = auth.user
    since = utcnow() - timedelta(days=3650)
    records = db.scalars(
        select(AttendanceRecord).where(AttendanceRecord.user_id == u.id, AttendanceRecord.created_at >= since)
    ).all()
    consents = db.scalars(select(BiometricConsent).where(BiometricConsent.user_id == u.id)).all()
    templates = db.scalars(select(BiometricTemplate).where(BiometricTemplate.user_id == u.id)).all()
    attempts = db.scalars(select(VerificationAttempt).where(VerificationAttempt.user_id == u.id)).all()
    payload = {
        "generated_at": utcnow().isoformat(),
        "profile": {
            "id": str(u.id),
            "email": u.email,
            "full_name": u.full_name,
            "role": u.role.value,
            "employee_code": u.employee_code,
            "department": u.department,
            "position": u.position,
            "biometric_status": u.biometric_status.value,
        },
        "attendance": [record_out(r).model_dump(mode="json") for r in records],
        "consents": [
            {
                "version": c.consent_version,
                "granted_at": c.granted_at.isoformat(),
                "withdrawn_at": c.withdrawn_at.isoformat() if c.withdrawn_at else None,
            }
            for c in consents
        ],
        "biometric_templates": [
            {
                "id": str(t.id),
                "model": t.model_name,
                "created_at": t.created_at.isoformat(),
                "note": "encrypted template withheld",
            }
            for t in templates
        ],
        "verification_attempts": [
            {
                "at": a.created_at.isoformat(),
                "purpose": a.purpose.value,
                "decision": a.decision.value,
                "reasons": a.reason_codes,
            }
            for a in attempts
        ],
    }
    audit.add(db, category=AuditCategory.PRIVACY, action="privacy.data_export", actor_id=u.id, ctx=ctx)
    db.commit()
    return Response(
        json.dumps(payload, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="my-data.json"'},
    )
