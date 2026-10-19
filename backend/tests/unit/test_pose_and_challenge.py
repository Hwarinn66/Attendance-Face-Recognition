"""Pose geometry is tested against landmarks projected from a generic 3D face model, and the challenge
validator against synthetic pose sequences (no models involved)."""

from __future__ import annotations

import itertools
import math
from collections import Counter

import numpy as np
import pytest

from app.biometrics.challenge import (
    ChallengeConfig,
    compress,
    expected_pattern,
    generate_steps,
    validate_sequence,
)
from app.biometrics.pose import classify_pose, estimate_pose, plausible_landmarks
from app.biometrics.types import CHALLENGE_ACTIONS, FaceDetection, Pose, PoseState
from app.core.config import Settings

# Generic 3D landmarks (mm): x image-right, y down, z toward camera. Order matches YuNet.
FACE_3D = np.array([[-32, -30, -22], [32, -30, -22], [0, 8, 0], [-24, 40, -25], [24, 40, -25]], dtype=np.float64)

YAW_T, PITCH_T, C_YAW, C_PITCH = 0.15, 0.10, 0.07, 0.06


def project(yaw_deg: float = 0.0, pitch_deg: float = 0.0, scale: float = 3.0) -> FaceDetection:
    t, p = math.radians(yaw_deg), math.radians(pitch_deg)
    ry = np.array([[math.cos(t), 0, math.sin(t)], [0, 1, 0], [-math.sin(t), 0, math.cos(t)]])
    rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    pts = (rx @ ry @ FACE_3D.T).T
    xy = pts[:, :2] * scale + np.array([320, 240])
    x0, y0 = xy.min(axis=0) - 40
    x1, y1 = xy.max(axis=0) + 40
    return FaceDetection(x0, y0, x1 - x0, y1 - y0, tuple((float(a), float(b)) for a, b in xy), 0.95)


def classify(det: FaceDetection, baseline: Pose) -> PoseState:
    pose = estimate_pose(det)
    assert pose is not None
    return classify_pose(
        pose, baseline, yaw_threshold=YAW_T, pitch_threshold=PITCH_T, center_yaw_tol=C_YAW, center_pitch_tol=C_PITCH
    )


class TestPoseGeometry:
    def test_frontal_yaw_is_zero(self) -> None:
        pose = estimate_pose(project())
        assert pose is not None
        assert abs(pose.yaw) < 1e-6

    def test_yaw_monotonic_and_signed(self) -> None:
        yaws = [estimate_pose(project(yaw_deg=a)).yaw for a in range(-40, 41, 5)]  # type: ignore[union-attr]
        assert all(b > a for a, b in itertools.pairwise(yaws))
        assert yaws[0] < 0 < yaws[-1]

    def test_pitch_monotonic(self) -> None:
        # positive pitch_deg = chin up -> nose moves toward the eye line -> ratio decreases
        ps = [estimate_pose(project(pitch_deg=a)).pitch for a in range(-30, 31, 5)]  # type: ignore[union-attr]
        assert all(b < a for a, b in itertools.pairwise(ps))

    @pytest.mark.parametrize(
        ("yaw", "pitch", "expected"),
        [
            (0, 0, PoseState.CENTER),
            (6, 0, PoseState.CENTER),
            (0, 6, PoseState.CENTER),
            (30, 0, PoseState.LEFT),  # subject turns to their left: nose to image right
            (-30, 0, PoseState.RIGHT),
            (0, 22, PoseState.UP),
            (0, -22, PoseState.DOWN),
            (17, 0, PoseState.TRANSITION),
        ],
    )
    def test_classification_relative_to_baseline(self, yaw: float, pitch: float, expected: PoseState) -> None:
        baseline = estimate_pose(project())
        assert baseline is not None
        assert classify(project(yaw, pitch), baseline) == expected

    def test_scale_invariant(self) -> None:
        a = estimate_pose(project(25, 10, scale=2.0))
        b = estimate_pose(project(25, 10, scale=6.0))
        assert a is not None and b is not None
        assert a.yaw == pytest.approx(b.yaw, abs=1e-6)
        assert a.pitch == pytest.approx(b.pitch, abs=1e-6)

    def test_plausibility_rejects_inverted_face(self) -> None:
        det = project()
        flipped = FaceDetection(
            det.x, det.y, det.w, det.h, tuple((x, 2 * 240 - y) for x, y in det.landmarks), det.score
        )
        assert plausible_landmarks(det)
        assert not plausible_landmarks(flipped)


CFG = ChallengeConfig(
    yaw_threshold=YAW_T,
    pitch_threshold=PITCH_T,
    center_yaw_tolerance=C_YAW,
    center_pitch_tolerance=C_PITCH,
    min_pose_frames=2,
    max_missing_ratio=0.25,
    max_consecutive_missing=4,
)

POSES = {
    PoseState.CENTER: Pose(0.0, 0.5),
    PoseState.LEFT: Pose(0.3, 0.5),
    PoseState.RIGHT: Pose(-0.3, 0.5),
    PoseState.UP: Pose(0.0, 0.3),
    PoseState.DOWN: Pose(0.0, 0.7),
}


def sequence(states: list[PoseState], per: int = 4) -> list[Pose | None]:
    out: list[Pose | None] = []
    for s in states:
        out.extend([POSES[s]] * per)
    return out


