"""Live dashboard: attendance commits are pushed to administrators over Server-Sent Events."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.models.enums import Role
from app.services import live
from tests.integration.helpers import API, act, enroll, login, make_user, schedule, verify, web_login

JKT = ZoneInfo("Asia/Jakarta")
MONDAY = date(2026, 10, 12)
STREAM = f"{API}/events/attendance"


def at(h: int, m: int = 0) -> datetime:
    return datetime(MONDAY.year, MONDAY.month, MONDAY.day, h, m, tzinfo=JKT)


def wait_until_listening(timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not (live.broadcaster.is_listening and live.broadcaster.subscriber_count > 0):
        assert time.monotonic() < deadline, "the live event listener never started"
        time.sleep(0.02)


def read_stream(client: Any, out: dict[str, Any]) -> None:
    # TestClient returns once the stream ends (its lifetime is shortened in these tests).
    r = client.get(STREAM)
    out["status"], out["headers"], out["body"] = r.status_code, r.headers, r.text


def parse_events(body: str) -> list[dict[str, Any]]:
    events = []
    for block in body.split("\n\n"):
        lines = block.split("\n")
        if "event: attendance" in lines:
            events.append(json.loads(next(ln.removeprefix("data: ") for ln in lines if ln.startswith("data: "))))
    return events


def test_live_events_are_for_administrators(db, client_factory, settings, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "live_events_stream_seconds", 1.0)
    make_user(db, "admin@example.com", role=Role.ADMIN)
    make_user(db, "emp@example.com")
    assert client_factory().get(STREAM).status_code == 401
    ec = client_factory()
    login(ec, "emp@example.com")
    assert ec.get(STREAM).status_code == 403
    ac = client_factory()
    web_login(ac, "admin@example.com")
    r = ac.get(STREAM)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert "no-transform" in r.headers["cache-control"]
    assert r.text.startswith("retry: 3000\n")
    assert parse_events(r.text) == []


def test_attendance_changes_reach_the_console_without_refresh(db, client_factory, clock, settings, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "live_events_stream_seconds", 6.0)
    monkeypatch.setattr(settings, "live_events_heartbeat_seconds", 0.5)
    admin = make_user(db, "admin@example.com", role=Role.ADMIN)
    emp = make_user(db, "emp@example.com", full_name="Dewi Lestari")
    _, shift = schedule(db, [emp, admin])
    ec = client_factory()
    login(ec, "emp@example.com")
    clock.set(at(7, 0))
    enroll(ec, clock, identity=1)  # enrollment checks are not dashboard events
    ac, other = client_factory(), client_factory()
    web_login(ac, "admin@example.com")
    web_login(other, "admin@example.com")  # a second console acting while the first one watches

    out: dict[str, Any] = {}
    reader = threading.Thread(target=read_stream, args=(ac, out))
    reader.start()
    wait_until_listening()
    clock.set(at(8, 0))
    verify(ec, clock, "check_in", identity=2)  # someone else's face: rejected
    cid, _ = verify(ec, clock, "check_in", identity=1)
    rec_id = act(ec, "check-in", cid).json()["id"]
    r = other.request("DELETE", f"{API}/attendance/records/{rec_id}", json={"reason": "Not at the office"})
    assert r.status_code == 204
    r = other.post(
        f"{API}/attendance/corrections",
        json={
            "user_id": str(emp.id),
            "shift_id": str(shift.id),
            "shift_date": str(MONDAY),
            "requested_check_in_at": at(8, 0).isoformat(),
            "reason": "Checked in at the front desk",
            "apply_immediately": True,
        },
    )
    assert r.status_code == 201, r.text
    reader.join(timeout=20)
    assert not reader.is_alive()

    assert out["status"] == 200
    assert ": ping" in out["body"]  # heartbeats keep idle proxies from closing the stream
    failed, checked_in, deleted, corrected = parse_events(out["body"])
    assert failed["kind"] == "verification_failed" and failed["purpose"] == "check_in"
    assert failed["user_name"] == "Dewi Lestari" and failed["suspected_spoof"] is False
    assert checked_in["kind"] == "check_in" and checked_in["record_id"] == rec_id
    assert checked_in["user_id"] == str(emp.id) and checked_in["user_name"] == "Dewi Lestari"
    assert checked_in["status"] == "on_time" and at(8, 0) <= datetime.fromisoformat(checked_in["at"]) <= at(8, 1)
    assert deleted["kind"] == "deleted" and deleted["record_id"] == rec_id and deleted["actor_id"] == str(admin.id)
    assert corrected["kind"] == "correction_applied" and corrected["user_name"] == "Dewi Lestari"
    # Names and ids only: nothing biometric leaves the server on this channel.
    for ev in (failed, checked_in, deleted, corrected):
        assert not {"match_score", "pad_score", "embedding", "frames", "details"} & set(ev)


def test_events_are_sent_only_when_the_transaction_commits(db) -> None:  # type: ignore[no-untyped-def]
    async def scenario() -> dict[str, Any]:
        queue = live.broadcaster.subscribe()
        try:
            await asyncio.to_thread(wait_until_listening)
            live.publish(db, "check_in", n=1)
            db.rollback()
            live.publish(db, "check_in", n=2)
            db.commit()
            return json.loads(await asyncio.wait_for(queue.get(), timeout=5))
        finally:
            live.broadcaster.unsubscribe(queue)

    first = asyncio.run(scenario())
    assert first["kind"] == "check_in" and first["n"] == 2  # the rolled-back event was never delivered


def test_listener_url_drops_the_sqlalchemy_driver() -> None:
    assert live.libpq_url("postgresql+psycopg://u:p@h:5432/d") == "postgresql://u:p@h:5432/d"
    assert live.libpq_url("postgresql://u@h/d") == "postgresql://u@h/d"
