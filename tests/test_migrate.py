"""tests/test_migrate.py — v0→v1 migration tests."""

from __future__ import annotations

import sqlite3
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from pulse.migrate import _V0_USER_VERSION, _V1_USER_VERSION, run_migration
from pulse.storage import create_schema


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_v0_db(path: Path) -> None:
    """Create a v0 pulse DB at path.

    create_schema() now sets user_version=10 internally, so no manual PRAGMA needed.
    """
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    create_schema(conn)
    conn.close()
    path.chmod(0o600)


def _make_unversioned_v0_db(path: Path) -> None:
    """Create a v0 pulse DB with user_version=0 (pre-storage.py-fix production DBs)."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    create_schema(conn)
    # Explicitly reset user_version to 0 to simulate pre-fix DB
    conn.execute("PRAGMA user_version = 0")
    conn.close()
    path.chmod(0o600)


def _column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [row[1] for row in rows]


# ── tests ─────────────────────────────────────────────────────────────────────

def test_migration_adds_columns(tmp_path: Path) -> None:
    """Fresh v0 DB → migrated to v1; 3 new columns added, user_version=11."""
    db = tmp_path / "pulse.db"
    _make_v0_db(db)

    result = run_migration(db)
    assert result == "migrated"

    conn = sqlite3.connect(str(db))
    try:
        assert _V1_USER_VERSION == conn.execute("PRAGMA user_version").fetchone()[0]
        assert "upstream" in _column_names(conn, "repos")
        assert "vulnerability_alerts" in _column_names(conn, "repos")
        assert "review_events" in _column_names(conn, "prs")
        # schema_version already exists in v0 DDL; _column_exists guard skips ALTER TABLE.
        # Only 3 new columns added by this migration.
        schema_cols = _column_names(conn, "snapshots")
        assert "schema_version" in schema_cols
    finally:
        conn.close()


def test_migration_idempotent(tmp_path: Path) -> None:
    """Second run on an already-v1 DB returns 'no-op'."""
    db = tmp_path / "pulse.db"
    _make_v0_db(db)

    first = run_migration(db)
    assert first == "migrated"

    second = run_migration(db)
    assert second == "no-op"

    conn = sqlite3.connect(str(db))
    try:
        assert _V1_USER_VERSION == conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def test_migration_disk_full(tmp_path: Path) -> None:
    """Mocked low free space → RuntimeError with 'Insufficient disk space'."""
    db = tmp_path / "pulse.db"
    _make_v0_db(db)

    import shutil
    real_usage = shutil.disk_usage(tmp_path)
    # Report effectively zero free space
    fake_usage = shutil._ntuple_diskusage(real_usage.total, real_usage.used, 0)  # type: ignore[attr-defined]

    with patch("pulse.migrate.shutil.disk_usage", return_value=fake_usage):
        with pytest.raises(RuntimeError, match="Insufficient disk space"):
            run_migration(db)


def test_migration_backup_created(tmp_path: Path) -> None:
    """Backup file exists after migration and has 0600 permissions."""
    db = tmp_path / "pulse.db"
    _make_v0_db(db)

    run_migration(db)

    backups = list(tmp_path.glob("pulse.db.pre-v1-*"))
    assert len(backups) == 1, f"expected 1 backup, found {backups}"
    backup = backups[0]
    assert backup.exists()
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600


def test_migration_backup_is_v0(tmp_path: Path) -> None:
    """Backup file preserves the v0 user_version=10."""
    db = tmp_path / "pulse.db"
    _make_v0_db(db)

    run_migration(db)

    backups = list(tmp_path.glob("pulse.db.pre-v1-*"))
    assert len(backups) == 1
    conn = sqlite3.connect(str(backups[0]))
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == _V0_USER_VERSION, f"backup should be v0 ({_V0_USER_VERSION}), got {version}"
    finally:
        conn.close()


def test_migration_pre_integrity_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If integrity_check fails pre-migration, migration refuses."""
    db = tmp_path / "pulse.db"
    _make_v0_db(db)

    # Patch _integrity_check to fail the first call (pre-migration check)
    call_count = {"n": 0}
    import pulse.migrate as migrate_mod

    original = migrate_mod._integrity_check

    def fake_integrity(conn: sqlite3.Connection) -> bool:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return False  # simulate failure
        return original(conn)

    monkeypatch.setattr(migrate_mod, "_integrity_check", fake_integrity)

    with pytest.raises(RuntimeError, match="pre-migration integrity_check failed"):
        run_migration(db)


