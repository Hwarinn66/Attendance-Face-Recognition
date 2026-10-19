"""Portable PostgreSQL for local development and tests when Docker is unavailable.

Downloads the zonky.io embedded-postgres binaries (SHA-1 verified against Maven Central), initialises a
cluster under ``.local/`` (git-ignored), runs it on 127.0.0.1 only, and creates a least-privilege
application role plus the ``absen`` and ``absen_test`` databases.

    python scripts/dev_postgres.py install     # download + verify + extract (Windows/Linux/macOS x86_64)
    python scripts/dev_postgres.py init        # initdb + start + create role/databases, writes .local/pg.env
    python scripts/dev_postgres.py start|stop|status

The generated passwords live only in ``.local/`` and are printed as connection URLs for your ``.env``.
Use Docker Compose (see README) whenever it is available; this script is a fallback.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import platform
import secrets
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local"
PGROOT = LOCAL / "pgsql"
PGDATA = LOCAL / "pgdata"
PORT = 55432
VERSION = "17.11.0"
APP_ROLE = "absen"
DATABASES = ("absen", "absen_test", "absen_e2e")
MAVEN = "https://repo1.maven.org/maven2/io/zonky/test/postgres"


def _platform_artifact() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = "amd64" if machine in {"x86_64", "amd64"} else "arm64v8" if machine in {"arm64", "aarch64"} else machine
    name = {"windows": "windows", "linux": "linux", "darwin": "darwin"}[system]
    return f"embedded-postgres-binaries-{name}-{arch}"


def _bin(name: str) -> str:
    return str(PGROOT / "bin" / (name + (".exe" if platform.system() == "Windows" else "")))


def install() -> None:
    if (PGROOT / "bin").exists():
        print(f"already installed in {PGROOT}")
        return
    artifact = _platform_artifact()
    base = f"{MAVEN}/{artifact}/{VERSION}/{artifact}-{VERSION}.jar"
    print(f"downloading {base}")
    jar = urllib.request.urlopen(base, timeout=300).read()  # noqa: S310 - fixed HTTPS URL
    expected = urllib.request.urlopen(base + ".sha1", timeout=60).read().decode().split()[0]  # noqa: S310
    actual = hashlib.sha1(jar, usedforsecurity=False).hexdigest()
    if actual != expected:
        sys.exit(f"SHA-1 mismatch: expected {expected}, got {actual}")
    with zipfile.ZipFile(io.BytesIO(jar)) as zf:
        member = next(n for n in zf.namelist() if n.endswith(".txz"))
        with tarfile.open(fileobj=io.BytesIO(zf.read(member)), mode="r:xz") as tf:
            tf.extractall(PGROOT, filter="data")
    print(f"installed PostgreSQL {VERSION} into {PGROOT}")


def _run(args: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, text=True, capture_output=True, **kw)  # type: ignore[call-overload]


def start() -> None:
    if status(quiet=True):
        print("already running")
        return
    log = LOCAL / "pg.log"
    # Detach fully so the server does not inherit this console's handles.
    flags = 0
    if platform.system() == "Windows":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    subprocess.Popen(
        [_bin("pg_ctl"), "-D", str(PGDATA), "-l", str(log), "-o", f"-p {PORT} -c listen_addresses=localhost", "start"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags,
    )
    for _ in range(60):
        if status(quiet=True):
            print(f"running on 127.0.0.1:{PORT}")
            return
        time.sleep(0.5)
    sys.exit(f"server did not start; see {log}")


def stop() -> None:
    _run([_bin("pg_ctl"), "-D", str(PGDATA), "-m", "fast", "stop"])
    print("stopped")


def status(quiet: bool = False) -> bool:
    proc = subprocess.run([_bin("pg_ctl"), "-D", str(PGDATA), "status"], capture_output=True, text=True, check=False)
    running = proc.returncode == 0
    if not quiet:
        print("running" if running else "not running")
    return running


def init() -> None:
    install()
    LOCAL.mkdir(exist_ok=True)
    su_pw_file = LOCAL / "pg_password.txt"
    if not PGDATA.exists():
        su_pw_file.write_text(secrets.token_urlsafe(24), encoding="utf-8")
        _run([_bin("initdb"), "-D", str(PGDATA), "-U", "postgres", f"--pwfile={su_pw_file}", "-A", "scram-sha-256",
              "-E", "UTF8", "--locale=C"])
    start()
    import psycopg  # noqa: PLC0415 - available in the backend virtualenv

    su_pw = su_pw_file.read_text(encoding="utf-8").strip()
    app_pw_file = LOCAL / "app_password.txt"
    if not app_pw_file.exists():
        app_pw_file.write_text(secrets.token_urlsafe(24), encoding="utf-8")
    app_pw = app_pw_file.read_text(encoding="utf-8").strip()
    with psycopg.connect(host="127.0.0.1", port=PORT, user="postgres", password=su_pw, dbname="postgres",
                         autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_ROLE,)).fetchone()
        if not exists:
            conn.execute(f'CREATE ROLE "{APP_ROLE}" LOGIN NOSUPERUSER NOCREATEROLE NOCREATEDB '
                         f"PASSWORD '{app_pw}'")  # role/password are generated locally, not user input
        for db in DATABASES:
            if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db,)).fetchone():
                conn.execute(f'CREATE DATABASE "{db}" OWNER "{APP_ROLE}"')
    env = LOCAL / "pg.env"
    env.write_text(
        f"DATABASE_URL=postgresql+psycopg://{APP_ROLE}:{app_pw}@127.0.0.1:{PORT}/absen\n"
        f"TEST_DATABASE_URL=postgresql+psycopg://{APP_ROLE}:{app_pw}@127.0.0.1:{PORT}/absen_test\n"
        f"E2E_DATABASE_URL=postgresql+psycopg://{APP_ROLE}:{app_pw}@127.0.0.1:{PORT}/absen_e2e\n",
        encoding="utf-8",
    )
    print(f"databases ready; connection URLs written to {env}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["install", "init", "start", "stop", "status"])
    cmd = parser.parse_args().command
    {"install": install, "init": init, "start": start, "stop": stop, "status": status}[cmd]()


if __name__ == "__main__":
    main()
