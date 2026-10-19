"""Integration fixtures: a dedicated PostgreSQL test database, migrated with Alembic, truncated between
tests; a controllable clock; and the TEST FIXTURE vision provider (see app/biometrics/fake.py).

Safety: the suite refuses to run unless the database name ends in ``_test``.
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, make_url, text

BACKEND = Path(__file__).resolve().parents[2]


def _test_db_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        env_file = BACKEND / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("TEST_DATABASE_URL="):
                    url = line.split("=", 1)[1].strip()
    if not url:
        pytest.exit("TEST_DATABASE_URL is not set (see .env.example); integration tests need PostgreSQL", 2)
    if not (make_url(url).database or "").endswith("_test"):
        pytest.exit("Refusing to run: TEST_DATABASE_URL must point at a database whose name ends in '_test'", 2)
    return url


TEST_DB_URL = _test_db_url()
# Force every code path (including anything calling get_settings()) onto the test database and fixture provider.
os.environ.update(
    {
        "APP_ENV": "test",
        "DATABASE_URL": TEST_DB_URL,
        "BIOMETRIC_ENCRYPTION_KEYS": "ktest:" + base64.b64encode(os.urandom(32)).decode(),
        "VISION_PROVIDER": "fake",
        "PAD_MODE": "required",
        "LIVENESS_CHALLENGE": "active",
        "COOKIE_SECURE": "false",
        "ALLOWED_ORIGINS": "http://testserver",
        "LOG_LEVEL": "WARNING",
        # Attendance tests jump the clock across a working day; keep sessions alive across those jumps.
        "SESSION_IDLE_TIMEOUT_MINUTES": "1440",
        "SESSION_ABSOLUTE_TTL_MINUTES": "2880",
    }
)

from fastapi.testclient import TestClient  # noqa: E402

from app.biometrics.provider import reset_provider  # noqa: E402
from app.core.config import Settings, get_settings  # noqa: E402
from app.db.base import set_clock  # noqa: E402
from app.db.session import dispose_engine  # noqa: E402
from app.main import create_app  # noqa: E402

TABLES = [
    "attendance_corrections",
    "attendance_records",
    "biometric_templates",
    "enrollment_sessions",
    "verification_attempts",
    "liveness_challenges",
    "biometric_consents",
    "idempotency_keys",
    "shift_assignments",
    "system_settings",
    "auth_sessions",
    "login_attempts",
    "audit_events",
    "users",
    "holidays",
    "shifts",
    "workplaces",
]


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 12, 1, 5, tzinfo=UTC)  # Monday 08:05 Asia/Jakarta

    def __call__(self) -> datetime:
        return self.now

    def set(self, value: datetime) -> None:
        assert value.tzinfo is not None
        self.now = value.astimezone(UTC)

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)


@pytest.fixture(scope="session")
def migrated_db() -> str:
    env = {**os.environ, "ALEMBIC_DATABASE_URL": TEST_DB_URL}
    alembic = [sys.executable, "-m", "alembic"]
    for args in (["downgrade", "base"], ["upgrade", "head"], ["check"]):
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [*alembic, *args], cwd=BACKEND, env=env, capture_output=True, text=True, check=False
        )
        if proc.returncode != 0:
            pytest.exit(f"alembic {' '.join(args)} failed:\n{proc.stdout}\n{proc.stderr}", 2)
    return TEST_DB_URL


@pytest.fixture(scope="session")
def settings(migrated_db: str) -> Settings:
    get_settings.cache_clear()
    s = get_settings()
    assert s.app_env == "test" and s.vision_provider == "fake"
    return s


@pytest.fixture(scope="session")
def app(settings: Settings) -> Iterator[object]:
    reset_provider()
    application = create_app(settings)
    with TestClient(application):  # runs lifespan once for the whole session
        yield application
    dispose_engine()
    reset_provider()


@pytest.fixture
def clock() -> Iterator[Clock]:
    c = Clock()
    set_clock(c)
    yield c
    set_clock(None)


@pytest.fixture(autouse=True)
def _clean_db(migrated_db: str, request: pytest.FixtureRequest) -> Iterator[None]:
    yield
    engine = create_engine(migrated_db)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE"))
    engine.dispose()


@pytest.fixture
def db(app: object) -> Iterator[object]:
    from app.db.session import get_session_factory

    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client_factory(app: object) -> Iterator[object]:
    clients: list[TestClient] = []

    def make() -> TestClient:
        c = TestClient(app, base_url="http://testserver")  # type: ignore[arg-type]
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.close()
