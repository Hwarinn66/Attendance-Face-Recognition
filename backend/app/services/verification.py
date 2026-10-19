"""Liveness challenge lifecycle and server-side verification.

Flow: ``issue_challenge`` -> client captures frames while following the steps -> ``begin_submission``
(row lock; single use; bound to user, auth session, purpose, expiry) -> ``evaluate`` (inference in the
bounded pool) -> purpose-specific identity gate -> ``record`` (verification attempt + audit).

The client never tells the server whether liveness passed or the face matched; it only uploads frames.
"""

from __future__ import annotations

import concurrent.futures
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.biometrics.challenge import generate_steps
from app.biometrics.crypto import SealedTemplate, TemplateDecryptionError
from app.biometrics.decision import Decision, Reason, match_gate
from app.biometrics.imaging import decode_frame
from app.biometrics.models import cosine_similarity
from app.biometrics.pipeline import SequenceEvidence, analyze_sequence
from app.biometrics.runtime import BiometricRuntime
from app.biometrics.types import Embedding, FrameDecodeError, ModelUnavailable, PoseState
from app.core.errors import Conflict, NotFound, RateLimited
from app.db.base import utcnow
from app.models import BiometricTemplate, LivenessChallenge, User, VerificationAttempt
from app.models.enums import (
    AuditCategory,
    AuditOutcome,
    AuditSeverity,
    BiometricStatus,
    ChallengePurpose,
    ChallengeStatus,
    TemplateStatus,
    VerificationDecision,
)
from app.services import audit
from app.services.context import RequestContext

logger = logging.getLogger("app.verification")

RECOMMENDED_FPS = 5
STEP_SECONDS = 2.6  # UI guidance: hold each prompted pose ~1.6 s, return to centre ~1 s


@dataclass(slots=True)
class Evaluation:
    decision: Decision
    evidence: SequenceEvidence | None
    processing_ms: int


def _check_rate_limit(db: Session, runtime: BiometricRuntime, user: User, ctx: RequestContext) -> None:
    s = runtime.settings
    since = utcnow() - timedelta(minutes=s.verification_window_minutes)
    count = int(
        db.scalar(
            select(func.count())
            .select_from(VerificationAttempt)
            .where(VerificationAttempt.user_id == user.id, VerificationAttempt.created_at >= since)
        )
        or 0
    )
    if count >= s.verification_max_attempts:
        audit.emit(
            category=AuditCategory.SECURITY,
            action="biometric.verification.rate_limited",
            outcome=AuditOutcome.DENIED,
            severity=AuditSeverity.WARNING,
            actor_id=user.id,
            ctx=ctx,
            details={"attempts": count},
        )
        raise RateLimited(
            "Too many verification attempts. Please wait before trying again.",
            headers={"Retry-After": str(s.verification_window_minutes * 60)},
        )


def issue_challenge(
    db: Session,
    runtime: BiometricRuntime,
    user: User,
    auth_session_id: uuid.UUID,
    purpose: ChallengePurpose,
    ctx: RequestContext,
) -> LivenessChallenge:
    _check_rate_limit(db, runtime, user, ctx)
    now = utcnow()
    # Only one outstanding challenge per user and purpose.
    db.execute(
        update(LivenessChallenge)
        .where(
            LivenessChallenge.user_id == user.id,
            LivenessChallenge.purpose == purpose,
            LivenessChallenge.status == ChallengeStatus.ISSUED,
        )
        .values(status=ChallengeStatus.EXPIRED)
    )
    challenge = LivenessChallenge(
        user_id=user.id,
        auth_session_id=auth_session_id,
        purpose=purpose,
        # Passive liveness: no movement prompts; the frames must show a steady frontal face (pattern [centre]).
        steps=[]
        if runtime.settings.liveness_challenge == "passive"
        else [
            s.value for s in generate_steps(runtime.settings.challenge_steps, runtime.settings.challenge_action_states)
        ],
        status=ChallengeStatus.ISSUED,
        issued_at=now,
        expires_at=now + timedelta(seconds=runtime.settings.challenge_ttl_seconds),
    )
    db.add(challenge)
    db.flush()
    audit.add(
        db,
        category=AuditCategory.BIOMETRIC,
        action="biometric.challenge.issued",
        actor_id=user.id,
        target_type="challenge",
        target_id=challenge.id,
        ctx=ctx,
        details={"purpose": purpose.value},
    )
    return challenge


