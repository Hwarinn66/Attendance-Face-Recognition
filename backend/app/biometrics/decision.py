"""Layer D — explicit decision policy.

A capture is accepted only if **every** gate passes; gates are evaluated in order and all failures are
recorded. Scores from unrelated detectors are never averaged together. Each probabilistic gate has an
uncertainty band that yields a "retry" outcome instead of a pass.

Gates: session validity -> frame set -> capture quality (A) -> active challenge (B) -> temporal
continuity -> passive PAD (C) -> identity (match for attendance; consistency + conflict for enrollment).
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


class Reason(enum.StrEnum):
    # session
    CHALLENGE_EXPIRED = "challenge_expired"
    CHALLENGE_ALREADY_USED = "challenge_already_used"
    CHALLENGE_TOO_FAST = "challenge_too_fast"
    # frames
    TOO_FEW_FRAMES = "too_few_frames"
    TOO_MANY_FRAMES = "too_many_frames"
    INVALID_FRAME = "invalid_frame"
    INCONSISTENT_RESOLUTION = "inconsistent_resolution"
    # A: quality
    NO_FACE = "no_face"
    MULTIPLE_FACES = "multiple_faces"
    POOR_QUALITY = "poor_quality"
    # B: challenge
    NOT_CENTERED_AT_START = "not_centered_at_start"
    CHALLENGE_INCOMPLETE = "challenge_incomplete"
    CHALLENGE_MISMATCH = "challenge_mismatch"
    FACE_LOST = "face_lost"
    # temporal
    DISCONTINUITY = "discontinuity"
    IDENTITY_INCONSISTENT = "identity_inconsistent"
    # C: PAD
    PAD_SPOOF_SUSPECTED = "pad_spoof_suspected"
    PAD_UNCERTAIN = "pad_uncertain"
    PAD_UNAVAILABLE = "pad_unavailable"
    # identity
    INSUFFICIENT_FRONTAL_FRAMES = "insufficient_frontal_frames"
    NOT_ENROLLED = "not_enrolled"
    NO_MATCH = "no_match"
    MATCH_UNCERTAIN = "match_uncertain"
    INCONSISTENT_SAMPLES = "inconsistent_samples"
    IDENTITY_CONFLICT = "identity_conflict"
    REENROLL_MISMATCH = "reenroll_mismatch"
    MODEL_UNAVAILABLE = "model_unavailable"
    PROCESSING_ERROR = "processing_error"


class Category(enum.StrEnum):
    CAPTURE = "capture"
    CHALLENGE = "challenge"
    LIVENESS = "liveness"
    IDENTITY = "identity"
    REVIEW = "review"
    SESSION = "session"
    UNAVAILABLE = "unavailable"


_CATEGORY: dict[Reason, Category] = {
    Reason.CHALLENGE_EXPIRED: Category.SESSION,
    Reason.CHALLENGE_ALREADY_USED: Category.SESSION,
    Reason.CHALLENGE_TOO_FAST: Category.CHALLENGE,
    Reason.TOO_FEW_FRAMES: Category.CAPTURE,
    Reason.TOO_MANY_FRAMES: Category.CAPTURE,
    Reason.INVALID_FRAME: Category.CAPTURE,
    Reason.INCONSISTENT_RESOLUTION: Category.CAPTURE,
    Reason.NO_FACE: Category.CAPTURE,
    Reason.MULTIPLE_FACES: Category.CAPTURE,
    Reason.POOR_QUALITY: Category.CAPTURE,
    Reason.NOT_CENTERED_AT_START: Category.CHALLENGE,
    Reason.CHALLENGE_INCOMPLETE: Category.CHALLENGE,
    Reason.CHALLENGE_MISMATCH: Category.CHALLENGE,
    Reason.FACE_LOST: Category.CAPTURE,
    Reason.DISCONTINUITY: Category.LIVENESS,
    Reason.IDENTITY_INCONSISTENT: Category.LIVENESS,
    Reason.PAD_SPOOF_SUSPECTED: Category.LIVENESS,
    Reason.PAD_UNCERTAIN: Category.LIVENESS,
    Reason.PAD_UNAVAILABLE: Category.UNAVAILABLE,
    Reason.INSUFFICIENT_FRONTAL_FRAMES: Category.CAPTURE,
    Reason.NOT_ENROLLED: Category.IDENTITY,
    Reason.NO_MATCH: Category.IDENTITY,
    Reason.MATCH_UNCERTAIN: Category.IDENTITY,
    Reason.INCONSISTENT_SAMPLES: Category.CAPTURE,
    Reason.IDENTITY_CONFLICT: Category.REVIEW,
    Reason.REENROLL_MISMATCH: Category.REVIEW,
    Reason.MODEL_UNAVAILABLE: Category.UNAVAILABLE,
    Reason.PROCESSING_ERROR: Category.UNAVAILABLE,
}

# User-facing guidance. Deliberately coarse for liveness/identity so that the response does not tell an
# attacker which detector or threshold rejected the attempt.
_MESSAGES: dict[Category, str] = {
    Category.CAPTURE: "We couldn't get a clear view of your face. Use even lighting, keep only your face in view, "
    "and hold the camera steady.",
    Category.CHALLENGE: "The requested head movements weren't detected in order. Start facing the camera, follow "
    "each prompt, and return to centre after each movement.",
    Category.LIVENESS: "We couldn't confirm a live capture. Make sure you are in front of the camera yourself and "
    "try again.",
    Category.IDENTITY: "Your face did not match your enrolled profile. Try again, or contact HR if this keeps "
    "happening.",
    Category.REVIEW: "Your enrollment needs review by HR before it can be activated.",
    Category.SESSION: "This verification session is no longer valid. Please start again.",
    Category.UNAVAILABLE: "Face verification is temporarily unavailable. Please try again later.",
}

_QUALITY_HINTS = {
    "too_dark": "The image is too dark.",
    "too_bright": "The image is over-exposed.",
    "blurry": "The image is blurry — hold still.",
    "face_too_small": "Move closer to the camera.",
    "face_too_large": "Move a little further from the camera.",
    "off_center": "Centre your face in the frame.",
    "multiple_faces": "Only one person should be in view.",
    "no_face": "No face was detected.",
    "low_confidence": "Your face is not clearly visible.",
    "implausible_landmarks": "Keep your face upright and unobstructed.",
}


def category_of(reason: Reason) -> Category:
    return _CATEGORY[reason]


@dataclass(slots=True)
class Decision:
    accepted: bool
    reasons: list[Reason] = field(default_factory=list)
    pad_score: float | None = None
    match_score: float | None = None
    details: dict[str, Any] = field(default_factory=dict)
    quality_hints: list[str] = field(default_factory=list)

    @property
    def primary_category(self) -> Category | None:
        return category_of(self.reasons[0]) if self.reasons else None

    def public(self) -> dict[str, Any]:
        """Client-safe view: outcome, coarse category, guidance. No scores or thresholds."""
        cat = self.primary_category
        return {
            "accepted": self.accepted,
            "category": cat.value if cat else None,
            "message": _MESSAGES[cat] if cat else "Verified.",
            "hints": [_QUALITY_HINTS[h] for h in self.quality_hints if h in _QUALITY_HINTS][:3],
        }


@dataclass(frozen=True, slots=True)
class BandPolicy:
    """Two-sided decision with an uncertainty band: pass >= threshold (+margin for "high-is-good"
    scores); fail below the lower bound; anything in between is "uncertain"."""

    threshold: float
    margin: float


def pad_gate(frame_scores: Sequence[float], policy: BandPolicy) -> Reason | None:
    """Pass iff mean >= threshold and every frame >= threshold - margin. Mean below threshold - margin
    is a suspected attack; anything else is uncertain."""
    if not frame_scores:
        return Reason.PAD_UNCERTAIN
    mean = sum(frame_scores) / len(frame_scores)
    lowest = min(frame_scores)
    if mean >= policy.threshold and lowest >= policy.threshold - policy.margin:
        return None
    if mean < policy.threshold - policy.margin:
        return Reason.PAD_SPOOF_SUSPECTED
    return Reason.PAD_UNCERTAIN


def match_gate(score: float, policy: BandPolicy) -> Reason | None:
    """Accept iff score >= threshold + margin; below threshold is a non-match; between is uncertain."""
    if score >= policy.threshold + policy.margin:
        return None
    if score < policy.threshold:
        return Reason.NO_MATCH
    return Reason.MATCH_UNCERTAIN
