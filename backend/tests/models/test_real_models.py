"""Component tests with the REAL pretrained models (``pytest -m models``).

They require downloaded weights (scripts/fetch_models.py + scripts/convert_minifasnet.py) and fetch a
handful of public sample images (pinned by SHA-256) into a git-ignored cache. They verify that weights load,
preprocessing matches the reference implementations, and decisions behave plausibly on known samples.
They are NOT an accuracy evaluation; see scripts/evaluate_face_verification.py and docs/BIOMETRICS.md.
"""

from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.biometrics.challenge import generate_steps
from app.biometrics.crypto import TemplateCipher
from app.biometrics.decision import Reason
from app.biometrics.models import align_face, cosine_similarity
from app.biometrics.pipeline import InferenceExecutor, analyze_sequence
from app.biometrics.provider import OnnxVisionProvider
from app.biometrics.runtime import BiometricRuntime
from app.core.config import Settings

pytestmark = pytest.mark.models

BACKEND = Path(__file__).resolve().parents[2]
CACHE = BACKEND / ".cache" / "samples"
SFAS = "https://raw.githubusercontent.com/minivision-ai/Silent-Face-Anti-Spoofing/b6d5f04ad78778917853b25c778acef6d5626d15/images/sample"
OPENCV = "https://raw.githubusercontent.com/opencv/opencv/4.x/samples/data"
SAMPLES = {
    "sfas_image_T1.jpg": (f"{SFAS}/image_T1.jpg", "f4455149f488f76205fdee5499ec5261d08ef6279a1cff7b778ea85405331e94"),
    "sfas_image_F1.jpg": (f"{SFAS}/image_F1.jpg", "4b11b5d7a8a8e4a88f5f16a5426a0a7692e39e5bb45bb03b4ebe5e1606336860"),
    "sfas_image_F2.jpg": (f"{SFAS}/image_F2.jpg", "fbbea73450ae9d9bb555c8ccac77bf39d234261fe3be4190e3ed2999690c485f"),
    "lena.jpg": (f"{OPENCV}/lena.jpg", "7de7ed51a1594fff247f4cae2301eceacf5313d6011e37b4a4c8733f7bb72c07"),
    "messi5.jpg": (f"{OPENCV}/messi5.jpg", "1d570e49654e84c7a943918537bd9e5e1ef82920152e147c834006e235be97c9"),
}


def sample(name: str) -> np.ndarray:
    url, digest = SAMPLES[name]
    path = CACHE / name
    if not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        try:
            data = urllib.request.urlopen(url, timeout=60).read()  # noqa: S310 - pinned HTTPS URL
        except OSError as exc:
            pytest.skip(f"sample image unavailable (offline?): {exc}")
        path.write_bytes(data)
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == digest, f"{name} hash mismatch"
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    assert img is not None
    return img


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings(
        database_url="postgresql+psycopg://unused",
        biometric_encryption_keys="k:" + "A" * 43 + "=",
        app_env="test",
        vision_provider="onnx",
        pad_mode="required",
    )


@pytest.fixture(scope="module")
def provider(settings: Settings) -> OnnxVisionProvider:
    p = OnnxVisionProvider(settings)
    unavailable = [s for s in p.statuses() if not s.available]
    if unavailable:
        pytest.skip("real model weights not available: " + "; ".join(f"{s.role}: {s.error}" for s in unavailable))
    return p


def test_all_models_load_with_verified_hashes(provider: OnnxVisionProvider) -> None:
    roles = {s.role: s for s in provider.statuses()}
    assert roles["detector"].model_id == "yunet-2023mar"
    assert roles["recognizer"].model_id == "sface-2021dec"
    assert roles["pad"].model_id is not None and roles["pad"].available
    assert provider.embedding_dim == 128


@pytest.mark.parametrize("name", list(SAMPLES))
def test_detector_finds_exactly_one_face(provider: OnnxVisionProvider, name: str) -> None:
    faces = provider.sequence([], 1).detect(0, sample(name))
    assert len(faces) == 1 and faces[0].score > 0.85


