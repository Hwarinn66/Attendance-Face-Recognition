"""Pipeline wiring with the TEST FIXTURE provider. These tests check that the real quality, pose,
challenge, temporal and decision code is exercised end-to-end; they do NOT measure biometric accuracy."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from app.biometrics.challenge import ChallengeConfig
from app.biometrics.decision import BandPolicy, Reason
from app.biometrics.fake import FakeVisionProvider
from app.biometrics.imaging import decode_frame
from app.biometrics.pipeline import PipelineConfig, analyze_sequence
from app.biometrics.quality import QualityThresholds
from app.biometrics.types import FrameDecodeError, PoseState
from app.core.config import Settings
from tests.frames import DARK, LIVE, SPOOF, STATIC, colored_frame, frame_set

STEPS = [PoseState.LEFT, PoseState.DOWN, PoseState.RIGHT]


def cfg(pad_required: bool = True) -> PipelineConfig:
    s = Settings(database_url="postgresql+psycopg://x", biometric_encryption_keys="k:" + "A" * 43 + "=", app_env="test")
    return PipelineConfig(
        quality=QualityThresholds(
            s.quality_min_detection_score,
            s.quality_min_face_width_ratio,
            s.quality_max_face_width_ratio,
            s.quality_min_brightness,
            s.quality_max_brightness,
            s.quality_min_sharpness,
            s.quality_max_center_offset,
            s.detector_score_threshold,
        ),
        challenge=ChallengeConfig(
            s.challenge_yaw_threshold,
            s.challenge_pitch_threshold,
            s.challenge_center_yaw_tolerance,
            s.challenge_center_pitch_tolerance,
            s.challenge_min_pose_frames,
            s.challenge_max_missing_ratio,
            s.challenge_max_consecutive_missing,
        ),
        max_center_jump=s.temporal_max_center_jump,
        max_scale_jump=s.temporal_max_scale_jump,
        identity_threshold=0.363,
        pad_policy=BandPolicy(s.pad_threshold, s.pad_uncertainty_margin),
        pad_required=pad_required,
        pad_max_frames=s.pad_max_frames,
        min_frontal_frames=3,
    )


def provider(pad_mode: str = "required") -> FakeVisionProvider:
    return FakeVisionProvider(
        Settings(
            database_url="postgresql+psycopg://x",
            app_env="test",
            pad_mode=pad_mode,
            biometric_encryption_keys="k:" + "A" * 43 + "=",
        )
    )


def run(frames: list[bytes], pad_required: bool = True):  # type: ignore[no-untyped-def]
    imgs = [decode_frame(f, max_bytes=1 << 20, max_pixels=1 << 22) for f in frames]
    p = provider("required" if pad_required else "disabled")
    return analyze_sequence(imgs, STEPS, p.sequence(STEPS, len(imgs)), cfg(pad_required))


def test_fake_provider_refuses_non_test_env() -> None:
    with pytest.raises(RuntimeError):
        FakeVisionProvider(
            Settings(
                database_url="postgresql+psycopg://x",
                app_env="development",
                biometric_encryption_keys="k:" + "A" * 43 + "=",
            )
        )


def test_live_sequence_passes_all_layers() -> None:
    ev = run(frame_set(LIVE))
    assert ev.reasons == []
    assert ev.challenge is not None and ev.challenge.passed
    assert ev.probe is not None and ev.pad_evaluated and ev.pad_mean is not None and ev.pad_mean > 0.9


def test_static_face_fails_challenge() -> None:
    ev = run(frame_set(STATIC))
    assert ev.reasons == [Reason.CHALLENGE_INCOMPLETE]
    assert ev.probe is None  # no embeddings extracted for a failed liveness challenge


def test_spoof_fails_pad() -> None:
    assert run(frame_set(SPOOF)).reasons == [Reason.PAD_SPOOF_SUSPECTED]


def test_no_face() -> None:
    assert run(frame_set(DARK)).reasons == [Reason.NO_FACE]


OTHER = (20, 230, 128)  # another identity bucket
# 28 frames over the pattern centre, left, centre, down, centre, right, centre: 4 frames each.
TURNS = (slice(4, 8), slice(12, 16), slice(20, 24))


def others(n: int, seed: int = 100) -> list[bytes]:
    return [colored_frame(OTHER, seed=seed + i) for i in range(n)]


def test_identity_swap_mid_sequence_detected() -> None:
    frames = frame_set(LIVE)
    frames[10:16] = others(6)  # the second frontal view and the next movement
    assert run(frames).reasons == [Reason.IDENTITY_INCONSISTENT]


def test_identity_swap_in_the_final_frontal_view_detected() -> None:
    frames = frame_set(LIVE)
    frames[24:28] = others(4)
    ev = run(frames)
    assert ev.reasons == [Reason.IDENTITY_INCONSISTENT]
    assert ev.details["identity_continuity_min"] < 0.363


def test_turned_faces_are_not_compared_by_identity() -> None:
    # The recogniser is unreliable on turned faces (a genuine user scored 0.09 against their own frontal face
    # on a real phone), so turned frames are checked for spatial continuity only, never by embedding.
    frames = frame_set(LIVE)
    for s in TURNS:
        frames[s] = others(4, seed=200 + s.start)
    ev = run(frames)
    assert ev.reasons == []
    assert ev.details["frontal_segments"] == [4, 4, 4, 4]
    assert ev.details["identity_continuity_min"] > 0.99


def test_first_and_last_frontal_views_must_be_usable() -> None:
    ok, buf = cv2.imencode(".jpg", np.full((360, 480, 3), LIVE, dtype=np.uint8))  # flat image: blurry
    assert ok
    blurry = bytes(buf.tobytes())
    frames = frame_set(LIVE)
    frames[24:28] = [blurry] * 4  # the final centre is tracked (pose) but has no usable face
    ev = run(frames)
    assert ev.challenge is not None and ev.challenge.passed
    assert ev.reasons == [Reason.INSUFFICIENT_FRONTAL_FRAMES]


def test_pad_disabled_is_recorded() -> None:
    ev = run(frame_set(LIVE), pad_required=False)
    assert ev.reasons == [] and not ev.pad_evaluated
    assert "disabled" in str(ev.details["pad"])


def test_inconsistent_resolution() -> None:
    frames = frame_set(LIVE)
    frames[3] = colored_frame(LIVE, width=640, height=480)
    assert run(frames).reasons == [Reason.INCONSISTENT_RESOLUTION]


@pytest.mark.parametrize("payload", [b"", b"GIF89a....", b"\xff\xd8\xffgarbage"])
def test_decode_rejects_bad_frames(payload: bytes) -> None:
    with pytest.raises(FrameDecodeError):
        decode_frame(payload, max_bytes=1 << 20, max_pixels=1 << 22)


def test_decode_rejects_oversize() -> None:
    with pytest.raises(FrameDecodeError):
        decode_frame(colored_frame(LIVE), max_bytes=100, max_pixels=1 << 22)
    with pytest.raises(FrameDecodeError):
        decode_frame(colored_frame(LIVE), max_bytes=1 << 20, max_pixels=1000)
