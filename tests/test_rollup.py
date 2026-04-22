"""tests/test_rollup.py — 7-day reviewer activity rollup tests."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from pulse.rollup import (
    classify_author,
    compute_reviewer_activity_7d,
    count_snapshots_in_last_7d,
    oldest_snapshot_in_7d,
)
from pulse.snapshot import DEPENDABOT_AUTHORS, RENOVATE_AUTHORS
from pulse.storage import create_schema


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    create_schema(conn)
    return conn


def _utc_iso(offset_days: float = 0) -> str:
    """Return an ISO-8601 UTC string offset from now."""
    ts = datetime.now(timezone.utc) - timedelta(days=offset_days)
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def _insert_snapshot(
    conn: sqlite3.Connection,
    snapshot_id: str,
    captured_at_utc: str,
    capture_status: str = "success",
) -> str:
    conn.execute(
        """
        INSERT INTO snapshots
          (id, captured_at_utc, captured_at_et, duration_ms, orgs_queried,
           repos_succeeded, repos_failed, repos_partial, schema_version, capture_status)
        VALUES (?, ?, '2026-01-01 00:00 ET', 500, ?, 1, 0, 0, '1.0', ?)
        """,
        (snapshot_id, captured_at_utc, json.dumps(["testorg"]), capture_status),
    )
    conn.commit()
    return snapshot_id


def _insert_repo(conn: sqlite3.Connection, snapshot_id: str) -> int:
    cur = conn.execute(
        """
        INSERT INTO repos
          (snapshot_id, org, name, default_branch, is_fork, is_archived,
           parent_owner, parent_name, parent_is_deleted, capture_status, field_statuses)
        VALUES (?, 'testorg', 'testrepo', 'main', 0, 0, NULL, NULL, 0, 'success', '{}')
        """,
        (snapshot_id,),
    )
    conn.commit()
    return cur.lastrowid


def _insert_pr_with_events(
    conn: sqlite3.Connection,
    repo_id: int,
    pr_number: int,
    events: list[dict],
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO prs
          (repo_id, number, title, author, created_at, updated_at,
           is_draft, is_dependabot, is_renovate, hours_idle, stalled, review_events)
        VALUES (?, ?, 'Test PR', 'testuser', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z',
                0, 0, 0, 1.0, 0, ?)
        """,
        (repo_id, pr_number, json.dumps(events)),
    )
    conn.commit()


def _db_with_events(events: list[dict], days_ago: float = 0.5) -> sqlite3.Connection:
    """Convenience: create an in-memory DB with one snapshot/repo/PR containing the given events."""
    conn = _make_db()
    snap_id = f"snap-{days_ago}"
    captured_at = _utc_iso(days_ago)
    _insert_snapshot(conn, snap_id, captured_at)
    repo_id = _insert_repo(conn, snap_id)
    _insert_pr_with_events(conn, repo_id, 1, events)
    return conn


# ── classify_author tests ─────────────────────────────────────────────────────

def test_classify_copilot() -> None:
    assert classify_author("Copilot") == "copilot"
    assert classify_author("copilot-pull-request-reviewer[bot]") == "copilot"


def test_classify_gemini_ca() -> None:
    assert classify_author("gemini-code-assist") == "gemini-ca"
    assert classify_author("gemini-code-assist[bot]") == "gemini-ca"


def test_classify_claude_subagent() -> None:
    assert classify_author("claude-subagent") == "claude-subagent"
    assert classify_author("claude-code") == "claude-subagent"


def test_classify_dependabot() -> None:
    # Must reuse the imported set — pick one entry from the actual constant
    for author in DEPENDABOT_AUTHORS:
        assert classify_author(author) == "dependabot"


def test_classify_renovate() -> None:
    for author in RENOVATE_AUTHORS:
        assert classify_author(author) == "renovate"


def test_classify_human() -> None:
    assert classify_author("liam") == "human:liam"
    assert classify_author("octocat") == "human:octocat"


# ── compute_reviewer_activity_7d tests ───────────────────────────────────────

