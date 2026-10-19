"""Enrollment / liveness API flows with the TEST FIXTURE provider. These verify server-side state machines,
binding, replay protection and storage — not real-world biometric accuracy."""

from __future__ import annotations

from sqlalchemy import select

from app.models import AuditEvent, BiometricTemplate, LivenessChallenge, User, VerificationAttempt
from app.models.enums import BiometricStatus, ChallengeStatus, Role
from tests.frames import DARK, SPOOF, STATIC, frame_set, live_identity
from tests.integration.helpers import API, consent, enroll, files, login, make_user


def test_enrollment_requires_consent(db, client_factory) -> None:  # type: ignore[no-untyped-def]
    make_user(db, "emp@example.com")
    c = client_factory()
    login(c, "emp@example.com")
    r = c.post(f"{API}/biometrics/enrollments")
    assert r.status_code == 409 and r.json()["error"]["code"] == "consent_required"
    outdated = c.post(f"{API}/biometrics/consent", json={"consent_version": "old", "accepted": True})
    assert outdated.status_code == 422
    refused = c.post(f"{API}/biometrics/consent", json={"consent_version": "2026-10-v1", "accepted": False})
    assert refused.status_code == 422


def test_successful_enrollment_stores_encrypted_template(db, client_factory, clock) -> None:  # type: ignore[no-untyped-def]
    make_user(db, "emp@example.com")
    c = client_factory()
    login(c, "emp@example.com")
    result = enroll(c, clock, identity=1)
    assert result["accepted"] is True and result["biometric_status"] == "active"
    t = db.scalar(select(BiometricTemplate))
    assert t is not None and t.status.value == "active" and t.embedding_dim == 128 and len(t.nonce) == 12
    assert t.model_name == "fake-test-fixture"
    status = c.get(f"{API}/biometrics/me").json()
    assert status["status"] == "active" and status["template"]["id"] == str(t.id) and status["test_fixture"] is True
    attempt = db.scalar(select(VerificationAttempt))
    assert (
        attempt is not None
        and attempt.decision.value == "accepted"
        and attempt.provider.startswith("fake-test-fixture")
    )


def test_failed_liveness_does_not_activate(db, client_factory, clock) -> None:  # type: ignore[no-untyped-def]
    make_user(db, "emp@example.com")
    c = client_factory()
    login(c, "emp@example.com")
    for frames, category in (
        (frame_set(SPOOF), "liveness"),
        (frame_set(STATIC), "challenge"),
        (frame_set(DARK), "capture"),
    ):
        result = enroll(c, clock, frames=frames)
        assert result["accepted"] is False and result["category"] == category, result
        assert result["biometric_status"] == "pending_retry"
        assert "pad" not in result["message"].lower()
    assert db.scalar(select(BiometricTemplate)) is None


def test_attempt_limit_locks_enrollment_until_reset(db, client_factory, clock, settings) -> None:  # type: ignore[no-untyped-def]
    emp = make_user(db, "emp@example.com")
    make_user(db, "admin@example.com", role=Role.ADMIN)
    c = client_factory()
    login(c, "emp@example.com")
    for _ in range(settings.enrollment_max_attempts_per_day):
        result = enroll(c, clock, frames=frame_set(DARK))
    assert result["biometric_status"] == "verification_failed"
    r = c.post(f"{API}/biometrics/enrollments")
    assert r.status_code == 409 and r.json()["error"]["code"] == "enrollment_not_allowed"
    admin = client_factory()
    login(admin, "admin@example.com")
    assert admin.post(f"{API}/biometrics/users/{emp.id}/reset").json()["biometric_status"] == "not_enrolled"
    assert enroll(c, clock, identity=2)["accepted"] is True


def test_identity_conflict_blocks_second_account(db, client_factory, clock) -> None:  # type: ignore[no-untyped-def]
    make_user(db, "alice@example.com")
    make_user(db, "bob@example.com")
    a, b = client_factory(), client_factory()
    login(a, "alice@example.com")
    login(b, "bob@example.com")
    assert enroll(a, clock, identity=3)["accepted"] is True
    result = enroll(b, clock, identity=3)  # same face, different account (buddy punching)
    assert result["accepted"] is False and result["category"] == "review"
    assert result["biometric_status"] == "verification_failed"
    assert (
        db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "biometric.enrollment.failed", AuditEvent.severity == "warning"
            )
        )
        is not None
    )
    assert len(db.scalars(select(BiometricTemplate)).all()) == 1


