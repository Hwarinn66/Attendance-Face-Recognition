"""Sequence analysis: runs layers A (quality), B (challenge), temporal checks and C (PAD) over the
ordered frames of one challenge submission and produces evidence for the decision policy."""

from __future__ import annotations

import concurrent.futures
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.biometrics.challenge import ChallengeConfig, ChallengeResult, validate_sequence
from app.biometrics.decision import BandPolicy, Reason, pad_gate
from app.biometrics.pose import estimate_pose
from app.biometrics.provider import SequenceAnalyzer
from app.biometrics.quality import QualityThresholds, assess_quality
from app.biometrics.temporal import centroid, identity_continuity, spatial_continuity
from app.biometrics.types import (
    Embedding,
    FaceDetection,
    FrameAnalysis,
    ImageBGR,
    ModelUnavailable,
    Pose,
    PoseState,
    QualityIssue,
)

_CHALLENGE_REASON = {
    "no_frames": Reason.TOO_FEW_FRAMES,
    "face_lost": Reason.FACE_LOST,
    "incomplete": Reason.CHALLENGE_INCOMPLETE,
    "sequence_mismatch": Reason.CHALLENGE_MISMATCH,
    "not_centered_at_start": Reason.NOT_CENTERED_AT_START,
}
# Issues that make a frame unusable for identity/PAD but not for coarse pose tracking.
_POSE_TOLERATED = {
    QualityIssue.LOW_CONFIDENCE,
    QualityIssue.OFF_CENTER,
    QualityIssue.BLURRY,
    QualityIssue.FACE_TOO_SMALL,
    QualityIssue.FACE_TOO_LARGE,
}
_CONTINUITY_BLOCKING = {
    QualityIssue.BLURRY,
    QualityIssue.TOO_DARK,
    QualityIssue.TOO_BRIGHT,
    QualityIssue.IMPLAUSIBLE_LANDMARKS,
}


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    quality: QualityThresholds
    challenge: ChallengeConfig
    max_center_jump: float
    max_scale_jump: float
    identity_threshold: float
    pad_policy: BandPolicy
    pad_required: bool
    pad_max_frames: int
    min_frontal_frames: int
    max_frontal_embeddings: int = 8
    max_continuity_embeddings: int = 10
    max_multi_face_frames: int = 1


@dataclass(slots=True)
class SequenceEvidence:
    reasons: list[Reason] = field(default_factory=list)
    frames: list[FrameAnalysis] = field(default_factory=list)
    challenge: ChallengeResult | None = None
    frontal_embeddings: list[Embedding] = field(default_factory=list)
    probe: Embedding | None = None
    pad_scores: list[float] = field(default_factory=list)
    pad_evaluated: bool = False
    quality_hints: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def pad_mean(self) -> float | None:
        return float(np.mean(self.pad_scores)) if self.pad_scores else None


def _evenly(indices: Sequence[int], k: int) -> list[int]:
    if len(indices) <= k:
        return list(indices)
    picks = np.linspace(0, len(indices) - 1, k).round().astype(int)
    return [indices[i] for i in sorted(set(picks.tolist()))]


