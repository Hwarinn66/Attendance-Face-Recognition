from __future__ import annotations

import uuid
from datetime import date, time
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models import Shift, ShiftAssignment, User, Workplace
from app.models.enums import Role
from tests.frames import frame_set, live_identity

PASSWORD = "Corr3ct-Horse-Battery"
API = "/api/v1"


def make_user(db: Session, email: str, role: Role = Role.EMPLOYEE, **fields: Any) -> User:
    u = User(
        email=email,
        full_name=fields.pop("full_name", email.split("@", maxsplit=1)[0].title()),
        role=role,
        password_hash=hash_password(fields.pop("password", PASSWORD)),
        **fields,
    )
    db.add(u)
    db.commit()
    return u


def login(client: TestClient, email: str, password: str = PASSWORD) -> dict[str, Any]:
    """Mobile-app session (bearer token): what employees use, and the only way to attend or enroll."""
    r = client.post(f"{API}/auth/token", json={"login": email, "password": password})
    assert r.status_code == 200, r.text
    client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return r.json()["user"]


def web_login(client: TestClient, email: str, password: str = PASSWORD) -> dict[str, Any]:
    """Web console session (cookie + CSRF token); administrators only."""
    r = client.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return r.json()["user"]


def schedule(
    db: Session,
    users: list[User],
    *,
    tz: str = "Asia/Jakarta",
    start: time = time(8, 0),
    end: time = time(17, 0),
    workdays: int = 0b1111111,
    geofence: dict[str, Any] | None = None,
    **policy: Any,
) -> tuple[Workplace, Shift]:
    wp = Workplace(name=f"WP {uuid.uuid4().hex[:6]}", timezone=tz, **(geofence or {}))
    db.add(wp)
    db.flush()
    shift = Shift(
        workplace_id=wp.id,
        name="Day",
        start_time=start,
        end_time=end,
        workdays_mask=workdays,
        **{
            "check_in_opens_before_min": 60,
            "check_in_closes_after_min": 60,
            "grace_min": 15,
            "check_out_opens_before_min": 60,
            "check_out_closes_after_min": 120,
            "break_min": 60,
            **policy,
        },
    )
    db.add(shift)
    db.flush()
    for u in users:
        db.add(ShiftAssignment(user_id=u.id, shift_id=shift.id, effective_from=date(2026, 1, 1)))
        u.workplace_id = wp.id
    db.commit()
    return wp, shift


def files(frames: list[bytes]) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("frames", (f"f{i:03d}.jpg", f, "image/jpeg")) for i, f in enumerate(frames)]


def consent(client: TestClient) -> None:
    version = client.get(f"{API}/biometrics/consent-text").json()["version"]
    r = client.post(f"{API}/biometrics/consent", json={"consent_version": version, "accepted": True})
    assert r.status_code == 200, r.text


def enroll(client: TestClient, clock: Any, identity: int = 0, frames: list[bytes] | None = None) -> dict[str, Any]:
    consent(client)
    r = client.post(f"{API}/biometrics/enrollments")
    assert r.status_code == 200, r.text
    body = r.json()
    clock.advance(seconds=12)
    r = client.post(
        f"{API}/biometrics/enrollments/{body['enrollment_id']}/submit",
        files=files(frames or frame_set(live_identity(identity))),
    )
    assert r.status_code == 200, r.text
    return r.json()


def verify(
    client: TestClient, clock: Any, purpose: str, identity: int = 0, frames: list[bytes] | None = None
) -> tuple[str, dict[str, Any]]:
    r = client.post(f"{API}/attendance/challenges", json={"purpose": purpose})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    clock.advance(seconds=12)
    r = client.post(
        f"{API}/attendance/challenges/{cid}/submit", files=files(frames or frame_set(live_identity(identity)))
    )
    assert r.status_code == 200, r.text
    return cid, r.json()


def act(client: TestClient, kind: str, challenge_id: str, key: str | None = None, **body: Any) -> Any:
    return client.post(
        f"{API}/attendance/{kind}",
        json={"challenge_id": challenge_id, **body},
        headers={"Idempotency-Key": key or uuid.uuid4().hex},
    )