def begin_submission(
    db: Session,
    user: User,
    auth_session_id: uuid.UUID,
    challenge_id: uuid.UUID,
    purposes: set[ChallengePurpose],
    ctx: RequestContext,
) -> LivenessChallenge:
    """Atomically claim an ISSUED challenge for processing. Commits so the row lock is not held during
    inference; any later submission of the same challenge sees PROCESSING and is rejected as a replay."""
    challenge = db.scalar(select(LivenessChallenge).where(LivenessChallenge.id == challenge_id).with_for_update())
    if challenge is None or challenge.user_id != user.id:
        if challenge is not None:
            audit.emit(
                category=AuditCategory.SECURITY,
                action="security.challenge_foreign_user",
                outcome=AuditOutcome.DENIED,
                severity=AuditSeverity.WARNING,
                actor_id=user.id,
                target_type="challenge",
                target_id=challenge_id,
                ctx=ctx,
            )
        db.rollback()
        raise NotFound("Verification session not found")
    if challenge.purpose not in purposes:
        db.rollback()
        raise Conflict("Verification session has a different purpose", code="challenge_wrong_purpose")
    if challenge.auth_session_id != auth_session_id:
        db.rollback()
        audit.emit(
            category=AuditCategory.SECURITY,
            action="security.challenge_session_mismatch",
            outcome=AuditOutcome.DENIED,
            severity=AuditSeverity.WARNING,
            actor_id=user.id,
            target_type="challenge",
            target_id=challenge_id,
            ctx=ctx,
        )
        raise Conflict("Verification session belongs to a different sign-in", code="challenge_session_mismatch")
    if challenge.status != ChallengeStatus.ISSUED:
        db.rollback()
        audit.emit(
            category=AuditCategory.SECURITY,
            action="security.challenge_replay",
            outcome=AuditOutcome.DENIED,
            severity=AuditSeverity.WARNING,
            actor_id=user.id,
            target_type="challenge",
            target_id=challenge_id,
            ctx=ctx,
            details={"status": challenge.status.value},
        )
        raise Conflict("This verification session has already been used", code=Reason.CHALLENGE_ALREADY_USED.value)
    now = utcnow()
    if now > challenge.expires_at:
        challenge.status = ChallengeStatus.EXPIRED
        db.commit()
        raise Conflict(
            "This verification session has expired. Please start again.", code=Reason.CHALLENGE_EXPIRED.value
        )
    challenge.status = ChallengeStatus.PROCESSING
    challenge.submitted_at = now
    db.commit()
    return challenge


def _analyze(runtime: BiometricRuntime, frames: list[bytes], steps: list[PoseState]) -> SequenceEvidence:
    s = runtime.settings
    images = [decode_frame(f, max_bytes=s.max_frame_bytes, max_pixels=s.max_frame_pixels) for f in frames]
    analyzer = runtime.provider.sequence(steps, len(images))
    return analyze_sequence(images, steps, analyzer, runtime.pipeline_config())


def evaluate(runtime: BiometricRuntime, challenge: LivenessChallenge, frames: list[bytes]) -> Evaluation:
    """Run layers A-D (except the identity gate, which is purpose specific)."""
    s = runtime.settings
    t0 = time.perf_counter()
    decision = Decision(accepted=False)

    def done(evidence: SequenceEvidence | None = None) -> Evaluation:
        return Evaluation(decision, evidence, int((time.perf_counter() - t0) * 1000))

    if len(frames) < s.min_frames:
        decision.reasons.append(Reason.TOO_FEW_FRAMES)
        return done()
    if len(frames) > s.max_frames:
        decision.reasons.append(Reason.TOO_MANY_FRAMES)
        return done()
    assert challenge.submitted_at is not None
    elapsed = (challenge.submitted_at - challenge.issued_at).total_seconds()
    min_elapsed = (
        len(challenge.steps) * s.challenge_min_seconds_per_step if challenge.steps else s.challenge_passive_min_seconds
    )
    if elapsed < min_elapsed:
        decision.reasons.append(Reason.CHALLENGE_TOO_FAST)
        return done()
    if runtime.pad_required and not runtime.provider.pad_available:
        decision.reasons.append(Reason.PAD_UNAVAILABLE)  # fail closed before doing any work
        return done()
    steps = [PoseState(v) for v in challenge.steps]
    try:
        evidence: SequenceEvidence = runtime.executor.run(
            _analyze, runtime, frames, steps, timeout=s.inference_timeout_seconds
        )
    except FrameDecodeError:
        decision.reasons.append(Reason.INVALID_FRAME)
        return done()
    except ModelUnavailable:
        decision.reasons.append(Reason.MODEL_UNAVAILABLE)
        return done()
    except concurrent.futures.TimeoutError:
        logger.error("inference timed out", extra={"challenge_id": str(challenge.id)})
        decision.reasons.append(Reason.PROCESSING_ERROR)
        return done()
    decision.reasons.extend(evidence.reasons)
    decision.pad_score = evidence.pad_mean
    decision.quality_hints = evidence.quality_hints
    decision.details = dict(evidence.details)
    return done(evidence)


