"""Inference wrappers around the pretrained models (YuNet detector, SFace / ArcFace recognisers,
MiniFASNet PAD ensemble). These classes only load real weight files; there is no fallback output."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import cv2
import numpy as np
import numpy.typing as npt
import onnxruntime as ort

from app.biometrics.types import Embedding, FaceDetection, ImageBGR

# ArcFace canonical 5-point template for 112x112 crops (also used by OpenCV FaceRecognizerSF.alignCrop).
ARCFACE_TEMPLATE_112 = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)


def _ort_session(path: Path, threads: int = 1) -> ort.InferenceSession:
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = threads
    opts.inter_op_num_threads = 1
    opts.log_severity_level = 3  # errors only; SFace emits benign initializer warnings
    return ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])


class YuNetDetector:
    """OpenCV Zoo YuNet. ``cv2.FaceDetectorYN`` instances are not thread-safe, so one is kept per thread."""

    def __init__(self, model_path: Path, score_threshold: float, nms_threshold: float = 0.3, top_k: int = 20) -> None:
        if not model_path.is_file():
            raise FileNotFoundError(model_path)
        self.model_path = model_path
        self.score_threshold = score_threshold
        self.nms_threshold = nms_threshold
        self.top_k = top_k
        self._local = threading.local()

    def _get(self, w: int, h: int) -> cv2.FaceDetectorYN:
        det = getattr(self._local, "det", None)
        if det is None:
            det = cv2.FaceDetectorYN.create(
                str(self.model_path), "", (w, h), self.score_threshold, self.nms_threshold, self.top_k
            )
            self._local.det = det
        det.setInputSize((w, h))
        return det

    def detect(self, image: ImageBGR) -> list[FaceDetection]:
        h, w = image.shape[:2]
        _, raw = self._get(w, h).detect(image)
        # The OpenCV stubs declare a matrix, but detect() returns None when no face is found.
        faces = cast("npt.NDArray[np.float32] | None", raw)
        if faces is None:
            return []
        result = []
        for row in faces:
            lm = tuple((float(row[4 + 2 * i]), float(row[5 + 2 * i])) for i in range(5))
            result.append(
                FaceDetection(
                    x=float(row[0]),
                    y=float(row[1]),
                    w=float(row[2]),
                    h=float(row[3]),
                    landmarks=lm,
                    score=float(row[14]),
                )
            )
        return sorted(result, key=lambda d: d.score, reverse=True)


def align_face(image: ImageBGR, detection: FaceDetection, size: int = 112) -> ImageBGR:
    """Similarity-transform the 5 landmarks onto the ArcFace template."""
    src = detection.landmarks_array()
    dst = ARCFACE_TEMPLATE_112 * (size / 112.0)
    matrix, _ = cv2.estimateAffinePartial2D(src, dst, method=cv2.LMEDS)
    if matrix is None:
        raise ValueError("could not estimate alignment transform")
    aligned = cv2.warpAffine(image, matrix, (size, size), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))
    return np.asarray(aligned, dtype=np.uint8)


def l2_normalize(v: npt.NDArray[np.floating]) -> Embedding:
    v = np.asarray(v, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(v))
    if not np.isfinite(norm) or norm < 1e-6:
        raise ValueError("degenerate embedding")
    return (v / norm).astype(np.float32)


def cosine_similarity(a: Embedding, b: Embedding) -> float:
    return float(np.dot(l2_normalize(a), l2_normalize(b)))


@dataclass(frozen=True, slots=True)
class RecognizerSpec:
    name: str
    input_size: int
    swap_rb: bool  # BGR -> RGB
    mean: float
    scale: float  # pixel' = (pixel - mean) * scale


SFACE_SPEC = RecognizerSpec("sface-2021dec", 112, swap_rb=True, mean=0.0, scale=1.0)
ARCFACE_SPEC = RecognizerSpec("arcface-insightface", 112, swap_rb=True, mean=127.5, scale=1 / 127.5)


class OnnxRecognizer:
    """ArcFace-compatible embedding extractor: 112x112 aligned crop -> L2-normalised embedding."""

    def __init__(self, model_path: Path, spec: RecognizerSpec, model_id: str) -> None:
        if not model_path.is_file():
            raise FileNotFoundError(model_path)
        self.spec = spec
        self.model_id = model_id
        self._session = _ort_session(model_path, threads=2)
        inp = self._session.get_inputs()[0]
        self._input_name = inp.name
        out = self._session.get_outputs()[0]
        self.dim = int(out.shape[-1])

    def preprocess(self, aligned: ImageBGR) -> npt.NDArray[np.float32]:
        size = self.spec.input_size
        img = np.asarray(aligned if aligned.shape[:2] == (size, size) else cv2.resize(aligned, (size, size)))
        if self.spec.swap_rb:
            img = img[:, :, ::-1]
        x = (img.astype(np.float32) - np.float32(self.spec.mean)) * np.float32(self.spec.scale)
        return np.ascontiguousarray(x.transpose(2, 0, 1)[None, ...])

    def embed(self, aligned: ImageBGR) -> Embedding:
        out = self._session.run(None, {self._input_name: self.preprocess(aligned)})[0]
        return l2_normalize(out[0])


def pad_crop(image: ImageBGR, det: FaceDetection, scale: float, out_size: int) -> ImageBGR:
    """Port of minivision ``CropImage._get_new_box`` + resize (reference preprocessing)."""
    src_h, src_w = image.shape[:2]
    box_w, box_h = det.w, det.h
    scale = min((src_h - 1) / box_h, (src_w - 1) / box_w, scale)
    new_w, new_h = box_w * scale, box_h * scale
    cx, cy = det.x + box_w / 2, det.y + box_h / 2
    x0, y0 = cx - new_w / 2, cy - new_h / 2
    x1, y1 = cx + new_w / 2, cy + new_h / 2
    if x0 < 0:
        x1 -= x0
        x0 = 0
    if y0 < 0:
        y1 -= y0
        y0 = 0
    if x1 > src_w - 1:
        x0 -= x1 - src_w + 1
        x1 = src_w - 1
    if y1 > src_h - 1:
        y0 -= y1 - src_h + 1
        y1 = src_h - 1
    crop = image[int(y0) : int(y1) + 1, int(x0) : int(x1) + 1]
    return np.asarray(cv2.resize(crop, (out_size, out_size)), dtype=np.uint8)


@dataclass(frozen=True, slots=True)
class PadMember:
    file: Path
    crop_scale: float
    input_size: int


class MiniFASNetEnsemble:
    """Passive PAD: mean of softmax[1] ("live") across MiniFASNetV2 (scale 2.7) and V1SE (scale 4.0)."""

    LIVE_INDEX = 1

    def __init__(self, members: list[PadMember], model_id: str) -> None:
        if not members:
            raise ValueError("no PAD members")
        self.model_id = model_id
        self._members = []
        for m in members:
            if not m.file.is_file():
                raise FileNotFoundError(m.file)
            sess = _ort_session(m.file)
            self._members.append((m, sess, sess.get_inputs()[0].name))

    def live_probability(self, image: ImageBGR, det: FaceDetection) -> tuple[float, list[float]]:
        per_model: list[float] = []
        for member, sess, input_name in self._members:
            crop = pad_crop(image, det, member.crop_scale, member.input_size)
            x = np.ascontiguousarray(crop.astype(np.float32).transpose(2, 0, 1)[None, ...])
            logits = sess.run(None, {input_name: x})[0][0].astype(np.float64)
            e = np.exp(logits - logits.max())
            probs = e / e.sum()
            per_model.append(float(probs[self.LIVE_INDEX]))
        return float(np.mean(per_model)), per_model