def test_7d_filter_excludes_old_snapshots() -> None:
    """Events in snapshots older than 7 days must not appear in the rollup."""
    events = [{"type": "PULL_REQUEST_REVIEW", "author": "liam", "state": "APPROVED",
                "label": None, "submitted_at": None, "created_at": None}]
    conn = _db_with_events(events, days_ago=8.0)  # 8 days ago — outside window
    result = compute_reviewer_activity_7d(conn)
    assert result == {}


def test_empty_window_returns_empty_rollup() -> None:
    """No snapshots in 7d → empty dict."""
    conn = _make_db()
    result = compute_reviewer_activity_7d(conn)
    assert result == {}


def test_multiple_buckets_counted_correctly() -> None:
    """Multiple distinct reviewers produce separate buckets with correct counts."""
    events = [
        {"type": "PULL_REQUEST_REVIEW", "author": "liam", "state": "APPROVED",
         "label": None, "submitted_at": None, "created_at": None},
        {"type": "PULL_REQUEST_REVIEW", "author": "liam", "state": "APPROVED",
         "label": None, "submitted_at": None, "created_at": None},
        {"type": "PULL_REQUEST_REVIEW", "author": "Copilot", "state": "CHANGES_REQUESTED",
         "label": None, "submitted_at": None, "created_at": None},
    ]
    conn = _db_with_events(events)
    result = compute_reviewer_activity_7d(conn)

    assert "human:liam" in result
    assert result["human:liam"]["total"] == 2
    assert result["human:liam"]["approved"] == 2
    assert result["human:liam"]["change_requested"] == 0

    assert "copilot" in result
    assert result["copilot"]["total"] == 1
    assert result["copilot"]["change_requested"] == 1


def test_rollup_cached_to_snapshot_row() -> None:
    """compute_reviewer_activity_7d result can be written and re-read from snapshots table."""
    events = [{"type": "PULL_REQUEST_REVIEW", "author": "alice", "state": "APPROVED",
               "label": None, "submitted_at": None, "created_at": None}]
    conn = _db_with_events(events)

    rollup = compute_reviewer_activity_7d(conn)
    snap_id = conn.execute("SELECT id FROM snapshots LIMIT 1").fetchone()[0]
    conn.execute(
        "UPDATE snapshots SET reviewer_activity_7d=? WHERE id=?",
        (json.dumps(rollup), snap_id),
    )
    conn.commit()

    row = conn.execute(
        "SELECT reviewer_activity_7d FROM snapshots WHERE id=?", (snap_id,)
    ).fetchone()
    assert row is not None
    cached = json.loads(row[0])
    assert "human:alice" in cached
    assert cached["human:alice"]["approved"] == 1


def test_failed_snapshot_excluded() -> None:
    """Snapshots with capture_status='failed' are excluded from the rollup."""
    conn = _make_db()
    snap_id = "failed-snap"
    _insert_snapshot(conn, snap_id, _utc_iso(0.5), capture_status="failed")
    repo_id = _insert_repo(conn, snap_id)
    events = [{"type": "PULL_REQUEST_REVIEW", "author": "bob", "state": "APPROVED",
               "label": None, "submitted_at": None, "created_at": None}]
    _insert_pr_with_events(conn, repo_id, 1, events)

    result = compute_reviewer_activity_7d(conn)
    assert result == {}


# ── count_snapshots_in_last_7d tests ─────────────────────────────────────────

def test_count_snapshots_excludes_old() -> None:
    conn = _make_db()
    _insert_snapshot(conn, "recent", _utc_iso(1.0))
    _insert_snapshot(conn, "old", _utc_iso(8.0))
    assert count_snapshots_in_last_7d(conn) == 1


def test_count_snapshots_excludes_failed() -> None:
    conn = _make_db()
    _insert_snapshot(conn, "ok", _utc_iso(1.0))
    _insert_snapshot(conn, "bad", _utc_iso(0.5), capture_status="failed")
    assert count_snapshots_in_last_7d(conn) == 1


# ── warm-up banner integration ────────────────────────────────────────────────