def analyze_sequence(
    images: Sequence[ImageBGR],
    steps: Sequence[PoseState],
    analyzer: SequenceAnalyzer,
    cfg: PipelineConfig,
) -> SequenceEvidence:
    ev = SequenceEvidence()
    shapes = {img.shape for img in images}
    if len(shapes) != 1:
        ev.reasons.append(Reason.INCONSISTENT_RESOLUTION)
        return ev

    poses: list[Pose | None] = []
    detections: list[FaceDetection | None] = []
    issue_counter: Counter[str] = Counter()
    multi_face_frames = 0
    no_face_frames = 0
    for i, img in enumerate(images):
        h, w = img.shape[:2]
        q = assess_quality(img, analyzer.detect(i, img), cfg.quality)
        pose = None
        if q.primary is not None and set(q.issues) <= _POSE_TOLERATED:
            pose = estimate_pose(q.primary)
        ev.frames.append(
            FrameAnalysis(
                index=i,
                width=w,
                height=h,
                face_count=q.face_count,
                detection=q.primary,
                issues=q.issues,
                brightness=q.brightness,
                sharpness=q.sharpness,
                pose=pose,
            )
        )
        poses.append(pose)
        detections.append(q.primary if pose is not None else None)
        issue_counter.update(issue.value for issue in q.issues)
        multi_face_frames += QualityIssue.MULTIPLE_FACES in q.issues
        no_face_frames += QualityIssue.NO_FACE in q.issues

    ev.quality_hints = [k for k, _ in issue_counter.most_common(3)]
    ev.details["issue_counts"] = dict(issue_counter)
    ev.details["frames"] = len(images)

    if multi_face_frames > cfg.max_multi_face_frames:
        ev.reasons.append(Reason.MULTIPLE_FACES)
        return ev
    if no_face_frames > len(images) / 2:
        ev.reasons.append(Reason.NO_FACE)
        return ev

    # Layer B -------------------------------------------------------------------------------------
    ev.challenge = validate_sequence(poses, steps, cfg.challenge)
    ev.details["challenge"] = ev.challenge.summary()
    if not ev.challenge.passed:
        ev.reasons.append(_CHALLENGE_REASON.get(ev.challenge.reason or "", Reason.CHALLENGE_MISMATCH))
        return ev

    # Temporal: spatial continuity ---------------------------------------------------------------------
    ok, jump, scale = spatial_continuity(detections, cfg.max_center_jump, cfg.max_scale_jump)
    ev.details["temporal"] = {"max_center_jump": round(jump, 3), "max_scale_jump": round(scale, 3)}
    if not ok:
        ev.reasons.append(Reason.DISCONTINUITY)
        return ev

    # Frontal, fully-usable frames -> probe embedding -------------------------------------------------
    states = ev.challenge.states
    frontal = [f.index for f in ev.frames if f.usable and states[f.index] == PoseState.CENTER]
    ev.details["frontal_frames"] = len(frontal)
    if len(frontal) < cfg.min_frontal_frames:
        ev.reasons.append(Reason.INSUFFICIENT_FRONTAL_FRAMES)
        return ev
    for idx in _evenly(frontal, cfg.max_frontal_embeddings):
        det = ev.frames[idx].detection
        assert det is not None
        emb = analyzer.embed(images[idx], det)
        ev.frames[idx].embedding = emb
        ev.frontal_embeddings.append(emb)
    ev.probe = centroid(ev.frontal_embeddings)

    # Temporal: identity continuity across the movement frames ----------------------------------
    moving = [
        f.index
        for f in ev.frames
        if f.detection is not None
        and f.pose is not None
        and states[f.index] != PoseState.CENTER
        and not set(f.issues) & _CONTINUITY_BLOCKING
    ]
    cont_embs = []
    for idx in _evenly(moving, cfg.max_continuity_embeddings):
        det = ev.frames[idx].detection
        assert det is not None
        cont_embs.append(analyzer.embed(images[idx], det))
    ok_id, worst = identity_continuity(ev.frontal_embeddings + cont_embs, ev.probe, cfg.identity_threshold)
    ev.details["identity_continuity_min"] = round(worst, 4)
    if not ok_id:
        ev.reasons.append(Reason.IDENTITY_INCONSISTENT)
        return ev

    # Layer C: passive PAD ----------------------------------------------------------------------------
    if cfg.pad_required:
        try:
            for idx in _evenly(frontal, cfg.pad_max_frames):
                det = ev.frames[idx].detection
                assert det is not None
                score, _ = analyzer.pad(images[idx], det)
                ev.pad_scores.append(score)
        except ModelUnavailable:
            ev.reasons.append(Reason.PAD_UNAVAILABLE)
            return ev
        ev.pad_evaluated = True
        ev.details["pad"] = {
            "frames": len(ev.pad_scores),
            "mean": round(float(np.mean(ev.pad_scores)), 4),
            "min": round(min(ev.pad_scores), 4),
        }
        reason = pad_gate(ev.pad_scores, cfg.pad_policy)
        if reason is not None:
            ev.reasons.append(reason)
    else:
        ev.details["pad"] = "disabled (development configuration)"
    return ev


class InferenceExecutor:
    """Bounded pool for CPU-bound inference so request threads never pile up unbounded model work."""

    def __init__(self, workers: int) -> None:
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="inference")

    def run(self, fn: Any, *args: Any, timeout: float) -> Any:
        future = self._pool.submit(fn, *args)
        return future.result(timeout=timeout)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
