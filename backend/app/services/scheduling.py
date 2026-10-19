"""Workplaces, shifts, assignments and holidays — plus loaders that build rule-engine inputs."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import date

from sqlalchemy import and_, or_, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.models import Holiday, Shift, ShiftAssignment, User, Workplace
from app.models.enums import AuditCategory
from app.schemas.org import AssignmentIn, HolidayIn, ShiftIn, WorkplaceIn
from app.services import audit
from app.services.attendance.rules import AssignedPolicy, RuleError, ShiftPolicy, validate_policy
from app.services.context import RequestContext


def policy_from_shift(shift: Shift) -> ShiftPolicy:
    return ShiftPolicy(
        shift_id=shift.id,
        workplace_id=shift.workplace_id,
        timezone=shift.workplace.timezone,
        start_time=shift.start_time,
        end_time=shift.end_time,
        check_in_opens_before_min=shift.check_in_opens_before_min,
        check_in_closes_after_min=shift.check_in_closes_after_min,
        grace_min=shift.grace_min,
        check_out_opens_before_min=shift.check_out_opens_before_min,
        check_out_closes_after_min=shift.check_out_closes_after_min,
        early_departure_grace_min=shift.early_departure_grace_min,
        overtime_threshold_min=shift.overtime_threshold_min,
        break_min=shift.break_min,
        workdays_mask=shift.workdays_mask,
    )


def workdays_list(mask: int) -> list[int]:
    return [d for d in range(7) if mask & (1 << d)]


# --- workplaces ---------------------------------------------------------------------------------------


def save_workplace(
    db: Session, data: WorkplaceIn, actor: User, ctx: RequestContext, existing: Workplace | None = None
) -> Workplace:
    wp = existing or Workplace()
    for k, v in data.model_dump().items():
        setattr(wp, k, v)
    if existing is None:
        db.add(wp)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("A workplace with this name already exists") from exc
    audit.add(
        db,
        category=AuditCategory.ADMIN,
        action="workplace.updated" if existing else "workplace.created",
        actor_id=actor.id,
        target_type="workplace",
        target_id=wp.id,
        ctx=ctx,
        details=data.model_dump(mode="json"),
    )
    db.commit()
    return wp


# --- shifts --------------------------------------------------------------------------------------------


def save_shift(db: Session, data: ShiftIn, actor: User, ctx: RequestContext, existing: Shift | None = None) -> Shift:
    workplace = db.get(Workplace, data.workplace_id)
    if workplace is None:
        raise ValidationFailed("workplace does not exist", details={"field": "workplace_id"})
    shift = existing or Shift()
    fields = data.model_dump(exclude={"workdays"})
    for k, v in fields.items():
        setattr(shift, k, v)
    shift.workdays_mask = data.workdays_mask
    shift.workplace = workplace
    try:
        validate_policy(
            ShiftPolicy(
                shift_id=shift.id or uuid.uuid4(),
                workplace_id=workplace.id,
                timezone=workplace.timezone,
                start_time=data.start_time,
                end_time=data.end_time,
                check_in_opens_before_min=data.check_in_opens_before_min,
                check_in_closes_after_min=data.check_in_closes_after_min,
                grace_min=data.grace_min,
                check_out_opens_before_min=data.check_out_opens_before_min,
                check_out_closes_after_min=data.check_out_closes_after_min,
                early_departure_grace_min=data.early_departure_grace_min,
                overtime_threshold_min=data.overtime_threshold_min,
                break_min=data.break_min,
                workdays_mask=data.workdays_mask,
            )
        )
    except RuleError as exc:
        db.rollback()
        raise ValidationFailed(str(exc), code="invalid_shift_policy") from exc
    if existing is None:
        db.add(shift)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("A shift with this name already exists at the workplace") from exc
    audit.add(
        db,
        category=AuditCategory.ADMIN,
        action="shift.updated" if existing else "shift.created",
        actor_id=actor.id,
        target_type="shift",
        target_id=shift.id,
        ctx=ctx,
        details=data.model_dump(mode="json"),
    )
    db.commit()
    return shift


# --- assignments -------------------------------------------------------------------------------------


def create_assignment(db: Session, data: AssignmentIn, actor: User, ctx: RequestContext) -> ShiftAssignment:
    user = db.get(User, data.user_id)
    shift = db.get(Shift, data.shift_id)
    if user is None or shift is None:
        raise ValidationFailed("user or shift does not exist")
    if not shift.is_active:
        raise ValidationFailed("shift is inactive")
    # The same shift may not be assigned twice over overlapping date ranges.
    overlap = db.scalar(
        select(ShiftAssignment.id).where(
            ShiftAssignment.user_id == data.user_id,
            ShiftAssignment.shift_id == data.shift_id,
            or_(ShiftAssignment.effective_to.is_(None), ShiftAssignment.effective_to >= data.effective_from),
            true() if data.effective_to is None else ShiftAssignment.effective_from <= data.effective_to,
        )
    )
    if overlap is not None:
        raise Conflict("This shift is already assigned to the user for an overlapping period")
    a = ShiftAssignment(
        user_id=data.user_id,
        shift_id=data.shift_id,
        effective_from=data.effective_from,
        effective_to=data.effective_to,
        created_by_id=actor.id,
    )
    db.add(a)
    db.flush()
    audit.add(
        db,
        category=AuditCategory.ADMIN,
        action="assignment.created",
        actor_id=actor.id,
        target_type="user",
        target_id=data.user_id,
        ctx=ctx,
        details=data.model_dump(mode="json"),
    )
    db.commit()
    return a


def end_assignment(
    db: Session, assignment_id: uuid.UUID, effective_to: date | None, actor: User, ctx: RequestContext
) -> ShiftAssignment:
    a = db.get(ShiftAssignment, assignment_id)
    if a is None:
        raise NotFound("Assignment not found")
    if effective_to is not None and effective_to < a.effective_from:
        raise ValidationFailed("effective_to must not precede effective_from")
    old = a.effective_to
    a.effective_to = effective_to
    audit.add(
        db,
        category=AuditCategory.ADMIN,
        action="assignment.updated",
        actor_id=actor.id,
        target_type="user",
        target_id=a.user_id,
        ctx=ctx,
        details={"assignment_id": str(a.id), "effective_to": {"from": str(old), "to": str(effective_to)}},
    )
    db.commit()
    return a


def delete_assignment(db: Session, assignment_id: uuid.UUID, actor: User, ctx: RequestContext) -> None:
    a = db.get(ShiftAssignment, assignment_id)
    if a is None:
        raise NotFound("Assignment not found")
    db.delete(a)
    audit.add(
        db,
        category=AuditCategory.ADMIN,
        action="assignment.deleted",
        actor_id=actor.id,
        target_type="user",
        target_id=a.user_id,
        ctx=ctx,
        details={"assignment_id": str(assignment_id)},
    )
    db.commit()


# --- holidays --------------------------------------------------------------------------------------------


def create_holiday(db: Session, data: HolidayIn, actor: User, ctx: RequestContext) -> Holiday:
    if data.workplace_id is not None and db.get(Workplace, data.workplace_id) is None:
        raise ValidationFailed("workplace does not exist")
    h = Holiday(**data.model_dump())
    db.add(h)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("A holiday already exists on this date for this scope") from exc
    audit.add(
        db,
        category=AuditCategory.ADMIN,
        action="holiday.created",
        actor_id=actor.id,
        target_type="holiday",
        target_id=h.id,
        ctx=ctx,
        details=data.model_dump(mode="json"),
    )
    db.commit()
    return h


def delete_holiday(db: Session, holiday_id: uuid.UUID, actor: User, ctx: RequestContext) -> None:
    h = db.get(Holiday, holiday_id)
    if h is None:
        raise NotFound("Holiday not found")
    db.delete(h)
    audit.add(
        db,
        category=AuditCategory.ADMIN,
        action="holiday.deleted",
        actor_id=actor.id,
        target_type="holiday",
        target_id=holiday_id,
        ctx=ctx,
        details={"date": str(h.holiday_date)},
    )
    db.commit()


# --- loaders for the rule engine --------------------------------------------------------------------


def load_assigned_policies(
    db: Session, user_ids: Iterable[uuid.UUID], start: date, end: date
) -> dict[uuid.UUID, list[AssignedPolicy]]:
    """Assignments overlapping [start, end] for the given users, keyed by user id."""
    ids = list(user_ids)
    out: dict[uuid.UUID, list[AssignedPolicy]] = {uid: [] for uid in ids}
    if not ids:
        return out
    rows = db.scalars(
        select(ShiftAssignment)
        .join(Shift)
        .where(
            ShiftAssignment.user_id.in_(ids),
            Shift.is_active.is_(True),
            ShiftAssignment.effective_from <= end,
            or_(ShiftAssignment.effective_to.is_(None), ShiftAssignment.effective_to >= start),
        )
    ).all()
    for a in rows:
        out[a.user_id].append(AssignedPolicy(policy_from_shift(a.shift), a.effective_from, a.effective_to))
    return out


def load_holidays(db: Session, start: date, end: date) -> set[tuple[uuid.UUID | None, date]]:
    rows = db.execute(
        select(Holiday.workplace_id, Holiday.holiday_date).where(
            and_(Holiday.holiday_date >= start, Holiday.holiday_date <= end)
        )
    ).all()
    return {(wp, d) for wp, d in rows}
