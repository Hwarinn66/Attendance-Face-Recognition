# Absen — face-verified employee attendance

Web application for employee check-in/check-out with server-side face verification and layered liveness
checks (capture quality → randomised head-movement challenge → temporal consistency → passive
anti-spoofing → 1:1 face match), configurable shifts and attendance policy, administration pages, reports,
audit trail and biometric privacy workflows.

| Part | Stack |
| --- | --- |
| Backend | Python 3.13, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, PostgreSQL 17 |
| Vision | OpenCV (YuNet detector), ONNX Runtime (SFace / ArcFace-compatible recogniser, MiniFASNet PAD) — CPU only |
| Frontend | Next.js 16 (App Router), React 19, TypeScript 6 (strict), Tailwind CSS 4, TanStack Query |
| Tests | pytest + HTTPX, Vitest + Testing Library, Playwright |

Read next: [ARCHITECTURE.md](ARCHITECTURE.md) · [SECURITY.md](SECURITY.md) ·
[docs/BIOMETRICS.md](docs/BIOMETRICS.md) · [PROGRESS.md](PROGRESS.md) (what is verified and what is not).

> **Status:** functional and tested (see PROGRESS.md), **not certified production-ready**. The PAD
> model has not been evaluated on a presentation-attack dataset, and browser capture cannot prove that frames
> come from a physical camera. Read SECURITY.md before any deployment.

---

## 1. Prerequisites

