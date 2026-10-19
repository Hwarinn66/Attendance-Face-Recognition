"""Safe frame decoding. Only JPEG/PNG are accepted, sizes are bounded before and after decoding,
and decoded pixels live only in memory for the duration of the request."""

from __future__ import annotations

import cv2
import numpy as np

from app.biometrics.types import FrameDecodeError, ImageBGR

_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MIN_SIDE = 160


def decode_frame(data: bytes, *, max_bytes: int, max_pixels: int) -> ImageBGR:
    if not data:
        raise FrameDecodeError("empty frame")
    if len(data) > max_bytes:
        raise FrameDecodeError("frame exceeds size limit")
    if not (data.startswith(_JPEG_MAGIC) or data.startswith(_PNG_MAGIC)):
        raise FrameDecodeError("unsupported image format (JPEG or PNG required)")
    # IMREAD_COLOR drops alpha and EXIF orientation is ignored (IMREAD_IGNORE_ORIENTATION) so that the
    # server sees exactly the pixel grid the browser encoded.
    buf = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
    if img is None:
        raise FrameDecodeError("frame could not be decoded")
    h, w = img.shape[:2]
    if h * w > max_pixels:
        raise FrameDecodeError("frame resolution exceeds limit")
    if min(h, w) < MIN_SIDE:
        raise FrameDecodeError("frame resolution too low")
    return np.ascontiguousarray(img, dtype=np.uint8)
