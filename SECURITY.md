# Security

This document describes the threat model, the controls that are implemented and tested, and — just as
importantly — what the system **cannot** guarantee.

## Assets

1. Biometric templates (face embeddings) and verification evidence (scores, reason codes).
2. Attendance records (pay-relevant, legally relevant).
3. Accounts, sessions and the privileged administrator role.
4. Audit trail.
5. Secrets: `BIOMETRIC_ENCRYPTION_KEYS`, database credentials.

## Threats and controls

| Threat | Controls (implemented) | Evidence |
| --- | --- | --- |
| Credential stuffing / brute force | Argon2id; DB-backed limits per email (5/15 min) and per IP (30/15 min); generic errors; dummy-hash timing equalisation; audit `auth.login.*` | `test_login_rate_limit`, `test_wrong_password_is_generic_and_audited` |
| Session theft / fixation | opaque 256-bit tokens, only SHA-256 stored; `HttpOnly`, `SameSite=Lax`, `Secure` + `__Host-` prefix in production; absolute (12 h) and idle (60 min) expiry; revoke on logout, password change, role change, deactivation, reset | `test_expired_and_idle_sessions`, `test_password_change_revokes_other_sessions`, `test_session_list_and_revoke` |
| CSRF | double-submit token bound to the session (hash compare) **and** Origin allow-list for unsafe methods | `test_csrf_required_for_unsafe_methods` |
| Web vs app separation | the cookie (web) sign-in accepts administrators only (`auth.login.web_denied`); attendance and face-enrollment endpoints require an app (bearer) session (`use_mobile_app`) | `test_web_console_is_for_administrators_only`, `test_attendance_and_enrollment_only_from_the_app` |
| Native-app sessions (mobile) | `POST /auth/token` returns the opaque session token in the body (same login checks, rate limits and audit), never as a cookie; requests send `Authorization: Bearer`, which a browser never attaches cross-site, so CSRF checks are not needed on that path and cookies are ignored there; same expiry/revocation as web sessions | `test_bearer_token_for_native_clients`, `test_cookie_sessions_still_require_csrf` |
| Privilege escalation / IDOR | server-side permission per endpoint; two roles (employee: own data only; admin: everything), data scoping in SQL; no self role change/deactivation; no self-approval of corrections | `test_rbac_matrix`, `test_data_scoping`, `test_employee_cannot_escalate_or_create_users`, `test_corrections_workflow` |
| Client-asserted results | client sends only frames; liveness/match/attendance decided on the server; unknown JSON fields rejected; client timestamps not accepted | `test_client_cannot_assert_verification`, `test_server_time_is_authoritative` |
| Replay of a verification | challenges single-use (row lock + state), bound to user, auth session, purpose, TTL 60 s; PASSED result consumable once within 120 s | `test_challenge_replay_and_expiry`, `test_challenge_bound_to_user_and_session`, `test_verification_is_single_use_and_purpose_bound`, `test_verification_expires` |
| Pre-recorded video | randomised step order (12 sequences for 3 steps with the default actions left/right/down; 36 if `CHALLENGE_ACTIONS` also includes `look_up`), full-sequence validation, minimum elapsed time | `test_wrong_order_fails`, `test_challenge_too_fast_rejected`, `test_replayed_still_photo_fails_active_challenge` (real models) |
| Print / screen replay | passive PAD (MiniFASNet ensemble) with fail-closed and uncertainty band | real-model sample check only — **no dataset evaluation yet** |
| Face swap mid-sequence | identity continuity across all embedded frames; spatial continuity | `test_identity_swap_mid_sequence_detected`, `test_frames_from_other_identity_mid_sequence_rejected` |
| Buddy punching via a second account | 1:N identity-conflict check at enrollment → locked + security event | `test_identity_conflict_blocks_second_account` |
| Account takeover → re-enroll attacker's face | re-enrollment must match the current template; failure locks for administrator review | `test_reenrollment_must_match_existing_template` |
| Duplicate / concurrent attendance | idempotency keys; UNIQUE (user, shift, date); `FOR UPDATE` on check-out | `test_concurrent_check_ins_create_one_record`, `test_idempotent_retry_returns_same_record` |
| Template theft from the database | AES-256-GCM per template, key outside the DB, AAD binds ciphertext to template id/user/model (copying it to another row fails) ; key rotation command | `TestTemplateCipher` |
| Missing / tampered model weights | SHA-256 verification at load; warm-up inference; unavailable models fail closed (`PAD_MODE=required` mandatory in production) | `test_pad_missing_fails_closed`, `check-models` |
| Oversized / malicious uploads | 12 MiB body limit (streamed count), ≤ 90 frames, 512 KiB/frame, magic-byte check, pixel limit, in-memory only | `test_body_size_limit`, `test_frame_validation`, `test_decode_rejects_*` |
| Information leakage | no stack traces to clients; validation errors do not echo input; coarse client-facing liveness messages; JSON logs with redaction | `test_validation_errors_do_not_echo_input`, `test_public_decision_hides_scores` |
| Silent removal of attendance | only administrators may delete a record, never their own; a reason is mandatory; the full record snapshot and the reason are written to the append-only audit log (`attendance.record.deleted`); pending corrections for that occurrence are closed so they cannot resurrect it | `test_admin_deletes_invalid_attendance` |
| Live event stream (SSE) | requires a session with `attendance:read_all` (administrators); checked when the stream opens and on every reconnect. Streams end after `LIVE_EVENTS_STREAM_SECONDS` (120 s), so a revoked session stops receiving events within that window. Events are emitted inside the business transaction and delivered only on commit. Payloads carry names, ids, times and status only, never images, embeddings or scores | `test_live_events_are_for_administrators`, `test_attendance_changes_reach_the_console_without_refresh`, `test_events_are_sent_only_when_the_transaction_commits` |
| CSV formula injection | cells starting with `= + - @ \t \r` prefixed with `'` | `test_exceptions_reports_and_csv` |
| Audit tampering | append-only for the app: DB trigger rejects UPDATE; only the retention purge deletes, and it audits itself | verified directly against PostgreSQL |
| Clickjacking / XSS hardening | CSP, `frame-ancestors 'none'`, `X-Frame-Options`, `nosniff`, Permissions-Policy (camera/geolocation self only), HSTS in production | `test_security_headers_and_request_id` |

