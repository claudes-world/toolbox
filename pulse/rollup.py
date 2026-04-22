"""pulse/rollup.py — 7-day reviewer activity rollup."""

from __future__ import annotations

import datetime as dt
import logging
import sqlite3
import time
from datetime import datetime, timezone

from pulse.snapshot import DEPENDABOT_AUTHORS, RENOVATE_AUTHORS

COPILOT_AUTHORS = frozenset({"Copilot", "copilot-pull-request-reviewer[bot]"})
GEMINI_CA_AUTHORS = frozenset({"gemini-code-assist", "gemini-code-assist[bot]"})
CLAUDE_SUBAGENT_AUTHORS = frozenset({
    "claude-subagent", "claude-code", "anthropic-claude", "claude-ai"
    # PR Review Toolkit bot patterns — extend as needed
})

_log = logging.getLogger(__name__)


def classify_author(author: str) -> str:
    """Return bucket key for a reviewer author login."""
    if author in COPILOT_AUTHORS:
        return "copilot"
    if author in GEMINI_CA_AUTHORS:
        return "gemini-ca"
    if author in CLAUDE_SUBAGENT_AUTHORS:
        return "claude-subagent"
    if author in DEPENDABOT_AUTHORS:
        return "dependabot"
    if author in RENOVATE_AUTHORS:
        return "renovate"
    return f"human:{author}"


def _seven_days_ago_iso() -> str:
    return (datetime.now(timezone.utc) - dt.timedelta(days=7)).isoformat().replace("+00:00", "Z")


def compute_reviewer_activity_7d(conn: sqlite3.Connection) -> dict:
    """
    Query prs.review_events JSON blobs across last 7 days via json_each.
    Returns bucketed reviewer activity dict.
    Uses SQLite JSON1 extension — no Python-side deserialization for filtering.
    """
    seven_days_ago = _seven_days_ago_iso()

    start = time.monotonic()

    rows = conn.execute("""
        SELECT
            json_extract(evt.value, '$.author') as author,
            json_extract(evt.value, '$.state') as state,
            json_extract(evt.value, '$.type') as type
        FROM (
            SELECT MAX(id) as snap_id FROM snapshots
            WHERE capture_status != 'failed'
              AND captured_at_utc >= ?
        ) latest
        JOIN repos r ON r.snapshot_id = latest.snap_id
        JOIN prs p ON p.repo_id = r.id,
        json_each(p.review_events) AS evt
        WHERE p.review_events IS NOT NULL
          AND p.review_events != 'null'
          AND (json_extract(evt.value, '$.submitted_at') >= ?
               OR json_extract(evt.value, '$.submitted_at') IS NULL)
    """, (seven_days_ago, seven_days_ago)).fetchall()

    elapsed = time.monotonic() - start
    if elapsed > 2.0:
        _log.warning("reviewer_activity_7d rollup took %.2fs — consider indexing", elapsed)

    buckets: dict[str, dict] = {}

    for author, state, event_type in rows:
        if not author:
            continue
        bucket = classify_author(author)
        if bucket not in buckets:
            buckets[bucket] = {"total": 0, "approved": 0, "change_requested": 0, "commented": 0, "dismissed": 0}
        b = buckets[bucket]
        if event_type in ("PULL_REQUEST_REVIEW", "IssueComment"):
            b["total"] += 1
            if state == "APPROVED":
                b["approved"] += 1
            elif state == "CHANGES_REQUESTED":
                b["change_requested"] += 1
            elif event_type == "IssueComment" or state in ("COMMENTED", None):
                b["commented"] += 1
            elif state == "DISMISSED":
                b["dismissed"] += 1
        # non-review timeline events (MERGED_EVENT, etc.) — not counted in any bucket

    return buckets


def count_snapshots_in_last_7d(conn: sqlite3.Connection) -> int:
    """Count completed snapshots within the last 7 days."""
    seven_days_ago = _seven_days_ago_iso()
    row = conn.execute("""
        SELECT count(*) FROM snapshots
        WHERE captured_at_utc >= ? AND capture_status != 'failed'
    """, (seven_days_ago,)).fetchone()
    return row[0] if row else 0


def oldest_snapshot_in_7d(conn: sqlite3.Connection) -> str | None:
    """Return captured_at_utc of oldest snapshot in 7-day window."""
    seven_days_ago = _seven_days_ago_iso()
    row = conn.execute("""
        SELECT captured_at_utc FROM snapshots
        WHERE captured_at_utc >= ? AND capture_status != 'failed'
        ORDER BY captured_at_utc ASC LIMIT 1
    """, (seven_days_ago,)).fetchone()
    return row[0] if row else None
