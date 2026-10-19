from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select

from app.api.deps import AuthContext, ContextDep, DbDep, require
from app.core.errors import NotFound
from app.core.permissions import Permission
from app.models import ShiftAssignment, User
from app.models.enums import Role
from app.schemas.common import Message, Page
from app.schemas.org import AssignmentOut
from app.schemas.users import PasswordResetIn, UserCreate, UserOut, UserUpdate
from app.services import users as user_service

router = APIRouter(prefix="/users", tags=["users"])

UsersRead = Annotated[AuthContext, Depends(require(Permission.USERS_READ))]
UsersManage = Annotated[AuthContext, Depends(require(Permission.USERS_MANAGE))]
SchedulesRead = Annotated[AuthContext, Depends(require(Permission.SCHEDULES_READ))]


@router.get("", response_model=Page[UserOut])
def list_users(
    auth: UsersRead,
    db: DbDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
    role: Role | None = None,
    active: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[UserOut]:
    rows, total = user_service.list_users(db, auth.user, q=q, role=role, active=active, limit=limit, offset=offset)
    return Page(items=[UserOut.model_validate(u) for u in rows], total=total, limit=limit, offset=offset)


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, auth: UsersManage, db: DbDep, ctx: ContextDep) -> UserOut:
    return UserOut.model_validate(user_service.create_user(db, body, auth.user, ctx))


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: uuid.UUID, auth: UsersRead, db: DbDep, ctx: ContextDep) -> UserOut:
    return UserOut.model_validate(user_service.get_visible_user(db, auth.user, user_id, ctx))


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: uuid.UUID, body: UserUpdate, auth: UsersManage, db: DbDep, ctx: ContextDep) -> UserOut:
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("User not found")
    return UserOut.model_validate(user_service.update_user(db, user, body, auth.user, ctx))


@router.post("/{user_id}/reset-password", response_model=Message)
def reset_password(user_id: uuid.UUID, body: PasswordResetIn, auth: UsersManage, db: DbDep, ctx: ContextDep) -> Message:
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("User not found")
    user_service.reset_password(db, user, body.temporary_password, auth.user, ctx, must_change=body.must_change)
    return Message(
        message="Password set; the user must change it at next sign-in" if body.must_change else "Password set"
    )


@router.get("/{user_id}/assignments", response_model=list[AssignmentOut])
def list_assignments(user_id: uuid.UUID, auth: SchedulesRead, db: DbDep, ctx: ContextDep) -> list[AssignmentOut]:
    user_service.get_visible_user(db, auth.user, user_id, ctx)
    rows = db.scalars(
        select(ShiftAssignment)
        .where(ShiftAssignment.user_id == user_id)
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
