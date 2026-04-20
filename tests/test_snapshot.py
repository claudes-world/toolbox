from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pulse.snapshot import (
    DEPENDABOT_AUTHORS,
    RENOVATE_AUTHORS,
    _capture_issues,
    _capture_prs,
    _capture_releases,
    run_snapshot,
)
from pulse.storage import create_schema


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    create_schema(conn)
    return conn


def _make_cfg(
    stall_pr_hours: int = 12,
    stall_issue_hours: int = 72,
    max_prs: int = 30,
    max_issues: int = 50,
    max_releases: int = 10,
    orgs: dict | None = None,
):
    from pulse.config import Defaults, OrgConfig, PulseConfig
    return PulseConfig(
        schema_version="1.0",
        orgs=orgs or {"testorg": OrgConfig()},
        defaults=Defaults(
            stall_pr_hours=stall_pr_hours,
            stall_issue_hours=stall_issue_hours,
            max_prs_per_repo=max_prs,
            max_issues_per_repo=max_issues,
            max_releases_per_repo=max_releases,
        ),
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_pr_body(
    number: int = 1,
    title: str = "PR title",
    author: str = "alice",
    updated_at: str = "2026-01-01T00:00:00Z",
    is_draft: bool = False,
    total_count: int = 1,
    has_next_page: bool = False,
    cursor: str = "abc",
) -> dict:
    """Build a mock GQL response for PRS_QUERY."""
    return {
        "data": {
            "rateLimit": {"cost": 1, "remaining": 4000, "resetAt": "", "used": 1000},
            "repository": {
                "pullRequests": {
                    "totalCount": total_count,
                    "pageInfo": {"hasNextPage": has_next_page, "endCursor": cursor},
                    "nodes": [
                        {
                            "number": number,
                            "title": title,
                            "author": {"login": author},
                            "createdAt": "2026-01-01T00:00:00Z",
                            "updatedAt": updated_at,
                            "isDraft": is_draft,
                        }
                    ],
                }
            },
        }
    }


def _make_issues_body(
    number: int = 1,
    total_count: int = 1,
    has_next_page: bool = False,
    updated_at: str = "2026-01-01T00:00:00Z",
) -> dict:
    return {
        "data": {
            "rateLimit": {"cost": 1, "remaining": 4000, "resetAt": "", "used": 1000},
            "repository": {
                "issues": {
                    "totalCount": total_count,
                    "pageInfo": {"hasNextPage": has_next_page, "endCursor": "xyz"},
                    "nodes": [
                        {
                            "number": number,
                            "title": "Issue title",
                            "author": {"login": "bob"},
                            "createdAt": "2026-01-01T00:00:00Z",
                            "updatedAt": updated_at,
                            "labels": {"nodes": [{"name": "bug"}]},
                        }
                    ],
                }
            },
        }
    }


def _make_releases_body(tag: str = "v1.0.0") -> dict:
    return {
        "data": {
            "rateLimit": {"cost": 1, "remaining": 4000, "resetAt": "", "used": 1000},
            "repository": {
                "releases": {
                    "nodes": [
                        {
                            "tagName": tag,
                            "name": "Release 1.0",
                            "createdAt": "2026-01-01T00:00:00Z",
                            "isPrerelease": False,
                        }
                    ]
                }
            },
        }
    }



def _make_repos_body(
    repo_name: str = "testrepo",
    has_issues: bool = True,
    is_fork: bool = False,
    has_next_page: bool = False,
) -> dict:
    return {
        "data": {
            "rateLimit": {"cost": 1, "remaining": 4000, "resetAt": "", "used": 1000},
            "organization": {
                "repositories": {
                    "totalCount": 1,
                    "pageInfo": {"hasNextPage": has_next_page, "endCursor": "end"},
                    "nodes": [
                        {
                            "name": repo_name,
                            "defaultBranchRef": {"name": "main"},
                            "isFork": is_fork,
                            "isArchived": False,
                            "hasIssuesEnabled": has_issues,
                            "pullRequests": {"totalCount": 0},
                            "issues": {"totalCount": 0},
                            "parent": None,
                        }
                    ],
                }
            },
        }
    }


# ── tests ─────────────────────────────────────────────────────────────────────

def test_field_status_success():
    db = _make_db()
    gql = MagicMock()
    gql.execute.return_value = _make_pr_body(total_count=1)

    prs, status = _capture_prs(gql, "org", "repo", 30, 12.0, _now(), db, None, "snap")
    assert status.status == "success"
    assert len(prs) == 1


def test_field_status_partial_prs():
    db = _make_db()
    gql = MagicMock()
    # totalCount=47 but we fetch at most max_prs=30; build a response with 30 nodes
    nodes = [
        {
            "number": i,
            "title": f"PR {i}",
            "author": {"login": "alice"},
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
            "isDraft": False,
        }
        for i in range(1, 31)
    ]
    body = _make_pr_body(total_count=47)
    body["data"]["repository"]["pullRequests"]["nodes"] = nodes
    gql.execute.return_value = body

    prs, status = _capture_prs(gql, "org", "repo", 30, 12.0, _now(), db, None, "snap")
    assert status.status == "partial"
    assert "truncated" in (status.error_note or "")
    assert "47" in (status.error_note or "")


def test_field_status_issues_disabled():
    db = _make_db()
    gql = MagicMock()

    # Run via run_snapshot with hasIssuesEnabled=False
    gql.paginate.side_effect = [
        # repos paginate
        [
            {
                "name": "repo1",
                "defaultBranchRef": {"name": "main"},
                "isFork": False,
                "isArchived": False,
                "hasIssuesEnabled": False,
                "pullRequests": {"totalCount": 0},
                "issues": {"totalCount": 0},
                "parent": None,
            }
        ],
    ]
    # execute is used for single-page PR fetch (returns empty nodes)
    gql.execute.return_value = _make_pr_body(total_count=0, has_next_page=False)

    # Mock releases only — no more _capture_alerts
    with patch("pulse.snapshot._capture_releases") as mock_rel:
        from pulse.schema import FieldStatus
        mock_rel.return_value = ([], FieldStatus(status="success"))

        cfg = _make_cfg()
        db_conn = _make_db()
        snapshot_id = run_snapshot(cfg, db_conn, gql, deadline=None)

    repo_row = db_conn.execute("SELECT * FROM repos WHERE snapshot_id=?", (snapshot_id,)).fetchone()
    assert repo_row is not None
    field_statuses = json.loads(repo_row["field_statuses"])
    assert field_statuses["issues"]["status"] == "disabled"


def test_field_status_failed():
    db = _make_db()
    gql = MagicMock()
    gql.execute.side_effect = RuntimeError("network failure")

    prs, status = _capture_prs(gql, "org", "repo", 30, 12.0, _now(), db, None, "snap")
    assert status.status == "failed"
    assert "network failure" in (status.error_note or "")
    assert prs == []


def test_repos_succeeded_count():
    gql = MagicMock()

    # 3 repos
    repo_nodes = [
        {
            "name": f"repo{i}",
            "defaultBranchRef": {"name": "main"},
            "isFork": False,
            "isArchived": False,
            "hasIssuesEnabled": True,
            "pullRequests": {"totalCount": 0},
            "issues": {"totalCount": 0},
            "parent": None,
        }
        for i in range(1, 4)
    ]
    gql.paginate.return_value = repo_nodes
    gql.execute.return_value = _make_pr_body(total_count=0)

    with patch("pulse.snapshot._capture_prs") as mock_prs, \
         patch("pulse.snapshot._capture_issues") as mock_iss, \
         patch("pulse.snapshot._capture_releases") as mock_rel:
        from pulse.schema import FieldStatus
        success_fs = FieldStatus(status="success")
        mock_prs.return_value = ([], success_fs)
        mock_iss.return_value = ([], success_fs)
        mock_rel.return_value = ([], success_fs)

        cfg = _make_cfg()
        db_conn = _make_db()
        snapshot_id = run_snapshot(cfg, db_conn, gql, deadline=None)

    snap_row = db_conn.execute("SELECT * FROM snapshots WHERE id=?", (snapshot_id,)).fetchone()
    assert snap_row["repos_succeeded"] == 3
    assert snap_row["repos_failed"] == 0


def test_repos_partial_count():
    gql = MagicMock()

    repo_nodes = [
        {
            "name": "repo1",
            "defaultBranchRef": {"name": "main"},
            "isFork": False,
            "isArchived": False,
            "hasIssuesEnabled": True,
            "pullRequests": {"totalCount": 0},
            "issues": {"totalCount": 0},
            "parent": None,
        }
    ]
    gql.paginate.return_value = repo_nodes
    gql.execute.return_value = _make_pr_body(total_count=0)

    with patch("pulse.snapshot._capture_prs") as mock_prs, \
         patch("pulse.snapshot._capture_issues") as mock_iss, \
         patch("pulse.snapshot._capture_releases") as mock_rel:
        from pulse.schema import FieldStatus
        mock_prs.return_value = ([], FieldStatus(status="failed", error_note="boom"))
        mock_iss.return_value = ([], FieldStatus(status="success"))
        mock_rel.return_value = ([], FieldStatus(status="success"))

        cfg = _make_cfg()
        db_conn = _make_db()
        snapshot_id = run_snapshot(cfg, db_conn, gql, deadline=None)

    snap_row = db_conn.execute("SELECT * FROM snapshots WHERE id=?", (snapshot_id,)).fetchone()
    assert snap_row["repos_partial"] == 1
    assert snap_row["repos_succeeded"] == 0


def test_dependabot_author_pattern():
    db = _make_db()

    for bot_login in DEPENDABOT_AUTHORS:
        gql = MagicMock()
        gql.execute.return_value = _make_pr_body(total_count=1, author=bot_login)
        prs, _ = _capture_prs(gql, "org", "repo", 30, 12.0, _now(), db, None, "snap")
        assert prs[0].is_dependabot is True, f"Expected is_dependabot=True for {bot_login}"
        assert prs[0].is_renovate is False

    for bot_login in RENOVATE_AUTHORS:
        gql = MagicMock()
        gql.execute.return_value = _make_pr_body(total_count=1, author=bot_login)
        prs, _ = _capture_prs(gql, "org", "repo", 30, 12.0, _now(), db, None, "snap")
        assert prs[0].is_renovate is True, f"Expected is_renovate=True for {bot_login}"
        assert prs[0].is_dependabot is False


def test_stall_detection():
    """PR updated 25h ago with stall_pr_hours=12 → stalled=True."""
    from datetime import timedelta
    db = _make_db()

    # 25 hours ago
    updated_at = (datetime.now(timezone.utc) - timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M:%SZ")

    gql = MagicMock()
    gql.execute.return_value = _make_pr_body(total_count=1, updated_at=updated_at)

    now = datetime.now(timezone.utc)
    prs, _ = _capture_prs(gql, "org", "repo", 30, 12.0, now, db, None, "snap")
    assert len(prs) == 1
    assert prs[0].stalled is True
    assert prs[0].hours_idle is not None
    assert prs[0].hours_idle > 24
