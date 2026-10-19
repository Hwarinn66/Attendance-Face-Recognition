from __future__ import annotations

import threading
import uuid
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.models import AttendanceRecord, AuditEvent, Holiday
from app.models.enums import Role
from tests.frames import frame_set, live_identity
from tests.integration.helpers import API, act, enroll, login, make_user, schedule, verify

JKT = ZoneInfo("Asia/Jakarta")
MONDAY = date(2026, 10, 12)


def at(h: int, m: int = 0, d: date = MONDAY) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, tzinfo=JKT)


def setup_employee(db, client_factory, clock, *, email="emp@example.com", identity=0, **sched):  # type: ignore[no-untyped-def]
    user = make_user(db, email)
    wp, shift = schedule(db, [user], **sched)
    c = client_factory()
    login(c, email)
    clock.set(at(7, 0))  # deterministic start: 07:00 local on Monday
    enroll(c, clock, identity=identity)
    return user, wp, shift, c


def test_status_reports_windows(db, client_factory, clock) -> None:  # type: ignore[no-untyped-def]
    _, _, _, c = setup_employee(db, client_factory, clock)
    clock.set(at(7, 30))
    st = c.get(f"{API}/attendance/status").json()
    assert st["can_check_in"] is True and st["can_check_out"] is False
    occ = next(o for o in st["today"] if o["shift_date"] == str(MONDAY))
    assert datetime.fromisoformat(occ["check_in_opens_at"]) == at(7, 0)
    assert datetime.fromisoformat(occ["check_out_closes_at"]) == at(19, 0)


def test_full_day_on_time(db, client_factory, clock) -> None:  # type: ignore[no-untyped-def]
    _, _, _, c = setup_employee(db, client_factory, clock)
    clock.set(at(8, 10))
    cid, v = verify(c, clock, "check_in")
    assert v["accepted"] is True
    r = act(c, "check-in", cid)
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["check_in_status"] == "on_time" and rec["late_minutes"] == 0
    clock.set(at(17, 5))
    cid, _ = verify(c, clock, "check_out")
    r = act(c, "check-out", cid)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["check_out_status"] == "on_time" and out["worked_minutes"] == (17 * 60 + 5 + 0) - (8 * 60 + 10) - 60 + 0


def test_late_and_early_departure(db, client_factory, clock) -> None:  # type: ignore[no-untyped-def]
    _, _, _, c = setup_employee(db, client_factory, clock)
    clock.set(at(8, 40))
    cid, _ = verify(c, clock, "check_in")
    rec = act(c, "check-in", cid).json()
    assert rec["check_in_status"] == "late" and rec["late_minutes"] == 41  # 08:40:12 -> ceil = 41
    clock.set(at(16, 20))
    cid, _ = verify(c, clock, "check_out")
    out = act(c, "check-out", cid).json()
    assert out["check_out_status"] == "early" and out["early_departure_minutes"] == 40  # 16:20:12 -> ceil(39.8)


def test_overtime(db, client_factory, clock) -> None:  # type: ignore[no-untyped-def]
    _, _, _, c = setup_employee(db, client_factory, clock)
    clock.set(at(7, 55))
    cid, _ = verify(c, clock, "check_in")
    act(c, "check-in", cid)
    clock.set(at(18, 30))
    cid, _ = verify(c, clock, "check_out")
    out = act(c, "check-out", cid).json()
    assert out["check_out_status"] == "overtime" and out["overtime_minutes"] == 90