def test_reenrollment_must_match_existing_template(db, client_factory, clock) -> None:  # type: ignore[no-untyped-def]
    make_user(db, "emp@example.com")
    c = client_factory()
    login(c, "emp@example.com")
    assert enroll(c, clock, identity=1)["accepted"] is True
    first_id = db.scalar(select(BiometricTemplate.id))
    takeover = enroll(c, clock, identity=4)  # stolen password, attacker's face
    assert takeover["accepted"] is False and takeover["category"] == "review"
    assert takeover["biometric_status"] == "active"  # old template stays in force
    db.expire_all()
    assert db.scalar(select(BiometricTemplate.id)) == first_id
    again = enroll(c, clock, identity=1)  # genuine owner refreshes their template
    assert again["accepted"] is True
    db.expire_all()
    templates = db.scalars(select(BiometricTemplate)).all()
    assert len(templates) == 1 and templates[0].id != first_id


def test_challenge_replay_and_expiry(db, client_factory, clock, settings) -> None:  # type: ignore[no-untyped-def]
    make_user(db, "emp@example.com")
    c = client_factory()
    login(c, "emp@example.com")
    consent(c)
    start = c.post(f"{API}/biometrics/enrollments").json()
    clock.advance(seconds=12)
    url = f"{API}/biometrics/enrollments/{start['enrollment_id']}/submit"
    assert c.post(url, files=files(frame_set(live_identity(0)))).json()["accepted"] is True
    replay = c.post(url, files=files(frame_set(live_identity(0))))
    assert replay.status_code == 409
    # expiry
    start = c.post(f"{API}/biometrics/enrollments").json()
    clock.advance(seconds=settings.challenge_ttl_seconds + 1)
    late = c.post(
        f"{API}/biometrics/enrollments/{start['enrollment_id']}/submit", files=files(frame_set(live_identity(0)))
    )
    assert late.status_code == 409 and late.json()["error"]["code"] == "challenge_expired"


def test_challenge_too_fast_rejected(db, client_factory, clock) -> None:  # type: ignore[no-untyped-def]
    make_user(db, "emp@example.com")
    c = client_factory()
    login(c, "emp@example.com")
    consent(c)
    start = c.post(f"{API}/biometrics/enrollments").json()
    clock.advance(seconds=1)  # 3 steps cannot be performed in 1 s
    r = c.post(
        f"{API}/biometrics/enrollments/{start['enrollment_id']}/submit", files=files(frame_set(live_identity(0)))
    )
    assert r.json()["accepted"] is False and r.json()["category"] == "challenge"
    attempt = db.scalar(select(VerificationAttempt))
    assert attempt is not None and attempt.reason_codes == ["challenge_too_fast"]


def test_challenge_bound_to_user_and_session(db, client_factory, clock) -> None:  # type: ignore[no-untyped-def]
    make_user(db, "alice@example.com")
    make_user(db, "bob@example.com")
    a, a2, b = client_factory(), client_factory(), client_factory()
    login(a, "alice@example.com")
    login(a2, "alice@example.com")
    login(b, "bob@example.com")
    consent(a)
    consent(b)
    start = a.post(f"{API}/biometrics/enrollments").json()
    clock.advance(seconds=12)
    # Bob cannot submit Alice's enrollment; Alice's other session cannot either.
    url = f"{API}/biometrics/enrollments/{start['enrollment_id']}/submit"
    assert b.post(url, files=files(frame_set(live_identity(0)))).status_code == 404
    r = a2.post(url, files=files(frame_set(live_identity(0))))
    assert r.status_code == 409 and r.json()["error"]["code"] == "challenge_session_mismatch"
    ch = db.scalar(select(LivenessChallenge))
    assert ch is not None and ch.status == ChallengeStatus.ISSUED


def test_frame_validation(db, client_factory, clock, settings) -> None:  # type: ignore[no-untyped-def]
    make_user(db, "emp@example.com")
    c = client_factory()
    login(c, "emp@example.com")
    consent(c)
    start = c.post(f"{API}/biometrics/enrollments").json()
    url = f"{API}/biometrics/enrollments/{start['enrollment_id']}/submit"
    bad_type = [("frames", ("a.gif", b"GIF89a", "image/gif"))]
    assert c.post(url, files=bad_type).json()["error"]["code"] == "invalid_frame_type"
    too_many = files(frame_set(live_identity(0), n=settings.max_frames + 1, width=160, height=160))
    assert c.post(url, files=too_many).json()["error"]["code"] == "too_many_frames"
    clock.advance(seconds=12)
    few = c.post(url, files=files(frame_set(live_identity(0), n=settings.min_frames - 1)))
    assert few.json()["accepted"] is False and few.json()["category"] == "capture"


