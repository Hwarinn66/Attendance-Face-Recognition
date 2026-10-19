"""Transactional attendance operations.

Guarantees for check-in / check-out:
* the server clock is authoritative (no client timestamps are accepted);
* a liveness verification that PASSED for this user, auth session and purpose is consumed exactly once
  (row lock + status transition), and only within ``verification_validity_seconds``;
* ``Idempotency-Key`` makes retries safe: the first request's response is stored and replayed;
* the UNIQUE (user, shift, shift_date) constraint is the final guard against concurrent duplicates;
* check-out locks the open record (SELECT ... FOR UPDATE).
"""

from __future__ import annotations

import enum
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, Conflict, NotFound, PermissionDenied, ValidationFailed
from app.core.permissions import Permission, has_permission
from app.db.base import utcnow
from app.models import (
    AttendanceCorrection,
    AttendanceRecord,
    IdempotencyKey,
    LivenessChallenge,
    Shift,
    User,
    Workplace,
)
from app.models.enums import (
    AuditCategory,
    AuditOutcome,
    AuditSeverity,
    BiometricStatus,
    ChallengePurpose,
    ChallengeStatus,
    CorrectionStatus,
)
from app.schemas.attendance import AttendanceActionIn, CorrectionIn
from app.services import audit
from app.services.attendance.rules import (
    AssignedPolicy,
    ShiftOccurrence,
    check_in_metrics,
    check_out_metrics,
    evaluate_check_in,
    evaluate_check_out,
    haversine_m,
    occurrence_for,
    occurrences_around,
    scheduled_occurrences,
    select_check_in_occurrence,
)
from app.services.context import RequestContext
from app.services.scheduling import load_assigned_policies, load_holidays, policy_from_shift
from app.services.users import visibility_clause

MAX_LOCATION_ACCURACY_M = 150.0


# --- status --------------------------------------------------------------------------------------------


@dataclass(slots=True)
class StatusResult:
    server_time: datetime
    occurrences: list[ShiftOccurrence]
    records: dict[tuple[uuid.UUID, date], AttendanceRecord]
    check_in_target: ShiftOccurrence | None
    check_out_target: AttendanceRecord | None
    check_in_blocker: str | None
    check_out_blocker: str | None
    next_occurrence: ShiftOccurrence | None
    geofence_required: bool


def _user_policies(db: Session, user_id: uuid.UUID, around: date) -> list[AssignedPolicy]:
    return load_assigned_policies(db, [user_id], around - timedelta(days=2), around + timedelta(days=8))[user_id]


def _records_for(
    db: Session, user_id: uuid.UUID, occs: list[ShiftOccurrence]
) -> dict[tuple[uuid.UUID, date], AttendanceRecord]:
    if not occs:
        return {}
    dates = {o.shift_date for o in occs}
    rows = db.scalars(
        select(AttendanceRecord).where(AttendanceRecord.user_id == user_id, AttendanceRecord.shift_date.in_(dates))
    ).all()
    return {(r.shift_id, r.shift_date): r for r in rows}


def _open_record_for_checkout(
    db: Session, user_id: uuid.UUID, now: datetime, *, lock: bool
) -> tuple[AttendanceRecord | None, str | None]:
    stmt = (
        select(AttendanceRecord)
        .where(
            AttendanceRecord.user_id == user_id,
            AttendanceRecord.check_out_at.is_(None),
            AttendanceRecord.check_out_closes_at >= now,
        )
        .order_by(AttendanceRecord.scheduled_end_at)
    )
    if lock:
        stmt = stmt.with_for_update(of=AttendanceRecord)
    candidates = db.scalars(stmt).all()
    if not candidates:
        return None, "no_open_check_in"
    for rec in candidates:
        occ = occurrence_for(policy_from_shift(rec.shift), rec.shift_date)
        if occ.check_out_open(now):
            return rec, None
    return None, "check_out_not_open_yet"