def test_sface_onnxruntime_matches_opencv_reference(provider: OnnxVisionProvider, settings: Settings) -> None:
    img = sample("sfas_image_T1.jpg")
    weights = settings.weights_dir
    ref = cv2.FaceRecognizerSF.create(str(weights / "face_recognition_sface_2021dec.onnx"), "")
    det = cv2.FaceDetectorYN.create(
        str(weights / "face_detection_yunet_2023mar.onnx"), "", (img.shape[1], img.shape[0])
    )
    _, rows = det.detect(img)
    ref_emb = ref.feature(ref.alignCrop(img, rows[0])).reshape(-1)
    ours = provider.sequence([], 1)
    our_det = ours.detect(0, img)[0]
    emb = ours.embed(img, our_det)
    assert cosine_similarity(emb, ref_emb) > 0.999


def test_embedding_separates_different_people(provider: OnnxVisionProvider, settings: Settings) -> None:
    seq = provider.sequence([], 1)
    embs = {}
    for name in ("lena.jpg", "messi5.jpg", "sfas_image_T1.jpg"):
        img = sample(name)
        embs[name] = seq.embed(img, seq.detect(0, img)[0])
    flipped = cv2.flip(sample("lena.jpg"), 1)
    flip_emb = seq.embed(flipped, seq.detect(0, flipped)[0])
    assert settings.match_threshold is not None
    assert cosine_similarity(embs["lena.jpg"], flip_emb) > settings.match_threshold + settings.match_uncertainty_margin
    for a, b in (("lena.jpg", "messi5.jpg"), ("lena.jpg", "sfas_image_T1.jpg"), ("messi5.jpg", "sfas_image_T1.jpg")):
        assert cosine_similarity(embs[a], embs[b]) < settings.match_threshold, (a, b)


def test_alignment_produces_112_crop(provider: OnnxVisionProvider) -> None:
    img = sample("lena.jpg")
    det = provider.sequence([], 1).detect(0, img)[0]
    assert align_face(img, det).shape == (112, 112, 3)


def test_pad_on_reference_samples(provider: OnnxVisionProvider) -> None:
    """Upstream sample labels: T1 genuine, F1/F2 attacks. Checks preprocessing fidelity, not accuracy."""
    seq = provider.sequence([], 1)
    scores = {}
    for name in ("sfas_image_T1.jpg", "sfas_image_F1.jpg", "sfas_image_F2.jpg"):
        img = sample(name)
        scores[name], _ = seq.pad(img, seq.detect(0, img)[0])
    assert scores["sfas_image_T1.jpg"] > 0.9
    assert scores["sfas_image_F1.jpg"] < 0.5
    assert scores["sfas_image_F2.jpg"] < 0.1


def test_replayed_still_photo_fails_active_challenge(provider: OnnxVisionProvider, settings: Settings) -> None:
    """A static image (e.g. a photo held up or injected) repeated for the whole sequence cannot perform the
    randomised movements, so the real pipeline rejects it before identity or PAD are even evaluated."""
    img = sample("sfas_image_T1.jpg")
    runtime = BiometricRuntime(settings, provider, InferenceExecutor(1), TemplateCipher(settings.keyring))
    steps = generate_steps(3)
    frames = [img.copy() for _ in range(30)]
    ev = analyze_sequence(frames, steps, provider.sequence(steps, len(frames)), runtime.pipeline_config())
    runtime.shutdown()
    # Rejected by the active-challenge layer (which reason depends on the photo's head pose).
    assert ev.reasons in ([Reason.CHALLENGE_INCOMPLETE], [Reason.NOT_CENTERED_AT_START])
    assert ev.probe is None


def test_detector_handles_blank_and_noise(provider: OnnxVisionProvider) -> None:
    seq = provider.sequence([], 1)
    assert seq.detect(0, np.zeros((480, 640, 3), np.uint8)) == []
    noise = np.random.default_rng(0).integers(0, 255, (480, 640, 3), dtype=np.uint8)
    assert all(d.score < 0.85 for d in seq.detect(0, noise))