def test_consent_withdrawal_deletes_template(db, client_factory, clock) -> None:  # type: ignore[no-untyped-def]
    make_user(db, "emp@example.com")
    c = client_factory()
    login(c, "emp@example.com")
    enroll(c, clock)
    assert c.delete(f"{API}/biometrics/consent").status_code == 200
    assert db.scalar(select(BiometricTemplate)) is None
    db.expire_all()
    assert db.scalar(select(User)).biometric_status == BiometricStatus.DELETED  # type: ignore[union-attr]
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "biometric.consent.withdrawn")) is not None


def test_biometric_deletion_authorisation(db, client_factory, clock) -> None:  # type: ignore[no-untyped-def]
    alice = make_user(db, "alice@example.com")
    make_user(db, "bob@example.com")
    make_user(db, "admin@example.com", role=Role.ADMIN)
    a, b, admin = (client_factory() for _ in range(3))
    login(a, "alice@example.com")
    login(b, "bob@example.com")
    login(admin, "admin@example.com")
    enroll(a, clock)
    assert b.delete(f"{API}/biometrics/users/{alice.id}").status_code == 403
    assert db.scalar(select(BiometricTemplate)) is not None
    assert admin.post(f"{API}/biometrics/users/{alice.id}/suspend").json()["biometric_status"] == "suspended"
    assert admin.post(f"{API}/biometrics/users/{alice.id}/reinstate").json()["biometric_status"] == "active"
    assert admin.delete(f"{API}/biometrics/users/{alice.id}").status_code == 200
    assert db.scalar(select(BiometricTemplate)) is None


def test_pad_missing_fails_closed(db, client_factory, clock, app) -> None:  # type: ignore[no-untyped-def]
    make_user(db, "emp@example.com")
    c = client_factory()
    login(c, "emp@example.com")
    provider = app.state.biometrics.provider
    provider._pad_enabled = False  # simulate PAD weights missing while PAD_MODE=required
    try:
        result = enroll(c, clock)
    finally:
        provider._pad_enabled = True
    assert result["accepted"] is False and result["category"] == "unavailable"
    attempt = db.scalar(select(VerificationAttempt))
    assert attempt is not None and attempt.decision.value == "error" and attempt.reason_codes == ["pad_unavailable"]
    assert db.scalar(select(BiometricTemplate)) is None


def test_verification_rate_limit(db, client_factory, clock, settings) -> None:  # type: ignore[no-untyped-def]
    make_user(db, "emp@example.com")
    c = client_factory()
    login(c, "emp@example.com")
    consent(c)
    db.add_all(
        [
            VerificationAttempt(
                user_id=db.scalar(select(User.id)),
                purpose="enrollment",
                decision="rejected",
                reason_codes=[],
                provider="x",
                created_at=clock.now,
            )
            for _ in range(settings.verification_max_attempts)
        ]
    )
    db.commit()
    r = c.post(f"{API}/biometrics/enrollments")
    assert r.status_code == 429


def test_passive_liveness_mode(db, client_factory, clock, settings, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """LIVENESS_CHALLENGE=passive: no movement prompts, but PAD, timing and identity gates still apply."""
    monkeypatch.setattr(settings, "liveness_challenge", "passive")
    make_user(db, "emp@example.com")
    c = client_factory()
    login(c, "emp@example.com")
    consent(c)
    start = c.post(f"{API}/biometrics/enrollments").json()
    assert start["challenge"]["steps"] == []
    clock.advance(seconds=1)  # below the passive minimum capture time
    r = c.post(
        f"{API}/biometrics/enrollments/{start['enrollment_id']}/submit", files=files(frame_set(live_identity(0)))
    )
    assert r.json()["accepted"] is False and r.json()["category"] == "challenge"
    # A presentation attack is still rejected by PAD.
    assert enroll(c, clock, frames=frame_set(SPOOF))["category"] == "liveness"
    # A steady live frontal face is accepted.
    result = enroll(c, clock)
    assert result["accepted"] is True and result["biometric_status"] == "active"
    reasons = [a.reason_codes for a in db.scalars(select(VerificationAttempt).order_by(VerificationAttempt.created_at))]
    assert reasons[0] == ["challenge_too_fast"]
