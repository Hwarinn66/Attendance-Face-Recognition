# Implementation Plan — Face Recognition Attendance System ("Absen")

Status of each milestone is tracked in [PROGRESS.md](PROGRESS.md). Architecture is in
[ARCHITECTURE.md](ARCHITECTURE.md); the threat model and controls are in [SECURITY.md](SECURITY.md).

## 0. Repository audit (2026-10-16)

| Item | Finding |
| --- | --- |
| Repository | Empty directory, not a git repo. `git init` performed; nothing pre-existing to preserve. |
| OS / hardware | Windows 10 Pro, 4 logical CPUs, 8 GB RAM, Intel integrated graphics (no CUDA GPU). |
| Runtimes | Python 3.13.14, Node 24.19 / npm 11.17, git 2.54. `uv` 0.13 installed (user site) for locking. |
| Docker | **Not installed.** Docker Compose files are written but cannot be executed in this environment. |
| PostgreSQL | Not installed. Portable PostgreSQL 17.11 binaries (zonky embedded-postgres, SHA-1 verified) are used for local dev/test via `scripts/dev_postgres.py`. |
| Network | Available (PyPI, npm, GitHub, Hugging Face reachable). |

## 1. Key decisions (defaults, documented and reversible)

| Area | Decision | Rationale |
| --- | --- | --- |
| Backend | FastAPI + Pydantic v2 + SQLAlchemy 2.x (sync, psycopg 3) + Alembic | Sync sessions in FastAPI's threadpool are simple and correct; CPU-bound inference runs in a bounded executor. |
| Frontend | Next.js 16 (App Router) + React 19 + TypeScript 6.0 strict + Tailwind 4 | TS pinned < 6.1 for typescript-eslint compatibility. |
| API transport | Next.js rewrites `/api/*` to FastAPI | Same-origin cookies, no permissive CORS. |
| Sessions | Opaque random session token in `HttpOnly` cookie; SHA-256 hash stored in DB; CSRF token (double-submit, bound to session) + Origin check | Revocable server-side sessions; no JWT revocation problem. |
| Passwords | Argon2id (argon2-cffi) | Modern memory-hard KDF. |
| Rate limiting | DB-backed counters (login attempts, verification attempts) | Works across instances without Redis. |
| Face detector | OpenCV Zoo **YuNet** 2023mar (MIT) via `cv2.FaceDetectorYN` | Commercially usable, CPU-fast, gives 5 landmarks. |
| Face recognizer | Default **SFace** 2021dec (Apache-2.0); pluggable **ArcFace ONNX** backend (e.g. InsightFace `w600k_*`) | Both use 112×112 ArcFace-template alignment and cosine similarity. InsightFace weights are non-commercial-only, so not the default. |
| PAD | **MiniFASNetV2 + MiniFASNetV1SE** (minivision Silent-Face-Anti-Spoofing, Apache-2.0) converted to ONNX by a reproducible script; ONNX Runtime at runtime | Real pretrained PAD weights with a permissive license; no torch at runtime. |
| Head pose | Geometric yaw/pitch from YuNet 5-point landmarks, relative to the user's own neutral frames | No extra model dependency; documented accuracy limits. MediaPipe not needed. |
| Template protection | AES-256-GCM application-level encryption of embeddings, key from env (`BIOMETRIC_ENCRYPTION_KEYS`), key-id for rotation | Embeddings never stored in plaintext. |
| Time | All timestamps stored UTC (`timestamptz`); policy evaluated in workplace IANA time zone via `zoneinfo` (+`tzdata`) | DST-safe evaluation. |

## 2. Milestones

Each milestone ends with the listed verification commands actually being run.

### M1 — Foundation
- Repo layout (`backend/`, `frontend/`, `scripts/`, docs), `.gitignore`, `.env.example`, Docker Compose, CI.
- Backend app factory, settings with production validation, structured logging, health endpoint, error envelope.
- **Acceptance:** `pytest` passes a health test; `ruff` + `mypy` clean.
- **Verify:** `cd backend && uv run ruff check . && uv run mypy app && uv run pytest`

