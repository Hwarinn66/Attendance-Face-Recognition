"""Vision provider: the single seam between the biometric pipeline and model inference.

``OnnxVisionProvider`` loads the real pretrained models, verifies their SHA-256 against the manifest /
conversion metadata, and runs a warm-up inference. A model that fails any step is reported as
unavailable (with the reason) and every operation that needs it raises ``ModelUnavailable`` — there is
no degraded "guess" mode.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from app.biometrics.models import (
    ARCFACE_SPEC,
    SFACE_SPEC,
    MiniFASNetEnsemble,
    OnnxRecognizer,
    PadMember,
    YuNetDetector,
    align_face,
)
from app.biometrics.types import (
    Embedding,
    FaceDetection,
    ImageBGR,
    ModelUnavailable,
    PoseState,
    ProviderInfo,
)
from app.core.config import Settings

logger = logging.getLogger("app.biometrics")


@dataclass(slots=True)
class ModelStatus:
    role: str
    model_id: str | None
    available: bool
    error: str | None = None
    license: str | None = None

    def public(self) -> dict[str, object]:
        return {"role": self.role, "model_id": self.model_id, "available": self.available}


class SequenceAnalyzer(Protocol):
    def detect(self, index: int, image: ImageBGR) -> list[FaceDetection]: ...
    def embed(self, image: ImageBGR, det: FaceDetection) -> Embedding: ...
    def pad(self, image: ImageBGR, det: FaceDetection) -> tuple[float, list[float]]: ...


class VisionProvider(Protocol):
    info: ProviderInfo

    @property
    def recognizer_model_id(self) -> str | None: ...
    @property
    def embedding_dim(self) -> int | None: ...
    @property
    def pad_available(self) -> bool: ...
    def statuses(self) -> list[ModelStatus]: ...
    def require_recognition(self) -> None: ...
    def sequence(self, steps: Sequence[PoseState], n_frames: int) -> SequenceAnalyzer: ...


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify(path: Path, expected: str | None) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"{path.name} not found in weights directory")
    if expected is None:
        raise RuntimeError(f"{path.name} has no pinned SHA-256; refusing to load an unverified model")
    actual = _sha256(path)
    if actual != expected:
        raise RuntimeError(f"{path.name} SHA-256 mismatch")


class OnnxVisionProvider:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        weights = settings.weights_dir
        manifest = json.loads((weights / "manifest.json").read_text(encoding="utf-8"))
        self._detector: YuNetDetector | None = None
        self._recognizer: OnnxRecognizer | None = None
        self._pad: MiniFASNetEnsemble | None = None
        self._status: dict[str, ModelStatus] = {}

        # Detector -----------------------------------------------------------------------------
        y = manifest["models"]["yunet"]
        try:
            _verify(weights / y["file"], y["sha256"])
            det = YuNetDetector(weights / y["file"], settings.detector_score_threshold)
            det.detect(np.zeros((240, 320, 3), dtype=np.uint8))  # warm-up / load validation
            self._detector = det
            self._status["detector"] = ModelStatus("detector", f"yunet-{y['version']}", True, license=y["license"])
        except Exception as exc:
            self._fail("detector", f"yunet-{y['version']}", exc)

        # Recogniser ---------------------------------------------------------------------------
        if settings.recognizer == "sface":
            entry, spec, model_id = manifest["models"]["sface"], SFACE_SPEC, "sface-2021dec"
        else:
            entry = dict(manifest["optional"]["arcface_w600k_mbf"])
            entry["file"] = settings.arcface_model_file
            spec, model_id = ARCFACE_SPEC, f"arcface:{Path(settings.arcface_model_file).stem}"
        try:
            _verify(weights / entry["file"], entry.get("sha256"))
            rec = OnnxRecognizer(weights / entry["file"], spec, model_id)
            rec.embed(np.zeros((112, 112, 3), dtype=np.uint8) + 127)
            self._recognizer = rec
            self._status["recognizer"] = ModelStatus("recognizer", model_id, True, license=entry.get("license"))
        except Exception as exc:
            self._fail("recognizer", model_id, exc)

        # PAD ------------------------------------------------------------------------------------
        pad_id = "minifasnet-v2+v1se@b6d5f04a"
        if settings.pad_mode == "disabled":
            self._status["pad"] = ModelStatus("pad", None, False, error="disabled by configuration (development)")
        else:
            try:
                meta_path = weights / "pad_minifasnet.meta.json"
                if not meta_path.is_file():
                    raise FileNotFoundError("pad_minifasnet.meta.json missing; run scripts/convert_minifasnet.py")
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                members = []
                for m in meta["models"]:
                    _verify(weights / m["file"], m["sha256"])
                    members.append(PadMember(weights / m["file"], float(m["crop_scale"]), int(m["input_size"])))
                pad = MiniFASNetEnsemble(members, pad_id)
                pad.live_probability(
                    np.zeros((240, 320, 3), dtype=np.uint8),
                    FaceDetection(100, 60, 120, 120, ((130, 100), (190, 100), (160, 130), (135, 160), (185, 160)), 1.0),
                )
                self._pad = pad
                self._status["pad"] = ModelStatus("pad", pad_id, True, license="Apache-2.0")
            except Exception as exc:
                self._fail("pad", pad_id, exc)

        self.info = ProviderInfo(
            name="onnx",
            detector=self._status["detector"].model_id if self._detector else None,
            recognizer=model_id if self._recognizer else None,
            pad=pad_id if self._pad else None,
        )

    def _fail(self, role: str, model_id: str, exc: Exception) -> None:
        logger.error("model unavailable", extra={"role": role, "model_id": model_id, "reason": str(exc)})
        self._status[role] = ModelStatus(role, model_id, False, error=str(exc))

    # ------------------------------------------------------------------------------------------
    @property
    def recognizer_model_id(self) -> str | None:
        return self._recognizer.model_id if self._recognizer else None

    @property
    def embedding_dim(self) -> int | None:
        return self._recognizer.dim if self._recognizer else None

    @property
    def pad_available(self) -> bool:
        return self._pad is not None

    def statuses(self) -> list[ModelStatus]:
        return list(self._status.values())

    def require_recognition(self) -> None:
        if self._detector is None or self._recognizer is None:
            raise ModelUnavailable("face detection/recognition models are not available")

    def sequence(self, steps: Sequence[PoseState], n_frames: int) -> SequenceAnalyzer:
        self.require_recognition()
        return _OnnxSequence(self)


class _OnnxSequence:
    def __init__(self, provider: OnnxVisionProvider) -> None:
        self._p = provider

    def detect(self, index: int, image: ImageBGR) -> list[FaceDetection]:
        assert self._p._detector is not None
        return self._p._detector.detect(image)

    def embed(self, image: ImageBGR, det: FaceDetection) -> Embedding:
        assert self._p._recognizer is not None
        return self._p._recognizer.embed(align_face(image, det))

    def pad(self, image: ImageBGR, det: FaceDetection) -> tuple[float, list[float]]:
        if self._p._pad is None:
            raise ModelUnavailable("PAD model is not available")
        return self._p._pad.live_probability(image, det)


_provider: VisionProvider | None = None


def get_provider(settings: Settings) -> VisionProvider:
    global _provider  # noqa: PLW0603 - models are loaded once per process
    if _provider is None:
        if settings.vision_provider == "fake":
            from app.biometrics.fake import FakeVisionProvider  # noqa: PLC0415 - test-only import

            _provider = FakeVisionProvider(settings)
        else:
            _provider = OnnxVisionProvider(settings)
    return _provider


def reset_provider() -> None:
    global _provider  # noqa: PLW0603
    _provider = None
