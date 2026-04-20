from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path


class DBCorrupt(Exception):
    """Raised when the database file is unreadable or internally inconsistent.

    Covers: file is not valid SQLite (detected on first execute), or
    PRAGMA integrity_check returns a failure result.
    """


class DBSetupError(Exception):
    """Raised when the database file is valid but schema creation or PRAGMA setup fails."""


def open_db(path: Path) -> sqlite3.Connection:
    """Open (or create) the SQLite database at path with WAL mode and integrity check."""
    # Stage 1: open the file. Failure here is an environment/permissions problem, not corruption.
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=5.0)
        conn.row_factory = sqlite3.Row
    except Exception as e:
        raise DBSetupError(f"cannot open database at {path}: {e}") from e

    # Stage 2: detect corrupt file — first execute triggers SQLite's lazy file validation.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError as e:
        conn.close()
        raise DBSetupError(f"PRAGMA journal_mode failed (env/permissions issue): {e}") from e
    except sqlite3.DatabaseError as e:
        conn.close()
        raise DBCorrupt(f"database file is corrupt or not valid SQLite: {e}") from e
    except Exception as e:
        conn.close()
        raise DBSetupError(f"PRAGMA journal_mode failed: {e}") from e

    # Stage 3: remaining PRAGMAs + integrity check + schema. Failures here are setup/env issues.
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        rows = conn.execute("PRAGMA integrity_check").fetchall()
        row_values = [tuple(r) for r in rows]
        if row_values != [("ok",)]:
            raise DBCorrupt(f"integrity_check failed: {row_values}")
        create_schema(conn)
        return conn
    except DBCorrupt:
        conn.close()
        raise
    except Exception as e:
        conn.close()
        raise DBSetupError(f"database setup failed: {e}") from e


def create_schema(conn: sqlite3.Connection) -> None:
    """Create all pulse tables if they do not exist."""
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id TEXT PRIMARY KEY,
                captured_at_utc TEXT NOT NULL,
                captured_at_et TEXT NOT NULL,
                duration_ms INTEGER,
                orgs_queried TEXT NOT NULL,
                repos_succeeded INTEGER NOT NULL DEFAULT 0,
                repos_failed INTEGER NOT NULL DEFAULT 0,
                repos_partial INTEGER NOT NULL DEFAULT 0,
                schema_version TEXT NOT NULL DEFAULT '1.0',
                capture_status TEXT NOT NULL DEFAULT 'success'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS repos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
                org TEXT NOT NULL,
                name TEXT NOT NULL,
                default_branch TEXT,
                is_fork INTEGER NOT NULL DEFAULT 0,
                is_archived INTEGER NOT NULL DEFAULT 0,
                parent_owner TEXT,
                parent_name TEXT,
                parent_is_deleted INTEGER NOT NULL DEFAULT 0,
                capture_status TEXT NOT NULL DEFAULT 'success',
                field_statuses TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
                number INTEGER NOT NULL,
                title TEXT NOT NULL,
                author TEXT,
                created_at TEXT,
                updated_at TEXT,
                is_draft INTEGER NOT NULL DEFAULT 0,
                is_dependabot INTEGER NOT NULL DEFAULT 0,
                is_renovate INTEGER NOT NULL DEFAULT 0,
                hours_idle REAL,
                stalled INTEGER NOT NULL DEFAULT 0,
                UNIQUE(repo_id, number)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
                number INTEGER NOT NULL,
                title TEXT NOT NULL,
                author TEXT,
                created_at TEXT,
                updated_at TEXT,
                labels TEXT,
                hours_idle REAL,
                stalled INTEGER NOT NULL DEFAULT 0,
                UNIQUE(repo_id, number)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS releases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
                tag_name TEXT NOT NULL,
                name TEXT,
                created_at TEXT,
                is_prerelease INTEGER NOT NULL DEFAULT 0,
                UNIQUE(repo_id, tag_name)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
                severity TEXT,
                ghsa_id TEXT,
                package_name TEXT,
                ecosystem TEXT,
                age_days INTEGER,
                dependabot_pr_number INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pagination_state (
                -- repo is TEXT (not FK) so pagination state survives snapshot deletion/pruning
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo TEXT NOT NULL,
                field TEXT NOT NULL,
                last_cursor TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                UNIQUE(repo, field)
            )
        """)


def atomic_write_json(path: Path, data: dict) -> None:
    """Write data as JSON to path atomically (crash-safe). Sets permissions to 0o600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    fd_open = True  # track whether fd is still open (not yet owned by fdopen)
    try:
        os.fchmod(fd, 0o600)  # set permissions before any data is written
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fd_open = False  # fdopen took ownership
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        # fsync parent directory to persist the rename
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        if fd_open:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise
