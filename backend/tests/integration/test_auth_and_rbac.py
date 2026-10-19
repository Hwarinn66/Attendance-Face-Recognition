from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models import AuditEvent, AuthSession
from app.models.enums import Role
from tests.integration.helpers import API, PASSWORD, login, make_user, web_login


def test_health(client_factory) -> None:  # type: ignore[no-untyped-def]
    c = client_factory()
    assert c.get(f"{API}/health/live").json() == {"status": "ok"}
    body = c.get(f"{API}/health/ready").json()
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["test_fixture"] is True


def test_login_me_logout(db, client_factory) -> None:  # type: ignore[no-untyped-def]
    make_user(db, "admin@example.com", Role.ADMIN)
    c = client_factory()
    user = web_login(c, "ADMIN@example.com")
    assert user["email"] == "admin@example.com" and "self_service" in user["permissions"]
    assert "absen_session" in c.cookies
    assert c.get(f"{API}/auth/me").status_code == 200
    assert c.post(f"{API}/auth/logout").status_code == 200
    assert c.get(f"{API}/auth/me").status_code == 401


def test_session_cookie_flags(db, client_factory) -> None:  # type: ignore[no-untyped-def]
    make_user(db, "admin@example.com", Role.ADMIN)
    c = client_factory()
    r = c.post(f"{API}/auth/login", json={"email": "admin@example.com", "password": PASSWORD})
    cookies = r.headers.get_list("set-cookie")
    session_cookie = next(h for h in cookies if h.startswith("absen_session="))
    assert "HttpOnly" in session_cookie and "SameSite=lax" in session_cookie
    csrf_cookie = next(h for h in cookies if h.startswith("absen_csrf="))
    assert "HttpOnly" not in csrf_cookie


def test_wrong_password_is_generic_and_audited(db, client_factory) -> None:  # type: ignore[no-untyped-def]
    make_user(db, "emp@example.com")
    c = client_factory()
    r1 = c.post(f"{API}/auth/login", json={"email": "emp@example.com", "password": "nope"})
    r2 = c.post(f"{API}/auth/login", json={"email": "ghost@example.com", "password": "nope"})
    assert r1.status_code == r2.status_code == 401
    assert r1.json()["error"]["message"] == r2.json()["error"]["message"]
    actions = db.scalars(select(AuditEvent.action)).all()
    assert actions.count("auth.login.failure") == 2


def test_login_rate_limit(db, client_factory, settings) -> None:  # type: ignore[no-untyped-def]
    make_user(db, "emp@example.com")
    c = client_factory()
    for _ in range(settings.login_max_failures):
        assert c.post(f"{API}/auth/login", json={"email": "emp@example.com", "password": "bad"}).status_code == 401
    r = c.post(f"{API}/auth/login", json={"email": "emp@example.com", "password": PASSWORD})
    assert r.status_code == 429 and "Retry-After" in r.headers
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "auth.login.rate_limited")) is not None


def test_inactive_user_cannot_login(db, client_factory) -> None:  # type: ignore[no-untyped-def]
    make_user(db, "gone@example.com", is_active=False)
    r = client_factory().post(f"{API}/auth/login", json={"email": "gone@example.com", "password": PASSWORD})
    assert r.status_code == 401


def test_csrf_required_for_unsafe_methods(db, client_factory) -> None:  # type: ignore[no-untyped-def]
    make_user(db, "admin@example.com", Role.ADMIN)
    c = client_factory()
    web_login(c, "admin@example.com")
    token = c.headers.pop("X-CSRF-Token")
    assert c.post(f"{API}/auth/logout").status_code == 403
    assert c.post(f"{API}/auth/logout", headers={"X-CSRF-Token": "forged"}).status_code == 403
    r = c.post(f"{API}/auth/logout", headers={"X-CSRF-Token": token, "Origin": "https://evil.example"})
    assert r.status_code == 403
    assert c.post(f"{API}/auth/logout", headers={"X-CSRF-Token": token}).status_code == 200
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "security.csrf_rejected")) is not None


def test_expired_and_idle_sessions(db, client_factory, clock, settings) -> None:  # type: ignore[no-untyped-def]
    make_user(db, "emp@example.com")
    c = client_factory()
    login(c, "emp@example.com")
    clock.advance(minutes=settings.session_idle_timeout_minutes + 1)
    r = c.get(f"{API}/auth/me")
    assert r.status_code == 401 and r.json()["error"]["code"] == "session_expired"
    login(c, "emp@example.com")
    for _ in range(int(settings.session_absolute_ttl_minutes / 30) + 1):
        clock.advance(minutes=30)
        c.get(f"{API}/auth/me")
    assert c.get(f"{API}/auth/me").status_code == 401


def test_password_change_revokes_other_sessions(db, client_factory) -> None:  # type: ignore[no-untyped-def]
    make_user(db, "emp@example.com")
    a, b = client_factory(), client_factory()
    login(a, "emp@example.com")
    login(b, "emp@example.com")
    weak = a.post(f"{API}/auth/change-password", json={"current_password": PASSWORD, "new_password": "short"})
    assert weak.status_code == 422
    r = a.post(
        f"{API}/auth/change-password", json={"current_password": PASSWORD, "new_password": "N3w-Secure-Passphrase"}
    )
    assert r.status_code == 200, r.text
    assert a.get(f"{API}/auth/me").status_code == 200
    assert b.get(f"{API}/auth/me").status_code == 401


def test_rbac_matrix(db, client_factory) -> None:  # type: ignore[no-untyped-def]
    for role in Role:
        make_user(db, f"{role.value}@example.com", role=role)
    expectations = {
        # path: roles allowed
        f"{API}/users": {Role.ADMIN},
        f"{API}/audit/events": {Role.ADMIN},
        f"{API}/settings": {Role.ADMIN},
        f"{API}/reports/overview": {Role.ADMIN},
        f"{API}/attendance/records": {Role.ADMIN},
        f"{API}/biometrics/attempts": {Role.ADMIN},
        f"{API}/shifts": {Role.ADMIN},
    }
    for role in Role:
        c = client_factory()
        login(c, f"{role.value}@example.com")
        for path, allowed in expectations.items():
            code = c.get(path).status_code
            assert (code == 200) == (role in allowed), (role, path, code)


def test_employee_cannot_escalate_or_create_users(db, client_factory) -> None:  # type: ignore[no-untyped-def]
    emp = make_user(db, "emp@example.com")
    c = client_factory()
    login(c, "emp@example.com")
    assert c.patch(f"{API}/users/{emp.id}", json={"role": "admin"}).status_code == 403
    assert (
        c.post(
            f"{API}/users",
            json={"email": "x@example.com", "full_name": "X", "temporary_password": "Sup3r-Secret-Value"},
        ).status_code
        == 403
    )
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "security.permission_denied")) is not None


def test_admin_user_lifecycle(db, client_factory) -> None:  # type: ignore[no-untyped-def]
    make_user(db, "admin@example.com", role=Role.ADMIN)
    admin = client_factory()
    login(admin, "admin@example.com")
    r = admin.post(
        f"{API}/users",
        json={
            "email": "New@Example.com",
            "full_name": "New Hire",
            "role": "employee",
            "temporary_password": "Temp-Passw0rd-123",
        },
    )
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    assert r.json()["email"] == "new@example.com" and r.json()["must_change_password"] is True
    dup = admin.post(
        f"{API}/users", json={"email": "new@example.com", "full_name": "Dup", "temporary_password": "Temp-Passw0rd-123"}
    )
    assert dup.status_code == 409
    # the new user must change the password before using privileged endpoints
    u = client_factory()
    login(u, "new@example.com", "Temp-Passw0rd-123")
    assert u.get(f"{API}/attendance/status").status_code == 200  # self-service still allowed
    # deactivation revokes sessions immediately
    assert admin.patch(f"{API}/users/{uid}", json={"is_active": False}).status_code == 200
    assert u.get(f"{API}/auth/me").status_code == 401
    # admins cannot demote or deactivate themselves
    me = admin.get(f"{API}/auth/me").json()
    assert admin.patch(f"{API}/users/{me['id']}", json={"role": "employee"}).status_code == 422


def test_session_list_and_revoke(db, client_factory) -> None:  # type: ignore[no-untyped-def]
    make_user(db, "emp@example.com")
    a, b = client_factory(), client_factory()
    login(a, "emp@example.com")
    login(b, "emp@example.com")
    sessions = a.get(f"{API}/auth/sessions").json()
    assert len(sessions) == 2 and sum(s["current"] for s in sessions) == 1
    other = next(s for s in sessions if not s["current"])
    assert a.delete(f"{API}/auth/sessions/{other['id']}").status_code == 204
    assert b.get(f"{API}/auth/me").status_code == 401
    assert db.scalar(select(AuthSession).where(AuthSession.revoked_reason == "user_revoked")) is not None


def test_security_headers_and_request_id(client_factory) -> None:  # type: ignore[no-untyped-def]
    r = client_factory().get(f"{API}/health/live", headers={"X-Request-ID": "abc12345-test"})
    assert r.headers["x-request-id"] == "abc12345-test"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"


def test_body_size_limit(db, client_factory, settings) -> None:  # type: ignore[no-untyped-def]
    c = client_factory()
    r = c.post(
        f"{API}/auth/login",
        content=b"x" * (settings.max_request_bytes + 1),
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"


def test_validation_errors_do_not_echo_input(client_factory) -> None:  # type: ignore[no-untyped-def]
    secret = "S3cr3t-Leak-" + "x" * 300  # exceeds max length -> validation error
    r = client_factory().post(f"{API}/auth/login", json={"email": "a@example.com", "password": secret})
    assert r.status_code == 422
    assert "S3cr3t-Leak" not in r.text


def test_unknown_fields_rejected(db, client_factory) -> None:  # type: ignore[no-untyped-def]
    r = client_factory().post(f"{API}/auth/login", json={"email": "a@example.com", "password": "x", "role": "admin"})
    assert r.status_code == 422


def test_session_expiry_time(db, client_factory, clock, settings) -> None:  # type: ignore[no-untyped-def]
    make_user(db, "emp@example.com")
    c = client_factory()
    login(c, "emp@example.com")
    s = db.scalar(select(AuthSession))
    assert s is not None
    assert s.expires_at - s.created_at == timedelta(minutes=settings.session_absolute_ttl_minutes)


def test_login_with_username_or_email(db, client_factory) -> None:  # type: ignore[no-untyped-def]
    make_user(db, "admin@example.com", Role.ADMIN, username="admin123")
    c = client_factory()
    r = c.post(f"{API}/auth/login", json={"login": "Admin123", "password": PASSWORD})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["username"] == "admin123"
    # The legacy field name and the email address keep working.
    assert c.post(f"{API}/auth/login", json={"email": "admin@example.com", "password": PASSWORD}).status_code == 200
    # Wrong password by username gets the same generic error as an unknown user.
    bad = c.post(f"{API}/auth/login", json={"login": "admin123", "password": "nope"})
    ghost = c.post(f"{API}/auth/login", json={"login": "nobody123", "password": "nope"})
    assert bad.status_code == ghost.status_code == 401
    assert bad.json()["error"]["message"] == ghost.json()["error"]["message"]


def test_dev_set_login_refused_outside_development() -> None:
    from app.cli import dev_set_login

    with pytest.raises(SystemExit, match="only allowed when APP_ENV=development"):
        dev_set_login("admin@example.com", "admin123", "Admin")


def test_bearer_token_for_native_clients(db, client_factory) -> None:  # type: ignore[no-untyped-def]
    make_user(db, "emp@example.com", username="emp001")
    c = client_factory()
    r = c.post(f"{API}/auth/token", json={"login": "emp001", "password": PASSWORD})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer" and body["user"]["email"] == "emp@example.com"
    assert "set-cookie" not in r.headers  # never an ambient credential
    auth = {"Authorization": f"Bearer {body['access_token']}"}
    native = client_factory()
    assert native.get(f"{API}/auth/me", headers=auth).json()["email"] == "emp@example.com"
    # Unsafe methods work without CSRF token or Origin on the bearer path.
    version = native.get(f"{API}/biometrics/consent-text", headers=auth).json()["version"]
    consent_body = {"consent_version": version, "accepted": True}
    consent = native.post(f"{API}/biometrics/consent", headers=auth, json=consent_body)
    assert consent.status_code in (200, 201), consent.text
    # Wrong credentials are generic and rate limited exactly like /login.
    assert c.post(f"{API}/auth/token", json={"login": "emp001", "password": "nope"}).status_code == 401
    # A bad or revoked bearer token is rejected; cookies are not consulted on the bearer path.
    assert native.get(f"{API}/auth/me", headers={"Authorization": "Bearer not-a-token"}).status_code == 401
    assert native.post(f"{API}/auth/logout", headers=auth).status_code == 200
    assert native.get(f"{API}/auth/me", headers=auth).status_code == 401


def test_cookie_sessions_still_require_csrf(db, client_factory) -> None:  # type: ignore[no-untyped-def]
    make_user(db, "admin@example.com", Role.ADMIN)
    c = client_factory()
    web_login(c, "admin@example.com")
    del c.headers["X-CSRF-Token"]
    r = c.post(f"{API}/auth/logout")
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"


def test_web_console_is_for_administrators_only(db, client_factory) -> None:  # type: ignore[no-untyped-def]
    make_user(db, "emp@example.com")
    c = client_factory()
    r = c.post(f"{API}/auth/login", json={"email": "emp@example.com", "password": PASSWORD})
    assert r.status_code == 403 and r.json()["error"]["code"] == "use_mobile_app"
    assert "absen_session" not in c.cookies
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "auth.login.web_denied")) is not None
    # The same account still works in the mobile app.
    assert login(client_factory(), "emp@example.com")["email"] == "emp@example.com"


