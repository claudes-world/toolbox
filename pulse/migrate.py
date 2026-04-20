"""pulse/migrate.py — v0→v1 SQLite migration."""

from __future__ import annotations

import contextlib
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_V0_USER_VERSION = 10
_V1_USER_VERSION = 11
_SAFETY_MULTIPLIER = 2  # backup size × 2 must fit in free space


def _get_user_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _integrity_check(conn: sqlite3.Connection) -> bool:
    result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    return result == "ok"


def _verify_v0_schema_shape(conn: sqlite3.Connection) -> bool:
    """Verify expected v0 tables exist (for user_version=0 DBs)."""
    expected = {"snapshots", "repos", "prs", "issues", "releases"}
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    found = {r[0] for r in rows}
    return expected.issubset(found)


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def run_migration(db_path: Path) -> str:
    """
    Idempotent v0→v1 migration.

    Returns a status string: "no-op" if already v1, "migrated" on success.
    Raises RuntimeError with a clear message on any failure.

    Steps:
    1. Verify DB exists
    2. Open DB (read-only check of user_version)
    3. If already v1 (user_version=11), return "no-op"
    4. Pre-migration integrity check
    5. Disk-space check (backup size × 2 ≤ free space, including WAL/SHM)
    6. Backup DB → pulse.db.pre-v1-<ISO-8601-microseconds>
    7. ALTER TABLE — 4 new columns (upstream, vulnerability_alerts, node_id, review_events)
    8. Post-migration integrity check
    9. PRAGMA user_version = 11
    10. Return "migrated"
    """
    # Fix 8: refuse if pulse.db doesn't exist
    if not db_path.exists():
        raise RuntimeError(
            f"pulse.db not found at {db_path} — run pulse --now first to create it"
        )

    conn = sqlite3.connect(str(db_path))
    try:
        current_version = _get_user_version(conn)

        # Step 2: idempotency gate
        if current_version == _V1_USER_VERSION:
            return "no-op"

        # Fix 2: accept user_version=0 (pre-fix v0 DBs) OR user_version=10
        if current_version == 0:
            # Unversioned v0 DB — verify schema shape before proceeding
            if not _verify_v0_schema_shape(conn):
                raise RuntimeError(
                    "user_version=0 but expected v0 tables not found — "
                    "this does not look like a pulse v0 database"
                )
        elif current_version != _V0_USER_VERSION:
            raise RuntimeError(
                f"unexpected user_version; expected {_V0_USER_VERSION} (v0) or 0 (unversioned v0), "
                f"got {current_version}"
            )

        # Step 3: pre-migration integrity check
        if not _integrity_check(conn):
            raise RuntimeError(
                "pre-migration integrity_check failed — refusing to migrate"
            )

        # Fix 7: disk-space check includes WAL + SHM
        db_size = db_path.stat().st_size
        wal_path = db_path.with_suffix(db_path.suffix + "-wal")
        shm_path = db_path.with_suffix(db_path.suffix + "-shm")
        wal_size = wal_path.stat().st_size if wal_path.exists() else 0
        shm_size = shm_path.stat().st_size if shm_path.exists() else 0
        total_size = db_size + wal_size + shm_size
        usage = shutil.disk_usage(db_path.parent)
        if total_size * _SAFETY_MULTIPLIER > usage.free:
            raise RuntimeError(
                f"Insufficient disk space for backup: need {total_size * _SAFETY_MULTIPLIER:,} bytes "
                f"(2× {total_size:,}), have {usage.free:,} free at {db_path.parent}"
            )

        # Fix 6: microsecond uniqueness in backup filename
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = db_path.parent / f"pulse.db.pre-v1-{ts}"

        # Create backup file with 0o600 from the start (atomic, no world-readable window)
        backup_fd = os.open(str(backup_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(backup_fd)
        # Fix 5: SQLite-safe backup using built-in backup API (handles WAL mode correctly)
        # Fix 3: contextlib.closing() ensures backup_conn is closed even if backup() raises
        with contextlib.closing(sqlite3.connect(str(backup_path))) as backup_conn:
            conn.backup(backup_conn)

        # Fix 3: IF NOT EXISTS guards for ALTER TABLE
        # SQLite ALTER TABLE autocommits — with conn: does NOT roll back DDL.
        # Check column existence first to make partial migration recovery safe.
        # 4 new columns added: upstream, vulnerability_alerts, node_id, review_events.
        # schema_version already exists in v0 DDL (DEFAULT '1.0') — no ALTER needed.
        if not _column_exists(conn, "repos", "upstream"):
            conn.execute("ALTER TABLE repos ADD COLUMN upstream TEXT")
        if not _column_exists(conn, "repos", "vulnerability_alerts"):
            conn.execute("ALTER TABLE repos ADD COLUMN vulnerability_alerts TEXT")
        if not _column_exists(conn, "prs", "node_id"):
            conn.execute("ALTER TABLE prs ADD COLUMN node_id TEXT")
        if not _column_exists(conn, "prs", "review_events"):
            conn.execute("ALTER TABLE prs ADD COLUMN review_events TEXT")

        # Fix 4: integrity_check BEFORE user_version bump
        # If check fails, DB is NOT permanently tagged v1
        if not _integrity_check(conn):
            raise RuntimeError(
                f"post-migration integrity_check failed — backup preserved at {backup_path}"
            )

        # PRAGMA user_version must be OUTSIDE any transaction
        conn.execute(f"PRAGMA user_version = {_V1_USER_VERSION}")

    finally:
        conn.close()

    return "migrated"