* Python 3.12–3.13 and [uv](https://docs.astral.sh/uv/) ≥ 0.13
* Node.js ≥ 20.9 (developed with 24) and npm
* PostgreSQL 17 — either Docker, a local server, or the bundled portable fallback (`scripts/dev_postgres.py`)
* A webcam and a Chromium, Firefox or Safari browser for the attendance UI
* No GPU is required.

## 2. Installation

```bash
cd backend && uv sync          # creates backend/.venv from uv.lock
cd ../frontend && npm ci       # installs from package-lock.json
```

## 3. Environment configuration

Copy [.env.example](.env.example) to `backend/.env` and fill in real values. Required:

| Variable | Notes |
| --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://user:pass@host:port/db` |
| `BIOMETRIC_ENCRYPTION_KEYS` | `kid:base64(32 bytes)`; generate with the command in `.env.example`; keep in a secret manager |
| `ALLOWED_ORIGINS` | the browser origin(s) of the frontend (HTTPS in production) |
| `COOKIE_SECURE` | `false` only for `http://localhost` development |
| `PAD_MODE` | `required` (default; mandatory in production) or `disabled` (development only) |

The backend refuses to start with an unsafe production configuration (insecure cookies, PAD disabled,
missing/HTTP origins, the test-only fake vision provider, or a seeded dev password).

## 4. Database initialisation

With your own PostgreSQL:

```bash
cd backend
uv run alembic upgrade head          # create schema
uv run alembic check                 # verify models and migrations agree
uv run python -m app.cli create-admin --email admin@yourcompany.com   # prompts for a strong password
```

Without Docker or a PostgreSQL install (Windows/Linux/macOS x86_64):

```bash
python scripts/dev_postgres.py init   # downloads PostgreSQL 17 (SHA-1 verified), runs on 127.0.0.1:55432
# connection URLs for DATABASE_URL / TEST_DATABASE_URL / E2E_DATABASE_URL are written to .local/pg.env
python scripts/dev_postgres.py start|stop|status
```

Optional development sample data (only when `APP_ENV=development`): set `DEV_SEED_ADMIN_EMAIL` and a strong
`DEV_SEED_ADMIN_PASSWORD`, then `uv run python -m app.cli seed-dev`. There is no default administrator.

Users can sign in with their email or with an optional user ID (case-insensitive). The command below gives an
account a user ID together with a temporary password. It skips the password policy, so a short password such
as `Admin` is accepted, and it is refused unless `APP_ENV=development`. Never use it against a database that
will be deployed.

```bash
uv run python -m app.cli dev-set-login --email admin@example.com --username Admin123 --password Admin
```

## 5. Model weights and licensing

Weights are not committed. Fetch and verify them (SHA-256 against [backend/weights/manifest.json](backend/weights/manifest.json)):

```bash
cd backend
uv run python scripts/fetch_models.py
# One-off PAD conversion (PyTorch is used only here, never at runtime; on Linux add
#   --index https://download.pytorch.org/whl/cpu to avoid the CUDA wheels):
uv run --no-project --python 3.13 --with torch==2.14.1 --with onnx==1.23.2 --with onnxscript \
       --with onnxruntime==1.31.0 --with "numpy<3" python scripts/convert_minifasnet.py
uv run python -m app.cli check-models         # exit 1 if verification would be unavailable
```

| Role | Model | License |
| --- | --- | --- |
| Detector | OpenCV Zoo YuNet 2023mar | MIT |
| Recogniser (default) | OpenCV Zoo SFace 2021dec, 128-d | Apache-2.0 |
| Recogniser (optional) | InsightFace ArcFace (`w600k_mbf`, `--with-arcface`) | **non-commercial research only** |
| PAD | minivision MiniFASNetV2 + MiniFASNetV1SE (converted to ONNX, parity-checked) | Apache-2.0 |

Details, thresholds and evaluation results: [docs/BIOMETRICS.md](docs/BIOMETRICS.md).

## 6. Running locally

```bash
# terminal 1 — API on http://127.0.0.1:8000 (OpenAPI docs at /api/v1/docs outside production)
cd backend && uv run uvicorn --factory app.main:app_factory --host 127.0.0.1 --port 8000 --reload --timeout-graceful-shutdown 5

# terminal 2 — web app on http://127.0.0.1:3000 (proxies /api to API_ORIGIN, default http://127.0.0.1:8000)
cd frontend && npm run dev
```

Two clients, two roles:

* **Mobile app** (`mobile/`). Everyone uses it to enroll their face and check in and out. Administrators are
  employees too and use it in the same way. Attendance and enrollment endpoints accept only app sessions
  (bearer token), so they cannot be called from a web session either.
* **Web console**. Administrators only; employee accounts are refused at sign-in. Its menu:
  * **Overview**: today's counts.
    * They update **live**, without a page refresh, when someone checks in or out, a correction is filed
      or reviewed, or a record is deleted. A pop-up names the employee; a face check suspected of using a
      photo or video raises a warning.
    * A badge next to the logo shows *Langsung* (live), *Menyambung…* (reconnecting) or *Terputus*
      (disconnected). While the live stream is unavailable the console refreshes every 30 s instead.
  * **Attendance**: records, exceptions, corrections, reports and CSV export, and face checks.
    * An administrator can **delete** an invalid record (e.g. someone checked in while not at work), but
      not their own, and must give a reason.
    * The record disappears from reports, CSV and the employee's history.
    * A full copy and the reason stay in the activity log, and pending corrections for that day are
      rejected.
  * **Users**: profile, sign-in ID and password, shifts and face data, all in one dialog.
  * **Schedules**: shifts, workplaces and holidays.
  * **Settings**: system rules and the activity/audit log.

Passwords are stored hashed (Argon2id). An administrator can set or replace a password, and can choose
whether it must be changed at the next sign-in, but can never view it.

Docker Compose (written but **not verified here** — Docker was unavailable): see the header of
[docker-compose.yml](docker-compose.yml). `API_ORIGIN` is baked into the frontend at build time.

### Mobile app (Flutter, employees) — local trial with the laptop as server

`mobile/` is an Android/iOS app for employees: sign-in, face enrollment, check-in and check-out, and history.
Administration stays on the web.

**How it talks to the backend**

* The app signs in through `POST /api/v1/auth/token` and sends `Authorization: Bearer <token>`. The web app's
  cookie, CSRF and Origin checks are unchanged.
* The phone only captures frames. The front-camera stream is converted to upright, un-mirrored JPEGs, and the
  server makes every decision.
* Rotation is calibrated automatically: the app tries 0/90/180/270° until `/biometrics/quality-check` finds a face.

**Running the trial**

```bash
# backend reachable from the Wi-Fi (phone and laptop on the same network, Windows profile "Private")
cd backend && uv run uvicorn --factory app.main:app_factory --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 5

cd mobile
flutter test                      # unit tests + every screen rendered against a mock backend
# PNG screenshots of every screen (real Roboto/Material Icons fonts), for design review:
SCREENSHOT_DIR=/path/to/dir flutter test test/screens_test.dart
flutter build apk --release       # -> build/app/outputs/flutter-apk/app-release.apk
```

Install the APK on the phone. In the app's settings, set the server to `http://<laptop-ip>:8000`.

**Trial-build caveats**

* The Android manifest enables cleartext HTTP (`usesCleartextTraffic`) only for this LAN trial. Release signing
  uses the debug key.
* A real deployment needs an HTTPS server, a proper signing key, and removal of `usesCleartextTraffic`.
* iOS builds need a Mac with Xcode and an Apple developer account.
* Geofenced shifts are not supported in the app yet; use the web app for those.
* Release builds run without R8 (`shrink=false` in `mobile/android/gradle.properties`).
  * Why: JNI-based plugins (`package:jni`, `shared_preferences_android`) look up Kotlin classes, methods and
    fields by name from Dart, which R8 cannot see.
  * What happened with R8 on: renamed classes made the app fail at start-up with `NoSuchMethodError` and then
    `ClassNotFoundException` (seen on a Samsung Galaxy A56).
  * Before re-enabling R8, keep every class those packages name in Dart, then check the APK with
    `apkanalyzer dex packages --defined-only`.

## 7. Tests

```bash
cd backend
uv run pytest                 # unit + integration; needs TEST_DATABASE_URL (database name must end in _test)
uv run pytest -m models tests/models   # component tests with the REAL models (needs fetched weights)

cd frontend
npm test                      # Vitest + Testing Library
npm run e2e                   # Playwright: starts the API in APP_ENV=test with the labelled fake vision
                              # provider against E2E_DATABASE_URL (must end in _e2e; it is truncated)
```

Integration and E2E tests use `VISION_PROVIDER=fake`, a clearly labelled **test fixture** that replaces only
model outputs; quality, challenge, temporal and decision logic run for real. Passing them says nothing about
real-world biometric accuracy — that is what `pytest -m models` and the evaluation scripts are for:

```bash
uv run python scripts/evaluate_face_verification.py   # LFW protocol, FAR/FRR/EER (see docs/BIOMETRICS.md)
uv run python scripts/evaluate_pad.py <dataset-root>   # APCER/BPCER/ACER on a licensed PAD dataset
```

## 8. Lint and type checks

```bash
cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy app
cd frontend && npm run lint && npm run typecheck && npm run build
```

## 9. Troubleshooting camera access

* **"Camera access requires a secure (HTTPS) connection"** — browsers only expose the camera on HTTPS or on
  `localhost`/`127.0.0.1`. Use HTTPS for any other host.
* **Permission denied** — re-allow the camera in the site settings (lock icon in the address bar), reload.
* **Camera in use** — close video-call apps or other tabs using it.
* **"Couldn't get a clear view"** — even front lighting, face inside the oval, only one person in view,
  hold still. The preview gives live hints before you press *Begin*.
* **"Head movements weren't detected in order"** — start facing the camera, move clearly (about 25°), and return
  to centre after each prompt. The order changes every attempt by design.

## 10. Troubleshooting model weights

* `check-models` reports `UNAVAILABLE (... not found)` — run `fetch_models.py` (and the conversion step for PAD).
* `SHA-256 mismatch` — the file is corrupt or not the pinned artifact; delete it and fetch again. The service
  never loads an unverified model.
* `/api/v1/health/ready` shows `"verification": "unavailable"` — attendance verification fails closed until the
  missing model is restored (in production the readiness probe also returns 503).
* `PAD_MODE=disabled` is rejected in production by design.

## 11. Production deployment considerations

* Terminate TLS in front of the frontend; set `COOKIE_SECURE=true` (cookies then use the `__Host-` prefix) and
  HTTPS `ALLOWED_ORIGINS`. Set `TRUST_FORWARDED_FOR=true` only behind a proxy that overwrites `X-Forwarded-For`.
* Store `BIOMETRIC_ENCRYPTION_KEYS` in a secret manager; rotate by prepending a new key and running
  `python -m app.cli rotate-template-keys`.
* Run `python -m app.cli purge` daily (retention); back up PostgreSQL with encryption; restrict DB network access.
* Run migrations as a separate step (`alembic upgrade head`) before rolling out new backend versions.
* Calibrate match and PAD thresholds on your own cameras and population before go-live (docs/BIOMETRICS.md),
  and get legal review of the biometric consent text for your jurisdiction.
* Rate limits are database-backed (shared across instances); the quality pre-check limiter is per process.
* Live dashboard (Server-Sent Events on `/api/v1/events/attendance`):
  * A reverse proxy must not buffer that path. The response sends `X-Accel-Buffering: no` (honoured by
    nginx) and `Cache-Control: no-transform`. Proxy idle timeouts must exceed the 15 s heartbeat.
  * Each backend process holds one extra PostgreSQL connection (`LISTEN`) while at least one console is open.
  * Streams end every `LIVE_EVENTS_STREAM_SECONDS` (120 s) and the browser reconnects. Still, start uvicorn with
    `--timeout-graceful-shutdown` (the Dockerfile uses 10 s) so open streams cannot delay a restart.