def open_template(runtime: BiometricRuntime, t: BiometricTemplate) -> Embedding:
    return runtime.cipher.open(
        SealedTemplate(t.key_id, t.nonce, t.ciphertext),
        template_id=t.id,
        user_id=t.user_id,
        model_name=t.model_name,
        dim=t.embedding_dim,
    )


def active_template(db: Session, user_id: uuid.UUID) -> BiometricTemplate | None:
    return db.scalar(
        select(BiometricTemplate).where(
            BiometricTemplate.user_id == user_id, BiometricTemplate.status == TemplateStatus.ACTIVE
        )
    )


def apply_match_gate(db: Session, runtime: BiometricRuntime, user: User, ev: Evaluation) -> None:
    """Identity gate for attendance: probe vs the user's own active template (1:1)."""
    d = ev.decision
    if d.reasons or ev.evidence is None or ev.evidence.probe is None:
        return
    template = active_template(db, user.id)
    if template is None or user.biometric_status != BiometricStatus.ACTIVE:
        d.reasons.append(Reason.NOT_ENROLLED)
        return
    if template.model_name != runtime.provider.recognizer_model_id:
        # Template was produced by a different model; embeddings are not comparable.
        d.reasons.append(Reason.NOT_ENROLLED)
        d.details["template_model_mismatch"] = True
        return
    try:
        reference = open_template(runtime, template)
    except TemplateDecryptionError:
        logger.error("template decryption failed", extra={"user_id": str(user.id)})
        d.reasons.append(Reason.PROCESSING_ERROR)
        return
    score = cosine_similarity(ev.evidence.probe, reference)
    d.match_score = score
    reason = match_gate(score, runtime.match_policy)
    if reason is not None:
        d.reasons.append(reason)


def record(
    db: Session,
    runtime: BiometricRuntime,
    user: User,
    challenge: LivenessChallenge,
    ev: Evaluation,
    frame_count: int,
    ctx: RequestContext,
) -> VerificationAttempt:
    d = ev.decision
    d.accepted = not d.reasons
    unavailable = any(
        r in (Reason.MODEL_UNAVAILABLE, Reason.PAD_UNAVAILABLE, Reason.PROCESSING_ERROR) for r in d.reasons
    )
    attempt = VerificationAttempt(
        user_id=user.id,
        challenge_id=challenge.id,
        purpose=challenge.purpose,
        decision=VerificationDecision.ACCEPTED
        if d.accepted
        else (VerificationDecision.ERROR if unavailable else VerificationDecision.REJECTED),
        reason_codes=[r.value for r in d.reasons],
        frame_count=frame_count,
        usable_frame_count=sum(f.usable for f in ev.evidence.frames) if ev.evidence else 0,
        pad_score=d.pad_score,
        match_score=d.match_score,
        details={**d.details, "steps": challenge.steps, "pad_enforced": runtime.pad_required},
        provider=_provider_label(runtime),
        processing_ms=ev.processing_ms,
        ip_address=ctx.ip_address,
    )
    db.add(attempt)
    db.flush()
    challenge.verification_attempt_id = attempt.id
    challenge.status = ChallengeStatus.PASSED if d.accepted else ChallengeStatus.FAILED
    spoof = any(
        r in (Reason.PAD_SPOOF_SUSPECTED, Reason.IDENTITY_INCONSISTENT, Reason.DISCONTINUITY) for r in d.reasons
    )
    audit.add(
        db,
        category=AuditCategory.SECURITY if spoof else AuditCategory.BIOMETRIC,
        action=f"biometric.verification.{attempt.decision.value}",
        actor_id=user.id,
        outcome=AuditOutcome.SUCCESS if d.accepted else AuditOutcome.FAILURE,
        severity=AuditSeverity.WARNING if spoof else AuditSeverity.INFO,
        target_type="verification_attempt",
        target_id=attempt.id,
        ctx=ctx,
        details={"purpose": challenge.purpose.value, "reasons": attempt.reason_codes},
    )
    return attempt


def _provider_label(runtime: BiometricRuntime) -> str:
    info = runtime.provider.info
    parts = [info.name, info.detector or "-", info.recognizer or "-", info.pad or "pad:none"]
    return "/".join(parts)[:120]
