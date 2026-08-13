"""Restore (or verify) a backup created by scripts/backup.py.

A backup nobody has ever restored from is not a tested backup -- this
exists so restoring is a real, exercised path, not an assumption.

Usage:
    # Restore into a throwaway verification database (default, safe --
    # never touches your real data) and print row counts so you can
    # confirm the backup is actually usable:
    python scripts/restore_backup.py --backup latest

    # Restore a specific backup instead of the newest one:
    python scripts/restore_backup.py --backup backups/2026-08-13T12-00-00Z

    # Actually restore over the LIVE database -- only for a real
    # disaster-recovery situation. Requires typing the database name to
    # confirm, same guard rail as a destructive migration would use.
    python scripts/restore_backup.py --backup latest --live

The data/ files archive is extracted alongside the chosen backup folder
as <backup>/data-extracted/ for manual inspection -- it is never
auto-copied over the live data/ directory, live or not, since that would
silently overwrite uploaded files with no equivalent "throwaway target"
safety net to fall back on.
"""

from __future__ import annotations

import argparse
import gzip
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", str(PROJECT_ROOT / "backups")))

POSTGRES_CONTAINER = os.environ.get("POSTGRES_CONTAINER", "deeptutor-postgres")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "deeptutor")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "deeptutor")
LIVE_DB = os.environ.get("POSTGRES_DB", "deeptutor")
VERIFY_DB = os.environ.get("POSTGRES_VERIFY_DB", "deeptutor_restore_verify")


def _docker() -> str:
    docker = shutil.which("docker")
    if not docker:
        print("ERROR: docker was not found on PATH", file=sys.stderr)
        sys.exit(1)
    return docker


def _psql(*args: str, database: str = "postgres") -> subprocess.CompletedProcess:
    docker = _docker()
    env = os.environ.copy()
    env["PGPASSWORD"] = POSTGRES_PASSWORD
    cmd = [docker, "exec", "-i", "-e", f"PGPASSWORD={POSTGRES_PASSWORD}", POSTGRES_CONTAINER,
           "psql", "-U", POSTGRES_USER, "-d", database, *args]
    return subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)


def resolve_backup(spec: str) -> Path:
    if spec == "latest":
        candidates = sorted(
            (p for p in BACKUP_DIR.iterdir() if p.is_dir() and (p / "manifest.json").exists()),
            key=lambda p: p.name,
        )
        if not candidates:
            print(f"No backups found under {BACKUP_DIR}", file=sys.stderr)
            sys.exit(1)
        return candidates[-1]
    path = Path(spec)
    if not path.exists():
        print(f"Backup path not found: {path}", file=sys.stderr)
        sys.exit(1)
    return path


def restore_database(backup: Path, target_db: str) -> None:
    dump_path = backup / "database.sql.gz"
    if not dump_path.exists():
        print(f"No database.sql.gz in {backup}", file=sys.stderr)
        sys.exit(1)

    docker = _docker()
    env = os.environ.copy()
    env["PGPASSWORD"] = POSTGRES_PASSWORD

    print(f"Dropping and recreating database '{target_db}'...")
    drop = subprocess.run(
        [docker, "exec", "-e", f"PGPASSWORD={POSTGRES_PASSWORD}", POSTGRES_CONTAINER,
         "psql", "-U", POSTGRES_USER, "-d", "postgres", "-c", f'DROP DATABASE IF EXISTS "{target_db}";'],
        env=env, capture_output=True, text=True, check=False,
    )
    if drop.returncode != 0:
        print(f"DROP DATABASE failed: {drop.stderr}", file=sys.stderr)
        sys.exit(1)
    create = subprocess.run(
        [docker, "exec", "-e", f"PGPASSWORD={POSTGRES_PASSWORD}", POSTGRES_CONTAINER,
         "psql", "-U", POSTGRES_USER, "-d", "postgres", "-c", f'CREATE DATABASE "{target_db}";'],
        env=env, capture_output=True, text=True, check=False,
    )
    if create.returncode != 0:
        print(f"CREATE DATABASE failed: {create.stderr}", file=sys.stderr)
        sys.exit(1)

    print(f"Restoring {dump_path.name} into '{target_db}'...")
    with gzip.open(dump_path, "rb") as f:
        sql_bytes = f.read()
    restore = subprocess.run(
        [docker, "exec", "-i", "-e", f"PGPASSWORD={POSTGRES_PASSWORD}", POSTGRES_CONTAINER,
         "psql", "-U", POSTGRES_USER, "-d", target_db, "-v", "ON_ERROR_STOP=1"],
        input=sql_bytes, env=env, capture_output=True, check=False,
    )
    if restore.returncode != 0:
        print(f"Restore failed: {restore.stderr.decode('utf-8', errors='replace')}", file=sys.stderr)
        sys.exit(1)


def print_row_counts(target_db: str) -> None:
    tables = ["users", "course_units", "enrollments", "assignments", "submissions"]
    print(f"\nRow counts in '{target_db}':")
    for table in tables:
        result = _psql("-t", "-c", f"SELECT count(*) FROM {table};", database=target_db)
        count = result.stdout.strip() if result.returncode == 0 else f"error: {result.stderr.strip()}"
        print(f"  {table}: {count}")


def extract_data_archive(backup: Path) -> Path:
    archive = backup / "data.tar.gz"
    dest = backup / "data-extracted"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    print(f"Extracting {archive.name} to {dest} for inspection (not auto-copied anywhere)...")
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(dest)
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backup", required=True, help='Backup folder path, or "latest"')
    parser.add_argument(
        "--live",
        action="store_true",
        help=f"Restore over the LIVE database ('{LIVE_DB}') instead of a throwaway verification database",
    )
    args = parser.parse_args()

    backup = resolve_backup(args.backup)
    print(f"Using backup: {backup}")

    target_db = LIVE_DB if args.live else VERIFY_DB

    if args.live:
        print(
            f"\n*** This will DROP AND REPLACE the LIVE database '{LIVE_DB}' with the "
            f"contents of {backup.name}. This cannot be undone. ***"
        )
        typed = input(f"Type the database name ('{LIVE_DB}') to confirm, anything else cancels: ")
        if typed != LIVE_DB:
            print("Cancelled -- input did not match.")
            return 1

    restore_database(backup, target_db)
    print_row_counts(target_db)
    extract_data_archive(backup)

    if not args.live:
        print(
            f"\nVerification restore complete in database '{target_db}' -- your live "
            f"database was not touched. Drop it manually when done inspecting: "
            f'docker exec {POSTGRES_CONTAINER} psql -U {POSTGRES_USER} -d postgres '
            f'-c \'DROP DATABASE "{target_db}";\''
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
