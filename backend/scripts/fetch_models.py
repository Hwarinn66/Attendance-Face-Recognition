"""Download and verify pretrained model weights listed in ``weights/manifest.json``.

Usage (from ``backend/``)::

    uv run python scripts/fetch_models.py              # required models
    uv run python scripts/fetch_models.py --with-arcface   # + optional InsightFace ArcFace (non-commercial!)
    uv run python scripts/fetch_models.py --pin        # write computed SHA-256 for entries that lack one

Every file is verified against the pinned SHA-256 (and, where the upstream only
publishes a git blob SHA-1, against that). A mismatch deletes the download and
exits non-zero. Nothing is ever faked: if a download fails the script says so.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
WEIGHTS_DIR = BACKEND_DIR / "weights"
MANIFEST = WEIGHTS_DIR / "manifest.json"
TIMEOUT_S = 120


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_blob_sha1(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode()
    return hashlib.sha1(header + data, usedforsecurity=False).hexdigest()


def download(url: str) -> bytes:
    if not url.startswith("https://"):
        raise ValueError(f"refusing non-HTTPS URL: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "absen-fetch-models/1"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return bytes(resp.read())


def verify(name: str, entry: dict[str, Any], data: bytes) -> str:
    digest = sha256_of(data)
    expected = entry.get("sha256")
    if expected and digest != expected:
        raise RuntimeError(f"{name}: SHA-256 mismatch (expected {expected}, got {digest})")
    blob = entry.get("git_blob_sha1")
    if blob and git_blob_sha1(data) != blob:
        raise RuntimeError(f"{name}: git blob SHA-1 mismatch (expected {blob})")
    return digest


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def fetch_entry(name: str, entry: dict[str, Any], force: bool) -> str:
    target = WEIGHTS_DIR / entry["file"]
    if target.exists() and not force:
        digest = verify(name, entry, target.read_bytes())
        print(f"[ok]   {name}: present, verified ({digest[:16]}...)")
        return digest
    if "archive_url" in entry:
        print(f"[get]  {name}: {entry['archive_url']}")
        archive = download(entry["archive_url"])
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            member = next((m for m in zf.namelist() if m.endswith(entry["archive_member"])), None)
            if member is None:
                raise RuntimeError(f"{name}: {entry['archive_member']} not found in archive")
            data = zf.read(member)
    else:
        print(f"[get]  {name}: {entry['url']}")
        data = download(entry["url"])
    digest = verify(name, entry, data)
    write_atomic(target, data)
    print(f"[ok]   {name}: saved {target.name} ({len(data)} bytes, sha256 {digest})")
    return digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--with-arcface", action="store_true", help="also fetch optional InsightFace ArcFace")
    parser.add_argument("--pin", action="store_true", help="record computed SHA-256 for unpinned entries")
    parser.add_argument("--force", action="store_true", help="re-download even if present")
    args = parser.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    sections = [("models", manifest["models"])]
    if args.with_arcface:
        print("WARNING: InsightFace pretrained weights are licensed for NON-COMMERCIAL research use only.")
        sections.append(("optional", manifest["optional"]))

    failures = 0
    changed = False
    for section, entries in sections:
        for name, entry in entries.items():
            try:
                digest = fetch_entry(name, entry, args.force)
            except Exception as exc:  # report every failure, keep going, exit non-zero
                print(f"[FAIL] {name}: {exc}", file=sys.stderr)
                failures += 1
                continue
            if entry.get("sha256") is None:
                if args.pin:
                    manifest[section][name]["sha256"] = digest
                    changed = True
                    print(f"[pin]  {name}: sha256 recorded")
                else:
                    print(f"[warn] {name}: no pinned sha256 in manifest; run with --pin to record {digest}")
    if changed:
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if failures:
        print(f"{failures} artifact(s) failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
