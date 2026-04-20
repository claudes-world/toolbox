from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from pulse.digest import md_escape, render_digest
from pulse.storage import create_schema


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    create_schema(conn)
    return conn


def _insert_snapshot(
    conn: sqlite3.Connection,
    snapshot_id: str = "20260101T000000Z",
    captured_at_utc: str = "2026-01-01T00:00:00Z",
    captured_at_et: str = "2025-12-31 19:00 ET",
    duration_ms: int = 500,
    repos_succeeded: int = 1,
    repos_failed: int = 0,
    repos_partial: int = 0,
) -> str:
    with conn:
        conn.execute(
            """
            INSERT INTO snapshots
              (id, captured_at_utc, captured_at_et, duration_ms, orgs_queried,
               repos_succeeded, repos_failed, repos_partial, schema_version, capture_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, '1.0', 'success')
            """,
            (
                snapshot_id,
                captured_at_utc,
                captured_at_et,
                duration_ms,
                json.dumps(["testorg"]),
                repos_succeeded,
                repos_failed,
                repos_partial,
            ),
        )
    return snapshot_id


def _insert_repo(
    conn: sqlite3.Connection,
    snapshot_id: str,
    org: str = "testorg",
    name: str = "testrepo",
    capture_status: str = "success",
    field_statuses: dict | None = None,
) -> int:
    fs = field_statuses or {}
    with conn:
        cur = conn.execute(
            """
            INSERT INTO repos
              (snapshot_id, org, name, default_branch, is_fork, is_archived,
               parent_owner, parent_name, parent_is_deleted, capture_status, field_statuses)
            VALUES (?, ?, ?, 'main', 0, 0, NULL, NULL, 0, ?, ?)
            """,
            (snapshot_id, org, name, capture_status, json.dumps(fs)),
        )
    return cur.lastrowid


def _insert_pr(
    conn: sqlite3.Connection,
    repo_id: int,
    number: int = 1,
    title: str = "Test PR",
    author: str = "alice",
    hours_idle: float = 5.0,
    stalled: bool = False,
) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO prs
              (repo_id, number, title, author, created_at, updated_at,
               is_draft, is_dependabot, is_renovate, hours_idle, stalled)
            VALUES (?, ?, ?, ?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 0, 0, 0, ?, ?)
            """,
            (repo_id, number, title, author, hours_idle, int(stalled)),
        )


def _insert_issue(
    conn: sqlite3.Connection,
    repo_id: int,
    number: int = 1,
    title: str = "Test Issue",
    author: str = "bob",
    hours_idle: float = 5.0,
    stalled: bool = False,
    labels: list[str] | None = None,
) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO issues
              (repo_id, number, title, author, created_at, updated_at,
               labels, hours_idle, stalled)
            VALUES (?, ?, ?, ?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', ?, ?, ?)
            """,
            (repo_id, number, title, author, json.dumps(labels or []), hours_idle, int(stalled)),
        )


def _make_cfg():
    """Minimal config object for render_digest."""
    from pulse.config import Defaults, OrgConfig, PulseConfig
    return PulseConfig(
        schema_version="1.0",
        orgs={"testorg": OrgConfig()},
        defaults=Defaults(),
    )


# ── tests ─────────────────────────────────────────────────────────────────────

def test_alerts_at_top_on_capture_failure():
    conn = _make_db()
    sid = _insert_snapshot(conn, repos_failed=1, repos_succeeded=0)
    _insert_repo(conn, sid)
    cfg = _make_cfg()

    digest = render_digest(conn, cfg)

    alerts_pos = digest.index("## ⚠️ Alerts")
    prs_pos = digest.index("## Open PRs")
    assert alerts_pos < prs_pos, "Alerts section must appear before Open PRs"


def test_no_alerts_section_when_clean():
    conn = _make_db()
    sid = _insert_snapshot(conn, repos_succeeded=1, repos_failed=0)
    _insert_repo(conn, sid)
    cfg = _make_cfg()

    digest = render_digest(conn, cfg)

    assert "No alerts — all repos captured successfully." in digest


def test_md_escape_script_injection():
    result = md_escape("<script>alert(1)</script>")
    assert "<" not in result
    assert ">" not in result
    assert "&lt;" in result
    assert "&gt;" in result


def test_md_escape_backtick():
    result = md_escape("`foo`")
    assert "\\`" in result


def test_md_escape_markdown_meta():
    result = md_escape("*bold* _italic_ [link](url)")
    assert "\\*" in result
    assert "\\_" in result
    assert "\\[" in result
    assert "\\]" in result


def test_stalled_prs_first():
    conn = _make_db()
    sid = _insert_snapshot(conn)
    repo_id = _insert_repo(conn, sid)
    _insert_pr(conn, repo_id, number=1, title="Normal PR", hours_idle=2.0, stalled=False)
    _insert_pr(conn, repo_id, number=2, title="Stalled PR", hours_idle=48.0, stalled=True)
    cfg = _make_cfg()

    digest = render_digest(conn, cfg)

    stalled_pos = digest.index("Stalled PR")
    normal_pos = digest.index("Normal PR")
    assert stalled_pos < normal_pos, "Stalled PR must appear before normal PR"


def test_empty_repo():
    conn = _make_db()
    sid = _insert_snapshot(conn)
    _insert_repo(conn, sid)
    cfg = _make_cfg()

    # Should not raise
    digest = render_digest(conn, cfg)
    assert "# Org Pulse" in digest


def test_disabled_issues_field():
    conn = _make_db()
    sid = _insert_snapshot(conn)
    field_statuses = {"issues": {"status": "disabled", "error_note": None}}
    _insert_repo(conn, sid, field_statuses=field_statuses)
    cfg = _make_cfg()

    digest = render_digest(conn, cfg)

    assert "⚠️" in digest
    assert "issues field disabled" in digest


def test_md_escape_truncation():
    long_str = "a" * 200
    result = md_escape(long_str)
    assert len(result) <= 121  # 120 + ellipsis char
    assert result.endswith("…")


def test_md_escape_truncation_exact_boundary():
    # String at exactly 120 chars should NOT be truncated
    s = "a" * 120
    result = md_escape(s)
    assert not result.endswith("…")
    assert len(result) == 120

    # String at 121 chars SHOULD be truncated
    s2 = "a" * 121
    result2 = md_escape(s2)
    assert result2.endswith("…")
