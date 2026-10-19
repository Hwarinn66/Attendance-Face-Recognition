"""Layer B — active randomised challenge-response.

The server picks an unpredictable sequence of head movements. Validation is done over the *whole*
ordered frame sequence: the subject must start centred, perform each requested movement in order and
return to centre after each one, holding each pose for several frames. A single frame in the right
pose is never sufficient, and extra or out-of-order movements fail the challenge.
"""

from __future__ import annotations

import secrets
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.biometrics.pose import classify_pose
from app.biometrics.types import CHALLENGE_ACTIONS, Pose, PoseState

_rng = secrets.SystemRandom()


def generate_steps(n_steps: int, actions: Sequence[PoseState] = CHALLENGE_ACTIONS) -> list[PoseState]:
    """Uniformly random actions with no immediate repetition: k * (k-1)^(n-1) possible sequences for k
    actions (36 for all four movements and 3 steps, 12 for the default three)."""
    if len(set(actions)) < 2:
        raise ValueError("at least two distinct actions are required")
    steps: list[PoseState] = []
    for _ in range(n_steps):
        choices = [a for a in dict.fromkeys(actions) if not steps or a != steps[-1]]
        steps.append(_rng.choice(choices))
    return steps


def expected_pattern(steps: Sequence[PoseState]) -> list[PoseState]:
    pattern = [PoseState.CENTER]
    for s in steps:
        pattern.extend([s, PoseState.CENTER])
    return pattern


@dataclass(frozen=True, slots=True)
class ChallengeConfig:
    yaw_threshold: float
    pitch_threshold: float
    center_yaw_tolerance: float
    center_pitch_tolerance: float
    min_pose_frames: int
    max_missing_ratio: float
    max_consecutive_missing: int
    baseline_frames: int = 3
    # Measured on public samples: near-frontal faces show |yaw| up to ~0.25 and per-frame jitter (std) of
    # 0.015-0.05 under small shifts/scale/JPEG noise; movement thresholds are relative to this baseline.
    baseline_max_abs_yaw: float = 0.25


@dataclass(slots=True)
class ChallengeResult:
    passed: bool
    reason: str | None
    states: list[PoseState] = field(default_factory=list)
    observed: list[PoseState] = field(default_factory=list)
    baseline: Pose | None = None
    # Per-frame (yaw, pitch) relative to the baseline; None where no usable face. Numbers only (no image
    # data); kept with the attempt so thresholds can be calibrated on real captures (docs/BIOMETRICS.md).
    deltas: list[tuple[float, float] | None] = field(default_factory=list)

    def summary(self) -> dict[str, object]:
        out: dict[str, object] = {
            "passed": self.passed,
            "reason": self.reason,
            "observed": [s.value for s in self.observed],
            "baseline": None
            if self.baseline is None
            else {"yaw": round(self.baseline.yaw, 4), "pitch": round(self.baseline.pitch, 4)},
        }
        present = [d for d in self.deltas if d is not None]
        if present:
            out["pose_range"] = {
                "yaw": [round(min(d[0] for d in present), 3), round(max(d[0] for d in present), 3)],
                "pitch": [round(min(d[1] for d in present), 3), round(max(d[1] for d in present), 3)],
            }
            out["trace"] = [None if d is None else [round(d[0], 3), round(d[1], 3)] for d in self.deltas]
        return out


def _runs(states: Sequence[PoseState]) -> list[tuple[PoseState, int]]:
    runs: list[tuple[PoseState, int]] = []
    for s in states:
        if runs and runs[-1][0] == s:
            runs[-1] = (s, runs[-1][1] + 1)
        else:
            runs.append((s, 1))
    return runs


def compress(states: Sequence[PoseState], min_frames: int) -> list[PoseState]:
    """Drop transitions/missing frames and runs shorter than ``min_frames``; merge neighbours."""
    kept = [s for s, n in _runs(states) if s not in (PoseState.TRANSITION, PoseState.MISSING) and n >= min_frames]
    merged: list[PoseState] = []
    for s in kept:
        if not merged or merged[-1] != s:
            merged.append(s)
    return merged


def validate_sequence(
    poses: Sequence[Pose | None], steps: Sequence[PoseState], cfg: ChallengeConfig
) -> ChallengeResult:
    """``poses`` is ordered by capture; ``None`` marks frames without a usable face."""
    if not poses:
        return ChallengeResult(False, "no_frames")
    missing = sum(p is None for p in poses)
    if missing / len(poses) > cfg.max_missing_ratio:
        return ChallengeResult(False, "face_lost")
    longest_gap = max(
        (
            n
            for s, n in _runs([PoseState.MISSING if p is None else PoseState.CENTER for p in poses])
            if s == PoseState.MISSING
        ),
        default=0,
    )
    if longest_gap > cfg.max_consecutive_missing:
        return ChallengeResult(False, "face_lost")

    present = [p for p in poses if p is not None]
    head = present[: cfg.baseline_frames]
    if len(head) < cfg.baseline_frames:
        return ChallengeResult(False, "incomplete")
    baseline = Pose(yaw=statistics.median(p.yaw for p in head), pitch=statistics.median(p.pitch for p in head))
    if abs(baseline.yaw) > cfg.baseline_max_abs_yaw:
        return ChallengeResult(False, "not_centered_at_start", baseline=baseline)

    states = [
        PoseState.MISSING
        if p is None
        else classify_pose(
            p,
            baseline,
            yaw_threshold=cfg.yaw_threshold,
            pitch_threshold=cfg.pitch_threshold,
            center_yaw_tol=cfg.center_yaw_tolerance,
            center_pitch_tol=cfg.center_pitch_tolerance,
        )
        for p in poses
    ]
    deltas = [None if p is None else (p.yaw - baseline.yaw, p.pitch - baseline.pitch) for p in poses]
    observed = compress(states, cfg.min_pose_frames)
    expected = expected_pattern(steps)
    if observed == expected:
        return ChallengeResult(True, None, states, observed, baseline, deltas)
    # Distinguish "did not finish" from "did something else" for user guidance.
    is_prefix = observed == expected[: len(observed)]
    reason = "incomplete" if is_prefix else "sequence_mismatch"
    return ChallengeResult(False, reason, states, observed, baseline, deltas)