## Known limitations (read before deploying)

* **Passive liveness mode (`LIVENESS_CHALLENGE=passive`) removes the head-movement challenge.**
  * What still runs: PAD, the minimum capture time and identity continuity. A printed photo or a screen
    therefore depends on PAD alone, which is uncalibrated.
  * What is lost: a replayed video, or a digitally injected image of the real person, is no longer
    stopped by an unpredictable prompt. MiniFASNet scored the public `lena.jpg` 0.997 live.
  * The default is `active`. The trial deployment uses `passive` at the owner's request, after real users
    could not complete the prompts reliably (docs/BIOMETRICS.md). The server logs a warning at start-up
    in passive mode.

* **Browser capture is not attestation.** JavaScript cannot prove frames came from a physical camera. A
  virtual camera, a modified client or digital injection can feed arbitrary frames. MiniFASNet looks for
  *re-capture* artefacts (print, screen), so a digitally injected image of a real face can score as live
  (the public `lena.jpg` scores 0.997). Mitigations in place: random challenge, temporal checks, PAD, audit.
  For high-assurance use, run on managed kiosks / MDM-controlled devices, consider hardware with IR/depth,
  and treat remote attendance as lower assurance.
* **A real-time deepfake/puppet that follows the prompts** can defeat the active challenge; PAD performance
  against such attacks and against 3D masks is unknown.
* **PAD has not been evaluated on a presentation-attack dataset** in this repository (none was available
  under a usable licence). Thresholds are conservative engineering defaults. Run `scripts/evaluate_pad.py`
  on site data before production.
* Head pose comes from 5 landmarks — coarse; extreme lighting or partial occlusion causes false rejects.
* Browser geolocation is spoofable; geofencing is a deterrent, not proof of presence.
* Face recognition accuracy varies across demographics, lighting and cameras. Calibrate on your population.
* The quality-check pre-flight rate limiter is per process (advisory endpoint only).
* The mobile trial build talks plain HTTP to a laptop on the local Wi-Fi (`usesCleartextTraffic`) and stores the
  session token in the platform keystore (`flutter_secure_storage`). Tokens on an unencrypted LAN can be sniffed:
  use HTTPS for anything beyond a trial.
* Deleted templates may persist in PostgreSQL WAL/backups until those roll over; plan backup retention.

## Biometric data protection

* Only an encrypted 128-d template (SFace) per user is stored; raw frames are processed in memory and
  discarded after the request (each frame is below the 1 MiB multipart spool threshold, so it never
  touches disk).
* Consent is explicit, versioned and hashed; withdrawal deletes the template immediately.
* Self-service deletion and data export (`/me/export`; template metadata only). Admin deletion is audited.
* Templates of deactivated users are purged after `retention.inactive_biometric_days` by `app.cli purge`.
* Verification attempts (scores, reasons — no images) are kept `retention.verification_attempts_days`.
* Never log images, embeddings, tokens or passwords. The log formatter redacts known sensitive keys.

## Key management

`BIOMETRIC_ENCRYPTION_KEYS="k2027:<new>,k2026:<old>"` — the first key encrypts, all keys decrypt. Rotate by
prepending a new key, deploying, running `python -m app.cli rotate-template-keys`, then removing the old key
after backups that need it expire. Losing every key makes all templates unreadable (users must re-enroll).

## Reporting a vulnerability

Report privately to the system owner's security contact; do not open a public issue. Include steps to
reproduce and the request id (`X-Request-ID`) of affected requests.
