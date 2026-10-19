"""Reproducible face-verification evaluation on LFW (View 2, 10 folds x 600 pairs).

Data (not redistributed; research use per the LFW site): lfw.tgz + pairs.txt, verified by SHA-256 against
the checksums pinned by scikit-learn's ``fetch_lfw_pairs``. Download them into ``backend/.eval-data/lfw/``.

    uv run python scripts/evaluate_face_verification.py              # default recognizer from settings
    uv run python scripts/evaluate_face_verification.py --recognizer arcface --model-file w600k_mbf.onnx

Pipeline per image = the production pipeline: YuNet detection -> 5-point ArcFace-template alignment ->
recognizer -> L2-normalised embedding; score = cosine similarity. An image with no usable face is a
failure-to-acquire (FTA); pairs that include one are counted as *rejected* (so they raise FRR, never FAR).

Reported: FAR / FRR at the configured match threshold and at the deployed accept boundary
(threshold + uncertainty margin), EER, the threshold that achieves FAR <= 0.1 %, and the standard 10-fold
cross-validated accuracy. Each rate carries a 95 % Wilson interval. Results are written to docs/evaluation/.

Caveat: LFW is single, uncontrolled web photos; attendance compares a multi-frame live capture with an
enrolled template. LFW therefore measures the recogniser, not the end-to-end system, and LFW may have been
used upstream to choose the published SFace threshold.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.biometrics.models import (  # noqa: E402
    ARCFACE_SPEC,
    SFACE_SPEC,
    OnnxRecognizer,
    YuNetDetector,
    align_face,
)

LFW_SHA256 = "055f7d9c632d7370e6fb4afc7468d40f970c34a80d4c6f50ffec63f5a8d536c0"
PAIRS_SHA256 = "ea42330c62c92989f9d7c03237ed5d591365e89b3e649747777b70e692dc1592"
DEFAULT_DATA = BACKEND / ".eval-data" / "lfw"
OUT_DIR = BACKEND.parent / "docs" / "evaluation"


@dataclass(frozen=True)
class Pair:
    fold: int
    a: str
    b: str
    genuine: bool


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def img_path(name: str, idx: str) -> str:
    return f"lfw/{name}/{name}_{int(idx):04d}.jpg"


def parse_pairs(path: Path) -> list[Pair]:
    lines = path.read_text(encoding="utf-8").splitlines()
    folds, per = (int(x) for x in lines[0].split())
    pairs: list[Pair] = []
    i = 1
    for fold in range(folds):
        for _ in range(per):
            name, a, b = lines[i].split()
            pairs.append(Pair(fold, img_path(name, a), img_path(name, b), True))
            i += 1
        for _ in range(per):
            n1, a, n2, b = lines[i].split()
            pairs.append(Pair(fold, img_path(n1, a), img_path(n2, b), False))
            i += 1
    return pairs


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def rates(scores: np.ndarray, genuine: np.ndarray, valid: np.ndarray, thr: float) -> dict[str, object]:
    accept = valid & (scores >= thr)
    g, imp = genuine, ~genuine
    fr = int((g & ~accept).sum())
    fa = int((imp & accept).sum())
    ng, ni = int(g.sum()), int(imp.sum())
    return {
        "threshold": round(thr, 4),
        "FRR": fr / ng,
        "FRR_95ci": [round(x, 5) for x in wilson(fr, ng)],
        "false_rejects": fr,
        "genuine_pairs": ng,
        "FAR": fa / ni,
        "FAR_95ci": [round(x, 5) for x in wilson(fa, ni)],
        "false_accepts": fa,
        "impostor_pairs": ni,
    }


def best_accuracy_threshold(scores: np.ndarray, genuine: np.ndarray) -> float:
    candidates = np.unique(np.round(scores, 4))
    accs = [((scores >= t) == genuine).mean() for t in candidates]
    return float(candidates[int(np.argmax(accs))])


def main() -> int:
    from app.core.config import Settings  # noqa: PLC0415

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA)
    ap.add_argument("--recognizer", choices=["sface", "arcface"], default="sface")
    ap.add_argument("--model-file", default=None, help="ArcFace ONNX file in weights/ (arcface only)")
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--margin", type=float, default=None)
    args = ap.parse_args()

    settings = Settings(
        database_url="postgresql+psycopg://unused",
        biometric_encryption_keys="k:" + "A" * 43 + "=",
        recognizer=args.recognizer,
        match_threshold=args.threshold,
    )
    threshold = args.threshold if args.threshold is not None else settings.match_threshold
    margin = args.margin if args.margin is not None else settings.match_uncertainty_margin
    tgz, pairs_txt = args.data / "lfw.tgz", args.data / "pairs.txt"
    for f, digest in ((tgz, LFW_SHA256), (pairs_txt, PAIRS_SHA256)):
        if not f.exists():
            print(f"missing {f}; see the module docstring", file=sys.stderr)
            return 2
        if sha256(f) != digest:
            print(f"{f.name}: SHA-256 mismatch — refusing to evaluate on unverified data", file=sys.stderr)
            return 2
    images_root = args.data / "extracted"
    marker = images_root / ".complete"
    if not marker.exists():
        # Extract into a temporary directory and rename only on success, so an interrupted run can never
        # leave a partial image set that would silently inflate the failure-to-acquire rate.
        tmp = args.data / "extracted.tmp"
        for d in (tmp, images_root):
            if d.exists():
                shutil.rmtree(d)
        print("extracting lfw.tgz ...")
        with tarfile.open(tgz) as tf:
            tf.extractall(tmp, filter="data")
        (tmp / ".complete").write_text(LFW_SHA256, encoding="utf-8")
        tmp.rename(images_root)

    weights = settings.weights_dir
    detector = YuNetDetector(weights / "face_detection_yunet_2023mar.onnx", settings.detector_score_threshold)
    if args.recognizer == "sface":
        recognizer = OnnxRecognizer(weights / "face_recognition_sface_2021dec.onnx", SFACE_SPEC, "sface-2021dec")
    else:
        model_file = args.model_file or settings.arcface_model_file
        recognizer = OnnxRecognizer(weights / model_file, ARCFACE_SPEC, f"arcface:{Path(model_file).stem}")
    if threshold is None:
        print("--threshold is required for this recognizer", file=sys.stderr)
        return 2

    pairs = parse_pairs(pairs_txt)
    unique = sorted({p.a for p in pairs} | {p.b for p in pairs})
    print(f"{len(pairs)} pairs, {len(unique)} unique images, recognizer={recognizer.model_id}")
    t0 = time.perf_counter()
    embeddings: dict[str, np.ndarray | None] = {}
    for i, rel in enumerate(unique):
        img = cv2.imread(str(images_root / rel))
        if img is None:
            # A missing/unreadable dataset file is a data error, not a failure-to-acquire.
            print(f"cannot read {rel}; the extracted dataset is incomplete", file=sys.stderr)
            return 2
        emb = None
        faces = [d for d in detector.detect(img) if d.score >= settings.detector_score_threshold]
        if faces:
            h, w = img.shape[:2]
            # LFW images are centred on the subject: choose the detection nearest the centre.
            det = min(faces, key=lambda d: (d.center[0] - w / 2) ** 2 + (d.center[1] - h / 2) ** 2)
            emb = recognizer.embed(align_face(img, det))
        embeddings[rel] = emb
        if (i + 1) % 1000 == 0:
            print(f"  embedded {i + 1}/{len(unique)}")
    elapsed = time.perf_counter() - t0

    fta_images = sum(e is None for e in embeddings.values())
    scores = np.full(len(pairs), -1.0)
    valid = np.zeros(len(pairs), dtype=bool)
    for k, p in enumerate(pairs):
        ea, eb = embeddings[p.a], embeddings[p.b]
        if ea is not None and eb is not None:
            scores[k] = float(np.dot(ea, eb))
            valid[k] = True
    genuine = np.array([p.genuine for p in pairs])
    folds = np.array([p.fold for p in pairs])

    # EER and FAR-target thresholds over a fine sweep.
    sweep = np.linspace(-0.2, 1.0, 2401)
    far = np.array([((~genuine) & valid & (scores >= t)).sum() / (~genuine).sum() for t in sweep])
    frr = np.array([(genuine & ~(valid & (scores >= t))).sum() / genuine.sum() for t in sweep])
    i_eer = int(np.argmin(np.abs(far - frr)))
    ok = np.where(far <= 0.001)[0]
    t_far = float(sweep[ok[0]]) if ok.size else float("nan")

    accs = []
    for f in range(int(folds.max()) + 1):
        train, test = folds != f, folds == f
        t = best_accuracy_threshold(scores[train & valid], genuine[train & valid])
        accs.append(float((((scores >= t) & valid) == genuine)[test].mean()))

    result = {
        "dataset": {
            "name": "LFW View 2 (pairs.txt)",
            "lfw_tgz_sha256": LFW_SHA256,
            "pairs_sha256": PAIRS_SHA256,
            "pairs": len(pairs),
            "unique_images": len(unique),
        },
        "pipeline": {
            "detector": "yunet-2023mar",
            "recognizer": recognizer.model_id,
            "embedding_dim": recognizer.dim,
            "alignment": "5-point similarity to ArcFace 112x112 template",
            "score": "cosine similarity",
        },
        "failure_to_acquire": {
            "images": fta_images,
            "rate": fta_images / len(unique),
            "pairs_affected": int((~valid).sum()),
        },
        "at_match_threshold": rates(scores, genuine, valid, float(threshold)),
        "at_accept_boundary": {
            **rates(scores, genuine, valid, float(threshold) + float(margin)),
            "note": "scores in [threshold, threshold+margin) are 'uncertain -> retry'; counted as not accepted",
        },
        "eer": {"value": round(float((far[i_eer] + frr[i_eer]) / 2), 5), "threshold": round(float(sweep[i_eer]), 4)},
        "far_0.1pct": {
            "threshold": round(t_far, 4),
            **({} if math.isnan(t_far) else {"FRR": rates(scores, genuine, valid, t_far)["FRR"]}),
        },
        "ten_fold_accuracy": {"mean": round(float(np.mean(accs)), 5), "std": round(float(np.std(accs)), 5)},
        "score_stats": {
            "genuine_mean": round(float(scores[genuine & valid].mean()), 4),
            "impostor_mean": round(float(scores[~genuine & valid].mean()), 4),
            "impostor_max": round(float(scores[~genuine & valid].max()), 4),
        },
        "runtime_seconds": round(elapsed, 1),
        "hardware_note": "CPU only (ONNX Runtime CPUExecutionProvider)",
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"face_verification_lfw_{recognizer.model_id.replace(':', '_')}"
    (OUT_DIR / f"{stem}.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"wrote {OUT_DIR / (stem + '.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
