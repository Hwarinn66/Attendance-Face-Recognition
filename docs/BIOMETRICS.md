# Biometric subsystem

What runs, where the weights come from, how decisions are made, what has been measured — and what has not.

## Models

| | Detector | Recogniser (default) | Recogniser (optional) | PAD |
| --- | --- | --- | --- | --- |
| Model | YuNet `face_detection_yunet_2023mar.onnx` | SFace `face_recognition_sface_2021dec.onnx` | InsightFace ArcFace `w600k_mbf.onnx` (buffalo_s) | MiniFASNetV2 (crop ×2.7) + MiniFASNetV1SE (crop ×4.0), 80×80 |
| Source | OpenCV Zoo (HF `opencv/face_detection_yunet` @ 3cc26e7f) | OpenCV Zoo (HF `opencv/face_recognition_sface` @ 3d708243) | `deepinsight/insightface` release v0.7 | `minivision-ai/Silent-Face-Anti-Spoofing` @ b6d5f04a |
| License | MIT | Apache-2.0 | **non-commercial research only** | Apache-2.0 |
| Integrity | SHA-256 pinned (= HF LFS hash) | SHA-256 pinned (= HF LFS hash) | SHA-256 recorded on first fetch | `.pth` git-blob SHA-1 + SHA-256 pinned; ONNX SHA-256 in `pad_minifasnet.meta.json` |
| Runtime | `cv2.FaceDetectorYN` (per-thread instance) | ONNX Runtime CPU | ONNX Runtime CPU | ONNX Runtime CPU |
| Input | BGR frame, score ≥ 0.6, NMS 0.3 | 112×112 aligned, RGB, 0–255, NCHW | 112×112 aligned, RGB, (x−127.5)/127.5 | BGR crop, 0–255 (no /255, as in the reference code) |
| Output | box + 5 landmarks + score | 128-d, L2-normalised | 512-d, L2-normalised | softmax over 3 classes; index 1 = live; ensemble = mean |

