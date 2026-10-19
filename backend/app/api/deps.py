"""FastAPI dependencies: settings, DB session, request context, authentication, CSRF, RBAC."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import NotAuthenticated, PermissionDenied
from app.core.permissions import Permission, has_permission
from app.core.security import constant_time_equals, token_digest
from app.db.session import get_db
from app.models import AuthSession, User
from app.models.enums import AuditCategory, AuditOutcome, AuditSeverity
from app.services import audit
from app.services import auth as auth_service
from app.services.context import RequestContext

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

SettingsDep = Annotated[Settings, Depends(get_settings)]
DbDep = Annotated[Session, Depends(get_db)]


def cookie_name(settings: Settings, base: str) -> str:
    # The __Host- prefix makes browsers enforce Secure, Path=/ and no Domain attribute.
    return f"__Host-{base}" if settings.cookie_secure else base


def get_request_context(request: Request, settings: SettingsDep) -> RequestContext:
    return RequestContext.from_request(request, settings)


ContextDep = Annotated[RequestContext, Depends(get_request_context)]


@dataclass(frozen=True, slots=True)
class AuthContext:
    user: User
    session: AuthSession
    # True when the request came from the mobile app (bearer token), False for the web (cookie session).
    via_app: bool = False


def _check_origin(request: Request, settings: Settings) -> bool:
    origin = request.headers.get("origin")
    if origin is None:
        # Non-browser clients omit Origin; browsers always send Sec-Fetch-Site on fetch().
        return request.headers.get("sec-fetch-site", "same-origin") in {"same-origin", "none"}
    origin = origin.rstrip("/")
    if origin in settings.allowed_origins:
        return True
    host = request.headers.get("host")
    return host is not None and origin in {f"http://{host}", f"https://{host}"}


def bearer_token(request: Request) -> str | None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


def get_auth(request: Request, db: DbDep, settings: SettingsDep, ctx: ContextDep) -> AuthContext:
    bearer = bearer_token(request)
    if bearer is not None:
        # Native clients (the mobile app) send the session token explicitly. A header is not an ambient
        # credential that a browser attaches to cross-site requests, so CSRF/Origin checks do not apply;
        # cookies are ignored entirely on this path.
        session = auth_service.resolve_session(db, settings, bearer)
        request.state.user_id = session.user_id
        return AuthContext(user=session.user, session=session, via_app=True)
    token = request.cookies.get(cookie_name(settings, settings.session_cookie_name))
    session = auth_service.resolve_session(db, settings, token)
    if request.method not in SAFE_METHODS:
        csrf = request.headers.get(settings.csrf_header_name, "")
        csrf_ok = bool(csrf) and len(csrf) <= 200 and constant_time_equals(token_digest(csrf), session.csrf_hash)
        origin_ok = _check_origin(request, settings)
        if not (csrf_ok and origin_ok):
            audit.emit(
                category=AuditCategory.SECURITY,
                action="security.csrf_rejected",
                outcome=AuditOutcome.DENIED,
                severity=AuditSeverity.WARNING,
                actor_id=session.user_id,
                ctx=ctx,
                details={"path": request.url.path, "csrf_ok": csrf_ok, "origin_ok": origin_ok},
            )
            raise PermissionDenied("CSRF validation failed", code="csrf_failed")
    request.state.user_id = session.user_id
    return AuthContext(user=session.user, session=session)


AuthDep = Annotated[AuthContext, Depends(get_auth)]


def require_app(auth: AuthDep) -> AuthContext:
    """Attendance and face enrollment happen only in the mobile app; the web is for administration."""
    if not auth.via_app:
        raise PermissionDenied(
            "Attendance and face enrollment are only available in the mobile app", code="use_mobile_app"
        )
    return auth


def require(permission: Permission) -> Callable[..., AuthContext]:
    def _dependency(auth: AuthDep, request: Request, ctx: ContextDep) -> AuthContext:
        if not has_permission(auth.user.role, permission):
            audit.emit(
                category=AuditCategory.SECURITY,
                action="security.permission_denied",
                outcome=AuditOutcome.DENIED,
                severity=AuditSeverity.WARNING,
                actor_id=auth.user.id,
                ctx=ctx,
                details={"permission": permission.value, "path": request.url.path, "role": auth.user.role.value},
            )
            raise PermissionDenied("You do not have permission to perform this action")
        if auth.user.must_change_password and permission != Permission.SELF_SERVICE:
            raise PermissionDenied("Password change required", code="password_change_required")
        return auth

    return _dependency


def require_no_pending_password_change(auth: AuthDep) -> AuthContext:
    if auth.user.must_change_password:
        raise PermissionDenied("Password change required", code="password_change_required")
    return auth


__all__ = [
    "AuthContext",
    "AuthDep",
    "ContextDep",
    "DbDep",
    "NotAuthenticated",
    "SettingsDep",
    "bearer_token",
    "cookie_name",
    "require",
    "require_app",
    "require_no_pending_password_change",
]
