# Architecture

## Components

```
Browser (Next.js app, React)                      FastAPI backend (Python)                     PostgreSQL 17
───────────────────────────                       ────────────────────────                     ─────────────
pages + TanStack Query  ──/api/v1/* (same origin)──▶ middleware: request id · body limit ·      users, sessions,
camera capture (getUserMedia → JPEG frames)          security headers · CORS (allow-list)       schedules, attendance,
                        ◀── JSON / CSV ──────────    deps: session cookie · CSRF · RBAC          biometric templates (AES-GCM),
                                                     routers (api/v1) → services → models        challenges, attempts,
   next.config rewrites /api → API_ORIGIN            biometrics: provider · pipeline · policy    audit (append-only), settings
                                                     bounded inference pool (ONNX Runtime CPU)
```

* The browser only talks to its own origin. `next.config.ts` rewrites `/api/*` to the backend, so the session
  cookie is first-party and CORS stays closed (only `ALLOWED_ORIGINS`).
* The frontend never decides anything security relevant: it captures frames and renders server decisions.

## Backend layout (`backend/app`)

| Package | Responsibility |
| --- | --- |
| `core/` | settings + production validation, errors (one envelope), JSON logging with redaction, ASGI middleware, password/token helpers, role→permission policy |
| `db/` | declarative base (constraint naming convention), engine/session (statement timeout, UTC), overridable clock (`utcnow`) |
| `models/` | SQLAlchemy 2 typed models (17 tables) |
| `schemas/` | Pydantic request/response models; inputs use `extra="forbid"` |
| `api/` | dependencies (`deps.py`) and versioned routers (`v1/`) |
| `services/` | business logic: auth, users, scheduling, attendance (pure `rules.py` + transactional `service.py`), verification, enrollment, biometric state machine, reports, retention, system settings, audit |
| `biometrics/` | vision provider seam, model wrappers, quality, pose, challenge, temporal checks, decision policy, template crypto, pipeline, runtime; `fake.py` is a test-only fixture |
| `cli.py` | create-admin, seed-dev, check-models, purge, rotate-template-keys |

Routers are thin; services own transactions and audit events. CPU-bound inference runs in
`InferenceExecutor` (bounded thread pool, timeout) so request threads cannot pile up unbounded model work.

## Key flows

### Authentication
`POST /auth/login` → DB-backed failure counters (per email and per IP) → Argon2id verify (dummy hash for
unknown users) → server-side `auth_sessions` row storing **SHA-256 of** the opaque token and of a CSRF token →
`HttpOnly` session cookie + readable CSRF cookie (`__Host-` prefix when `COOKIE_SECURE`). Every request
resolves the session (absolute + idle expiry, revocation, active user). Unsafe methods additionally require
`X-CSRF-Token` matching the session hash **and** an allowed `Origin`.

### Enrollment
```
consent (versioned text + SHA-256) ─▶ POST /biometrics/enrollments
   state machine: not_enrolled|pending_retry|deleted ─start─▶ in_progress   (active stays active)
   issues LivenessChallenge(user, auth session, purpose=enrollment, random steps, TTL 60 s)
client follows prompts, uploads ordered frames ─▶ POST /biometrics/enrollments/{id}/submit
   begin_submission: row lock, ISSUED→PROCESSING (single use), session/purpose/expiry checks
   evaluate (pool):  frames → A quality → B challenge sequence → temporal continuity → C PAD
   identity gates:   sample consistency · re-enrolment must match current template · 1:N conflict check
   accept  → encrypt centroid embedding (AES-256-GCM, AAD = template id|user|model) → status active
   reject  → pending_retry, or verification_failed (attempt limit / conflict) → administrator reset
   always  → VerificationAttempt (scores + reason codes, no images) + audit event
```

### Check-in / check-out
```
GET  /attendance/status                 server time, today's occurrences/windows, blockers
POST /attendance/challenges {purpose}   pre-flight: window open, enrolled → challenge
POST /attendance/challenges/{id}/submit frames → same pipeline → 1:1 match vs own template → PASSED/FAILED
POST /attendance/check-in  (Idempotency-Key, {challenge_id, location?})
     claim idempotency key (unique; replays stored response) → consume PASSED challenge
     (row lock; same user + auth session + purpose; ≤ 120 s old) → rule engine at server time →
     geofence (optional) → INSERT (UNIQUE user+shift+shift_date) → audit → commit
POST /attendance/check-out  … → lock open record (FOR UPDATE OF attendance_records) → metrics
```

### Live dashboard (`services/live.py`, `api/v1/events.py`)
```
commit of check-in/out, correction (requested/applied/approved/rejected), record deletion, failed face check
   pg_notify('attendance_events', {kind, at, ids, name, status}) in the SAME transaction
   → PostgreSQL delivers it only on COMMIT (a rolled-back action never appears)
each backend process: one listener thread (sync psycopg, LISTEN) while ≥ 1 console is connected
   → fan-out to per-connection asyncio queues (call_soon_threadsafe; a full queue drops its oldest event)
GET /events/attendance  text/event-stream, attendance:read_all; auth on a short-lived DB session
   "event: attendance" per change · ": ping" every 15 s · stream ends after 120 s → browser reconnects
web: AppShell → useLiveUpdates → refetch overview/records/exceptions/summary/corrections/attempts/audit
   (batched 300 ms) + pop-up; refetch on reconnect; 30 s polling while the stream is unavailable
```
psycopg's async connection does not run on Windows' default (Proactor) event loop, hence the thread.
NOTIFY goes to every listening process, so this works unchanged with several uvicorn workers or hosts.