def get_status(db: Session, user: User, now: datetime | None = None) -> StatusResult:
    now = now or utcnow()
    policies = _user_policies(db, user.id, now.date())
    holidays = load_holidays(db, now.date() - timedelta(days=2), now.date() + timedelta(days=8))
    occs = occurrences_around(policies, now, holidays)
    records = _records_for(db, user.id, occs)
    checked = set(records)
    target = select_check_in_occurrence(occs, now, checked)
    blocker: str | None = None
    if user.biometric_status != BiometricStatus.ACTIVE:
        blocker = "biometric_" + user.biometric_status.value
    elif not policies:
        blocker = "no_schedule"
    elif target is None:
        open_now = [o for o in occs if o.check_in_open(now)]
        blocker = "already_checked_in" if open_now else "outside_check_in_window"
    if blocker is not None:
        target = None
    out_rec, out_blocker = _open_record_for_checkout(db, user.id, now, lock=False)
    if out_rec is not None and user.biometric_status != BiometricStatus.ACTIVE:
        out_blocker = "biometric_" + user.biometric_status.value
    upcoming: list[ShiftOccurrence] = []
    for d in range(0, 8):
        day = now.date() + timedelta(days=d)
        upcoming.extend(o for o in scheduled_occurrences(policies, day, holidays) if o.check_in_opens_at > now)
    next_occ = min(upcoming, key=lambda o: o.start_at, default=None)
    geofence = False
    if target is not None:
        wp = db.get(Workplace, target.workplace_id)
        geofence = bool(wp and wp.geofence_enabled)
    elif out_rec is not None:
        wp = db.get(Workplace, out_rec.workplace_id)
        geofence = bool(wp and wp.geofence_enabled)
    return StatusResult(
        server_time=now,
        occurrences=occs,
        records=records,
        check_in_target=target,
        check_out_target=out_rec if out_blocker is None else None,
        check_in_blocker=blocker,
        check_out_blocker=out_blocker,
        next_occurrence=next_occ,
        geofence_required=geofence,
    )


# --- idempotency ---------------------------------------------------------------------------------------


@dataclass(slots=True)
class IdempotentReplay(Exception):
    status_code: int
    body: dict[str, Any]


def _request_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def claim_idempotency_key(
    db: Session, user_id: uuid.UUID, scope: str, key: str, payload: dict[str, Any]
) -> IdempotencyKey:
    if not key or len(key) > 100 or not key.isascii():
        raise ValidationFailed(
            "A valid Idempotency-Key header (<= 100 ASCII chars) is required", code="idempotency_key_required"
        )
    req_hash = _request_hash(payload)
    row = IdempotencyKey(user_id=user_id, scope=scope, key=key, request_hash=req_hash)
    nested = db.begin_nested()
    try:
        db.add(row)
        db.flush()  # blocks while a concurrent transaction holds the same key, then succeeds or conflicts
    except IntegrityError:
        nested.rollback()
        existing = db.scalar(
            select(IdempotencyKey).where(
                IdempotencyKey.user_id == user_id, IdempotencyKey.scope == scope, IdempotencyKey.key == key
            )
        )
        if existing is None:
            raise Conflict("Request is being processed; retry shortly", code="idempotency_in_progress") from None
        if existing.request_hash != req_hash:
            raise ValidationFailed(
                "Idempotency-Key was already used with a different request", code="idempotency_key_reused"
            ) from None
        if existing.response_status is None:
            raise Conflict("Request is being processed; retry shortly", code="idempotency_in_progress") from None
        raise IdempotentReplay(existing.response_status, existing.response_body or {}) from None
    return row


# --- verification consumption ------------------------------------------------------------------------