def test_migration_post_integrity_check_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Post-migration integrity_check failure must NOT bump user_version to 11."""
    db = tmp_path / "pulse.db"
    _make_v0_db(db)
    call_count = {"n": 0}
    import pulse.migrate as migrate_mod
    original_check = migrate_mod._integrity_check
    def fake_integrity(conn: sqlite3.Connection) -> bool:
        call_count["n"] += 1
        if call_count["n"] == 2:
            return False
        return original_check(conn)
    monkeypatch.setattr(migrate_mod, "_integrity_check", fake_integrity)
    with pytest.raises(RuntimeError, match="post-migration integrity_check failed"):
        run_migration(db)
    # user_version must still be v0 — NOT bumped to 11
    conn = sqlite3.connect(str(db))
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert version == _V0_USER_VERSION, f"user_version should remain {_V0_USER_VERSION} after post-integrity failure, got {version}"


def test_migration_v0_data_still_queryable(tmp_path: Path) -> None:
    """v0 rows inserted before migration are still queryable after."""
    db = tmp_path / "pulse.db"
    _make_v0_db(db)

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    # Insert a minimal v0 snapshot
    conn.execute(
        """INSERT INTO snapshots
           (id, captured_at_utc, captured_at_et, orgs_queried, repos_succeeded)
           VALUES ('snap1', '2026-01-01T00:00:00Z', '2025-12-31T19:00:00-05:00', '["testorg"]', 1)"""
    )
    conn.execute(
        """INSERT INTO repos
           (snapshot_id, org, name, capture_status)
           VALUES ('snap1', 'testorg', 'myrepo', 'success')"""
    )
    repo_id = conn.execute("SELECT id FROM repos WHERE name='myrepo'").fetchone()[0]
    conn.execute(
        """INSERT INTO prs
           (repo_id, number, title, stalled)
           VALUES (?, 1, 'Fix bug', 0)""",
        (repo_id,),
    )
    conn.execute(
        """INSERT INTO issues
           (repo_id, number, title, stalled)
           VALUES (?, 1, 'Report bug', 0)""",
        (repo_id,),
    )
    conn.execute(
        """INSERT INTO releases
           (repo_id, tag_name, is_prerelease)
           VALUES (?, 'v1.0.0', 0)""",
        (repo_id,),
    )
    conn.commit()
    conn.close()

    # Migrate
    result = run_migration(db)
    assert result == "migrated"

    # Verify v0 rows are still queryable
    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM repos").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM prs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 1
    finally:
        conn.close()


def test_migration_user_version_0_works(tmp_path: Path) -> None:
    """Unversioned v0 DB (user_version=0) → migration still succeeds."""
    db = tmp_path / "pulse.db"
    _make_unversioned_v0_db(db)

    # Confirm it's truly user_version=0
    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    conn.close()

    result = run_migration(db)
    assert result == "migrated"

    conn = sqlite3.connect(str(db))
    try:
        assert _V1_USER_VERSION == conn.execute("PRAGMA user_version").fetchone()[0]
        assert "upstream" in _column_names(conn, "repos")
        assert "review_events" in _column_names(conn, "prs")
    finally:
        conn.close()


def test_migration_missing_db_raises(tmp_path: Path) -> None:
    """Migration on a non-existent pulse.db raises RuntimeError with clear message."""
    db = tmp_path / "pulse.db"
    assert not db.exists()

    with pytest.raises(RuntimeError, match="pulse.db not found"):
        run_migration(db)


def test_migration_disk_space_includes_wal(tmp_path: Path) -> None:
    """Disk-space check accounts for WAL + SHM file sizes."""
    db = tmp_path / "pulse.db"
    _make_v0_db(db)

    # Create fake WAL and SHM files to inflate total_size
    wal = db.with_suffix(db.suffix + "-wal")
    shm = db.with_suffix(db.suffix + "-shm")
    wal.write_bytes(b"W" * 1024)
    shm.write_bytes(b"S" * 512)

    import shutil
    real_usage = shutil.disk_usage(tmp_path)
    # Report zero free space — even db_size alone would trigger, but confirms WAL/SHM included
    fake_usage = shutil._ntuple_diskusage(real_usage.total, real_usage.used, 0)  # type: ignore[attr-defined]

    with patch("pulse.migrate.shutil.disk_usage", return_value=fake_usage):
        with pytest.raises(RuntimeError, match="Insufficient disk space"):
            run_migration(db)
