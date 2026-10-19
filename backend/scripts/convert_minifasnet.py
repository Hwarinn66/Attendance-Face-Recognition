"""Convert the published MiniFASNet PAD weights (PyTorch) to ONNX and verify numerical parity.

PyTorch is only needed for this one-off conversion, never at runtime. Run from ``backend/``::

    uv run --no-project --python 3.13 --with torch==2.14.1 --with onnx==1.23.2 --with onnxscript \
        --with onnxruntime==1.31.0 --with "numpy<3" python scripts/convert_minifasnet.py

Outputs (in ``weights/``):
    minifasnet_v2_2.7_80x80.onnx, minifasnet_v1se_4.0_80x80.onnx, pad_minifasnet.meta.json

The meta file records source SHA-256s, toolchain versions, ONNX SHA-256s and the measured
max |softmax(torch) - softmax(onnxruntime)| on random inputs. The conversion fails (exit 1)
if parity exceeds the tolerance — a converted model is never written unverified.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import OrderedDict
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
import torch

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR / "scripts"))

from third_party.minifasnet_arch import MiniFASNetV1SE, MiniFASNetV2  # noqa: E402

WEIGHTS = BACKEND_DIR / "weights"
MANIFEST = json.loads((WEIGHTS / "manifest.json").read_text(encoding="utf-8"))
PARITY_TOLERANCE = 1e-4
INPUT_SIZE = 80
# get_kernel(80, 80) in the reference repo: ((h + 15) // 16, (w + 15) // 16)
CONV6_KERNEL = ((INPUT_SIZE + 15) // 16, (INPUT_SIZE + 15) // 16)

SPECS = [
    {"key": "minifasnet_v2_pth", "factory": MiniFASNetV2, "scale": 2.7, "out": "minifasnet_v2_2.7_80x80.onnx"},
    {"key": "minifasnet_v1se_pth", "factory": MiniFASNetV1SE, "scale": 4.0, "out": "minifasnet_v1se_4.0_80x80.onnx"},
]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x - x.max(axis=1, keepdims=True))
    return e / e.sum(axis=1, keepdims=True)


def load_model(spec: dict) -> torch.nn.Module:
    entry = MANIFEST["models"][spec["key"]]
    src = WEIGHTS / entry["file"]
    if not src.exists():
        raise SystemExit(f"missing {src}; run scripts/fetch_models.py first")
    digest = sha256_file(src)
    if digest != entry["sha256"]:
        raise SystemExit(f"{src.name}: SHA-256 mismatch ({digest})")
    state = torch.load(src, map_location="cpu", weights_only=True)
    if next(iter(state)).startswith("module."):
        state = OrderedDict((k[len("module.") :], v) for k, v in state.items())
    model = spec["factory"](conv6_kernel=CONV6_KERNEL)
    model.load_state_dict(state, strict=True)
    model.eval()
    return model


def main() -> int:
    rng = np.random.default_rng(20261016)
    meta: dict = {
        "format": "absen-pad-meta/1",
        "toolchain": {"torch": torch.__version__, "onnx": onnx.__version__, "onnxruntime": ort.__version__},
        "preprocessing": {
            "color": "BGR",
            "layout": "NCHW",
            "dtype": "float32",
            "value_range": "0-255 (no normalisation; matches reference to_tensor without div(255))",
            "crop": "detector bbox scaled by `scale` around its centre, clamped to the image, resized to 80x80",
            "output": "3 logits; softmax index 1 = live/real, indices 0 and 2 = attack classes",
            "ensemble": "mean of per-model softmax[1]",
        },
        "models": [],
    }
    for spec in SPECS:
        model = load_model(spec)
        out_path = WEIGHTS / spec["out"]
        dummy = torch.zeros(1, 3, INPUT_SIZE, INPUT_SIZE)
        torch.onnx.export(
            model,
            (dummy,),
            str(out_path),
            input_names=["input"],
            output_names=["logits"],
            dynamic_axes={"input": {0: "batch"}, "logits": {0: "batch"}},
            opset_version=17,
            dynamo=False,
        )
        onnx.checker.check_model(onnx.load(str(out_path)))

        sess = ort.InferenceSession(str(out_path), providers=["CPUExecutionProvider"])
        x = rng.uniform(0, 255, size=(16, 3, INPUT_SIZE, INPUT_SIZE)).astype(np.float32)
        with torch.no_grad():
            ref = torch.softmax(model(torch.from_numpy(x)), dim=1).numpy()
        got = softmax(sess.run(["logits"], {"input": x})[0])
        max_diff = float(np.abs(ref - got).max())
        print(f"{spec['out']}: parity max|d softmax| = {max_diff:.2e}")
        if max_diff > PARITY_TOLERANCE:
            out_path.unlink(missing_ok=True)
            print(f"parity check FAILED for {spec['out']}", file=sys.stderr)
            return 1
        meta["models"].append(
            {
                "file": spec["out"],
                "sha256": sha256_file(out_path),
                "source_file": MANIFEST["models"][spec["key"]]["file"],
                "source_sha256": MANIFEST["models"][spec["key"]]["sha256"],
                "crop_scale": spec["scale"],
                "input_size": INPUT_SIZE,
                "parity_max_abs_diff": max_diff,
                "license": "Apache-2.0 (minivision-ai/Silent-Face-Anti-Spoofing)",
            }
        )
    (WEIGHTS / "pad_minifasnet.meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print("wrote weights/pad_minifasnet.meta.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
