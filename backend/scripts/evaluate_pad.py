"""Presentation-attack-detection evaluation (ISO/IEC 30107-3 style metrics).

You must supply a labelled dataset you are licensed to use (e.g. captures from your own deployment
cameras, or OULU-NPU / SiW / CelebA-Spoof / Replay-Attack under their licence agreements). Layout::

    <root>/bona_fide/*.jpg                 genuine live captures
    <root>/attack/<species>/*.jpg          one folder per presentation-attack-instrument species
                                           e.g. print, phone_replay, monitor_replay, video_replay, mask

    uv run python scripts/evaluate_pad.py <root> [--threshold 0.8] [--margin 0.1]

Each image goes through the production path (YuNet detection -> MiniFASNet ensemble live probability). A
presentation is classified as bona fide iff score >= threshold. Metrics:

* APCER(species) = attacks of that species classified bona fide / attacks of that species
* APCER          = max over species (ISO/IEC 30107-3 reports the worst species)
* BPCER          = bona fide classified as attack / bona fide presentations
* ACER           = (APCER + BPCER) / 2   (competition metric; not defined by ISO/IEC 30107-3)

Images with no detectable face are reported separately (they never reach PAD in production: the
quality layer rejects them). Single-image scores are stricter than production, which averages several
frontal frames and adds an uncertainty band; report both if you change the decision policy.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.biometrics.models import MiniFASNetEnsemble, PadMember, YuNetDetector  # noqa: E402

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp"}


def score_dir(paths: list[Path], detector: YuNetDetector, pad: MiniFASNetEnsemble) -> tuple[list[float], int]:
    scores, no_face = [], 0
    for p in paths:
        img = cv2.imread(str(p))
        faces = detector.detect(img) if img is not None else []
        if not faces:
            no_face += 1
            continue
        score, _ = pad.live_probability(img, faces[0])
        scores.append(score)
    return scores, no_face


def list_images(d: Path) -> list[Path]:
    return sorted(p for p in d.rglob("*") if p.suffix.lower() in IMAGE_EXT)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path)
    ap.add_argument("--threshold", type=float, default=0.80)
    ap.add_argument("--margin", type=float, default=0.10)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    weights = BACKEND / "weights"
    meta = json.loads((weights / "pad_minifasnet.meta.json").read_text(encoding="utf-8"))
    pad = MiniFASNetEnsemble(
        [PadMember(weights / m["file"], float(m["crop_scale"]), int(m["input_size"])) for m in meta["models"]],
        "minifasnet-v2+v1se@b6d5f04a",
    )
    detector = YuNetDetector(weights / "face_detection_yunet_2023mar.onnx", 0.6)

    bona_paths = list_images(args.root / "bona_fide")
    attack_root = args.root / "attack"
    species_dirs = sorted(d for d in attack_root.iterdir() if d.is_dir()) if attack_root.exists() else []
    if not bona_paths or not species_dirs:
        print("dataset needs bona_fide/ images and at least one attack/<species>/ folder", file=sys.stderr)
        return 2

    bona, bona_noface = score_dir(bona_paths, detector, pad)
    attacks: dict[str, list[float]] = {}
    attack_noface: dict[str, int] = defaultdict(int)
    for d in species_dirs:
        attacks[d.name], attack_noface[d.name] = score_dir(list_images(d), detector, pad)

    def report(thr: float) -> dict[str, object]:
        apcer = {s: (float(np.mean(np.array(v) >= thr)) if v else None) for s, v in attacks.items()}
        bpcer = float(np.mean(np.array(bona) < thr)) if bona else None
        worst = max((v for v in apcer.values() if v is not None), default=None)
        return {
            "threshold": thr,
            "APCER_per_species": apcer,
            "APCER_max": worst,
            "BPCER": bpcer,
            "ACER": None if worst is None or bpcer is None else (worst + bpcer) / 2,
        }

    result = {
        "dataset_root": str(args.root),
        "counts": {
            "bona_fide": len(bona),
            "bona_fide_no_face": bona_noface,
            **{f"attack/{s}": len(v) for s, v in attacks.items()},
            **{f"attack/{s}_no_face": n for s, n in attack_noface.items()},
        },
        "model": pad.model_id,
        "at_threshold": report(args.threshold),
        "at_lower_band": report(args.threshold - args.margin),
        "score_means": {
            "bona_fide": float(np.mean(bona)) if bona else None,
            **{s: (float(np.mean(v)) if v else None) for s, v in attacks.items()},
        },
        "warning": "Small datasets give wide uncertainty; report counts with every rate.",
    }
    text = json.dumps(result, indent=2)
    print(text)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