def consume_verification(
    db: Session,
    user: User,
    auth_session_id: uuid.UUID,
    challenge_id: uuid.UUID,
    purpose: ChallengePurpose,
    validity_seconds: int,
    ctx: RequestContext,
) -> LivenessChallenge:
    ch = db.scalar(select(LivenessChallenge).where(LivenessChallenge.id == challenge_id).with_for_update())
    if ch is None or ch.user_id != user.id:
        raise NotFound("Verification not found")
    problem = None
    if ch.purpose != purpose:
        problem = "verification_wrong_purpose"
    elif ch.auth_session_id != auth_session_id:
        problem = "verification_session_mismatch"
    elif ch.status == ChallengeStatus.CONSUMED:
        problem = "verification_already_used"
    elif ch.status != ChallengeStatus.PASSED:
        problem = "verification_not_passed"
    elif ch.submitted_at is None or utcnow() > ch.submitted_at + timedelta(seconds=validity_seconds):
        problem = "verification_expired"
    if problem:
        audit.emit(
            category=AuditCategory.SECURITY,
            action="security.verification_rejected",
            outcome=AuditOutcome.DENIED,
            severity=AuditSeverity.WARNING,
            actor_id=user.id,
            target_type="challenge",
            target_id=challenge_id,
            ctx=ctx,
            details={"problem": problem},
        )
        raise Conflict("Face verification is missing, expired or already used. Please verify again.", code=problem)
    ch.status = ChallengeStatus.CONSUMED
    ch.consumed_at = utcnow()
    return ch


def _geofence(db: Session, workplace_id: uuid.UUID, body: AttendanceActionIn) -> float | None:
    wp = db.get(Workplace, workplace_id)
    if wp is None or not wp.geofence_enabled:
        return None
    if body.location is None:
        raise ValidationFailed("Location is required at this workplace", code="location_required")
    loc = body.location
    if loc.accuracy_m is not None and loc.accuracy_m > max(MAX_LOCATION_ACCURACY_M, float(wp.geofence_radius_m or 0)):
        raise ValidationFailed("Location is too imprecise; move to an open area and retry", code="location_imprecise")
    assert wp.latitude is not None and wp.longitude is not None and wp.geofence_radius_m is not None
    distance = haversine_m(loc.latitude, loc.longitude, wp.latitude, wp.longitude)
    if distance > wp.geofence_radius_m:
        raise AppError(
            "You are outside the permitted area for this workplace",
            code="outside_geofence",
            status_code=403,
            details={"distance_m": round(distance), "radius_m": wp.geofence_radius_m},
        )
    return distance


# --- check-in / check-out ----------------------------------------------------------------------------

_BLOCKER_MESSAGES = {
    "no_schedule": "You have no assigned shift.",
    "outside_check_in_window": "Check-in is not open right now.",
    "already_checked_in": "You have already checked in for this shift.",
    "no_open_check_in": "You have no open check-in to check out from.",
    "check_out_not_open_yet": "Check-out is not open yet for your current shift.",
}


def _blocked(code: str) -> Conflict:
    if code.startswith("biometric_"):
        return Conflict("Face verification is not active for your account.", code=code)
    return Conflict(_BLOCKER_MESSAGES.get(code, "Action not permitted now"), code=code)


def check_in(
    db: Session,
    user: User,
    auth_session_id: uuid.UUID,
    body: AttendanceActionIn,
    idem_key: str,
    validity_seconds: int,
    ctx: RequestContext,
) -> tuple[int, AttendanceRecord | dict[str, Any]]:
    try:
        idem = claim_idempotency_key(db, user.id, "check_in", idem_key, body.model_dump(mode="json"))
    except IdempotentReplay as replay:
        return replay.status_code, replay.body
    challenge = consume_verification(
        db, user, auth_session_id, body.challenge_id, ChallengePurpose.CHECK_IN, validity_seconds, ctx
    )
    now = utcnow()
    status = get_status(db, user, now)
    occ = status.check_in_target
    if occ is None:
        raise _blocked(status.check_in_blocker or "outside_check_in_window")
    shift = db.get(Shift, occ.shift_id)
    assert shift is not None
    distance = _geofence(db, occ.workplace_id, body)
    ev = evaluate_check_in(policy_from_shift(shift), occ, now)
    rec = AttendanceRecord(
        user_id=user.id,
        shift_id=occ.shift_id,
        workplace_id=occ.workplace_id,
        shift_date=occ.shift_date,
        scheduled_start_at=occ.start_at,
        scheduled_end_at=occ.end_at,
        check_out_closes_at=occ.check_out_closes_at,
        check_in_at=now,
        check_in_status=ev.status,
        late_minutes=ev.late_minutes,
        check_in_verification_id=challenge.verification_attempt_id,
        check_in_latitude=body.location.latitude if body.location else None,
        check_in_longitude=body.location.longitude if body.location else None,
        check_in_accuracy_m=body.location.accuracy_m if body.location else None,
        check_in_distance_m=distance,
        source="face",
    )
    nested = db.begin_nested()
    try:
        db.add(rec)
        db.flush()
    except IntegrityError:
        nested.rollback()
        raise _blocked("already_checked_in") from None
    audit.add(
        db,
        category=AuditCategory.ATTENDANCE,
        action="attendance.check_in",
        actor_id=user.id,
        target_type="attendance",
        target_id=rec.id,
        ctx=ctx,
        details={
            "status": ev.status.value,
            "late_minutes": ev.late_minutes,
            "shift_id": str(occ.shift_id),
            "shift_date": str(occ.shift_date),
        },
    )
    idem.response_status = 201
    idem.response_body = {"record_id": str(rec.id)}
    db.commit()
    return 201, rec


