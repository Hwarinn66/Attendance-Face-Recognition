"""Application configuration.

All configuration comes from environment variables (optionally a ``.env`` file in development).
``Settings.validate_for_environment`` enforces production-safety invariants at startup so that an
insecure configuration fails loudly instead of running.
"""

from __future__ import annotations

import base64
import binascii
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.biometrics.types import CHALLENGE_ACTIONS, PoseState

BACKEND_DIR = Path(__file__).resolve().parents[2]

AppEnv = Literal["development", "test", "production"]


class ConfigurationError(RuntimeError):
    """Raised when the configuration is unsafe or inconsistent."""


def parse_keyring(raw: str) -> dict[str, bytes]:
    """Parse ``kid:base64key[,kid:base64key...]``. The first key is the active encryption key."""
    keys: dict[str, bytes] = {}
    for part in [p.strip() for p in raw.split(",") if p.strip()]:
        kid, sep, b64 = part.partition(":")
        if not sep or not kid:
            raise ConfigurationError("BIOMETRIC_ENCRYPTION_KEYS entries must look like 'kid:base64key'")
        try:
            key = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ConfigurationError(f"biometric key '{kid}' is not valid base64") from exc
        if len(key) != 32:
            raise ConfigurationError(f"biometric key '{kid}' must decode to 32 bytes (AES-256)")
        if kid in keys:
            raise ConfigurationError(f"duplicate biometric key id '{kid}'")
        keys[kid] = key
    return keys


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False)

    app_env: AppEnv = "development"
    app_name: str = "Absen"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"

    database_url: SecretStr = Field(..., description="SQLAlchemy URL, e.g. postgresql+psycopg://...")
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_statement_timeout_ms: int = 15_000

    # --- sessions / cookies -------------------------------------------------------------
    session_cookie_name: str = "absen_session"
    csrf_cookie_name: str = "absen_csrf"
    csrf_header_name: str = "X-CSRF-Token"
    session_absolute_ttl_minutes: int = Field(default=12 * 60, ge=5)
    session_idle_timeout_minutes: int = Field(default=60, ge=5)
    cookie_secure: bool = True
    cookie_samesite: Literal["lax", "strict"] = "lax"
    # Origins allowed to make state-changing requests (Origin header check) and for CORS.
    allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    trust_forwarded_for: bool = False
    # Live dashboard (Server-Sent Events). A stream ends after this long and the browser reconnects, which
    # re-checks the session, so a revoked or expired login stops receiving events within this window.
    live_events_stream_seconds: float = Field(default=120.0, ge=1.0, le=3600.0)
    live_events_heartbeat_seconds: float = Field(default=15.0, ge=0.5, le=120.0)

    # --- rate limits ------------------------------------------------------------------------
    login_max_failures: int = 5
    login_window_minutes: int = 15
    login_ip_max_failures: int = 30
    verification_max_attempts: int = 10
    verification_window_minutes: int = 15
    quality_check_max_per_minute: int = 30

    # --- request limits -----------------------------------------------------------------
    max_request_bytes: int = 12 * 1024 * 1024
    max_frame_bytes: int = 512 * 1024
    max_frame_pixels: int = 1280 * 960
    min_frames: int = 12
    max_frames: int = 90

    # --- biometrics -----------------------------------------------------------------------
    biometric_encryption_keys: SecretStr = Field(..., description="kid:base64(32 bytes)[,...]; first is active")
    weights_dir: Path = BACKEND_DIR / "weights"
    vision_provider: Literal["onnx", "fake"] = "onnx"
    recognizer: Literal["sface", "arcface"] = "sface"
    arcface_model_file: str = "w600k_mbf.onnx"
    pad_mode: Literal["required", "disabled"] = "required"
    inference_workers: int = Field(default=2, ge=1, le=16)
    inference_timeout_seconds: float = 45.0

    # Detector / quality (engineering defaults; see docs/BIOMETRICS.md for calibration)
    detector_score_threshold: float = 0.6
    quality_min_detection_score: float = 0.85
    quality_min_face_width_ratio: float = 0.18
    quality_max_face_width_ratio: float = 0.80
    quality_min_brightness: float = 50.0
    quality_max_brightness: float = 215.0
    quality_min_sharpness: float = 25.0
    quality_max_center_offset: float = 0.30

    # Face matching. SFace default threshold 0.363 is the value published by OpenCV Zoo (LFW);
    # it is NOT locally validated. The margin creates an "uncertain -> retry" band above it.
    match_threshold: float | None = None
    match_uncertainty_margin: float = 0.05
    identity_conflict_threshold: float | None = None

    # Passive PAD (MiniFASNet ensemble live probability). Uncalibrated conservative defaults.
    pad_threshold: float = 0.80
    pad_uncertainty_margin: float = 0.10
    pad_max_frames: int = 5

    # Active challenge
    # active  = randomised head-movement prompts (layer B) + PAD + temporal checks.
    # passive = the user only looks straight at the camera for a few seconds; liveness then rests on PAD and
    #           temporal/identity continuity alone. Weaker against replayed video and injected images
    #           (SECURITY.md); chosen per deployment by the system owner.
    liveness_challenge: Literal["active", "passive"] = "active"
    challenge_passive_min_seconds: float = 2.0
    challenge_steps: int = Field(default=2, ge=2, le=5)
    # Movements the server may request. Default: left/right only. look_up/look_down are supported but off:
    # on real phone captures most failed challenges came from the vertical steps (look_up was not separable
    # from still-face pitch jitter with 5-point landmarks), see docs/BIOMETRICS.md.
    challenge_actions: str = "turn_left,turn_right"
    challenge_ttl_seconds: int = Field(default=60, ge=15, le=300)
    challenge_min_seconds_per_step: float = 1.0
    challenge_yaw_threshold: float = 0.15
    challenge_pitch_threshold: float = 0.10
    challenge_center_yaw_tolerance: float = 0.07
    challenge_center_pitch_tolerance: float = 0.06
    challenge_min_pose_frames: int = 2
    challenge_max_missing_ratio: float = 0.25
    challenge_max_consecutive_missing: int = 6
    temporal_max_center_jump: float = 0.6
    temporal_max_scale_jump: float = 1.6
    verification_validity_seconds: int = 120

    # Enrollment
    enrollment_min_frontal_frames: int = 3
    enrollment_max_attempts_per_day: int = 5
    consent_version: str = "2026-10-v1"

    # Retention (days)
    retention_verification_attempts_days: int = 90
    retention_audit_days: int = 730
    retention_login_attempts_days: int = 30
    retention_inactive_biometric_days: int = 30

    # Development seed — only honoured when APP_ENV=development
    dev_seed_admin_email: str | None = None
    dev_seed_admin_password: SecretStr | None = None

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [v.strip().rstrip("/") for v in value.split(",") if v.strip()]
        return value

    @field_validator("challenge_actions")
    @classmethod
    def _check_actions(cls, value: str) -> str:
        names = [v.strip() for v in value.split(",") if v.strip()]
        allowed = {a.value for a in CHALLENGE_ACTIONS}
        unknown = sorted(set(names) - allowed)
        if unknown:
            raise ValueError(f"unknown challenge actions: {unknown}; allowed: {sorted(allowed)}")
        if len(set(names)) < 2:
            raise ValueError("at least two distinct challenge actions are required")
        return ",".join(dict.fromkeys(names))

    @model_validator(mode="after")
    def _derive_defaults(self) -> Settings:
        if self.match_threshold is None and self.recognizer == "sface":
            self.match_threshold = 0.363
        if self.identity_conflict_threshold is None and self.match_threshold is not None:
            self.identity_conflict_threshold = self.match_threshold
        return self

    # ------------------------------------------------------------------------------------
    @property
    def challenge_action_states(self) -> tuple[PoseState, ...]:
        return tuple(PoseState(v) for v in self.challenge_actions.split(","))

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def keyring(self) -> dict[str, bytes]:
        return parse_keyring(self.biometric_encryption_keys.get_secret_value())

    def validate_for_environment(self) -> None:
        """Raise ConfigurationError if the configuration is unsafe for ``app_env``."""
        problems: list[str] = []
        keyring = self.keyring
        if not keyring:
            problems.append("BIOMETRIC_ENCRYPTION_KEYS must contain at least one key")
        if self.match_threshold is None:
            problems.append("MATCH_THRESHOLD must be set explicitly for the arcface recognizer (calibrate it first)")
        if self.vision_provider == "fake" and self.app_env != "test":
            problems.append("VISION_PROVIDER=fake is a test fixture and is only allowed when APP_ENV=test")
        if self.is_production:
            if not self.cookie_secure:
                problems.append("COOKIE_SECURE must be true in production")
            if self.pad_mode != "required":
                problems.append("PAD_MODE must be 'required' in production (fail closed)")
            if not self.allowed_origins:
                problems.append("ALLOWED_ORIGINS must list the HTTPS frontend origin(s) in production")
            if any(not o.startswith("https://") for o in self.allowed_origins):
                problems.append("ALLOWED_ORIGINS must be HTTPS in production")
            if self.dev_seed_admin_password is not None:
                problems.append("DEV_SEED_ADMIN_PASSWORD must not be set in production")
        if problems:
            raise ConfigurationError("; ".join(problems))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # values come from the environment
