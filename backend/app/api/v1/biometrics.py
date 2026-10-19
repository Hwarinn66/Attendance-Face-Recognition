from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from sqlalchemy import func, select

from app.api.deps import AuthContext, AuthDep, ContextDep, DbDep, SettingsDep, require, require_app
from app.biometrics.decision import Decision
from app.biometrics.imaging import decode_frame
from app.biometrics.quality import assess_quality
from app.biometrics.runtime import BiometricRuntime
from app.biometrics.types import FrameDecodeError, ModelUnavailable, PoseState
from app.core.config import Settings
from app.core.errors import RateLimited, ServiceUnavailable, ValidationFailed
from app.core.permissions import Permission
from app.db.base import utcnow
from app.models import LivenessChallenge, User, VerificationAttempt
from app.models.enums import VerificationDecision
from app.schemas.biometrics import (
    BiometricStatusOut,
    ChallengeOut,
    ConsentIn,
    ConsentOut,
    ConsentTextOut,
    EnrollmentStartOut,
    QualityCheckOut,
    TemplateMeta,
    VerificationAttemptOut,
    VerificationOut,
)
from app.schemas.common import Message, Page
from app.schemas.users import UserOut
from app.services import enrollment, system_settings, verification
from app.services.biometric_state import BiometricEvent
from app.services.users import get_visible_user

router = APIRouter(prefix="/biometrics", tags=["biometrics"])

BiometricsManage = Annotated[AuthContext, Depends(require(Permission.BIOMETRICS_MANAGE))]
BiometricsDeleteAny = Annotated[AuthContext, Depends(require(Permission.BIOMETRICS_DELETE_ANY))]
VerificationsRead = Annotated[AuthContext, Depends(require(Permission.VERIFICATIONS_READ))]
SelfService = Annotated[AuthContext, Depends(require(Permission.SELF_SERVICE))]
ALLOWED_TYPES = {"image/jpeg", "image/png"}


def get_runtime(request: Request) -> BiometricRuntime:
    runtime: BiometricRuntime | None = getattr(request.app.state, "biometrics", None)
    if runtime is None:
        raise ServiceUnavailable("Biometric service is not initialised", code="biometrics_unavailable")
    return runtime


RuntimeDep = Annotated[BiometricRuntime, Depends(get_runtime)]


def read_frames(files: list[UploadFile], settings: Settings) -> list[bytes]:
    """Frames stay in memory: each is below Starlette's 1 MiB spool threshold, so nothing touches disk."""
    if len(files) > settings.max_frames:
        raise ValidationFailed(f"At most {settings.max_frames} frames are accepted", code="too_many_frames")
    out = []
    for f in files:
        if f.content_type not in ALLOWED_TYPES:
            raise ValidationFailed("Frames must be JPEG or PNG images", code="invalid_frame_type")
        data = f.file.read(settings.max_frame_bytes + 1)
        if len(data) > settings.max_frame_bytes:
            raise ValidationFailed("A frame exceeds the size limit", code="frame_too_large")
        out.append(data)
    return out


def challenge_out(ch: LivenessChallenge, settings: Settings) -> ChallengeOut:
    return ChallengeOut(
        id=ch.id,
        purpose=ch.purpose,
        steps=ch.steps,
        expires_at=ch.expires_at,
        issued_at=ch.issued_at,
        min_frames=settings.min_frames,
        max_frames=settings.max_frames,
        recommended_fps=verification.RECOMMENDED_FPS,
        step_seconds=verification.STEP_SECONDS,
    )


def verification_out(ch: LivenessChallenge, d: Decision, settings: Settings, user: User) -> VerificationOut:
    pub = d.public()
    valid_until = None
    if d.accepted and ch.submitted_at is not None and ch.purpose.value != "enrollment":
        from datetime import timedelta  # noqa: PLC0415

        valid_until = ch.submitted_at + timedelta(seconds=settings.verification_validity_seconds)
    return VerificationOut(
        challenge_id=ch.id,
        accepted=pub["accepted"],
        category=pub["category"],
        message=pub["message"],
        hints=pub["hints"],
        biometric_status=user.biometric_status,
        valid_until=valid_until,
    )