def check_out(
    db: Session,
    user: User,
    auth_session_id: uuid.UUID,
    body: AttendanceActionIn,
    idem_key: str,
    validity_seconds: int,
    ctx: RequestContext,
) -> tuple[int, AttendanceRecord | dict[str, Any]]:
    try:
        idem = claim_idempotency_key(db, user.id, "check_out", idem_key, body.model_dump(mode="json"))
    except IdempotentReplay as replay:
        return replay.status_code, replay.body
    challenge = consume_verification(
        db, user, auth_session_id, body.challenge_id, ChallengePurpose.CHECK_OUT, validity_seconds, ctx
    )
    if user.biometric_status != BiometricStatus.ACTIVE:
        raise _blocked("biometric_" + user.biometric_status.value)
    now = utcnow()
    rec, blocker = _open_record_for_checkout(db, user.id, now, lock=True)
    if rec is None:
        raise _blocked(blocker or "no_open_check_in")
    policy = policy_from_shift(rec.shift)
    occ = occurrence_for(policy, rec.shift_date)
    distance = _geofence(db, rec.workplace_id, body)
    ev = evaluate_check_out(policy, occ, rec.check_in_at, now)
    rec.check_out_at = now
    rec.check_out_status = ev.status
    rec.early_departure_minutes = ev.early_departure_minutes
    rec.overtime_minutes = ev.overtime_minutes
    rec.worked_minutes = ev.worked_minutes
    rec.check_out_verification_id = challenge.verification_attempt_id
    rec.check_out_latitude = body.location.latitude if body.location else None
    rec.check_out_longitude = body.location.longitude if body.location else None
    rec.check_out_distance_m = distance
    audit.add(
        db,
        category=AuditCategory.ATTENDANCE,
        action="attendance.check_out",
        actor_id=user.id,
        target_type="attendance",
        target_id=rec.id,
        ctx=ctx,
        details={
            "status": ev.status.value,
            "early_minutes": ev.early_departure_minutes,
            "overtime_minutes": ev.overtime_minutes,
            "worked_minutes": ev.worked_minutes,
        },
    )
    idem.response_status = 200
    idem.response_body = {"record_id": str(rec.id)}
    db.commit()
    return 200, rec


def ensure_action_possible(db: Session, user: User, purpose: ChallengePurpose) -> None:
    """Pre-flight before issuing an attendance challenge, so users do not verify for nothing."""
    st = get_status(db, user)
    if purpose == ChallengePurpose.CHECK_IN and st.check_in_target is None:
        raise _blocked(st.check_in_blocker or "outside_check_in_window")
    if purpose == ChallengePurpose.CHECK_OUT:
        if user.biometric_status != BiometricStatus.ACTIVE:
            raise _blocked("biometric_" + user.biometric_status.value)
        if st.check_out_target is None:
            raise _blocked(st.check_out_blocker or "no_open_check_in")


