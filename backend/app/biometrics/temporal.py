"""Layer D (part) — temporal consistency across the captured sequence.

* Spatial continuity: consecutive face boxes must not jump in position or scale (splices / cuts).
* Identity continuity: every pair of frontal faces in the sequence (taken from the start, from between
  the movements and from the end) must be the same person, so a face cannot be swapped partway through.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from app.biometrics.models import l2_normalize
from app.biometrics.types import Embedding, FaceDetection


@dataclass(frozen=True, slots=True)
class TemporalResult:
    passed: bool
    reason: str | None
    max_center_jump: float
    max_scale_jump: float
    min_identity_similarity: float | None


def spatial_continuity(
    detections: Sequence[FaceDetection | None], max_center_jump: float, max_scale_jump: float
) -> tuple[bool, float, float]:
    worst_center = 0.0
    worst_scale = 1.0
    prev: FaceDetection | None = None
    for det in detections:
        if det is None:
            continue
        if prev is not None:
            (px, py), (cx, cy) = prev.center, det.center
            ref_w = max(prev.w, det.w, 1.0)
            jump = float(np.hypot(cx - px, cy - py)) / ref_w
            scale = max(det.w / max(prev.w, 1.0), prev.w / max(det.w, 1.0))
            worst_center = max(worst_center, jump)
            worst_scale = max(worst_scale, scale)
        prev = det
    ok = worst_center <= max_center_jump and worst_scale <= max_scale_jump
    return ok, worst_center, worst_scale


def centroid(embeddings: Sequence[Embedding]) -> Embedding:
    return l2_normalize(np.mean(np.stack([l2_normalize(e) for e in embeddings]), axis=0))


def min_pairwise_similarity(embeddings: Sequence[Embedding]) -> float:
    """Lowest cosine similarity between any two embeddings (1.0 for fewer than two).

    Pairwise rather than against the mean: two different people mixed half-and-half each sit about 0.7 from
    their joint centroid, which would pass a centroid comparison, but only ~0 from each other.
    """
    if len(embeddings) < 2:
        return 1.0
    m = np.stack([l2_normalize(e) for e in embeddings])
    sims = m @ m.T
    return float(sims[np.triu_indices(len(m), k=1)].min())