def status_out(view: enrollment.StatusView, runtime: BiometricRuntime) -> BiometricStatusOut:
    provider = runtime.provider
    return BiometricStatusOut(
        status=view.status,
        consent=ConsentOut.model_validate(view.consent) if view.consent else None,
        consent_current=view.consent_current,
        template=TemplateMeta.model_validate(view.template) if view.template else None,
        can_start_enrollment=view.can_start,
        attempts_remaining_today=view.attempts_remaining,
        verification_available=provider.recognizer_model_id is not None
        and (provider.pad_available or not runtime.pad_required),
        pad_enforced=runtime.pad_required,
        test_fixture=provider.info.is_test_fixture,
    )


# --- self service --------------------------------------------------------------------------------------
@router.get("/consent-text", response_model=ConsentTextOut)
def consent_text(auth: AuthDep, db: DbDep, settings: SettingsDep) -> ConsentTextOut:
    values = system_settings.get_all(db, settings)
    return ConsentTextOut(version=values["consent.version"], text=values["consent.text"])


@router.post("/consent", response_model=ConsentOut)
def grant_consent(body: ConsentIn, auth: SelfService, db: DbDep, runtime: RuntimeDep, ctx: ContextDep) -> ConsentOut:
    if not body.accepted:
        raise ValidationFailed("Consent must be explicitly accepted", code="consent_not_accepted")
    return ConsentOut.model_validate(enrollment.grant_consent(db, runtime, auth.user, body.consent_version, ctx))


@router.delete("/consent", response_model=Message)
def withdraw_consent(auth: SelfService, db: DbDep, ctx: ContextDep) -> Message:
    n = enrollment.withdraw_consent(db, auth.user, ctx)
    return Message(message=f"Consent withdrawn; {n} template(s) deleted")


@router.get("/me", response_model=BiometricStatusOut)
def my_status(auth: AuthDep, db: DbDep, runtime: RuntimeDep) -> BiometricStatusOut:
    return status_out(enrollment.status_view(db, runtime, auth.user), runtime)


@router.post("/enrollments", response_model=EnrollmentStartOut, dependencies=[Depends(require_app)])
def start_enrollment(auth: SelfService, db: DbDep, runtime: RuntimeDep, ctx: ContextDep) -> EnrollmentStartOut:
    try:
        runtime.provider.require_recognition()
    except ModelUnavailable as exc:
        raise ServiceUnavailable("Face recognition is temporarily unavailable", code="model_unavailable") from exc
    session, challenge = enrollment.start_enrollment(db, runtime, auth.user, auth.session.id, ctx)
    return EnrollmentStartOut(enrollment_id=session.id, challenge=challenge_out(challenge, runtime.settings))


@router.post("/enrollments/{enrollment_id}/submit", response_model=VerificationOut, dependencies=[Depends(require_app)])
def submit_enrollment(
    enrollment_id: uuid.UUID,
    auth: SelfService,
    db: DbDep,
    runtime: RuntimeDep,
    ctx: ContextDep,
    frames: Annotated[list[UploadFile], File(description="Ordered JPEG/PNG frames")],
) -> VerificationOut:
    data = read_frames(frames, runtime.settings)
    session, decision = enrollment.submit_enrollment(db, runtime, auth.user, auth.session.id, enrollment_id, data, ctx)
    challenge = db.get(LivenessChallenge, session.challenge_id)
    assert challenge is not None
    db.refresh(auth.user)
    return verification_out(challenge, decision, runtime.settings, auth.user)


@router.delete("/me", response_model=Message)
def delete_my_biometrics(auth: SelfService, db: DbDep, ctx: ContextDep) -> Message:
    n = enrollment.delete_biometrics(db, auth.user, auth.user, ctx, reason="self_service")
    return Message(message=f"{n} template(s) deleted")


class _MinuteLimiter:
    """Per-process sliding window for the advisory quality pre-check (not a security control)."""

    def __init__(self) -> None:
        self._hits: dict[uuid.UUID, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: uuid.UUID, limit: int) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True


_quality_limiter = _MinuteLimiter()
logger = logging.getLogger("app.biometrics")


