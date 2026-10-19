"""Authentication: login with DB-backed rate limiting, opaque server-side sessions, revocation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import NotAuthenticated, RateLimited, ValidationFailed
from app.core.security import (
    hash_password,
    new_token,
    password_needs_rehash,
    password_policy_errors,
    token_digest,
    verify_password,
)
from app.db.base import utcnow
from app.models import AuthSession, LoginAttempt, User
from app.models.enums import AuditCategory, AuditOutcome, AuditSeverity
from app.services import audit
from app.services.context import RequestContext

GENERIC_LOGIN_ERROR = "Invalid sign-in ID or password"


@dataclass(frozen=True, slots=True)
class IssuedSession:
    session: AuthSession
    token: str
    csrf_token: str


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _recent_failures(db: Session, *, since: datetime, email: str | None = None, ip: str | None = None) -> int:
    stmt = (
        select(func.count())
        .select_from(LoginAttempt)
        .where(LoginAttempt.created_at >= since, LoginAttempt.succeeded.is_(False))
    )
    if email is not None:
        stmt = stmt.where(LoginAttempt.email == email)
    if ip is not None:
        stmt = stmt.where(LoginAttempt.ip_address == ip)
    return int(db.scalar(stmt) or 0)


def _record_attempt(db: Session, email: str, ctx: RequestContext, succeeded: bool) -> None:
    db.add(LoginAttempt(email=email[:320], ip_address=ctx.ip_address, succeeded=succeeded))


def login(
    db: Session, settings: Settings, identifier: str, password: str, ctx: RequestContext
) -> tuple[User, IssuedSession]:
    identifier = normalize_email(identifier)
    now = utcnow()
    window_start = now - timedelta(minutes=settings.login_window_minutes)

    email_failures = _recent_failures(db, since=window_start, email=identifier)
    ip_failures = _recent_failures(db, since=window_start, ip=ctx.ip_address) if ctx.ip_address else 0
    if email_failures >= settings.login_max_failures or ip_failures >= settings.login_ip_max_failures:
        audit.emit(
            category=AuditCategory.SECURITY,
            action="auth.login.rate_limited",
            outcome=AuditOutcome.DENIED,
            severity=AuditSeverity.WARNING,
            ctx=ctx,
            details={"login": identifier, "email_failures": email_failures, "ip_failures": ip_failures},
        )
        raise RateLimited(
            "Too many failed sign-in attempts. Try again later.",
            headers={"Retry-After": str(settings.login_window_minutes * 60)},
        )

    # The identifier is an email address if it contains "@", otherwise a username (which cannot contain "@").
    by_identifier = User.email == identifier if "@" in identifier else User.username == identifier
    user = db.scalar(select(User).where(by_identifier))
    ok = verify_password(user.password_hash if user else None, password)
    if not ok or user is None or not user.is_active:
        _record_attempt(db, identifier, ctx, succeeded=False)
        audit.add(
            db,
            category=AuditCategory.AUTH,
            action="auth.login.failure",
            outcome=AuditOutcome.FAILURE,
            severity=AuditSeverity.WARNING if email_failures + 1 >= settings.login_max_failures else AuditSeverity.INFO,
            actor_id=user.id if user else None,
            ctx=ctx,
            details={
                "login": identifier,
                "reason": "inactive" if (ok and user and not user.is_active) else "credentials",
            },
        )
        db.commit()
        raise NotAuthenticated(GENERIC_LOGIN_ERROR, code="invalid_credentials")

    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = now
    _record_attempt(db, identifier, ctx, succeeded=True)
    issued = create_session(db, settings, user, ctx)
    audit.add(
        db,
        category=AuditCategory.AUTH,
        action="auth.login.success",
        actor_id=user.id,
        ctx=ctx,
        target_type="session",
        target_id=issued.session.id,
    )
    db.commit()
    return user, issued


def create_session(db: Session, settings: Settings, user: User, ctx: RequestContext) -> IssuedSession:
    token = new_token()
    csrf_token = new_token()
    now = utcnow()
    session = AuthSession(
        user_id=user.id,
        token_hash=token_digest(token),
        csrf_hash=token_digest(csrf_token),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(minutes=settings.session_absolute_ttl_minutes),
        ip_address=ctx.ip_address,
        user_agent=ctx.user_agent,
    )
    db.add(session)
    db.flush()
    return IssuedSession(session=session, token=token, csrf_token=csrf_token)


def resolve_session(db: Session, settings: Settings, token: str | None) -> AuthSession:
    """Return the live session for ``token`` or raise NotAuthenticated. Touches ``last_seen_at``."""
    if not token or len(token) > 200:
        raise NotAuthenticated("Authentication required")
    session = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_digest(token)))
    now = utcnow()
    if session is None or session.revoked_at is not None:
        raise NotAuthenticated("Authentication required")
    if session.expires_at <= now:
        raise NotAuthenticated("Session expired", code="session_expired")
    if session.last_seen_at + timedelta(minutes=settings.session_idle_timeout_minutes) <= now:
        session.revoked_at = now
        session.revoked_reason = "idle_timeout"
        db.commit()
        raise NotAuthenticated("Session expired", code="session_expired")
    if not session.user.is_active:
        raise NotAuthenticated("Authentication required")
    # Throttle writes: only bump last_seen_at once a minute.
    if now - session.last_seen_at > timedelta(seconds=60):
        session.last_seen_at = now
        db.commit()
    return session


def revoke_session(db: Session, session: AuthSession, reason: str) -> None:
    if session.revoked_at is None:
        session.revoked_at = utcnow()
        session.revoked_reason = reason


def revoke_all_sessions(db: Session, user_id: uuid.UUID, reason: str, *, except_id: uuid.UUID | None = None) -> int:
    stmt = (
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utcnow(), revoked_reason=reason)
    )
    if except_id is not None:
        stmt = stmt.where(AuthSession.id != except_id)
    result = db.execute(stmt)
    return int(getattr(result, "rowcount", 0) or 0)


def change_password(
    db: Session, user: User, current_session: AuthSession, current: str, new: str, ctx: RequestContext
) -> None:
    if not verify_password(user.password_hash, current):
        audit.add(
            db,
            category=AuditCategory.AUTH,
            action="auth.password_change.failure",
            outcome=AuditOutcome.FAILURE,
            actor_id=user.id,
            ctx=ctx,
        )
        db.commit()
        raise ValidationFailed("Current password is incorrect", code="invalid_current_password")
    errors = password_policy_errors(new, email=user.email)
    if errors:
        raise ValidationFailed("Password does not meet the policy", code="weak_password", details={"password": errors})
    if verify_password(user.password_hash, new):
        raise ValidationFailed("New password must differ from the current one", code="password_reused")
    user.password_hash = hash_password(new)
    user.must_change_password = False
    revoked = revoke_all_sessions(db, user.id, "password_changed", except_id=current_session.id)
    audit.add(
        db,
        category=AuditCategory.AUTH,
        action="auth.password_change.success",
        actor_id=user.id,
        ctx=ctx,
        details={"other_sessions_revoked": revoked},
    )
    db.commit()
