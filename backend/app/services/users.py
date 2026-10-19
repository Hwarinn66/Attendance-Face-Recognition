"""User administration and data-scoping helpers."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ColumnElement, func, or_, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.permissions import scope_for
from app.core.security import hash_password, password_policy_errors
from app.db.base import utcnow
from app.models import User, Workplace
from app.models.enums import AuditCategory, AuditOutcome, AuditSeverity, Role
from app.schemas.users import UserCreate, UserUpdate
from app.services import audit
from app.services import auth as auth_service
from app.services.context import RequestContext


def visibility_clause(actor: User) -> ColumnElement[bool]:
    """SQL condition on ``User`` selecting the users whose attendance ``actor`` may read."""
    scope = scope_for(actor.role)
    if scope.all_users:
        return true()
    if scope.include_direct_reports:
        return or_(User.id == actor.id, User.manager_id == actor.id)
    return User.id == actor.id


def get_visible_user(db: Session, actor: User, user_id: uuid.UUID, ctx: RequestContext) -> User:
    """Load a user the actor may see. Out-of-scope ids produce 404 (no existence oracle) and an audit event."""
    user = db.scalar(select(User).where(User.id == user_id, visibility_clause(actor)))
    if user is None:
        if user_id != actor.id:
            audit.emit(
                category=AuditCategory.SECURITY,
                action="security.out_of_scope_access",
                outcome=AuditOutcome.DENIED,
                severity=AuditSeverity.WARNING,
                actor_id=actor.id,
                target_type="user",
                target_id=user_id,
                ctx=ctx,
            )
        raise NotFound("User not found")
    return user


def _validate_refs(
    db: Session, workplace_id: uuid.UUID | None, manager_id: uuid.UUID | None, self_id: uuid.UUID | None = None
) -> None:
    if workplace_id is not None and db.get(Workplace, workplace_id) is None:
        raise ValidationFailed("workplace does not exist", details={"field": "workplace_id"})
    if manager_id is not None:
        if self_id is not None and manager_id == self_id:
            raise ValidationFailed("a user cannot manage themselves", details={"field": "manager_id"})
        mgr = db.get(User, manager_id)
        if mgr is None or not mgr.is_active:
            raise ValidationFailed("manager does not exist or is inactive", details={"field": "manager_id"})
        if mgr.role == Role.EMPLOYEE:
            raise ValidationFailed("manager must have the manager, hr or admin role", details={"field": "manager_id"})


def create_user(db: Session, data: UserCreate, actor: User, ctx: RequestContext) -> User:
    errors = password_policy_errors(data.temporary_password, email=data.email)
    if errors:
        raise ValidationFailed(
            "Password does not meet the policy", code="weak_password", details={"temporary_password": errors}
        )
    _validate_refs(db, data.workplace_id, data.manager_id)
    user = User(
        email=data.email.lower(),
        username=data.username,
        full_name=data.full_name,
        role=data.role,
        password_hash=hash_password(data.temporary_password),
        must_change_password=data.must_change_password,
        employee_code=data.employee_code or None,
        department=data.department,
        position=data.position,
        workplace_id=data.workplace_id,
        manager_id=data.manager_id,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("A user with this email, ID or employee code already exists") from exc
    audit.add(
        db,
        category=AuditCategory.ADMIN,
        action="user.created",
        actor_id=actor.id,
        target_type="user",
        target_id=user.id,
        ctx=ctx,
        details={"email": user.email, "role": user.role.value},
    )
    db.commit()
    return user


def update_user(db: Session, user: User, data: UserUpdate, actor: User, ctx: RequestContext) -> User:
    changes: dict[str, Any] = {}
    fields = data.model_dump(exclude_unset=True)
    if "role" in fields and fields["role"] is None:
        raise ValidationFailed("role cannot be null")
    if "is_active" in fields and fields["is_active"] is None:
        raise ValidationFailed("is_active cannot be null")
    if user.id == actor.id and (fields.get("role", user.role) != user.role or fields.get("is_active") is False):
        raise ValidationFailed("You cannot change your own role or deactivate yourself", code="self_modification")
    _validate_refs(db, fields.get("workplace_id"), fields.get("manager_id"), self_id=user.id)
    for key, value in fields.items():
        old = getattr(user, key)
        if old != value:
            changes[key] = {
                "from": str(old) if old is not None else None,
                "to": str(value) if value is not None else None,
            }
            setattr(user, key, value)
    if "is_active" in changes:
        if user.is_active:
            user.deactivated_at = None
        else:
            user.deactivated_at = utcnow()
            auth_service.revoke_all_sessions(db, user.id, "account_deactivated")
    if "role" in changes:
        # Privilege changes take effect immediately: force re-authentication.
        auth_service.revoke_all_sessions(db, user.id, "role_changed")
    if changes:
        severity = AuditSeverity.WARNING if {"role", "is_active"} & changes.keys() else AuditSeverity.INFO
        audit.add(
            db,
            category=AuditCategory.ADMIN,
            action="user.updated",
            actor_id=actor.id,
            target_type="user",
            target_id=user.id,
            severity=severity,
            ctx=ctx,
            details={"changes": changes},
        )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("ID or employee code already in use") from exc
    return user


def reset_password(
    db: Session, user: User, temporary_password: str, actor: User, ctx: RequestContext, *, must_change: bool = True
) -> None:
    errors = password_policy_errors(temporary_password, email=user.email)
    if errors:
        raise ValidationFailed(
            "Password does not meet the policy", code="weak_password", details={"temporary_password": errors}
        )
    user.password_hash = hash_password(temporary_password)
    user.must_change_password = must_change
    auth_service.revoke_all_sessions(db, user.id, "password_reset")
    audit.add(
        db,
        category=AuditCategory.ADMIN,
        action="user.password_reset",
        actor_id=actor.id,
        target_type="user",
        target_id=user.id,
        severity=AuditSeverity.WARNING,
        ctx=ctx,
        details={"must_change": must_change},
    )
    db.commit()


def list_users(
    db: Session, actor: User, *, q: str | None, role: Role | None, active: bool | None, limit: int, offset: int
) -> tuple[list[User], int]:
    stmt = select(User).where(visibility_clause(actor))
    if q:
        like = "%" + q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(
            or_(
                func.lower(User.full_name).like(like, escape="\\"),
                User.email.like(like, escape="\\"),
                func.coalesce(User.username, "").like(like, escape="\\"),
                func.lower(func.coalesce(User.employee_code, "")).like(like, escape="\\"),
            )
        )
    if role is not None:
        stmt = stmt.where(User.role == role)
    if active is not None:
        stmt = stmt.where(User.is_active.is_(active))
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = db.scalars(stmt.order_by(User.full_name).limit(limit).offset(offset)).all()
    return list(rows), total
