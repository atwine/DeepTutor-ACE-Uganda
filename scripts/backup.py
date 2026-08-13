"""Back up the database and the data/ files tree (issue #78).

Two things make up "the data" in this app:

1. Postgres (accounts, enrollments, grades, submissions, course structure)
   -- lives in a Docker-managed named volume, not a plain folder on disk,
   so the only reliable way to back it up is a logical dump (``pg_dump``)
   run inside the running container.
2. ``data/`` (uploaded course materials, generated books, per-user chat
   history SQLite files, knowledge-base indexes, settings) -- a plain
   bind-mounted folder, backed up as a compressed archive.

Usage:
    python scripts/backup.py

Writes a timestamped folder under ``backups/`` (override with
BACKUP_DIR) containing ``database.sql.gz``, ``data.tar.gz``, and a
``manifest.json`` recording what was captured. Deletes backup folders
older than BACKUP_RETENTION_DAYS (default 14) once a new backup succeeds.

Exits 1 on any failure (including a suspiciously small/empty database
dump) so this is safe to run on a schedule (cron / Windows Task
Scheduler / a systemd timer, whatever the eventual host uses) and alert
on a nonzero exit code.

Configuration (all optional, defaults match this repo's docker-compose.yml):
    POSTGRES_CONTAINER      default "deeptutor-postgres"
    POSTGRES_USER           default "deeptutor"
    POSTGRES_PASSWORD       default "deeptutor"
    POSTGRES_DB             default "deeptutor"
    DEEPTUTOR_DATA_DIR      default "<repo>/data"
    BACKUP_DIR              default "<repo>/backups"
    BACKUP_RETENTION_DAYS   default 14
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

POSTGRES_CONTAINER = os.environ.get("POSTGRES_CONTAINER", "deeptutor-postgres")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "deeptutor")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "deeptutor")
POSTGRES_DB = os.environ.get("POSTGRES_DB", "deeptutor")
DATA_DIR = Path(os.environ.get("DEEPTUTOR_DATA_DIR", str(PROJECT_ROOT / "data")))
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", str(PROJECT_ROOT / "backups")))
RETENTION_DAYS = int(os.environ.get("BACKUP_RETENTION_DAYS", "14"))

# A dump this small means pg_dump silently produced (near-)nothing --
# schema-only output, an auth failure that still exited 0, etc. -- treat
# it as a failure rather than filing away an unusable "backup".
MIN_DUMP_BYTES = 200


def _docker() -> str:
    docker = shutil.which("docker")
    if not docker:
        print("ERROR: docker was not found on PATH", file=sys.stderr)
        sys.exit(1)
    return docker


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def dump_database(dest: Path) -> int:
    """Run pg_dump inside the Postgres container and write a gzipped SQL
    dump to ``dest``. Returns the compressed file's byte size."""
    docker = _docker()
    env = os.environ.copy()
    env["PGPASSWORD"] = POSTGRES_PASSWORD
    cmd = [
        docker,
        "exec",
        "-e",
        f"PGPASSWORD={POSTGRES_PASSWORD}",
        POSTGRES_CONTAINER,
        "pg_dump",
        "-U",
        POSTGRES_USER,
        "--no-owner",
        "--no-privileges",
        POSTGRES_DB,
    ]
    print(f"Dumping database '{POSTGRES_DB}' from container '{POSTGRES_CONTAINER}'...")
    proc = subprocess.run(cmd, env=env, capture_output=True, check=False)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"pg_dump failed (exit {proc.returncode}): {stderr}")
    with gzip.open(dest, "wb") as f:
        f.write(proc.stdout)
    size = dest.stat().st_size
    if size < MIN_DUMP_BYTES:
        raise RuntimeError(
            f"database dump is suspiciously small ({size} bytes) -- "
            "treating as a failed backup rather than filing away something unusable"
        )
    return size


def archive_data_dir(dest: Path) -> int:
    """Compress ``DATA_DIR`` into ``dest`` (.tar.gz). Returns byte size."""
    if not DATA_DIR.exists():
        raise RuntimeError(f"data directory not found: {DATA_DIR}")
    print(f"Archiving {DATA_DIR}...")
    with tarfile.open(dest, "w:gz") as tar:
        tar.add(DATA_DIR, arcname="data")
    return dest.stat().st_size


def prune_old_backups(keep_dir: Path) -> list[str]:
    """Delete backup folders older than RETENTION_DAYS. Never touches the
    backup just created (``keep_dir``), even if the clock is weird."""
    if not BACKUP_DIR.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    removed = []
    for entry in BACKUP_DIR.iterdir():
        if not entry.is_dir() or entry == keep_dir:
            continue
        manifest_path = entry / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            created_at = datetime.fromisoformat(manifest["created_at"])
        except Exception:
            # No readable manifest -- fall back to directory mtime rather
            # than skipping it forever (a backup with no timestamp record
            # would otherwise never get pruned).
            created_at = datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc)
        if created_at < cutoff:
            shutil.rmtree(entry, ignore_errors=True)
            removed.append(entry.name)
    return removed


def main() -> int:
    started = time.monotonic()
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    backup_dir = BACKUP_DIR / timestamp
    backup_dir.mkdir(parents=True, exist_ok=False)

    try:
        db_size = dump_database(backup_dir / "database.sql.gz")
        data_size = archive_data_dir(backup_dir / "data.tar.gz")
    except Exception as exc:
        print(f"BACKUP FAILED: {exc}", file=sys.stderr)
        shutil.rmtree(backup_dir, ignore_errors=True)
        return 1

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "database_bytes": db_size,
        "data_archive_bytes": data_size,
        "postgres_db": POSTGRES_DB,
    }
    (backup_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    removed = prune_old_backups(backup_dir)

    elapsed = time.monotonic() - started
    print(
        f"Backup complete: {backup_dir} "
        f"(database.sql.gz={db_size / 1024:.1f} KB, "
        f"data.tar.gz={data_size / 1024 / 1024:.1f} MB) in {elapsed:.1f}s"
    )
    if removed:
        print(f"Pruned {len(removed)} backup(s) older than {RETENTION_DAYS} days: {', '.join(removed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