def test_warmup_banner_renders_with_fill_date() -> None:
    """When actual_count < full_window_snapshots, digest shows warm-up banner with fill date."""
    from pulse.config import Defaults, OrgConfig, PulseConfig
    from pulse.digest import _render_reviewer_activity

    conn = _make_db()
    snap_id = "snap-recent"
    captured_at = _utc_iso(1.0)
    _insert_snapshot(conn, snap_id, captured_at)

    # Read the snapshot row
    snap = conn.execute("SELECT * FROM snapshots WHERE id=?", (snap_id,)).fetchone()

    cfg = PulseConfig(
        schema_version="1.0",
        orgs={"testorg": OrgConfig()},
        defaults=Defaults(cadence_minutes=30),
    )

    lines = _render_reviewer_activity(conn, snap, cfg)
    combined = "\n".join(lines)
    # With 1 snapshot and cadence_minutes=30, full window = 336 snapshots — far short
    assert "7-day window fills at" in combined


def test_warmup_banner_absent_when_full_window() -> None:
    """When actual_count >= full_window_snapshots, no warm-up banner."""
    from pulse.config import Defaults, OrgConfig, PulseConfig
    from pulse.digest import _render_reviewer_activity

    conn = _make_db()

    # Use cadence_minutes=10080 so full_window_snapshots=1 — one snapshot satisfies it
    cfg = PulseConfig(
        schema_version="1.0",
        orgs={"testorg": OrgConfig()},
        defaults=Defaults(cadence_minutes=10080),
    )

    snap_id = "snap-full"
    _insert_snapshot(conn, snap_id, _utc_iso(0.5))
    snap = conn.execute("SELECT * FROM snapshots WHERE id=?", (snap_id,)).fetchone()

    lines = _render_reviewer_activity(conn, snap, cfg)
    combined = "\n".join(lines)
    assert "7-day window fills at" not in combined


def test_non_review_events_excluded_from_total() -> None:
    """Non-review timeline events (MERGED_EVENT etc.) must not inflate total."""
    events = [
        {"type": "PULL_REQUEST_REVIEW", "author": "alice", "state": "APPROVED",
         "label": None, "submitted_at": None, "created_at": None},
        {"type": "MERGED_EVENT", "author": "alice", "state": None,
         "label": None, "submitted_at": None, "created_at": None},
        {"type": "LABELED_EVENT", "author": "alice", "state": None,
         "label": None, "submitted_at": None, "created_at": None},
    ]
    conn = _db_with_events(events)
    result = compute_reviewer_activity_7d(conn)
    # Only the PULL_REQUEST_REVIEW counts — MERGED_EVENT and LABELED_EVENT must not inflate total
    assert result == {"human:alice": {"total": 1, "approved": 1, "change_requested": 0, "commented": 0, "dismissed": 0}}


def test_no_double_count_across_snapshots() -> None:
    """Non-review events mixed with review events across multiple snapshots must not inflate total."""
    conn = _make_db()
    # Each snapshot has 1 review + 2 non-review events
    events = [
        {"type": "PULL_REQUEST_REVIEW", "author": "alice", "state": "APPROVED",
         "label": None, "submitted_at": None, "created_at": None},
        {"type": "MERGED_EVENT", "author": "alice", "state": None,
         "label": None, "submitted_at": None, "created_at": None},
        {"type": "CLOSED_EVENT", "author": "alice", "state": None,
         "label": None, "submitted_at": None, "created_at": None},
    ]
    for i in range(3):
        snap_id = f"multi-snap-{i}"
        _insert_snapshot(conn, snap_id, _utc_iso(0.5))
        repo_id = _insert_repo(conn, snap_id)
        _insert_pr_with_events(conn, repo_id, 1, events)
    rollup = compute_reviewer_activity_7d(conn)
    # SQL uses MAX(id) — only the latest snapshot is queried; non-review events (MERGED, CLOSED) excluded from total
    assert rollup == {"human:alice": {"total": 1, "approved": 1, "change_requested": 0, "commented": 0, "dismissed": 0}}