### Attendance rule engine (`services/attendance/rules.py`)
Pure functions over `ShiftPolicy` (local wall-clock start/end + window offsets) and UTC instants. A shift
occurrence is anchored to the local date it **starts**; overnight shifts end the next day. Local→UTC uses
`zoneinfo` (PEP 495: gap → after the gap, fold → first occurrence), so DST nights are really shorter/longer.
Boundaries are inclusive and documented in the module docstring. Corrections reuse the metric functions
without window enforcement and keep previous values.

## Verification pipeline and decision policy

| Layer | Check | Reject reason (category) |
| --- | --- | --- |
| Session | owner, auth session, purpose, single use, TTL, minimum elapsed time | `challenge_*` (session/challenge) |
| Frames | 12–90 JPEG/PNG, ≤ 512 KiB and ≤ 1280×960 each, one resolution | `too_few_frames`, `invalid_frame` … (capture) |
| A Quality | one face, detection score, size, centring, brightness, sharpness, landmark plausibility | `no_face`, `multiple_faces` … (capture) |
| B Challenge | baseline from first frontal frames; per-frame pose classified **relative to baseline**; compressed state sequence must equal `center, s1, center, s2, center, …`; min frames per pose; max missing ratio/gap. Left/right-only challenges ignore pitch for movements (centre tolerance 0.10). With `LIVENESS_CHALLENGE=passive` the server sends no steps and the pattern is just `center` (steady frontal capture, ≥ 2 s) | `challenge_incomplete`, `challenge_mismatch`, `face_lost` |
| Temporal | box position/scale continuity over all tracked frames; frontal frames grouped by centre segment (first and last required), minimum pairwise similarity ≥ match threshold. Turned frames are not embedded (the recogniser is unreliable there). | `discontinuity`, `insufficient_frontal_frames`, `identity_inconsistent` (liveness) |
| C PAD | MiniFASNet ensemble on up to 5 frontal frames: pass iff mean ≥ T and min ≥ T − m; spoof iff mean < T − m; else uncertain | `pad_spoof_suspected`, `pad_uncertain`, `pad_unavailable` |
| Identity | attendance: cosine(probe, template) ≥ T+m accept, < T reject, between → uncertain | `no_match`, `match_uncertain` |

All gates must pass — scores are never averaged across detectors. Clients receive only a coarse category,
a fixed message and quality hints; scores and reason codes are stored server-side for administrator review.

## Data model (PostgreSQL)

`users` (profile fields, role, biometric_status) · `auth_sessions` · `login_attempts` · `workplaces`
(time zone, geofence) · `shifts` · `shift_assignments` · `holidays` (partial unique indexes for global vs
workplace) · `attendance_records` (UNIQUE user/shift/date, CHECKs) · `attendance_corrections`
(previous_values) · `idempotency_keys` (UNIQUE user/scope/key) · `biometric_consents` ·
`biometric_templates` (encrypted; partial unique: one active per user) · `enrollment_sessions` ·
`liveness_challenges` · `verification_attempts` · `audit_events` (trigger rejects UPDATE) · `system_settings`.
Enums are VARCHAR + CHECK. All timestamps are `timestamptz` in UTC. One Alembic revision (`0001`), verified with
`downgrade base → upgrade head → alembic check` on every integration run.

## Frontend layout (`frontend/src`)

The web app is the **administration console only**; attendance and face enrollment live in the Flutter app
(`mobile/`), and the server enforces the split.

* `app/login`: sign-in. The server refuses employee accounts here.
* `app/(app)/…`: five pages behind a layout that loads `/auth/me`:
  * `hr` (overview)
  * `hr/attendance` (tabs: records, exceptions, corrections, reports, face checks)
  * `admin/users` (one dialog: profile, sign-in ID and password, shifts, face data)
  * `admin/schedules`
  * `admin/settings` (tabs: general, activity log)
  * `account` (own password)
* `components/`: accessible primitives on native elements; `panels/` (reports, face checks, activity log);
  `TrendChart`; `CorrectionDialog`; `LiveUpdates` (EventSource hook, status badge, pop-ups).
* `lib/`: the `api.ts` client (CSRF and error envelope), the `auth.tsx` permission context, formatting, and
  `live.ts` (live event parsing and Indonesian pop-up text).

## Testing seams

* `app.db.base.set_clock()` — integration tests drive server time deterministically.
* `VISION_PROVIDER=fake` (`APP_ENV=test` only) — replaces model outputs, nothing else.
* Chromium fake camera + generated Y4M clips (`frontend/e2e/make-fake-video.mjs`) for Playwright.
