"""Layer A — per-frame capture quality."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from app.biometrics.pose import plausible_landmarks
from app.biometrics.types import FaceDetection, ImageBGR, QualityIssue


@dataclass(frozen=True, slots=True)
class QualityThresholds:
    min_detection_score: float
    min_face_width_ratio: float
    max_face_width_ratio: float
    min_brightness: float
    max_brightness: float
    min_sharpness: float
    max_center_offset: float
    secondary_face_score: float  # any additional face at/above this score makes the frame ambiguous


@dataclass(frozen=True, slots=True)
class QualityResult:
    issues: tuple[QualityIssue, ...]
    primary: FaceDetection | None
    brightness: float | None
    sharpness: float | None
    face_count: int


def _face_crop_gray(image: ImageBGR, det: FaceDetection) -> np.ndarray:
    h, w = image.shape[:2]
    x0, y0 = max(0, int(det.x)), max(0, int(det.y))
    x1, y1 = min(w, int(det.x + det.w)), min(h, int(det.y + det.h))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return np.zeros((8, 8), dtype=np.uint8)
    crop = cv2.cvtColor(image[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    return np.asarray(cv2.resize(crop, (112, 112), interpolation=cv2.INTER_AREA))


def assess_quality(image: ImageBGR, detections: list[FaceDetection], t: QualityThresholds) -> QualityResult:
    faces = [d for d in detections if d.score >= t.secondary_face_score]
    if not faces:
        return QualityResult((QualityIssue.NO_FACE,), None, None, None, 0)
    if len(faces) > 1:
        return QualityResult((QualityIssue.MULTIPLE_FACES,), None, None, None, len(faces))

    det = faces[0]
    h, w = image.shape[:2]
    issues: list[QualityIssue] = []
    if det.score < t.min_detection_score:
        issues.append(QualityIssue.LOW_CONFIDENCE)
    width_ratio = det.w / w
    if width_ratio < t.min_face_width_ratio:
        issues.append(QualityIssue.FACE_TOO_SMALL)
    elif width_ratio > t.max_face_width_ratio:
        issues.append(QualityIssue.FACE_TOO_LARGE)
    cx, cy = det.center
    if abs(cx / w - 0.5) > t.max_center_offset or abs(cy / h - 0.5) > t.max_center_offset:
        issues.append(QualityIssue.OFF_CENTER)
    if not plausible_landmarks(det):
        issues.append(QualityIssue.IMPLAUSIBLE_LANDMARKS)

    gray = _face_crop_gray(image, det)
    brightness = float(gray.mean())
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if brightness < t.min_brightness:
        issues.append(QualityIssue.TOO_DARK)
    elif brightness > t.max_brightness:
        issues.append(QualityIssue.TOO_BRIGHT)
    if sharpness < t.min_sharpness:
        issues.append(QualityIssue.BLURRY)
    return QualityResult(tuple(issues), det, brightness, sharpness, 1)