Alignment: similarity transform (`cv2.estimateAffinePartial2D`, LMEDS) of the 5 YuNet landmarks onto the
standard ArcFace 112×112 template (the same template OpenCV's `FaceRecognizerSF.alignCrop` uses).

**Fidelity checks actually executed**
* SFace via ONNX Runtime vs OpenCV `FaceRecognizerSF.feature` on the same aligned crop: cosine 1.000000;
  our alignment vs `alignCrop`: cosine > 0.999 (`tests/models::test_sface_onnxruntime_matches_opencv_reference`).
* MiniFASNet PyTorch → ONNX (opset 17): max |Δ softmax| = 4.9e-8 (V2) and 1.2e-7 (V1SE) on 16 random inputs.
* Upstream labelled samples: genuine `image_T1` → live 1.000; attacks `image_F1` → 0.229, `image_F2` → 0.002.
  A digital (non-recaptured) image `lena.jpg` → 0.997 live: PAD does not detect digital injection.

These are correctness checks of the integration, **not** accuracy evaluations.

Enrollment of a person never retrains a model: it stores an encrypted centroid of up to 8 frontal embeddings.

## Thresholds and how they were chosen

| Setting | Default | Origin |
| --- | --- | --- |
| `MATCH_THRESHOLD` (SFace cosine) | 0.363 | Value published by OpenCV Zoo for SFace cosine similarity. Measured locally on LFW (below). Must be set explicitly for ArcFace. |
| `MATCH_UNCERTAINTY_MARGIN` | 0.05 | Engineering choice: scores in [0.363, 0.413) → "retry" instead of accept. |
| `IDENTITY_CONFLICT_THRESHOLD` | = match threshold | Conservative (lower bar flags more conflicts for administrator review). |
| `PAD_THRESHOLD` / margin | 0.80 / 0.10 | **Uncalibrated** conservative engineering default (reference code uses argmax). |
| Pose: yaw / pitch move thresholds | 0.15 / 0.10 | ≈ 23° / 17° on a generic 3D face model (unit test); still-face jitter measured on public samples: yaw σ 0.015–0.05. |
| Pose: centre tolerances | 0.07 / 0.06 | Above measured still-face jitter for near-frontal faces. |
| `CHALLENGE_ACTIONS` / `CHALLENGE_STEPS` | `turn_left,turn_right` / 2 | Vertical moves disabled after phone measurements (below). With two actions a step may repeat: 4 sequences. |
| Centre pitch tolerance, left/right challenges | 0.10 | Pitch never produces a movement there; nods of up to 0.2 while turning were measured (below). |
| `LIVENESS_CHALLENGE` | `active` | `passive` = steady frontal capture (about 4 s), no movement prompts. Weaker; see SECURITY.md. |
| Quality (brightness 50–215, Laplacian var ≥ 25, face width 18–80 %) | — | Engineering defaults; adjust per camera. |

### Calibrating for a deployment
1. Collect consented captures from the actual devices: genuine pairs (same person, different days) and
   impostor pairs (different people), plus attack presentations per PAI species you care about.
2. Face matching: run the pipeline to produce scores and pick the threshold for your target FAR (e.g. 0.1 %
   or 0.01 %); report FRR at that point with confidence intervals. `scripts/evaluate_face_verification.py`
   shows the computation on LFW.
3. PAD: `scripts/evaluate_pad.py <root>`; choose the threshold for your target BPCER (user friction) and check
   APCER per species. Production averages up to 5 frontal frames, which is less noisy than single images.
4. Set the values via environment variables and re-run `pytest -m models`.

## Evaluation results

### Face verification — LFW View 2 (measured)

Run on 2026-10-17 with `uv run python scripts/evaluate_face_verification.py`; full report in
[`evaluation/face_verification_lfw_sface-2021dec.json`](evaluation/face_verification_lfw_sface-2021dec.json).
The dataset was verified by SHA-256 before use (`lfw.tgz`, `pairs.txt`; same hashes as scikit-learn's fetcher).

**Protocol.** 6,000 pairs (3,000 genuine and 3,000 impostor, in 10 folds) over 7,701 unique images. Each image
went through YuNet, then the detection nearest the image centre, then the same 5-point alignment, then SFace
128-d. The score is cosine similarity. Failure to acquire (FTA) counts as a reject. CPU only, 2,236 s.

| Metric | Value |
| --- | --- |
| Failure to acquire | 0 / 7,701 images |
| Score mean: genuine / impostor (impostor max) | 0.6515 / 0.0844 (0.4036) |
| At `MATCH_THRESHOLD` 0.363 | FAR 0.067 % (2 / 3,000; 95 % CI 0.018–0.243 %) · FRR 1.67 % (50 / 3,000; 95 % CI 1.27–2.19 %) |
| At the accept boundary 0.413 (threshold + margin; scores in between count as "retry", not accepted) | FAR 0 % (0 / 3,000; 95 % CI 0–0.128 %) · FRR 2.97 % (89 / 3,000; 95 % CI 2.42–3.64 %) |
| EER | 0.87 % at threshold 0.296 |
| Lowest threshold with FAR ≤ 0.1 % | 0.3525, giving FRR 1.43 % |
| 10-fold accuracy (threshold chosen on 9 folds, tested on the held-out fold) | 99.32 % ± 0.38 % |

The confidence intervals are Wilson score intervals.

**How to read this.**

What these numbers say:

* The integration (detector, then alignment, then recogniser) works end to end.
* The default threshold sits near this benchmark's FAR ≤ 0.1 % point.
* The uncertainty band trades about 1.3 points of FRR for zero observed false accepts.

Limits of the measurement:

* With 3,000 impostor pairs, LFW cannot resolve FAR below about 0.1 %. "0 false accepts" only bounds FAR at
  0.128 % with 95 % confidence.
* LFW is single-image vs single-image comparison of mostly frontal, well-lit web photos of public figures.
* This evaluation used none of the following, so the numbers say nothing about them:
  * the production quality gates,
  * the multi-frame enrollment centroid,
  * webcam captures,
  * the deployment population (demographics, lighting, cameras).
* Calibrate on site data before relying on a specific FAR (see "Calibrating for a deployment").

### Head-pose challenge on a phone (measured, one user)

Data: real enrollment attempts from the Flutter app. Each attempt records per-frame (yaw, pitch) deltas
against the user's own baseline in `verification_attempts.details.challenge.trace` (numbers only, no images).

Setup: Samsung Galaxy A56, front camera, 720×480 YUV, about 4 fps, hand-held. Results:

| Movement | Measured delta | Threshold | Result |
| --- | --- | --- | --- |
| Turn left / right | yaw 0.3–0.8 | 0.15 | detected reliably |
| Look down | pitch +0.13 to +0.23 | 0.10 | detected |
| Look up (asked to tilt clearly) | pitch −0.04 to −0.09 | 0.10 | **not** detected |
| Still face (centre) | pitch jitter up to ±0.06 | — | — |

What follows from these numbers:

* **Look-up is off by default.** Its response overlaps still-face jitter. This is expected: 5-point landmarks
  move little in pitch, and a phone held below the face shrinks it further. No threshold separates it from
  noise, so `look_up` is removed from the default `CHALLENGE_ACTIONS`.
* **Look-down is off as well (2026-10-19).**
  * Of the eight active attempts recorded, every `challenge_mismatch`/`incomplete` involved a vertical step
    or a missing final return.
  * Users also nodded while turning (pitch up to +0.2), which read as an unrequested `look_down`.
  * The default is now left/right only. In such challenges pitch never produces a movement, and the centre
    pitch tolerance widens to 0.10.
* **Weaker randomisation, accepted as a trade-off.**
  * With two actions a step may repeat (L C L is two separate turns), giving 2^n sequences: 4 for the
    default 2 steps, 8 for 3.
  * Without repeats there would be only two sequences.
* **A final hold was added to both clients.** Users reacted 0.5–0.7 s after each prompt, which cut off the
  last return to centre. Both clients now add a 1.2 s hold at the end.
* **A turned head can fail the identity gate, so identity is now judged on frontal frames only.**
  * With the final hold, a later attempt completed the movement sequence correctly, then failed
    `identity_inconsistent`: at yaw ≈ 0.45 the SFace similarity to the user's own frontal centroid was
    **0.09** (threshold 0.363). No threshold that passes this genuine user would still mean anything.
  * The owner switched the trial to `LIVENESS_CHALLENGE=passive` (2026-10-18). On 2026-10-19 `active` was
    re-enabled with the fix below.
  * Fix: frontal frames are grouped by centre segment (start, between movements, end). The first and last
    segments must contain usable frames, and the **minimum pairwise** similarity over the sampled frontal
    embeddings must reach the match threshold.
  * Pairwise rather than against the centroid: two different people mixed half-and-half each sit about
    0.7 from their joint centroid.
  * Turned frames are covered by spatial continuity only.
  * For reference, frontal-only continuity on this phone (passive attempts) was 0.93–0.98 against the
    centroid.
* **Re-measure before relying on these values.** This is one person on one device. Re-enable `look_up` only
  after measuring a better pitch estimator.

### Presentation attack detection — not measured

No presentation-attack dataset was available under a usable licence in the development environment.
APCER/BPCER/ACER are therefore **unknown** for this deployment. `scripts/evaluate_pad.py` implements the
ISO/IEC 30107-3 style computation (APCER per PAI species and its maximum, BPCER, competition ACER); it was
only smoke-tested on 3 upstream sample images (which says nothing statistically).

## Definitions

* **FAR** (false accept rate): impostor comparisons accepted / impostor comparisons.
* **FRR** (false reject rate): genuine comparisons rejected / genuine comparisons (failures-to-acquire included).
* **EER**: operating point where FAR = FRR.
* **APCER**: proportion of attack presentations of one PAI species classified as bona fide; report per species and the maximum.
* **BPCER**: proportion of bona fide presentations classified as attacks.
* **ACER**: (APCER + BPCER) / 2 — a competition metric, not defined by ISO/IEC 30107-3.
