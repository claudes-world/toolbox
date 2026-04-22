from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from pulse.storage import DBCorrupt, atomic_write_json, open_db

EXPECTED_TABLES = {
    "snapshots",
    "repos",
    "prs",
    "issues",
    "releases",
    "alerts",
    "pagination_state",
}


def test_open_db_creates_all_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    conn = open_db(db_path)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    # Filter out SQLite internal tables (e.g. sqlite_sequence from AUTOINCREMENT)
    tables = {r[0] for r in rows if not r[0].startswith("sqlite_")}
    assert EXPECTED_TABLES == tables
    conn.close()


def test_wal_mode_set(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    conn = open_db(db_path)
    row = conn.execute("PRAGMA journal_mode").fetchone()
    assert row[0] == "wal"
    conn.close()


def test_foreign_keys_on(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    conn = open_db(db_path)
    row = conn.execute("PRAGMA foreign_keys").fetchone()
    assert row[0] == 1
    conn.close()


def test_integrity_check_passes_fresh_db(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    # Should not raise
    conn = open_db(db_path)
    conn.close()


def test_integrity_check_runs(tmp_path: Path) -> None:
    """Verify integrity_check query runs and returns 'ok' on a valid DB."""
    db_path = tmp_path / "test.db"
    conn = open_db(db_path)
    rows = conn.execute("PRAGMA integrity_check").fetchall()
    assert rows[0][0] == "ok"
    conn.close()


def test_atomic_write_json_readable(tmp_path: Path) -> None:
    out = tmp_path / "data.json"
    payload = {"key": "value", "number": 42}
    atomic_write_json(out, payload)
    assert out.exists()
    loaded = json.loads(out.read_text())
    assert loaded == payload


def test_atomic_write_json_permissions(tmp_path: Path) -> None:
    out = tmp_path / "data.json"
    atomic_write_json(out, {"x": 1})
    mode = oct(os.stat(out).st_mode)
    # Last 3 digits should be 600
    assert mode.endswith("600")


def test_open_db_raises_on_corrupt_file(tmp_path: Path) -> None:
    db_path = tmp_path / "corrupt.db"
    db_path.write_bytes(b"this is not a sqlite database")
    with pytest.raises(DBCorrupt):
        open_db(db_path)
