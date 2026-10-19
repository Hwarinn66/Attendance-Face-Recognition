from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Header, Query, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, ContextDep, DbDep, require, require_app
from app.api.v1.biometrics import RuntimeDep, challenge_out, read_frames, verification_out
from app.biometrics.decision import Decision, Reason
from app.core.errors import NotFound, ValidationFailed
from app.core.permissions import Permission
from app.db.base import utcnow
from app.models import AttendanceCorrection, AttendanceRecord, ShiftAssignment
from app.models.enums import ChallengePurpose, CorrectionStatus
from app.schemas.attendance import (
    AttendanceActionIn,
    AttendanceRecordOut,
    AttendanceStatusOut,
    CorrectionIn,
    CorrectionOut,
    CorrectionReviewIn,
    ExceptionOut,
    OccurrenceOut,
)
from app.schemas.biometrics import ChallengeCreateIn, ChallengeOut, VerificationOut
from app.schemas.common import Page
from app.schemas.org import AssignmentOut
from app.services import verification
from app.services.attendance import service as attendance
from app.services.attendance.rules import ShiftOccurrence
from app.services.users import get_visible_user

router = APIRouter(prefix="/attendance", tags=["attendance"])

SelfService = Annotated[AuthContext, Depends(require(Permission.SELF_SERVICE))]
TeamRead = Annotated[AuthContext, Depends(require(Permission.ATTENDANCE_READ_TEAM))]
CorrectionReview = Annotated[AuthContext, Depends(require(Permission.CORRECTION_REVIEW))]
MAX_RANGE_DAYS = 370


def record_out(r: AttendanceRecord) -> AttendanceRecordOut:
    data: dict[str, Any] = {
        f: getattr(r, f)
        for f in AttendanceRecordOut.model_fields
        if f not in {"user_name", "shift_name", "missing_checkout"}
    }
    data.update(user_name=r.user.full_name, shift_name=r.shift.name, missing_checkout=attendance.is_missing_checkout(r))
    return AttendanceRecordOut.model_validate(data)


def correction_out(c: AttendanceCorrection) -> CorrectionOut:
    data: dict[str, Any] = {f: getattr(c, f) for f in CorrectionOut.model_fields if f != "user_name"}
    data["user_name"] = c.user.full_name
    return CorrectionOut.model_validate(data)


def _occ_out(db: Session, o: ShiftOccurrence, record_id: uuid.UUID | None = None) -> OccurrenceOut:
    from app.models import Shift  # noqa: PLC0415

    shift = db.get(Shift, o.shift_id)
    assert shift is not None
    return OccurrenceOut(
        shift_id=o.shift_id,
        shift_name=shift.name,
        workplace_id=o.workplace_id,
        timezone=shift.workplace.timezone,
        shift_date=o.shift_date,
        start_at=o.start_at,
        end_at=o.end_at,
        check_in_opens_at=o.check_in_opens_at,
        check_in_closes_at=o.check_in_closes_at,
        check_out_opens_at=o.check_out_opens_at,
        check_out_closes_at=o.check_out_closes_at,
        record_id=record_id,
    )


def _range(start: date | None, end: date | None, default_days: int = 30) -> tuple[date, date]:
    end = end or utcnow().date()
    start = start or end - timedelta(days=default_days - 1)
    if start > end:
        raise ValidationFailed("start must not be after end")
    if (end - start).days > MAX_RANGE_DAYS:
        raise ValidationFailed(f"date range must not exceed {MAX_RANGE_DAYS} days")
    return start, end


@router.get("/status", response_model=AttendanceStatusOut)
def get_status(auth: SelfService, db: DbDep) -> AttendanceStatusOut:
    st = attendance.get_status(db, auth.user)
    relevant = [
        o
        for o in st.occurrences
        if o.check_in_opens_at <= st.server_time + timedelta(hours=18)
        and o.check_out_closes_at >= st.server_time - timedelta(hours=6)
    ]
    return AttendanceStatusOut(
        server_time=st.server_time,
        biometric_status=auth.user.biometric_status.value,
        can_check_in=st.check_in_target is not None,
        can_check_out=st.check_out_target is not None,
        check_in_blocker=st.check_in_blocker,
        check_out_blocker=st.check_out_blocker,
        open_record=record_out(st.check_out_target) if st.check_out_target else None,
        today=[
            _occ_out(
                db, o, st.records[(o.shift_id, o.shift_date)].id if (o.shift_id, o.shift_date) in st.records else None
            )
            for o in relevant
        ],
        next_occurrence=_occ_out(db, st.next_occurrence) if st.next_occurrence else None,
        geofence_required=st.geofence_required,
    )


@router.post("/challenges", response_model=ChallengeOut, dependencies=[Depends(require_app)])
def create_challenge(
    body: ChallengeCreateIn, auth: SelfService, db: DbDep, runtime: RuntimeDep, ctx: ContextDep
) -> ChallengeOut:
    if body.purpose == ChallengePurpose.ENROLLMENT:
        raise ValidationFailed("Use /biometrics/enrollments to enroll", code="wrong_purpose")
    attendance.ensure_action_possible(db, auth.user, body.purpose)
    ch = verification.issue_challenge(db, runtime, auth.user, auth.session.id, body.purpose, ctx)
    db.commit()
    return challenge_out(ch, runtime.settings)


