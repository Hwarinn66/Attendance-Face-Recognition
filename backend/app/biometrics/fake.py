"""TEST FIXTURE ONLY — deterministic stand-in for model inference.

This module exists so that API, frontend and E2E tests can drive the full enrollment/attendance flow
without real faces. It replaces *only* model outputs (detections, embeddings, PAD scores); quality
checks, pose estimation, challenge validation, temporal checks and the decision policy all run the real
code. It refuses to load unless ``APP_ENV=test``, and every decision it influences is recorded with
provider name ``fake-test-fixture``. Results produced with it say nothing about real biometric accuracy.

Frame colour protocol (mean BGR of the frame; frames should carry noise texture so the real
sharpness check passes):

* mean brightness < 40          -> no face
* green-dominant                -> live face that follows the challenge; identity bucket = round(R / 32)
* blue-dominant                 -> live face that stays frontal (fails any movement challenge)
* red-dominant                  -> face that follows the challenge but scores as a presentation attack
* anything else                 -> no face
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from app.biometrics.challenge import expected_pattern
from app.biometrics.models import l2_normalize
from app.biometrics.provider import ModelStatus, SequenceAnalyzer
from app.biometrics.types import Embedding, FaceDetection, ImageBGR, PoseState, ProviderInfo
from app.core.config import Settings

FAKE_NAME = "fake-test-fixture"
FAKE_DIM = 128

_POSE_TARGETS: dict[PoseState, tuple[float, float]] = {
    PoseState.CENTER: (0.0, 0.5),
    PoseState.LEFT: (0.32, 0.5),
    PoseState.RIGHT: (-0.32, 0.5),
    PoseState.UP: (0.0, 0.28),
    PoseState.DOWN: (0.0, 0.72),
}


def classify_color(image: ImageBGR) -> tuple[str, int]:
    b, g, r = (float(v) for v in image.reshape(-1, 3).mean(axis=0))
    if (b + g + r) / 3 < 40:
        return "none", 0
    if g > r + 30 and g > b + 30:
        return "live", round(r / 32)
    if b > r + 30 and b > g + 30:
        return "static", round(g / 32)
    if r > g + 30 and r > b + 30:
        return "spoof", round(g / 32)
    return "none", 0


def fake_embedding(identity: int) -> Embedding:
    rng = np.random.default_rng(1000 + identity)
    return l2_normalize(rng.normal(size=FAKE_DIM))


def synth_detection(width: int, height: int, yaw: float, pitch: float) -> FaceDetection:
    """Landmarks constructed so that ``estimate_pose`` returns exactly (yaw, pitch)."""
    fw = 0.42 * width
    cx, cy = width / 2, height / 2
    iod, length = 0.40 * fw, 0.36 * fw
    r_eye = (cx - iod / 2, cy - 0.12 * fw)
    l_eye = (cx + iod / 2, cy - 0.12 * fw)
    eye_mid_y = cy - 0.12 * fw
    nose = (cx + yaw * iod, eye_mid_y + pitch * length)
    mouth_y = eye_mid_y + length
    r_mouth = (cx - 0.15 * fw, mouth_y)
    l_mouth = (cx + 0.15 * fw, mouth_y)
    return FaceDetection(
        x=cx - fw / 2,
        y=cy - 0.55 * fw,
        w=fw,
        h=1.15 * fw,
        landmarks=(r_eye, l_eye, nose, r_mouth, l_mouth),
        score=0.97,
    )


class FakeVisionProvider:
    def __init__(self, settings: Settings) -> None:
        if settings.app_env != "test":
            raise RuntimeError("FakeVisionProvider is a test fixture and requires APP_ENV=test")
        self._pad_enabled = settings.pad_mode == "required"
        self.info = ProviderInfo(
            name=FAKE_NAME,
            detector=FAKE_NAME,
            recognizer=FAKE_NAME,
            pad=FAKE_NAME if self._pad_enabled else None,
            is_test_fixture=True,
        )

    @property
    def recognizer_model_id(self) -> str | None:
        return FAKE_NAME

    @property
    def embedding_dim(self) -> int | None:
        return FAKE_DIM

    @property
    def pad_available(self) -> bool:
        return self._pad_enabled

    def statuses(self) -> list[ModelStatus]:
        return [
            ModelStatus("detector", FAKE_NAME, True, license="test fixture"),
            ModelStatus("recognizer", FAKE_NAME, True, license="test fixture"),
            ModelStatus("pad", FAKE_NAME if self._pad_enabled else None, self._pad_enabled, license="test fixture"),
        ]

    def require_recognition(self) -> None:
        return None

    def sequence(self, steps: Sequence[PoseState], n_frames: int) -> SequenceAnalyzer:
        return _FakeSequence(list(steps), n_frames)


class _FakeSequence:
    def __init__(self, steps: list[PoseState], n_frames: int) -> None:
        self._pattern = expected_pattern(steps)
        self._per_segment = max(1, n_frames // len(self._pattern))

    def _scripted_state(self, index: int) -> PoseState:
        return self._pattern[min(index // self._per_segment, len(self._pattern) - 1)]

    def detect(self, index: int, image: ImageBGR) -> list[FaceDetection]:
        kind, _ = classify_color(image)
        if kind == "none":
            return []
        h, w = image.shape[:2]
        state = PoseState.CENTER if kind == "static" else self._scripted_state(index)
        yaw, pitch = _POSE_TARGETS[state]
        return [synth_detection(w, h, yaw, pitch)]

    def embed(self, image: ImageBGR, det: FaceDetection) -> Embedding:
        _, identity = classify_color(image)
        return fake_embedding(identity)

    def pad(self, image: ImageBGR, det: FaceDetection) -> tuple[float, list[float]]:
        kind, _ = classify_color(image)
        score = 0.05 if kind == "spoof" else 0.97
        return score, [score, score]