@router.post("/quality-check", response_model=QualityCheckOut, dependencies=[Depends(require_app)])
def quality_check(
    auth: AuthDep,
    runtime: RuntimeDep,
    frame: Annotated[UploadFile, File()],
) -> QualityCheckOut:
    """Advisory, stateless framing feedback before a challenge starts. Never stored; never a decision."""
    if not _quality_limiter.allow(auth.user.id, runtime.settings.quality_check_max_per_minute):
        raise RateLimited("Slow down", code="rate_limited")
    data = read_frames([frame], runtime.settings)[0]
    try:
        img = decode_frame(
            data, max_bytes=runtime.settings.max_frame_bytes, max_pixels=runtime.settings.max_frame_pixels
        )
        analyzer = runtime.provider.sequence([PoseState.CENTER], 1)
        cfg = runtime.pipeline_config()
        q = runtime.executor.run(
            lambda: assess_quality(img, analyzer.detect(0, img), cfg.quality),
            timeout=runtime.settings.inference_timeout_seconds,
        )
    except FrameDecodeError as exc:
        logger.info("quality check rejected frame", extra={"bytes": len(data), "error": str(exc)})
        raise ValidationFailed("Frame could not be decoded", code="invalid_frame") from exc
    except ModelUnavailable as exc:
        raise ServiceUnavailable("Face detection is temporarily unavailable", code="model_unavailable") from exc
    # Operational trace for capture troubleshooting: sizes and counts only, never image content.
    logger.info(
        "quality check",
        extra={
            "width": img.shape[1],
            "height": img.shape[0],
            "bytes": len(data),
            "face_count": q.face_count,
            "issues": [i.value for i in q.issues],
        },
    )
    hints = Decision(accepted=False, quality_hints=[i.value for i in q.issues]).public()["hints"]
    return QualityCheckOut(ok=not q.issues, face_count=q.face_count, hints=hints)


# --- HR / admin ------------------------------------------------------------------------------------------
@router.get("/users/{user_id}", response_model=BiometricStatusOut)
def user_status(
    user_id: uuid.UUID, auth: BiometricsManage, db: DbDep, runtime: RuntimeDep, ctx: ContextDep
) -> BiometricStatusOut:
    user = get_visible_user(db, auth.user, user_id, ctx)
    return status_out(enrollment.status_view(db, runtime, user), runtime)


@router.post("/users/{user_id}/{action}", response_model=UserOut)
def user_action(
    user_id: uuid.UUID,
    action: Literal["suspend", "reinstate", "reset"],
    auth: BiometricsManage,
    db: DbDep,
    ctx: ContextDep,
) -> UserOut:
    user = get_visible_user(db, auth.user, user_id, ctx)
    event = {"suspend": BiometricEvent.SUSPEND, "reinstate": BiometricEvent.REINSTATE, "reset": BiometricEvent.RESET}[
        action
    ]
    return UserOut.model_validate(enrollment.admin_transition(db, user, event, auth.user, ctx))


@router.delete("/users/{user_id}", response_model=Message)
def delete_user_biometrics(user_id: uuid.UUID, auth: BiometricsDeleteAny, db: DbDep, ctx: ContextDep) -> Message:
    user = get_visible_user(db, auth.user, user_id, ctx)
    n = enrollment.delete_biometrics(db, user, auth.user, ctx, reason="administrator")
    return Message(message=f"{n} template(s) deleted")


@router.get("/attempts", response_model=Page[VerificationAttemptOut])
def list_attempts(
    auth: VerificationsRead,
    db: DbDep,
    user_id: uuid.UUID | None = None,
    decision: VerificationDecision | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[VerificationAttemptOut]:
    stmt = select(VerificationAttempt, User.full_name).join(User, User.id == VerificationAttempt.user_id)
    if user_id is not None:
        stmt = stmt.where(VerificationAttempt.user_id == user_id)
    if decision is not None:
        stmt = stmt.where(VerificationAttempt.decision == decision)
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = db.execute(stmt.order_by(VerificationAttempt.created_at.desc()).limit(limit).offset(offset)).all()
    items = [
        VerificationAttemptOut.model_validate(
            {**VerificationAttemptOut.model_validate(a).model_dump(), "user_name": name}
        )
        for a, name in rows
    ]
    return Page(items=items, total=total, limit=limit, offset=offset)


__all__ = ["RuntimeDep", "challenge_out", "get_runtime", "read_frames", "router", "utcnow", "verification_out"]