class TestChallengeGeneration:
    def test_steps_valid_and_no_immediate_repeat(self) -> None:
        for _ in range(500):
            steps = generate_steps(3)
            assert len(steps) == 3
            assert all(s in CHALLENGE_ACTIONS for s in steps)
            assert all(a != b for a, b in itertools.pairwise(steps))

    def test_configured_actions_only(self) -> None:
        actions = (PoseState.LEFT, PoseState.RIGHT, PoseState.DOWN)
        seen = Counter(tuple(generate_steps(3, actions)) for _ in range(1000))
        assert all(s in actions for seq in seen for s in seq)
        assert all(a != b for seq in seen for a, b in itertools.pairwise(seq))
        assert len(seen) == 12  # 3 * 2 * 2, all reachable
        with pytest.raises(ValueError):
            generate_steps(3, (PoseState.LEFT,))

    def test_default_settings_exclude_look_up(self) -> None:
        s = Settings(database_url="postgresql+psycopg://x", biometric_encryption_keys="k:" + "A" * 43 + "=")
        assert s.challenge_action_states == (PoseState.LEFT, PoseState.RIGHT, PoseState.DOWN)
        with pytest.raises(ValueError, match="unknown challenge actions"):
            Settings(
                database_url="postgresql+psycopg://x",
                biometric_encryption_keys="k:" + "A" * 43 + "=",
                challenge_actions="turn_left,spin",
            )

    def test_steps_are_not_constant(self) -> None:
        seen = Counter(tuple(generate_steps(3)) for _ in range(2000))
        # 36 possible sequences; a uniform generator hits (almost) all of them in 2000 draws
        assert len(seen) >= 30

    def test_expected_pattern(self) -> None:
        assert expected_pattern([PoseState.LEFT, PoseState.UP]) == [
            PoseState.CENTER,
            PoseState.LEFT,
            PoseState.CENTER,
            PoseState.UP,
            PoseState.CENTER,
        ]


class TestChallengeValidation:
    steps = [PoseState.LEFT, PoseState.UP, PoseState.RIGHT]

    def test_correct_sequence_passes(self) -> None:
        r = validate_sequence(sequence(expected_pattern(self.steps)), self.steps, CFG)
        assert r.passed, r.reason

    def test_summary_records_pose_deltas_for_calibration(self) -> None:
        poses = sequence(expected_pattern(self.steps))
        poses.insert(5, None)  # a frame without a usable face
        r = validate_sequence(poses, self.steps, CFG)
        summary = r.summary()
        trace = summary["trace"]
        assert isinstance(trace, list) and len(trace) == len(poses) and trace[5] is None
        assert trace[0] == [0.0, 0.0]  # relative to the baseline
        rng = summary["pose_range"]
        assert isinstance(rng, dict)
        present = [t for t in trace if t is not None]
        assert rng["yaw"] == [min(t[0] for t in present), max(t[0] for t in present)]
        assert rng["pitch"] == [min(t[1] for t in present), max(t[1] for t in present)]
        assert rng["yaw"] != [0.0, 0.0] or rng["pitch"] != [0.0, 0.0]  # the movements are captured

    def test_wrong_order_fails(self) -> None:
        wrong = [PoseState.RIGHT, PoseState.UP, PoseState.LEFT]
        r = validate_sequence(sequence(expected_pattern(wrong)), self.steps, CFG)
        assert not r.passed and r.reason == "sequence_mismatch"

    def test_static_face_fails(self) -> None:
        r = validate_sequence([POSES[PoseState.CENTER]] * 28, self.steps, CFG)
        assert not r.passed and r.reason == "incomplete"

    def test_single_final_frame_in_pose_is_not_enough(self) -> None:
        poses = [*sequence([PoseState.CENTER]), POSES[PoseState.LEFT]]  # one frame of movement
        r = validate_sequence(poses, [PoseState.LEFT], CFG)
        assert not r.passed

    def test_missing_return_to_center_fails(self) -> None:
        states = [PoseState.CENTER, PoseState.LEFT, PoseState.UP, PoseState.RIGHT, PoseState.CENTER]
        r = validate_sequence(sequence(states), self.steps, CFG)
        assert not r.passed

    def test_extra_movement_fails(self) -> None:
        states = [*expected_pattern(self.steps), PoseState.DOWN, PoseState.CENTER]
        r = validate_sequence(sequence(states), self.steps, CFG)
        assert not r.passed and r.reason == "sequence_mismatch"

    def test_brief_jitter_is_ignored(self) -> None:
        poses = sequence(expected_pattern(self.steps))
        poses.insert(2, POSES[PoseState.DOWN])  # a single-frame blip during the initial centre run
        r = validate_sequence(poses, self.steps, CFG)
        assert r.passed, r.reason

    def test_baseline_must_be_frontal(self) -> None:
        poses = [Pose(0.3, 0.5)] * 4 + sequence(expected_pattern(self.steps))
        r = validate_sequence(poses, self.steps, CFG)
        assert not r.passed and r.reason == "not_centered_at_start"

    def test_too_many_missing_frames_fails(self) -> None:
        poses = sequence(expected_pattern(self.steps))
        poses[6:12] = [None] * 6
        r = validate_sequence(poses, self.steps, CFG)
        assert not r.passed and r.reason == "face_lost"

    def test_baseline_is_relative(self) -> None:
        # A subject whose neutral pitch ratio is 0.62 (camera below eye level) still validates.
        shifted = [
            None if p is None else Pose(p.yaw + 0.03, p.pitch + 0.12) for p in sequence(expected_pattern(self.steps))
        ]
        assert validate_sequence(shifted, self.steps, CFG).passed

    def test_compress_drops_short_runs_and_merges(self) -> None:
        c, left, t = PoseState.CENTER, PoseState.LEFT, PoseState.TRANSITION
        assert compress([c, c, t, left, c, c, left, left, t, c, c], 2) == [c, left, c]
