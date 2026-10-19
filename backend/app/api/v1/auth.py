from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status
from sqlalchemy import select

from app.api.deps import AuthDep, ContextDep, DbDep, SettingsDep, cookie_name
from app.core.config import Settings
from app.core.errors import NotFound, PermissionDenied
from app.core.permissions import ROLE_PERMISSIONS
from app.db.base import utcnow
from app.models import AuthSession, User
from app.models.enums import AuditCategory, AuditOutcome, Role
from app.schemas.common import Message
from app.schemas.users import ChangePasswordIn, LoginIn, LoginOut, MeOut, SessionOut, TokenOut
from app.services import audit
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


def me_out(user: User) -> MeOut:
    data = {f: getattr(user, f) for f in MeOut.model_fields if f != "permissions"}
    data["permissions"] = sorted(p.value for p in ROLE_PERMISSIONS[user.role])
    return MeOut.model_validate(data)


def _set_auth_cookies(response: Response, settings: Settings, token: str, csrf: str) -> None:
    max_age = settings.session_absolute_ttl_minutes * 60
    response.set_cookie(
        cookie_name(settings, settings.session_cookie_name),
        token,
        max_age=max_age,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite,
    )
    # Readable by same-origin JS so the SPA can echo it in the X-CSRF-Token header (double submit).
    response.set_cookie(
        cookie_name(settings, settings.csrf_cookie_name),
        csrf,
        max_age=max_age,
        path="/",
        secure=settings.cookie_secure,
        httponly=False,
        samesite=settings.cookie_samesite,
    )


def _clear_auth_cookies(response: Response, settings: Settings) -> None:
    for base in (settings.session_cookie_name, settings.csrf_cookie_name):
        response.delete_cookie(
            cookie_name(settings, base), path="/", secure=settings.cookie_secure, samesite=settings.cookie_samesite
        )


@router.post("/login", response_model=LoginOut)
def login(body: LoginIn, response: Response, db: DbDep, settings: SettingsDep, ctx: ContextDep) -> LoginOut:
    user, issued = auth_service.login(db, settings, body.login, body.password, ctx)
    if user.role != Role.ADMIN:
        # The web is the administration console; employees sign in through the mobile app only.
        auth_service.revoke_session(db, issued.session, "web_admin_only")
        audit.add(
            db,
            category=AuditCategory.AUTH,
            action="auth.login.web_denied",
            outcome=AuditOutcome.DENIED,
            actor_id=user.id,
            ctx=ctx,
            details={"reason": "employee accounts use the mobile app"},
        )
        db.commit()
        raise PermissionDenied(
            "This account can only be used in the mobile app. The web is for administrators.", code="use_mobile_app"
        )
    _set_auth_cookies(response, settings, issued.token, issued.csrf_token)
    return LoginOut(user=me_out(user), csrf_token=issued.csrf_token)


@router.post("/token", response_model=TokenOut)
def token(body: LoginIn, db: DbDep, settings: SettingsDep, ctx: ContextDep) -> TokenOut:
    """Sign-in for native apps. Same checks, rate limits and audit as /login; send the token back as
    ``Authorization: Bearer <access_token>``. Never set as a cookie, so browsers cannot use it ambiently."""
    user, issued = auth_service.login(db, settings, body.login, body.password, ctx)
    return TokenOut(
        access_token=issued.token,
        expires_in=settings.session_absolute_ttl_minutes * 60,
        user=me_out(user),
    )


@router.post("/logout", response_model=Message)
def logout(auth: AuthDep, response: Response, db: DbDep, settings: SettingsDep, ctx: ContextDep) -> Message:
    auth_service.revoke_session(db, auth.session, "logout")
    audit.add(
        db,
        category=AuditCategory.AUTH,
        action="auth.logout",
        actor_id=auth.user.id,
        ctx=ctx,
        target_type="session",
        target_id=auth.session.id,
    )
    db.commit()
    _clear_auth_cookies(response, settings)
    return Message(message="Signed out")


@router.get("/me", response_model=MeOut)
def me(auth: AuthDep) -> MeOut:
    return me_out(auth.user)


@router.post("/change-password", response_model=Message)
def change_password(body: ChangePasswordIn, auth: AuthDep, db: DbDep, ctx: ContextDep) -> Message:
    auth_service.change_password(db, auth.user, auth.session, body.current_password, body.new_password, ctx)
    return Message(message="Password changed; other sessions were signed out")


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(auth: AuthDep, db: DbDep) -> list[SessionOut]:
    rows = db.scalars(
        select(AuthSession)
        .where(AuthSession.user_id == auth.user.id, AuthSession.revoked_at.is_(None), AuthSession.expires_at > utcnow())
        .order_by(AuthSession.last_seen_at.desc())
    ).all()
    return [
        SessionOut.model_validate({**SessionOut.model_validate(s).model_dump(), "current": s.id == auth.session.id})
        for s in rows
    ]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_session(session_id: uuid.UUID, auth: AuthDep, db: DbDep, ctx: ContextDep) -> Response:
    s = db.get(AuthSession, session_id)
    if s is None or s.user_id != auth.user.id:
        raise NotFound("Session not found")
    auth_service.revoke_session(db, s, "user_revoked")
    audit.add(
        db,
        category=AuditCategory.AUTH,
        action="auth.session.revoked",
        actor_id=auth.user.id,
        ctx=ctx,
        target_type="session",
        target_id=session_id,
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/logout-all", response_model=Message)
def logout_all(auth: AuthDep, response: Response, db: DbDep, settings: SettingsDep, ctx: ContextDep) -> Message:
    n = auth_service.revoke_all_sessions(db, auth.user.id, "logout_all")
    audit.add(
        db,
        category=AuditCategory.AUTH,
        action="auth.logout_all",
        actor_id=auth.user.id,
        ctx=ctx,
        details={"revoked": n},
    )
    db.commit()
    _clear_auth_cookies(response, settings)
    return Message(message=f"Signed out of {n} session(s)")
