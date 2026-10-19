"""Coarse head pose from the 5 YuNet landmarks.

The nose tip protrudes from the plane of the eyes and mouth, so rotating the head moves its projection
relative to the eye/mouth midline:

* yaw   = signed distance of the nose from the eye-mouth midline, normalised by inter-ocular distance
* pitch = position of the nose along the eye->mouth axis (0 = eye line, 1 = mouth line)

Both are dimensionless ratios, not degrees. Challenge classification is *relative to the subject's own
neutral frames*, which removes per-person geometry and camera-elevation offsets.
"""

from __future__ import annotations

import numpy as np

from app.biometrics.types import FaceDetection, Pose, PoseState


def estimate_pose(det: FaceDetection) -> Pose | None:
    lm = det.landmarks_array()
    r_eye, l_eye, nose, r_mouth, l_mouth = lm
    eye_mid = (r_eye + l_eye) / 2
    mouth_mid = (r_mouth + l_mouth) / 2
    iod = float(np.linalg.norm(l_eye - r_eye))
    axis = mouth_mid - eye_mid
    length = float(np.linalg.norm(axis))
    if iod < 1e-3 or length < 1e-3:
        return None
    u_v = axis / length  # points "down the face"
    u_h = np.array([u_v[1], -u_v[0]], dtype=np.float32)  # image-right when the face is upright
    d = nose - eye_mid
    yaw = float(np.dot(d, u_h)) / iod
    pitch = float(np.dot(d, u_v)) / length
    return Pose(yaw=yaw, pitch=pitch)


def plausible_landmarks(det: FaceDetection) -> bool:
    """Reject geometrically impossible landmark sets (e.g. detector hallucinations, heavy occlusion)."""
    lm = det.landmarks_array()
    r_eye, l_eye, _nose, r_mouth, l_mouth = lm
    eye_mid = (r_eye + l_eye) / 2
    mouth_mid = (r_mouth + l_mouth) / 2
    iod = float(np.linalg.norm(l_eye - r_eye))
    if iod < 0.15 * det.w:
        return False
    # Upright-ish face: mouth below eyes, roll under ~45 degrees.
    roll = abs(float(np.degrees(np.arctan2(l_eye[1] - r_eye[1], l_eye[0] - r_eye[0]))))
    if roll > 45 or mouth_mid[1] <= eye_mid[1]:
        return False
    # All landmarks inside an expanded bounding box.
    pad = 0.25
    x0, y0 = det.x - pad * det.w, det.y - pad * det.h
    x1, y1 = det.x + (1 + pad) * det.w, det.y + (1 + pad) * det.h
    return bool(np.all((lm[:, 0] >= x0) & (lm[:, 0] <= x1) & (lm[:, 1] >= y0) & (lm[:, 1] <= y1)))


def classify_pose(
    pose: Pose,
    baseline: Pose,
    *,
    yaw_threshold: float,
    pitch_threshold: float,
    center_yaw_tol: float,
    center_pitch_tol: float,
) -> PoseState:
    dy = pose.yaw - baseline.yaw
    dp = pose.pitch - baseline.pitch
    if abs(dy) <= center_yaw_tol and abs(dp) <= center_pitch_tol:
        return PoseState.CENTER
    yaw_strength = abs(dy) / yaw_threshold
    pitch_strength = abs(dp) / pitch_threshold
    if yaw_strength >= 1.0 and yaw_strength >= pitch_strength:
        return PoseState.LEFT if dy > 0 else PoseState.RIGHT
    if pitch_strength >= 1.0 and pitch_strength > yaw_strength:
        return PoseState.UP if dp < 0 else PoseState.DOWN
    return PoseState.TRANSITION