def test_attendance_and_enrollment_only_from_the_app(db, client_factory) -> None:  # type: ignore[no-untyped-def]
    make_user(db, "admin@example.com", Role.ADMIN)
    web = client_factory()
    web_login(web, "admin@example.com")
    for path, body in (
        ("/biometrics/enrollments", None),
        ("/attendance/challenges", {"purpose": "check_in"}),
        ("/attendance/check-in", {"challenge_id": "00000000-0000-0000-0000-000000000000"}),
        ("/attendance/check-out", {"challenge_id": "00000000-0000-0000-0000-000000000000"}),
    ):
        r = web.post(f"{API}{path}", json=body, headers={"Idempotency-Key": "k" * 16})
        assert r.status_code == 403 and r.json()["error"]["code"] == "use_mobile_app", (path, r.text)
    # Administration still works on the web.
    assert web.get(f"{API}/users").status_code == 200
    # The administrator attends like everyone else, from the app.
    app = client_factory()
    login(app, "admin@example.com")
    assert app.post(f"{API}/biometrics/enrollments").status_code != 403


def test_admin_manages_sign_in_id_and_password(db, client_factory) -> None:  # type: ignore[no-untyped-def]
    make_user(db, "admin@example.com", Role.ADMIN)
    web = client_factory()
    web_login(web, "admin@example.com")
    body = {"email": "budi@example.com", "full_name": "Budi", "username": "Budi.01", "temporary_password": PASSWORD}
    created = web.post(f"{API}/users", json=body)
    assert created.status_code == 201, created.text
    uid = created.json()["id"]
    assert created.json()["username"] == "budi.01"
    # IDs are unique and validated.
    dup = web.post(f"{API}/users", json={**body, "email": "other@example.com"})
    assert dup.status_code == 409
    bad = web.patch(f"{API}/users/{uid}", json={"username": "no spaces"})
    assert bad.status_code == 422
    assert web.patch(f"{API}/users/{uid}", json={"username": "budi02"}).json()["username"] == "budi02"
    # A final password (no forced change) lets the employee sign in to the app with the new ID right away.
    final = "Kuat-Sekali-Passw0rd"
    r = web.post(f"{API}/users/{uid}/reset-password", json={"temporary_password": final, "must_change": False})
    assert r.status_code == 200, r.text
    me = login(client_factory(), "budi02", final)
    assert me["must_change_password"] is False
    # The default still forces a change at next sign-in.
    web.post(f"{API}/users/{uid}/reset-password", json={"temporary_password": final})
    assert login(client_factory(), "budi02", final)["must_change_password"] is True
    # Accounts can also be created with a final password.
    body2 = {"email": "sari@example.com", "full_name": "Sari", "username": "sari01", "temporary_password": final}
    assert web.post(f"{API}/users", json={**body2, "must_change_password": False}).status_code == 201
    assert login(client_factory(), "sari01", final)["must_change_password"] is False
