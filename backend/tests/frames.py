"""TEST FIXTURE frame generators for the fake vision provider (see app/biometrics/fake.py)."""

from __future__ import annotations

import cv2
import numpy as np

# BGR base colours understood by the fake provider.
LIVE = (20, 200, 20)  # green: live, follows challenge (identity bucket from R)
STATIC = (230, 60, 60)  # blue: live but never moves
SPOOF = (40, 40, 210)  # red: follows challenge, PAD says attack
DARK = (5, 5, 5)  # no face


def colored_frame(bgr: tuple[int, int, int], *, width: int = 480, height: int = 360, seed: int = 0) -> bytes:
    rng = np.random.default_rng(seed)
    base = np.array(bgr, dtype=np.int16)[None, None, :]
    noise = rng.integers(-25, 26, size=(height, width, 3), dtype=np.int16)
    img = np.clip(base + noise, 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    assert ok
    return bytes(buf.tobytes())


def live_identity(identity: int) -> tuple[int, int, int]:
    """Green frame whose red channel encodes fake identity bucket ``identity`` (0..5)."""
    return (20, 230, identity * 32)


def frame_set(bgr: tuple[int, int, int], n: int = 28, **kw: int) -> list[bytes]:
    return [colored_frame(bgr, seed=i, **kw) for i in range(n)]