def test_outside_window_blocks_challenge(db, client_factory, clock) -> None:  # type: ignore[no-untyped-def]
    _, _, _, c = setup_employee(db, client_factory, clock)
    clock.set(at(9, 30))
    r = c.post(f"{API}/attendance/challenges", json={"purpose": "check_in"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "outside_check_in_window"
    r = c.post(f"{API}/attendance/challenges", json={"purpose": "check_out"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_open_check_in"


def test_checkout_window_not_open_yet(db, client_factory, clock) -> None:  # type: ignore[no-untyped-def]
    _, _, _, c = setup_employee(db, client_factory, clock)
    clock.set(at(8, 0))
    cid, _ = verify(c, clock, "check_in")
    act(c, "check-in", cid)
    clock.set(at(12, 0))
    r = c.post(f"{API}/attendance/challenges", json={"purpose": "check_out"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "check_out_not_open_yet"


def test_verification_is_single_use_and_purpose_bound(db, client_factory, clock, settings) -> None:  # type: ignore[no-untyped-def]
    _, _, _, c = setup_employee(db, client_factory, clock)
    clock.set(at(8, 0))
    cid_a, _ = verify(c, clock, "check_in")
    cid_b, _ = verify(c, clock, "check_in")  # issuing B expires nothing that already passed
    assert act(c, "check-in", cid_a).status_code == 201
    replay = act(c, "check-in", cid_a)
    assert replay.status_code == 409 and replay.json()["error"]["code"] == "verification_already_used"
    dup = act(c, "check-in", cid_b)  # a second valid verification still cannot double check-in
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "already_checked_in"
    assert db.scalar(select(func.count()).select_from(AttendanceRecord)) == 1
    # a check-in verification cannot be used to check out
    clock.set(at(16, 30))
    cid_in, _ = cid_b, None
    r = act(c, "check-out", cid_in)
    assert r.status_code == 409 and r.json()["error"]["code"] in {"verification_wrong_purpose", "verification_expired"}
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "security.verification_rejected")) is not None


def test_verification_expires(db, client_factory, clock, settings) -> None:  # type: ignore[no-untyped-def]
    _, _, _, c = setup_employee(db, client_factory, clock)
    clock.set(at(8, 0))
    cid, _ = verify(c, clock, "check_in")
    clock.advance(seconds=settings.verification_validity_seconds + 1)
    r = act(c, "check-in", cid)
    assert r.status_code == 409 and r.json()["error"]["code"] == "verification_expired"


def test_face_mismatch_cannot_check_in(db, client_factory, clock) -> None:  # type: ignore[no-untyped-def]
    _, _, _, c = setup_employee(db, client_factory, clock, identity=0)
    clock.set(at(8, 0))
    cid, v = verify(c, clock, "check_in", identity=5)  # someone else in front of the camera
    assert v["accepted"] is False and v["category"] == "identity"
    r = act(c, "check-in", cid)
    assert r.status_code == 409 and r.json()["error"]["code"] == "verification_not_passed"


def test_client_cannot_assert_verification(db, client_factory, clock) -> None:  # type: ignore[no-untyped-def]
    _, _, _, c = setup_employee(db, client_factory, clock)
    clock.set(at(8, 0))
    r = act(c, "check-in", str(uuid.uuid4()))
    assert r.status_code == 404
    r = c.post(
        f"{API}/attendance/check-in",
        json={"challenge_id": str(uuid.uuid4()), "verified": True},
        headers={"Idempotency-Key": "k"},
    )
    assert r.status_code == 422  # unknown fields such as "verified" are rejected


def test_idempotent_retry_returns_same_record(db, client_factory, clock) -> None:  # type: ignore[no-untyped-def]
    _, _, _, c = setup_employee(db, client_factory, clock)
    clock.set(at(8, 0))
    cid, _ = verify(c, clock, "check_in")
    first = act(c, "check-in", cid, key="retry-key-1")
    second = act(c, "check-in", cid, key="retry-key-1")
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    other = act(c, "check-in", str(uuid.uuid4()), key="retry-key-1")
    assert other.status_code == 422 and other.json()["error"]["code"] == "idempotency_key_reused"
    missing = c.post(f"{API}/attendance/check-in", json={"challenge_id": cid})
    assert missing.status_code == 422


def test_concurrent_check_ins_create_one_record(db, client_factory, clock) -> None:  # type: ignore[no-untyped-def]
    _, _, _, c = setup_employee(db, client_factory, clock)
    clock.set(at(8, 0))
    challenges = [verify(c, clock, "check_in")[0] for _ in range(4)]
    barrier = threading.Barrier(len(challenges))
    codes: list[int] = []

    def worker(cid: str) -> None:
        barrier.wait()
        codes.append(act(c, "check-in", cid).status_code)

    threads = [threading.Thread(target=worker, args=(cid,)) for cid in challenges]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes) == [201, 409, 409, 409], codes
    assert db.scalar(select(func.count()).select_from(AttendanceRecord)) == 1


def test_overnight_shift(db, client_factory, clock) -> None:  # type: ignore[no-untyped-def]
    _, _, _, c = setup_employee(db, client_factory, clock, start=time(22, 0), end=time(6, 0), break_min=30)
    clock.set(at(21, 50))
    cid, _ = verify(c, clock, "check_in")
    rec = act(c, "check-in", cid).json()
    assert rec["shift_date"] == str(MONDAY)
    clock.set(at(6, 10, d=date(2026, 10, 13)))
    cid, _ = verify(c, clock, "check_out")
    out = act(c, "check-out", cid)
    assert out.status_code == 200, out.text
    assert out.json()["check_out_status"] == "on_time" and out.json()["shift_date"] == str(MONDAY)


def test_holiday_and_weekend_block_check_in(db, client_factory, clock) -> None:  # type: ignore[no-untyped-def]
    _, wp, _, c = setup_employee(db, client_factory, clock, workdays=0b0011111)
    db.add(Holiday(holiday_date=MONDAY, name="Test holiday", workplace_id=wp.id))
    db.commit()
    clock.set(at(8, 0))
    r = c.post(f"{API}/attendance/challenges", json={"purpose": "check_in"})
    assert r.status_code == 409
    clock.set(at(8, 0, d=date(2026, 10, 17)))  # Saturday
    login(c, "emp@example.com")  # days later: the old session has idled out
    assert c.post(f"{API}/attendance/challenges", json={"purpose": "check_in"}).status_code == 409


def test_not_enrolled_cannot_verify(db, client_factory, clock) -> None:  # type: ignore[no-untyped-def]
    u = make_user(db, "new@example.com")
    schedule(db, [u])
    c = client_factory()
    login(c, "new@example.com")
    clock.set(at(8, 0))
    r = c.post(f"{API}/attendance/challenges", json={"purpose": "check_in"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "biometric_not_enrolled"


def test_geofence(db, client_factory, clock) -> None:  # type: ignore[no-untyped-def]
    geo = {"geofence_enabled": True, "latitude": -6.2, "longitude": 106.8, "geofence_radius_m": 200}
    _, _, _, c = setup_employee(db, client_factory, clock, geofence=geo)
    clock.set(at(8, 0))
    cid, _ = verify(c, clock, "check_in")
    assert act(c, "check-in", cid).json()["error"]["code"] == "location_required"
    far = act(c, "check-in", cid, location={"latitude": -6.25, "longitude": 106.8, "accuracy_m": 20})
    assert far.status_code == 403 and far.json()["error"]["code"] == "outside_geofence"
    near = act(c, "check-in", cid, location={"latitude": -6.2005, "longitude": 106.8, "accuracy_m": 15})
    assert near.status_code == 201 and 40 < near.json()["check_in_distance_m"] < 70


def test_data_scoping(db, client_factory, clock) -> None:  # type: ignore[no-untyped-def]
    admin = make_user(db, "admin@example.com", role=Role.ADMIN)
    report = make_user(db, "report@example.com", manager_id=admin.id)
    other = make_user(db, "other@example.com")
    schedule(db, [report, other])
    rc, oc, mc = client_factory(), client_factory(), client_factory()
    login(rc, "report@example.com")
    login(oc, "other@example.com")
    login(mc, "admin@example.com")
    clock.set(at(7, 0))
    enroll(rc, clock, identity=1)
    enroll(oc, clock, identity=2)
    clock.set(at(8, 0))
    for client, ident in ((rc, 1), (oc, 2)):
        cid, _ = verify(client, clock, "check_in", identity=ident)
        assert act(client, "check-in", cid).status_code == 201
    # employees see only themselves
    assert [r["user_id"] for r in rc.get(f"{API}/attendance/me").json()["items"]] == [str(report.id)]
    assert rc.get(f"{API}/attendance/records").status_code == 403
    assert rc.get(f"{API}/users/{other.id}").status_code == 403
    # administrators see everyone
    seen = {r["user_id"] for r in mc.get(f"{API}/attendance/records").json()["items"]}
    assert seen == {str(report.id), str(other.id)}
    assert mc.get(f"{API}/attendance/records", params={"user_id": str(other.id)}).status_code == 200


def test_corrections_workflow(db, client_factory, clock) -> None:  # type: ignore[no-untyped-def]
    admin = make_user(db, "admin@example.com", role=Role.ADMIN)
    emp = make_user(db, "emp@example.com", manager_id=admin.id)
    _, shift = schedule(db, [emp])
    ec, ac = client_factory(), client_factory()
    login(ec, "emp@example.com")
    login(ac, "admin@example.com")
    clock.set(at(20, 0))  # after the shift: employee forgot to check in
    body = {
        "shift_id": str(shift.id),
        "shift_date": str(MONDAY),
        "reason": "Badge reader outage at entrance",
        "requested_check_in_at": at(8, 5).isoformat(),
        "requested_check_out_at": at(17, 0).isoformat(),
    }
    r = ec.post(f"{API}/attendance/corrections", json=body)
    assert r.status_code == 201, r.text
    corr = r.json()
    assert corr["status"] == "pending"
    # employees cannot approve or apply directly
    assert ec.post(f"{API}/attendance/corrections/{corr['id']}/review", json={"approve": True}).status_code == 403
    assert ec.post(f"{API}/attendance/corrections", json={**body, "apply_immediately": True}).status_code == 403
    # out-of-range times are rejected
    bad = {**body, "requested_check_in_at": at(3, 0).isoformat()}
    assert ec.post(f"{API}/attendance/corrections", json=bad).status_code == 422
    r = ac.post(f"{API}/attendance/corrections/{corr['id']}/review", json={"approve": True, "note": "verified"})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    rec = db.scalar(select(AttendanceRecord))
    assert rec is not None and rec.is_corrected and rec.source == "correction" and rec.worked_minutes == 8 * 60 - 5
    assert ac.post(f"{API}/attendance/corrections/{corr['id']}/review", json={"approve": True}).status_code == 409
    # The administrator applies a direct correction; previous values are retained and audited
    r = ac.post(
        f"{API}/attendance/corrections",
        json={
            **body,
            "user_id": str(emp.id),
            "apply_immediately": True,
            "requested_check_in_at": at(8, 30).isoformat(),
            "requested_check_out_at": None,
        },
    )
    assert r.status_code == 201 and r.json()["status"] == "approved"
    db.expire_all()
    rec = db.scalar(select(AttendanceRecord))
    assert rec is not None and rec.late_minutes == 30
    applied = db.scalars(select(AuditEvent).where(AuditEvent.action == "attendance.correction.applied")).all()
    assert len(applied) == 2 and applied[-1].details["previous"]["late_minutes"] == 0
    # Administrators cannot directly correct their own attendance
    me = ac.get(f"{API}/auth/me").json()
    own = ac.post(f"{API}/attendance/corrections", json={**body, "user_id": me["id"], "apply_immediately": True})
    assert own.status_code == 403


def test_exceptions_reports_and_csv(db, client_factory, clock) -> None:  # type: ignore[no-untyped-def]
    make_user(db, "admin@example.com", role=Role.ADMIN)
    late = make_user(db, "late@example.com", full_name="=HYPERLINK(evil)")
    absent = make_user(db, "absent@example.com")
    schedule(db, [late, absent])
    lc, hc = client_factory(), client_factory()
    login(lc, "late@example.com")
    login(hc, "admin@example.com")
    clock.set(at(7, 0))
    enroll(lc, clock)
    clock.set(at(8, 45))
    cid, _ = verify(lc, clock, "check_in")
    act(lc, "check-in", cid)
    clock.set(at(20, 0))
    ex = hc.get(f"{API}/attendance/exceptions", params={"start": str(MONDAY), "end": str(MONDAY)}).json()
    kinds = {(e["user_name"], e["kind"]) for e in ex}
    assert ("=HYPERLINK(evil)", "late") in kinds and ("=HYPERLINK(evil)", "missing_checkout") in kinds
    assert ("Absent", "absent") in kinds
    rows = hc.get(f"{API}/reports/summary", params={"start": str(MONDAY), "end": str(MONDAY)}).json()
    by_name = {r["user_name"]: r for r in rows}
    assert by_name["Absent"]["absent"] == 1 and by_name["=HYPERLINK(evil)"]["late"] == 1
    csv_text = hc.get(f"{API}/reports/summary.csv", params={"start": str(MONDAY), "end": str(MONDAY)}).text
    assert "'=HYPERLINK(evil)" in csv_text and "\n=HYPERLINK" not in csv_text
    overview = hc.get(f"{API}/reports/overview", params={"day": str(MONDAY)}).json()
    assert overview["checked_in"] == 1 and overview["late"] == 1 and overview["absent_so_far"] >= 1


def test_personal_data_export(db, client_factory, clock) -> None:  # type: ignore[no-untyped-def]
    _, _, _, c = setup_employee(db, client_factory, clock)
    r = c.get(f"{API}/me/export")
    assert r.status_code == 200
    data = r.json()
    assert data["profile"]["email"] == "emp@example.com"
    assert data["biometric_templates"][0]["note"] == "encrypted template withheld"
    assert "ciphertext" not in r.text


def test_server_time_is_authoritative(db, client_factory, clock) -> None:  # type: ignore[no-untyped-def]
    _, _, _, c = setup_employee(db, client_factory, clock)
    clock.set(at(8, 0))
    cid, _ = verify(c, clock, "check_in")
    r = c.post(
        f"{API}/attendance/check-in",
        json={"challenge_id": cid, "check_in_at": "2026-10-12T00:00:00Z"},
        headers={"Idempotency-Key": "x1"},
    )
    assert r.status_code == 422  # client timestamps are not accepted at all
    rec = act(c, "check-in", cid).json()
    assert datetime.fromisoformat(rec["check_in_at"]).astimezone(UTC) == clock.now


def test_frames_from_other_identity_mid_sequence_rejected(db, client_factory, clock) -> None:  # type: ignore[no-untyped-def]
    _, _, _, c = setup_employee(db, client_factory, clock)
    clock.set(at(8, 0))
    frames = frame_set(live_identity(0))
    frames[12:18] = frame_set(live_identity(4))[12:18]
    _, v = verify(c, clock, "check_in", frames=frames)
    assert v["accepted"] is False and v["category"] == "liveness"


def test_my_shifts_returns_only_own_assignments(db, client_factory, clock) -> None:  # type: ignore[no-untyped-def]
    me = make_user(db, "me@example.com")
    other = make_user(db, "other@example.com")
    _, mine = schedule(db, [me])
    schedule(db, [other], start=time(13, 0), end=time(21, 0))
    c = client_factory()
    login(c, "me@example.com")
    rows = c.get(f"{API}/attendance/my-shifts").json()
    assert [r["shift_id"] for r in rows] == [str(mine.id)]
    assert {r["user_id"] for r in rows} == {str(me.id)}


def test_admin_deletes_invalid_attendance(db, client_factory, clock) -> None:  # type: ignore[no-untyped-def]
    """An employee checked in while not at work: the administrator removes the record with a reason."""
    admin = make_user(db, "admin@example.com", role=Role.ADMIN)
    emp = make_user(db, "emp@example.com")
    _, shift = schedule(db, [emp, admin])
    ec, ac = client_factory(), client_factory()
    login(ec, "emp@example.com")
    login(ac, "admin@example.com")
    clock.set(at(7, 0))
    enroll(ec, clock, identity=1)
    enroll(ac, clock, identity=2)
    clock.set(at(8, 0))
    cid, _ = verify(ec, clock, "check_in", identity=1)
    rec_id = act(ec, "check-in", cid).json()["id"]
    cid, _ = verify(ac, clock, "check_in", identity=2)
    own_id = act(ac, "check-in", cid).json()["id"]
    # The employee also asks for a correction of that day (still pending).
    corr = ec.post(
        f"{API}/attendance/corrections",
        json={
            "shift_id": str(shift.id),
            "shift_date": str(MONDAY),
            "reason": "Please fix my check-out",
            "requested_check_in_at": at(8, 0).isoformat(),
        },
    )
    assert corr.status_code == 201, corr.text

    def delete(client, record_id, reason="Checked in from outside the office"):  # type: ignore[no-untyped-def]
        return client.request("DELETE", f"{API}/attendance/records/{record_id}", json={"reason": reason})

    assert delete(ec, rec_id).status_code == 403  # employees cannot delete
    assert delete(ac, rec_id, reason="x").status_code == 422  # a reason is required
    own = delete(ac, own_id)
    assert own.status_code == 403 and own.json()["error"]["code"] == "self_deletion"
    assert delete(ac, rec_id).status_code == 204
    assert delete(ac, rec_id).status_code == 404

    db.expire_all()
    assert db.get(AttendanceRecord, uuid.UUID(rec_id)) is None
    assert ec.get(f"{API}/attendance/me").json()["items"] == []
    ev = db.scalar(select(AuditEvent).where(AuditEvent.action == "attendance.record.deleted"))
    assert ev is not None and ev.target_id == rec_id
    assert ev.details["reason"] == "Checked in from outside the office"
    assert ev.details["record"]["employee"] == "Emp" and ev.details["record"]["check_in_at"]
    assert ev.details["closed_corrections"] == [corr.json()["id"]]
    pending = ac.get(f"{API}/attendance/corrections", params={"status": "pending"}).json()["items"]
    assert pending == []
    # With the window still open the employee can check in again, this time properly verified.
    cid, _ = verify(ec, clock, "check_in", identity=1)
    assert act(ec, "check-in", cid).status_code == 201