@router.post("/challenges/{challenge_id}/submit", response_model=VerificationOut, dependencies=[Depends(require_app)])
def submit_challenge(
    challenge_id: uuid.UUID,
    auth: SelfService,
    db: DbDep,
    runtime: RuntimeDep,
    ctx: ContextDep,
    frames: Annotated[list[UploadFile], File(description="Ordered JPEG/PNG frames")],
) -> VerificationOut:
    data = read_frames(frames, runtime.settings)
    ch = verification.begin_submission(
        db, auth.user, auth.session.id, challenge_id, {ChallengePurpose.CHECK_IN, ChallengePurpose.CHECK_OUT}, ctx
    )
    try:
        ev = verification.evaluate(runtime, ch, data)
        verification.apply_match_gate(db, runtime, auth.user, ev)
    except Exception:
        db.rollback()
        failed = verification.Evaluation(Decision(accepted=False, reasons=[Reason.PROCESSING_ERROR]), None, 0)
        verification.record(db, runtime, auth.user, db.merge(ch), failed, len(data), ctx)
        db.commit()
        raise
    verification.record(db, runtime, auth.user, ch, ev, len(data), ctx)
    db.commit()
    return verification_out(ch, ev.decision, runtime.settings, auth.user)


def _do_action(
    kind: str,
    body: AttendanceActionIn,
    auth: AuthContext,
    db: Session,
    runtime: Any,
    ctx: Any,
    idem_key: str,
    response: Response,
) -> AttendanceRecordOut:
    fn = attendance.check_in if kind == "check_in" else attendance.check_out
    status_code, result = fn(
        db, auth.user, auth.session.id, body, idem_key, runtime.settings.verification_validity_seconds, ctx
    )
    response.status_code = status_code
    if isinstance(result, AttendanceRecord):
        return record_out(result)
    rec = db.get(AttendanceRecord, uuid.UUID(str(result["record_id"])))  # idempotent replay
    if rec is None:
        raise NotFound("Attendance record not found")
    return record_out(rec)


@router.post("/check-in", response_model=AttendanceRecordOut, status_code=201, dependencies=[Depends(require_app)])
def check_in(
    body: AttendanceActionIn,
    auth: SelfService,
    db: DbDep,
    runtime: RuntimeDep,
    ctx: ContextDep,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> AttendanceRecordOut:
    return _do_action("check_in", body, auth, db, runtime, ctx, idempotency_key, response)


@router.post("/check-out", response_model=AttendanceRecordOut, dependencies=[Depends(require_app)])
def check_out(
    body: AttendanceActionIn,
    auth: SelfService,
    db: DbDep,
    runtime: RuntimeDep,
    ctx: ContextDep,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> AttendanceRecordOut:
    return _do_action("check_out", body, auth, db, runtime, ctx, idempotency_key, response)


@router.get("/my-shifts", response_model=list[AssignmentOut])
def my_shifts(auth: SelfService, db: DbDep) -> list[AssignmentOut]:
    """The caller's own shift assignments (needed to file a correction for a day with no record)."""
    rows = db.scalars(
        select(ShiftAssignment)
        .where(ShiftAssignment.user_id == auth.user.id)
        .order_by(ShiftAssignment.effective_from.desc())
    ).all()
    return [
        AssignmentOut(
            id=a.id,
            user_id=a.user_id,
            shift_id=a.shift_id,
            shift_name=a.shift.name,
            effective_from=a.effective_from,
            effective_to=a.effective_to,
        )
        for a in rows
    ]


@router.get("/me", response_model=Page[AttendanceRecordOut])
def my_history(
    auth: SelfService,
    db: DbDep,
    start: date | None = None,
    end: date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[AttendanceRecordOut]:
    s, e = _range(start, end, 90)
    rows, total = attendance.list_records(
        db, auth.user, user_id=auth.user.id, start=s, end=e, limit=limit, offset=offset
    )
    return Page(items=[record_out(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/records", response_model=Page[AttendanceRecordOut])
def list_records(
    auth: TeamRead,
    db: DbDep,
    ctx: ContextDep,
    user_id: uuid.UUID | None = None,
    start: date | None = None,
    end: date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[AttendanceRecordOut]:
    if user_id is not None:
        get_visible_user(db, auth.user, user_id, ctx)
    s, e = _range(start, end)
    rows, total = attendance.list_records(db, auth.user, user_id=user_id, start=s, end=e, limit=limit, offset=offset)
    return Page(items=[record_out(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/exceptions", response_model=list[ExceptionOut])
def list_exceptions(
    auth: TeamRead, db: DbDep, start: date | None = None, end: date | None = None
) -> list[ExceptionOut]:
    s, e = _range(start, end, 7)
    return [
        ExceptionOut(
            kind=i.kind,
            user_id=i.user.id,
            user_name=i.user.full_name,
            shift_id=i.shift.id,
            shift_name=i.shift.name,
            shift_date=i.shift_date,
            minutes=i.minutes,
            record_id=i.record_id,
        )
        for i in attendance.list_exceptions(db, auth.user, s, e)
    ]


@router.get("/corrections", response_model=Page[CorrectionOut])
def list_corrections(
    auth: SelfService,
    db: DbDep,
    status: CorrectionStatus | None = None,
    mine: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[CorrectionOut]:
    rows, total = attendance.list_corrections(db, auth.user, status=status, mine=mine, limit=limit, offset=offset)
    return Page(items=[correction_out(c) for c in rows], total=total, limit=limit, offset=offset)


@router.post("/corrections", response_model=CorrectionOut, status_code=201)
def create_correction(body: CorrectionIn, auth: SelfService, db: DbDep, ctx: ContextDep) -> CorrectionOut:
    return correction_out(attendance.create_correction(db, auth.user, body, ctx))


@router.post("/corrections/{correction_id}/review", response_model=CorrectionOut)
def review_correction(
    correction_id: uuid.UUID, body: CorrectionReviewIn, auth: CorrectionReview, db: DbDep, ctx: ContextDep
) -> CorrectionOut:
    return correction_out(attendance.review_correction(db, auth.user, correction_id, body.approve, body.note, ctx))
