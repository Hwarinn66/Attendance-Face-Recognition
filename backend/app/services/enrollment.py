"""Consent, self-service enrollment, template lifecycle and administrative biometric actions."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta

import numpy as np
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.biometrics.decision import Decision, Reason
from app.biometrics.models import cosine_similarity
from app.biometrics.runtime import BiometricRuntime
from app.biometrics.types import Embedding
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.db.base import utcnow
from app.models import BiometricConsent, BiometricTemplate, EnrollmentSession, LivenessChallenge, User
from app.models.enums import (
    AuditCategory,
    AuditOutcome,
    AuditSeverity,
    BiometricStatus,
    ChallengePurpose,
    ChallengeStatus,
    EnrollmentSessionStatus,
    TemplateStatus,
)
from app.services import audit, system_settings, verification
from app.services.biometric_state import BiometricEvent, InvalidTransition, can, next_status
from app.services.context import RequestContext
from app.services.verification import Evaluation

logger = logging.getLogger("app.enrollment")


def _transition(user: User, event: BiometricEvent) -> None:
    new = next_status(user.biometric_status, event)
    if new != user.biometric_status:
        user.biometric_status = new
        user.biometric_status_changed_at = utcnow()


# --- consent ---------------------------------------------------------------------------------------------


def current_consent(db: Session, user_id: uuid.UUID) -> BiometricConsent | None:
    return db.scalar(
        select(BiometricConsent)
        .where(BiometricConsent.user_id == user_id, BiometricConsent.withdrawn_at.is_(None))
        .order_by(BiometricConsent.granted_at.desc())
        .limit(1)
    )


def grant_consent(
    db: Session, runtime: BiometricRuntime, user: User, version: str, ctx: RequestContext
) -> BiometricConsent:
    values = system_settings.get_all(db, runtime.settings)
    if version != values["consent.version"]:
        raise ValidationFailed("Consent text has changed; please review the current version", code="consent_outdated")
    existing = current_consent(db, user.id)
    if existing is not None and existing.consent_version == version:
        return existing
    consent = BiometricConsent(
        user_id=user.id,
        consent_version=version,
        consent_text_sha256=system_settings.consent_text_sha256(values["consent.text"]),
        ip_address=ctx.ip_address,
        user_agent=ctx.user_agent,
    )
    if existing is not None:
        existing.withdrawn_at = utcnow()  # superseded by consent to the new version
    db.add(consent)
    db.flush()
    audit.add(
        db,
        category=AuditCategory.PRIVACY,
        action="biometric.consent.granted",
        actor_id=user.id,
        target_type="consent",
        target_id=consent.id,
        ctx=ctx,
        details={"version": version},
    )
    db.commit()
    return consent


def withdraw_consent(db: Session, user: User, ctx: RequestContext) -> int:
    """Withdrawal deletes the template immediately (no biometric processing without consent)."""
    consent = current_consent(db, user.id)
    if consent is None:
        raise Conflict("No active consent to withdraw", code="no_consent")
    consent.withdrawn_at = utcnow()
    deleted = _delete_templates(db, user)
    audit.add(
        db,
        category=AuditCategory.PRIVACY,
        action="biometric.consent.withdrawn",
        actor_id=user.id,
        target_type="user",
        target_id=user.id,
        ctx=ctx,
        details={"templates_deleted": deleted},
    )
    db.commit()
    return deleted


# --- status ------------------------------------------------------------------------------------------------


def failed_attempts_today(db: Session, user_id: uuid.UUID) -> int:
    since = utcnow() - timedelta(hours=24)
    return int(
        db.scalar(
            select(func.count())
            .select_from(EnrollmentSession)
            .where(
                EnrollmentSession.user_id == user_id,
                EnrollmentSession.status == EnrollmentSessionStatus.FAILED,
                EnrollmentSession.created_at >= since,
            )
        )
        or 0
    )


@dataclass(slots=True)
class StatusView:
    status: BiometricStatus
    consent: BiometricConsent | None
    consent_current: bool
    template: BiometricTemplate | None
    can_start: bool
    attempts_remaining: int


def status_view(db: Session, runtime: BiometricRuntime, user: User) -> StatusView:
    values = system_settings.get_all(db, runtime.settings)
    consent = current_consent(db, user.id)
    remaining = max(0, int(values["enrollment.max_attempts_per_day"]) - failed_attempts_today(db, user.id))
    return StatusView(
        status=user.biometric_status,
        consent=consent,
        consent_current=consent is not None and consent.consent_version == values["consent.version"],
        template=verification.active_template(db, user.id),
        can_start=can(user.biometric_status, BiometricEvent.START) and remaining > 0,
        attempts_remaining=remaining,
    )


# --- enrollment ----------------------------------------------------------------------------------------


def start_enrollment(
    db: Session, runtime: BiometricRuntime, user: User, auth_session_id: uuid.UUID, ctx: RequestContext
) -> tuple[EnrollmentSession, LivenessChallenge]:
    view = status_view(db, runtime, user)
    if not view.consent_current or view.consent is None:
        raise Conflict("Biometric consent for the current terms is required first", code="consent_required")
    if not can(user.biometric_status, BiometricEvent.START):
        raise Conflict(
            {
                BiometricStatus.SUSPENDED: "Face verification is suspended for your account. Contact HR.",
                BiometricStatus.VERIFICATION_FAILED: (
                    "Enrollment is locked after failed attempts. Contact HR to reset it."
                ),
            }.get(user.biometric_status, "Enrollment cannot be started in the current state."),
            code="enrollment_not_allowed",
        )
    if view.attempts_remaining <= 0:
        raise Conflict("No enrollment attempts left today. Contact HR.", code="enrollment_attempts_exhausted")
    # Expire previous in-progress sessions and their challenges.
    open_sessions = db.scalars(
        select(EnrollmentSession).where(
            EnrollmentSession.user_id == user.id, EnrollmentSession.status == EnrollmentSessionStatus.IN_PROGRESS
        )
    ).all()
    for s in open_sessions:
        s.status = EnrollmentSessionStatus.EXPIRED
        s.completed_at = utcnow()
    challenge = verification.issue_challenge(db, runtime, user, auth_session_id, ChallengePurpose.ENROLLMENT, ctx)
    session = EnrollmentSession(
        user_id=user.id,
        consent_id=view.consent.id,
        challenge_id=challenge.id,
        status=EnrollmentSessionStatus.IN_PROGRESS,
    )
    db.add(session)
    _transition(user, BiometricEvent.START)
    db.flush()
    audit.add(
        db,
        category=AuditCategory.BIOMETRIC,
        action="biometric.enrollment.started",
        actor_id=user.id,
        target_type="enrollment",
        target_id=session.id,
        ctx=ctx,
    )
    db.commit()
    return session, challenge


def _other_active_templates(db: Session, user_id: uuid.UUID, model: str) -> list[BiometricTemplate]:
    return list(
        db.scalars(
            select(BiometricTemplate).where(
                BiometricTemplate.user_id != user_id,
                BiometricTemplate.status == TemplateStatus.ACTIVE,
                BiometricTemplate.model_name == model,
            )
        ).all()
    )


def _identity_gates(db: Session, runtime: BiometricRuntime, user: User, ev: Evaluation) -> None:
    d = ev.decision
    if d.reasons or ev.evidence is None or ev.evidence.probe is None:
        return
    probe = ev.evidence.probe
    policy = runtime.match_policy
    s = runtime.settings
    model = runtime.provider.recognizer_model_id
    assert model is not None

    # 1. Sample consistency: every frontal sample must confidently match the template it creates.
    sims = [float(np.dot(e, probe)) for e in ev.evidence.frontal_embeddings]
    d.details["consistency_min"] = round(min(sims), 4)
    if min(sims) < policy.threshold + policy.margin:
        d.reasons.append(Reason.INCONSISTENT_SAMPLES)
        return

    # 2. Re-enrollment continuity: the new face must match the user's current template.
    existing = verification.active_template(db, user.id)
    if existing is not None and existing.model_name == model:
        score = cosine_similarity(probe, verification.open_template(runtime, existing))
        d.details["reenroll_similarity"] = round(score, 4)
        if score < policy.threshold:
            d.reasons.append(Reason.REENROLL_MISMATCH)
            return

    # 3. Identity conflict (1:N): the face must not already be enrolled for someone else.
    assert s.identity_conflict_threshold is not None
    best = -1.0
    for t in _other_active_templates(db, user.id, model):
        best = max(best, cosine_similarity(probe, verification.open_template(runtime, t)))
    d.details["conflict_max_similarity"] = round(best, 4) if best > -1 else None
    if best >= s.identity_conflict_threshold:
        d.reasons.append(Reason.IDENTITY_CONFLICT)


def _store_template(
    db: Session,
    runtime: BiometricRuntime,
    user: User,
    probe: Embedding,
    session: EnrollmentSession,
    sample_count: int,
    summary: dict[str, object],
) -> BiometricTemplate:
    model = runtime.provider.recognizer_model_id
    assert model is not None
    # Data minimisation: the previous template is deleted, not archived.
    db.execute(delete(BiometricTemplate).where(BiometricTemplate.user_id == user.id))
    template_id = uuid.uuid4()
    sealed = runtime.cipher.seal(probe, template_id=template_id, user_id=user.id, model_name=model)
    t = BiometricTemplate(
        id=template_id,
        user_id=user.id,
        status=TemplateStatus.ACTIVE,
        model_name=model,
        embedding_dim=int(probe.size),
        key_id=sealed.key_id,
        nonce=sealed.nonce,
        ciphertext=sealed.ciphertext,
        sample_count=sample_count,
        quality_summary=summary,
        enrollment_session_id=session.id,
    )
    db.add(t)
    return t


def submit_enrollment(
    db: Session,
    runtime: BiometricRuntime,
    user: User,
    auth_session_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    frames: list[bytes],
    ctx: RequestContext,
) -> tuple[EnrollmentSession, Decision]:
    session = db.scalar(
        select(EnrollmentSession).where(EnrollmentSession.id == enrollment_id, EnrollmentSession.user_id == user.id)
    )
    if session is None:
        raise NotFound("Enrollment session not found")
    if session.status != EnrollmentSessionStatus.IN_PROGRESS:
        raise Conflict("This enrollment session is no longer active", code=Reason.CHALLENGE_ALREADY_USED.value)
    challenge = verification.begin_submission(
        db, user, auth_session_id, session.challenge_id, {ChallengePurpose.ENROLLMENT}, ctx
    )
    db.refresh(user)
    try:
        ev = verification.evaluate(runtime, challenge, frames)
        _identity_gates(db, runtime, user, ev)
    except Exception:
        _record_failure_after_error(db, runtime, user, session, challenge, len(frames), ctx)
        raise
    attempt = verification.record(db, runtime, user, challenge, ev, len(frames), ctx)
    d = ev.decision
    session.completed_at = utcnow()
    if d.accepted:
        assert ev.evidence is not None and ev.evidence.probe is not None
        summary = {
            "frontal_samples": len(ev.evidence.frontal_embeddings),
            "pad_mean": d.pad_score,
            "pad_enforced": runtime.pad_required,
            "consistency_min": d.details.get("consistency_min"),
            "provider": attempt.provider,
        }
        _store_template(db, runtime, user, ev.evidence.probe, session, len(ev.evidence.frontal_embeddings), summary)
        session.status = EnrollmentSessionStatus.SUCCEEDED
        _transition(user, BiometricEvent.SUCCEED)
        audit.add(
            db,
            category=AuditCategory.BIOMETRIC,
            action="biometric.enrollment.succeeded",
            actor_id=user.id,
            target_type="enrollment",
            target_id=session.id,
            ctx=ctx,
        )
    else:
        session.status = EnrollmentSessionStatus.FAILED
        session.failure_reasons = [r.value for r in d.reasons]
        limit = int(system_settings.get(db, runtime.settings, "enrollment.max_attempts_per_day"))
        final = (
            Reason.IDENTITY_CONFLICT in d.reasons
            or Reason.REENROLL_MISMATCH in d.reasons
            or failed_attempts_today(db, user.id) + 1 >= limit
        )
        unavailable = all(
            r in (Reason.MODEL_UNAVAILABLE, Reason.PAD_UNAVAILABLE, Reason.PROCESSING_ERROR) for r in d.reasons
        )
        if unavailable:
            # Service problem, not the user's fault: do not burn an attempt or lock the account.
            session.status = EnrollmentSessionStatus.EXPIRED
            _transition(user, BiometricEvent.FAIL_RETRYABLE)
        else:
            _transition(user, BiometricEvent.FAIL_FINAL if final else BiometricEvent.FAIL_RETRYABLE)
        conflict = Reason.IDENTITY_CONFLICT in d.reasons or Reason.REENROLL_MISMATCH in d.reasons
        audit.add(
            db,
            category=AuditCategory.SECURITY if conflict else AuditCategory.BIOMETRIC,
            action="biometric.enrollment.failed",
            actor_id=user.id,
            outcome=AuditOutcome.FAILURE,
            severity=AuditSeverity.WARNING if conflict else AuditSeverity.INFO,
            target_type="enrollment",
            target_id=session.id,
            ctx=ctx,
            details={"reasons": session.failure_reasons, "final": final},
        )
    db.commit()
    return session, d


def _record_failure_after_error(
    db: Session,
    runtime: BiometricRuntime,
    user: User,
    session: EnrollmentSession,
    challenge: LivenessChallenge,
    frame_count: int,
    ctx: RequestContext,
) -> None:
    db.rollback()
    logger.exception("enrollment processing failed", extra={"enrollment_id": str(session.id)})
    session = db.merge(session)
    challenge = db.merge(challenge)
    ev = Evaluation(Decision(accepted=False, reasons=[Reason.PROCESSING_ERROR]), None, 0)
    verification.record(db, runtime, user, challenge, ev, frame_count, ctx)
    session.status = EnrollmentSessionStatus.EXPIRED
    session.completed_at = utcnow()
    db.commit()


# --- deletion & admin actions ------------------------------------------------------------------------


def _delete_templates(db: Session, user: User) -> int:
    result = db.execute(delete(BiometricTemplate).where(BiometricTemplate.user_id == user.id))
    db.execute(
        update(LivenessChallenge)
        .where(
            LivenessChallenge.user_id == user.id,
            LivenessChallenge.status.in_([ChallengeStatus.ISSUED, ChallengeStatus.PASSED]),
        )
        .values(status=ChallengeStatus.EXPIRED)
    )
    db.execute(
        update(EnrollmentSession)
        .where(EnrollmentSession.user_id == user.id, EnrollmentSession.status == EnrollmentSessionStatus.IN_PROGRESS)
        .values(status=EnrollmentSessionStatus.EXPIRED, completed_at=utcnow())
    )
    if user.biometric_status != BiometricStatus.DELETED:
        _transition(user, BiometricEvent.DELETE)
    return int(getattr(result, "rowcount", 0) or 0)


def delete_biometrics(db: Session, user: User, actor: User, ctx: RequestContext, reason: str) -> int:
    deleted = _delete_templates(db, user)
    audit.add(
        db,
        category=AuditCategory.PRIVACY,
        action="biometric.template.deleted",
        actor_id=actor.id,
        target_type="user",
        target_id=user.id,
        severity=AuditSeverity.WARNING if actor.id != user.id else AuditSeverity.INFO,
        ctx=ctx,
        details={"templates_deleted": deleted, "reason": reason},
    )
    db.commit()
    return deleted


def admin_transition(db: Session, user: User, event: BiometricEvent, actor: User, ctx: RequestContext) -> User:
    if event == BiometricEvent.REINSTATE and verification.active_template(db, user.id) is None:
        raise Conflict("Cannot reinstate: the user has no active template", code="no_template")
    try:
        _transition(user, event)
    except InvalidTransition as exc:
        raise Conflict(str(exc), code="invalid_biometric_transition") from exc
    if event == BiometricEvent.RESET:
        db.execute(
            update(EnrollmentSession)
            .where(
                EnrollmentSession.user_id == user.id,
                EnrollmentSession.status == EnrollmentSessionStatus.FAILED,
                EnrollmentSession.created_at >= utcnow() - timedelta(hours=24),
            )
            .values(status=EnrollmentSessionStatus.EXPIRED)
        )
    audit.add(
        db,
        category=AuditCategory.BIOMETRIC,
        action=f"biometric.status.{event.value}",
        actor_id=actor.id,
        target_type="user",
        target_id=user.id,
        severity=AuditSeverity.WARNING,
        ctx=ctx,
        details={"new_status": user.biometric_status.value},
    )
    db.commit()
    return user
