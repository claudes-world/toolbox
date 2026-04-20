"""pulse/migrate.py — v0→v1 SQLite migration."""

from __future__ import annotations

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


def run_migration(db_path: Path) -> str:
    """
    Idempotent v0→v1 migration.

    Returns a status string: "no-op" if already v1, "migrated" on success.
    Raises RuntimeError with a clear message on any failure.

    Steps:
    1. Open DB (read-only check of user_version)
    2. If already v1 (user_version=11), return "no-op"
    3. Pre-migration integrity check
    4. Disk-space check (backup size × 2 ≤ free space)
    5. Backup DB → pulse.db.pre-v1-<ISO-8601>
    6. ALTER TABLE in single transaction
    7. PRAGMA user_version = 11
    8. Post-migration integrity check
    9. Return "migrated"
    """
    conn = sqlite3.connect(str(db_path))
    try:
        current_version = _get_user_version(conn)

        # Step 2: idempotency gate
        if current_version == _V1_USER_VERSION:
            return "no-op"

        # Step 2b: unexpected version guard
        if current_version != _V0_USER_VERSION:
            raise RuntimeError(
                f"unexpected user_version; expected {_V0_USER_VERSION} (v0), got {current_version}"
            )

        # Step 3: pre-migration integrity check
        if not _integrity_check(conn):
            raise RuntimeError(
                "pre-migration integrity_check failed — refusing to migrate"
            )

        # Step 4: disk-space check
        db_size = db_path.stat().st_size
        usage = shutil.disk_usage(db_path.parent)
        if db_size * _SAFETY_MULTIPLIER > usage.free:
            raise RuntimeError(
                f"Insufficient disk space for backup: need {db_size * _SAFETY_MULTIPLIER:,} bytes "
                f"(2× {db_size:,}), have {usage.free:,} free at {db_path.parent}"
            )

        # Step 5: backup
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = db_path.parent / f"pulse.db.pre-v1-{ts}"
        shutil.copy2(db_path, backup_path)
        backup_path.chmod(0o600)

        # Step 6: ALTER TABLE in single transaction.
        # schema_version already exists in the v0 DDL (DEFAULT '1.0'); skip if present.
        def _existing_columns(table: str) -> set[str]:
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            return {row[1] for row in rows}

        try:
            with conn:
                conn.execute("ALTER TABLE repos ADD COLUMN upstream TEXT")
                conn.execute("ALTER TABLE repos ADD COLUMN vulnerability_alerts TEXT")
                conn.execute("ALTER TABLE prs ADD COLUMN review_events TEXT")
                if "schema_version" not in _existing_columns("snapshots"):
                    conn.execute(
                        "ALTER TABLE snapshots ADD COLUMN schema_version TEXT DEFAULT '1.1'"
                    )
        except Exception as e:
            raise RuntimeError(f"ALTER TABLE failed: {e}") from e

        # Step 7: PRAGMA user_version — must be OUTSIDE the transaction
        conn.execute(f"PRAGMA user_version = {_V1_USER_VERSION}")

        # Step 8: post-migration integrity check
        if not _integrity_check(conn):
            raise RuntimeError(
                f"post-migration integrity_check failed — backup preserved at {backup_path}"
            )

    finally:
        conn.close()

    return "migrated"