### M2 — Database & identity
- Models + first Alembic migration: users, roles, sessions, login attempts, employee profiles, workplaces, shifts, assignments, holidays, attendance, corrections, biometric templates, consent, enrollment/liveness sessions, verification attempts, audit events, settings, idempotency keys.
- Auth (login/logout/me, session expiry/revocation, CSRF, rate limit), RBAC dependencies, account activation/deactivation, dev-only seed.
- **Acceptance:** cross-user access returns 403/404; wrong CSRF rejected; 6th failed login inside window → 429.
- **Verify:** `uv run alembic upgrade head && uv run alembic check && uv run pytest tests/integration/test_auth.py`

### M3 — Attendance engine
- Pure rule engine (shift occurrences, windows, grace, late/early/overtime, overnight, DST, holidays, weekdays).
- Transactional check-in/out with unique constraints, row locks, idempotency keys, single-use verification sessions.
- Corrections (request/approve/direct) with audit trail; absence/exception computation.
- **Acceptance:** boundary, timezone, overnight, holiday, duplicate and concurrent tests pass on PostgreSQL.
- **Verify:** `uv run pytest tests/unit/test_attendance_rules.py tests/integration/test_attendance_api.py`

### M4 — Computer vision foundations
- Model manifest (URL, SHA-256, license), `scripts/fetch_models.py`, MiniFASNet → ONNX conversion script with parity check.
- Detector, quality assessor, aligner, recognizers, PAD inference, model registry with load-time validation; inference executor.
- **Acceptance:** real models load and run on public sample images; missing weights are reported, never faked.
- **Verify:** `uv run python -m app.cli check-models && uv run pytest -m models`

### M5 — Enrollment & liveness
- Consent records, enrollment state machine, randomized server challenges bound to user/session/purpose/expiry, frame submission, pose-sequence + temporal validation, PAD, embedding consistency and identity-conflict check, encrypted template storage.
- Decision policy (hard gates, uncertainty bands, reason codes), verification attempt audit, replay prevention.
- Test-only fake vision provider (refused outside `APP_ENV=test`/explicit dev flag).
- **Acceptance:** replayed/expired/foreign challenge rejected; PAD-missing in production fails closed.
- **Verify:** `uv run pytest tests/unit/test_challenge.py tests/unit/test_decision.py tests/integration/test_enrollment_api.py`

### M6 — Frontend
- Login, dashboard, enrollment (consent → camera → guided challenge), check-in/out, history, profile, HR dashboard, users, schedules, attendance management, reports + CSV, audit/security events, settings.
- **Acceptance:** `tsc --noEmit`, ESLint, Vitest pass; `next build` succeeds.
- **Verify:** `cd frontend && npm run typecheck && npm run lint && npm test && npm run build`

### M7 — Security, audit & privacy
- Security headers, request-size limits, correlation IDs, audit coverage review, biometric deletion + retention purge command, consent withdrawal, data export.
- **Verify:** `uv run pytest tests/integration/test_security.py`

### M8 — Verification & hardening
- Full backend + frontend suites, Playwright E2E with labeled fake camera/model fixtures, migration round-trip, production build.

### M9 — Delivery
- README, PROGRESS, limitations, blockers.

## 3. Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| No licensed PAD/face evaluation dataset available locally | Cannot measure APCER/BPCER/FAR/FRR here | Ship reproducible evaluation scripts + protocol; report "not measured"; thresholds marked uncalibrated defaults. |
| MiniFASNet was trained with a different face detector/crop | Score distribution shift | Use the reference crop scales; calibrate on site data before production; fail closed on uncertainty. |
| Browser capture can be replaced by virtual cameras / injection | Liveness bypass | Documented; server-side decisions; randomized challenges; PAD; recommend managed kiosks/attested devices for high-risk deployments. |
| Docker unavailable locally | Compose files unverified | Documented as unverified; CI runs on Linux with service containers. |
| InsightFace weights non-commercial | Legal | Not the default; license warning in docs and manifest. |
| 5-point head pose is coarse | False rejections | Thresholds relative to user's neutral pose; configurable. |

## 4. Verification command index

```bash
# backend
cd backend
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy app
uv run alembic upgrade head && uv run alembic check
uv run pytest                    # unit + integration (needs TEST_DATABASE_URL)
uv run pytest -m models          # real-model component tests (needs weights)

# frontend
cd frontend
npm ci
npm run lint && npm run typecheck && npm test && npm run build
npm run e2e                      # Playwright (starts backend in test mode + frontend)
```