# --- queries ------------------------------------------------------------------------------------------------


def is_missing_checkout(rec: AttendanceRecord, now: datetime | None = None) -> bool:
    return rec.check_out_at is None and (now or utcnow()) > rec.check_out_closes_at


def list_records(
    db: Session,
    actor: User,
    *,
    user_id: uuid.UUID | None,
    start: date | None,
    end: date | None,
    limit: int,
    offset: int,
) -> tuple[list[AttendanceRecord], int]:
    stmt = select(AttendanceRecord).join(User, User.id == AttendanceRecord.user_id).where(visibility_clause(actor))
    if user_id is not None:
        stmt = stmt.where(AttendanceRecord.user_id == user_id)
    if start is not None:
        stmt = stmt.where(AttendanceRecord.shift_date >= start)
    if end is not None:
        stmt = stmt.where(AttendanceRecord.shift_date <= end)
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = db.scalars(
        stmt.order_by(AttendanceRecord.shift_date.desc(), AttendanceRecord.check_in_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return list(rows), total


@dataclass(slots=True)
class ExceptionItem:
    kind: str
    user: User
    shift: Shift
    shift_date: date
    minutes: int | None
    record_id: uuid.UUID | None


def expected_occurrences(
    db: Session, users: list[User], start: date, end: date
) -> dict[uuid.UUID, list[ShiftOccurrence]]:
    policies = load_assigned_policies(db, [u.id for u in users], start, end)
    holidays = load_holidays(db, start, end)
    out: dict[uuid.UUID, list[ShiftOccurrence]] = {}
    for u in users:
        occs: list[ShiftOccurrence] = []
        d = start
        while d <= end:
            occs.extend(scheduled_occurrences(policies[u.id], d, holidays))
            d += timedelta(days=1)
        out[u.id] = occs
    return out


def list_exceptions(db: Session, actor: User, start: date, end: date) -> list[ExceptionItem]:
    now = utcnow()
    users = list(db.scalars(select(User).where(visibility_clause(actor), User.is_active.is_(True))).all())
    by_id = {u.id: u for u in users}
    shifts = {s.id: s for s in db.scalars(select(Shift)).all()}
    records = db.scalars(
        select(AttendanceRecord).where(
            AttendanceRecord.user_id.in_(by_id),
            AttendanceRecord.shift_date >= start,
            AttendanceRecord.shift_date <= end,
        )
    ).all()
    have = {(r.user_id, r.shift_id, r.shift_date) for r in records}
    items: list[ExceptionItem] = []
    for r in records:
        u, s = by_id[r.user_id], shifts[r.shift_id]
        if r.late_minutes > 0:
            items.append(ExceptionItem("late", u, s, r.shift_date, r.late_minutes, r.id))
        if r.early_departure_minutes > 0:
            items.append(ExceptionItem("early_departure", u, s, r.shift_date, r.early_departure_minutes, r.id))
        if is_missing_checkout(r, now):
            items.append(ExceptionItem("missing_checkout", u, s, r.shift_date, None, r.id))
    for uid, occs in expected_occurrences(db, users, start, end).items():
        for o in occs:
            if o.check_in_closes_at < now and (uid, o.shift_id, o.shift_date) not in have:
                items.append(ExceptionItem("absent", by_id[uid], shifts[o.shift_id], o.shift_date, None, None))
    return sorted(items, key=lambda i: (i.shift_date, i.user.full_name, i.kind), reverse=True)


# --- corrections ------------------------------------------------------------------------------------------


def _scope_check(db: Session, actor: User, user_id: uuid.UUID) -> User:
    user = db.scalar(select(User).where(User.id == user_id, visibility_clause(actor)))
    if user is None:
        raise NotFound("User not found")
    return user


def create_correction(db: Session, actor: User, data: CorrectionIn, ctx: RequestContext) -> AttendanceCorrection:
    target_id = data.user_id or actor.id
    if target_id != actor.id and not has_permission(actor.role, Permission.CORRECTION_APPLY_DIRECT):
        raise PermissionDenied("You can only request corrections for yourself")
    if data.apply_immediately and not has_permission(actor.role, Permission.CORRECTION_APPLY_DIRECT):
        raise PermissionDenied("You are not allowed to apply corrections directly")
    if data.apply_immediately and target_id == actor.id:
        raise PermissionDenied("You cannot directly correct your own attendance", code="self_approval")
    user = _scope_check(db, actor, target_id)
    shift = db.get(Shift, data.shift_id)
    if shift is None:
        raise ValidationFailed("Shift does not exist")
    occ = occurrence_for(policy_from_shift(shift), data.shift_date)
    now = utcnow()
    lo, hi = occ.check_in_opens_at, occ.check_out_closes_at
    for label, value in (
        ("requested_check_in_at", data.requested_check_in_at),
        ("requested_check_out_at", data.requested_check_out_at),
    ):
        if value is not None and (value < lo or value > hi or value > now):
            raise ValidationFailed(
                f"{label} must fall within the shift's attendance window and not in the future",
                code="correction_out_of_range",
            )
    rec = db.scalar(
        select(AttendanceRecord).where(
            AttendanceRecord.user_id == user.id,
            AttendanceRecord.shift_id == shift.id,
            AttendanceRecord.shift_date == data.shift_date,
        )
    )
    if rec is None and data.requested_check_in_at is None:
        raise ValidationFailed("A check-in time is required when no attendance record exists", code="check_in_required")
    corr = AttendanceCorrection(
        user_id=user.id,
        attendance_record_id=rec.id if rec else None,
        shift_id=shift.id,
        shift_date=data.shift_date,
        requested_check_in_at=data.requested_check_in_at,
        requested_check_out_at=data.requested_check_out_at,
        reason=data.reason,
        status=CorrectionStatus.PENDING,
        requested_by_id=actor.id,
    )
    db.add(corr)
    db.flush()
    audit.add(
        db,
        category=AuditCategory.ATTENDANCE,
        action="attendance.correction.requested",
        actor_id=actor.id,
        target_type="correction",
        target_id=corr.id,
        ctx=ctx,
        details={"user_id": str(user.id), "shift_date": str(data.shift_date)},
    )
    if data.apply_immediately:
        _apply(db, corr, actor, note="applied directly by authorised staff", ctx=ctx)
    db.commit()
    return corr


def review_correction(
    db: Session, actor: User, correction_id: uuid.UUID, approve: bool, note: str | None, ctx: RequestContext
) -> AttendanceCorrection:
    corr = db.scalar(
        select(AttendanceCorrection)
        .where(AttendanceCorrection.id == correction_id)
        .with_for_update(of=AttendanceCorrection)
    )
    if corr is None:
        raise NotFound("Correction not found")
    _scope_check(db, actor, corr.user_id)
    if actor.id in {corr.user_id, corr.requested_by_id}:
        raise PermissionDenied("You cannot review your own correction request", code="self_approval")
    if corr.status != CorrectionStatus.PENDING:
        raise Conflict("Correction has already been reviewed", code="already_reviewed")
    if approve:
        _apply(db, corr, actor, note=note, ctx=ctx)
    else:
        corr.status = CorrectionStatus.REJECTED
        corr.reviewed_by_id = actor.id
        corr.reviewed_at = utcnow()
        corr.review_note = note
        audit.add(
            db,
            category=AuditCategory.ATTENDANCE,
            action="attendance.correction.rejected",
            actor_id=actor.id,
            target_type="correction",
            target_id=corr.id,
            ctx=ctx,
        )
    db.commit()
    return corr


def _jsonable(v: object) -> object:
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, enum.Enum):
        return v.value
    return v


def _snapshot(rec: AttendanceRecord) -> dict[str, Any]:
    return {
        k: _jsonable(v)
        for k, v in {
            "check_in_at": rec.check_in_at,
            "check_out_at": rec.check_out_at,
            "check_in_status": rec.check_in_status,
            "check_out_status": rec.check_out_status,
            "late_minutes": rec.late_minutes,
            "early_departure_minutes": rec.early_departure_minutes,
            "overtime_minutes": rec.overtime_minutes,
            "worked_minutes": rec.worked_minutes,
        }.items()
    }


def _apply(db: Session, corr: AttendanceCorrection, actor: User, note: str | None, ctx: RequestContext) -> None:
    shift = db.get(Shift, corr.shift_id)
    assert shift is not None
    policy = policy_from_shift(shift)
    occ = occurrence_for(policy, corr.shift_date)
    rec = db.scalar(
        select(AttendanceRecord)
        .where(
            AttendanceRecord.user_id == corr.user_id,
            AttendanceRecord.shift_id == corr.shift_id,
            AttendanceRecord.shift_date == corr.shift_date,
        )
        .with_for_update(of=AttendanceRecord)
    )
    previous = _snapshot(rec) if rec else None
    if rec is None:
        if corr.requested_check_in_at is None:
            raise ValidationFailed("A check-in time is required to create a record")
        ci = check_in_metrics(policy, occ, corr.requested_check_in_at)
        rec = AttendanceRecord(
            user_id=corr.user_id,
            shift_id=shift.id,
            workplace_id=shift.workplace_id,
            shift_date=corr.shift_date,
            scheduled_start_at=occ.start_at,
            scheduled_end_at=occ.end_at,
            check_out_closes_at=occ.check_out_closes_at,
            check_in_at=corr.requested_check_in_at,
            check_in_status=ci.status,
            late_minutes=ci.late_minutes,
            source="correction",
        )
        db.add(rec)
    elif corr.requested_check_in_at is not None:
        ci = check_in_metrics(policy, occ, corr.requested_check_in_at)
        rec.check_in_at = corr.requested_check_in_at
        rec.check_in_status = ci.status
        rec.late_minutes = ci.late_minutes
    if corr.requested_check_out_at is not None:
        if corr.requested_check_out_at < rec.check_in_at:
            raise ValidationFailed("Check-out must not precede check-in", code="correction_order")
        co = check_out_metrics(policy, occ, rec.check_in_at, corr.requested_check_out_at)
        rec.check_out_at = corr.requested_check_out_at
        rec.check_out_status = co.status
        rec.early_departure_minutes = co.early_departure_minutes
        rec.overtime_minutes = co.overtime_minutes
        rec.worked_minutes = co.worked_minutes
    elif rec.check_out_at is not None:
        co = check_out_metrics(policy, occ, rec.check_in_at, rec.check_out_at)
        rec.worked_minutes = co.worked_minutes
    rec.is_corrected = True
    db.flush()
    corr.attendance_record_id = rec.id
    corr.previous_values = previous
    corr.status = CorrectionStatus.APPROVED
    corr.reviewed_by_id = actor.id
    corr.reviewed_at = utcnow()
    corr.review_note = note
    audit.add(
        db,
        category=AuditCategory.ATTENDANCE,
        action="attendance.correction.applied",
        actor_id=actor.id,
        target_type="attendance",
        target_id=rec.id,
        severity=AuditSeverity.WARNING,
        ctx=ctx,
        details={"correction_id": str(corr.id), "previous": previous, "new": _snapshot(rec)},
    )


def list_corrections(
    db: Session, actor: User, *, status: CorrectionStatus | None, mine: bool, limit: int, offset: int
) -> tuple[list[AttendanceCorrection], int]:
    stmt = select(AttendanceCorrection).join(User, User.id == AttendanceCorrection.user_id)
    if mine or not has_permission(actor.role, Permission.CORRECTION_REVIEW):
        stmt = stmt.where(
            or_(AttendanceCorrection.user_id == actor.id, AttendanceCorrection.requested_by_id == actor.id)
        )
    else:
        stmt = stmt.where(visibility_clause(actor))
    if status is not None:
        stmt = stmt.where(AttendanceCorrection.status == status)
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = db.scalars(stmt.order_by(AttendanceCorrection.created_at.desc()).limit(limit).offset(offset)).all()
    return list(rows), total
