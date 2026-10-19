from __future__ import annotations

import enum
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

Embedding = npt.NDArray[np.float32]
ImageBGR = npt.NDArray[np.uint8]


class ModelUnavailable(RuntimeError):
    """A required model is not loaded. Callers must fail closed."""


class FrameDecodeError(ValueError):
    pass


class PoseState(enum.StrEnum):
    CENTER = "center"
    LEFT = "turn_left"
    RIGHT = "turn_right"
    UP = "look_up"
    DOWN = "look_down"
    TRANSITION = "transition"
    MISSING = "missing"


CHALLENGE_ACTIONS: tuple[PoseState, ...] = (PoseState.LEFT, PoseState.RIGHT, PoseState.UP, PoseState.DOWN)


class QualityIssue(enum.StrEnum):
    NO_FACE = "no_face"
    MULTIPLE_FACES = "multiple_faces"
    LOW_CONFIDENCE = "low_confidence"
    FACE_TOO_SMALL = "face_too_small"
    FACE_TOO_LARGE = "face_too_large"
    TOO_DARK = "too_dark"
    TOO_BRIGHT = "too_bright"
    BLURRY = "blurry"
    OFF_CENTER = "off_center"
    IMPLAUSIBLE_LANDMARKS = "implausible_landmarks"


@dataclass(frozen=True, slots=True)
class FaceDetection:
    """Bounding box (x, y, w, h) in pixels and 5 landmarks in YuNet order:
    subject's right eye, left eye, nose tip, right mouth corner, left mouth corner."""

    x: float
    y: float
    w: float
    h: float
    landmarks: tuple[tuple[float, float], ...]
    score: float

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.w / 2, self.y + self.h / 2)

    def landmarks_array(self) -> npt.NDArray[np.float32]:
        return np.asarray(self.landmarks, dtype=np.float32)


@dataclass(frozen=True, slots=True)
class Pose:
    """Geometric head-pose ratios (not degrees). yaw > 0: nose toward image right, i.e. the subject turned
    to *their* left in an un-mirrored camera frame. pitch: nose position along the eye->mouth axis
    (smaller = looking up)."""

    yaw: float
    pitch: float


@dataclass(slots=True)
class FrameAnalysis:
    index: int
    width: int
    height: int
    face_count: int
    detection: FaceDetection | None
    issues: tuple[QualityIssue, ...]
    brightness: float | None = None
    sharpness: float | None = None
    pose: Pose | None = None
    embedding: Embedding | None = None
    image: ImageBGR | None = field(default=None, repr=False)

    @property
    def usable(self) -> bool:
        return self.detection is not None and not self.issues


@dataclass(frozen=True, slots=True)
class ProviderInfo:
    name: str
    detector: str | None
    recognizer: str | None
    pad: str | None
    is_test_fixture: bool = False
