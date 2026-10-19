from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select

from app.api.deps import AuthContext, AuthDep, ContextDep, DbDep, require
from app.core.errors import NotFound
from app.core.permissions import Permission
from app.models import Holiday, Shift, Workplace
from app.schemas.org import (
    AssignmentIn,
    AssignmentOut,
    AssignmentUpdate,
    HolidayIn,
    HolidayOut,
    ShiftIn,
    ShiftOut,
    WorkplaceIn,
    WorkplaceOut,
)
from app.services import scheduling

router = APIRouter(tags=["organisation"])

SchedulesRead = Annotated[AuthContext, Depends(require(Permission.SCHEDULES_READ))]
SchedulesManage = Annotated[AuthContext, Depends(require(Permission.SCHEDULES_MANAGE))]
HolidaysManage = Annotated[AuthContext, Depends(require(Permission.HOLIDAYS_MANAGE))]


def shift_out(s: Shift) -> ShiftOut:
    return ShiftOut(
        id=s.id,
        workplace_id=s.workplace_id,
        name=s.name,
        start_time=s.start_time,
        end_time=s.end_time,
        check_in_opens_before_min=s.check_in_opens_before_min,
        check_in_closes_after_min=s.check_in_closes_after_min,
        grace_min=s.grace_min,
        check_out_opens_before_min=s.check_out_opens_before_min,
        check_out_closes_after_min=s.check_out_closes_after_min,
        early_departure_grace_min=s.early_departure_grace_min,
        overtime_threshold_min=s.overtime_threshold_min,
        break_min=s.break_min,
        workdays=scheduling.workdays_list(s.workdays_mask),
        is_active=s.is_active,
        is_overnight=s.end_time <= s.start_time,
    )


# --- workplaces ---------------------------------------------------------------------------------------
@router.get("/workplaces", response_model=list[WorkplaceOut])
def list_workplaces(auth: AuthDep, db: DbDep) -> list[WorkplaceOut]:
    return [WorkplaceOut.model_validate(w) for w in db.scalars(select(Workplace).order_by(Workplace.name))]


@router.post("/workplaces", response_model=WorkplaceOut, status_code=status.HTTP_201_CREATED)
def create_workplace(body: WorkplaceIn, auth: SchedulesManage, db: DbDep, ctx: ContextDep) -> WorkplaceOut:
    return WorkplaceOut.model_validate(scheduling.save_workplace(db, body, auth.user, ctx))


@router.put("/workplaces/{workplace_id}", response_model=WorkplaceOut)
def update_workplace(
    workplace_id: uuid.UUID, body: WorkplaceIn, auth: SchedulesManage, db: DbDep, ctx: ContextDep
) -> WorkplaceOut:
    wp = db.get(Workplace, workplace_id)
    if wp is None:
        raise NotFound("Workplace not found")
    return WorkplaceOut.model_validate(scheduling.save_workplace(db, body, auth.user, ctx, existing=wp))


# --- shifts --------------------------------------------------------------------------------------------
@router.get("/shifts", response_model=list[ShiftOut])
def list_shifts(auth: SchedulesRead, db: DbDep) -> list[ShiftOut]:
    return [shift_out(s) for s in db.scalars(select(Shift).order_by(Shift.name))]


@router.post("/shifts", response_model=ShiftOut, status_code=status.HTTP_201_CREATED)
def create_shift(body: ShiftIn, auth: SchedulesManage, db: DbDep, ctx: ContextDep) -> ShiftOut:
    return shift_out(scheduling.save_shift(db, body, auth.user, ctx))


@router.put("/shifts/{shift_id}", response_model=ShiftOut)
def update_shift(shift_id: uuid.UUID, body: ShiftIn, auth: SchedulesManage, db: DbDep, ctx: ContextDep) -> ShiftOut:
    shift = db.get(Shift, shift_id)
    if shift is None:
        raise NotFound("Shift not found")
    return shift_out(scheduling.save_shift(db, body, auth.user, ctx, existing=shift))


# --- assignments ---------------------------------------------------------------------------------------
@router.post("/assignments", response_model=AssignmentOut, status_code=status.HTTP_201_CREATED)
def create_assignment(body: AssignmentIn, auth: SchedulesManage, db: DbDep, ctx: ContextDep) -> AssignmentOut:
    a = scheduling.create_assignment(db, body, auth.user, ctx)
    return AssignmentOut(
        id=a.id,
        user_id=a.user_id,
        shift_id=a.shift_id,
        shift_name=a.shift.name,
        effective_from=a.effective_from,
        effective_to=a.effective_to,
    )


@router.patch("/assignments/{assignment_id}", response_model=AssignmentOut)
def update_assignment(
    assignment_id: uuid.UUID, body: AssignmentUpdate, auth: SchedulesManage, db: DbDep, ctx: ContextDep
) -> AssignmentOut:
    a = scheduling.end_assignment(db, assignment_id, body.effective_to, auth.user, ctx)
    return AssignmentOut(
        id=a.id,
        user_id=a.user_id,
        shift_id=a.shift_id,
        shift_name=a.shift.name,
        effective_from=a.effective_from,
        effective_to=a.effective_to,
    )


@router.delete("/assignments/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_assignment(assignment_id: uuid.UUID, auth: SchedulesManage, db: DbDep, ctx: ContextDep) -> Response:
    scheduling.delete_assignment(db, assignment_id, auth.user, ctx)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- holidays ----------------------------------------------------------------------------------------------
@router.get("/holidays", response_model=list[HolidayOut])
def list_holidays(
    auth: AuthDep,
    db: DbDep,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
) -> list[HolidayOut]:
    stmt = select(Holiday).order_by(Holiday.holiday_date)
    if start:
        stmt = stmt.where(Holiday.holiday_date >= start)
    if end:
        stmt = stmt.where(Holiday.holiday_date <= end)
    return [HolidayOut.model_validate(h) for h in db.scalars(stmt)]


@router.post("/holidays", response_model=HolidayOut, status_code=status.HTTP_201_CREATED)
def create_holiday(body: HolidayIn, auth: HolidaysManage, db: DbDep, ctx: ContextDep) -> HolidayOut:
    return HolidayOut.model_validate(scheduling.create_holiday(db, body, auth.user, ctx))


@router.delete("/holidays/{holiday_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_holiday(holiday_id: uuid.UUID, auth: HolidaysManage, db: DbDep, ctx: ContextDep) -> Response:
    scheduling.delete_holiday(db, holiday_id, auth.user, ctx)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
