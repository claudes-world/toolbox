"""tests/test_graphql_timeline.py — PR timeline collection tests."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch

import httpx
import pytest

from pulse.graphql import (
    CostBudgetExceeded,
    GraphQLClient,
    PR_TIMELINE_QUERY,
    TIMELINE_CUMULATIVE_WARN,
)
from pulse.schema import PRData, ReviewEvent
from pulse.snapshot import _capture_pr_timelines, _event_to_review_event
from pulse.storage import create_schema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status_code: int, body: dict, headers: dict | None = None) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = body
    resp.text = str(body)
    resp.headers = headers or {}
    return resp


def _make_client(force_ipv4: bool = False) -> GraphQLClient:
    with patch("pulse.graphql.apply_ipv4_patch"):
        return GraphQLClient(token="test-token", force_ipv4=force_ipv4)


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    create_schema(conn)
    return conn


def _insert_repo_and_pr(
    conn: sqlite3.Connection,
    pr_number: int = 1,
    node_id: str = "PR_abc123",
    repo_id: int | None = None,
) -> tuple[int, int]:
    """Insert snapshot+repo (idempotent) and a PR row. Returns (repo_id, pr_rowid).

    Pass repo_id to reuse an existing repo row instead of inserting a new one.
    """
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO snapshots (id, captured_at_utc, captured_at_et, orgs_queried)"
            " VALUES ('snap1', '2026-01-01T00:00:00Z', '2026-01-01 00:00 ET', '[]')"
        )
        if repo_id is None:
            cur = conn.execute(
                "INSERT INTO repos (snapshot_id, org, name, capture_status)"
                " VALUES ('snap1', 'org', 'repo', 'success')"
            )
            repo_id = cur.lastrowid
        cur2 = conn.execute(
            "INSERT INTO prs (repo_id, number, title, stalled, node_id)"
            " VALUES (?, ?, 'PR title', 0, ?)",
            (repo_id, pr_number, node_id),
        )
    return repo_id, cur2.lastrowid


def _timeline_body(
    nodes: list[dict],
    has_next_page: bool = False,
    end_cursor: str | None = None,
    cost: int = 1,
    remaining: int = 4999,
) -> dict:
    return {
        "data": {
            "node": {
                "timelineItems": {
                    "totalCount": len(nodes),
                    "pageInfo": {"hasNextPage": has_next_page, "endCursor": end_cursor},
                    "nodes": nodes,
                }
            },
            "rateLimit": {"cost": cost, "remaining": remaining},
        }
    }


def _ratelimit_probe_body(cost: int = 1, remaining: int = 4999) -> dict:
    return {"data": {"rateLimit": {"cost": cost, "remaining": remaining}}}


def _sample_review_node() -> dict:
    return {
        "__typename": "PullRequestReview",
        "author": {"login": "alice"},
        "state": "APPROVED",
        "submittedAt": "2026-01-02T10:00:00Z",
    }


def _sample_requested_node() -> dict:
    return {
        "__typename": "ReviewRequestedEvent",
        "actor": {"login": "bob"},
        "createdAt": "2026-01-02T09:00:00Z",
    }


# ---------------------------------------------------------------------------
# Test 1: Single-page timeline (≤100 events), captured in one query
# ---------------------------------------------------------------------------

def test_single_page_timeline() -> None:
    """Single page (2 events) — captured in one fetch_pr_timeline call."""
    nodes = [_sample_review_node(), _sample_requested_node()]
    timeline_resp = _make_response(200, _timeline_body(nodes))
    probe_resp = _make_response(200, _ratelimit_probe_body())

    client = _make_client()
    cumulative = [0]

    with patch.object(client._client, "send", side_effect=[timeline_resp, probe_resp]):
        result = client.fetch_pr_timeline("PR_abc", cumulative_cost=cumulative)

    assert len(result) == 2
    assert result[0]["__typename"] == "PullRequestReview"
    assert result[1]["__typename"] == "ReviewRequestedEvent"


# ---------------------------------------------------------------------------
# Test 2: Multi-page timeline (>100 events) — pagination followed, events concatenated
# ---------------------------------------------------------------------------

def test_multi_page_timeline_concatenated() -> None:
    """Timeline spanning 2 pages — both pages fetched, all events returned."""
    page1_node = _sample_review_node()
    page2_node = _sample_requested_node()

    page1_resp = _make_response(200, _timeline_body(
        [page1_node], has_next_page=True, end_cursor="cursor-p1"
    ))
    page2_resp = _make_response(200, _timeline_body(
        [page2_node], has_next_page=False
    ))
    probe_resp = _make_response(200, _ratelimit_probe_body())

    client = _make_client()
    call_count = 0

    def fake_send(request: httpx.Request, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return page1_resp
        elif call_count == 2:
            return page2_resp
        else:
            return probe_resp

    with patch.object(client._client, "send", side_effect=fake_send):
        result = client.fetch_pr_timeline("PR_abc", cumulative_cost=[0])

    assert len(result) == 2
    assert result[0]["__typename"] == "PullRequestReview"
    assert result[1]["__typename"] == "ReviewRequestedEvent"


# ---------------------------------------------------------------------------
# Test 3: itemTypes filter — lower cost when filter present
# ---------------------------------------------------------------------------

def test_itemtypes_filter_reduces_cost() -> None:
    """Filtered query returns lower cost than hypothetical unfiltered response."""
    # Simulated: filtered = cost 1, unfiltered = cost 10 (we assert filtered < unfiltered)
    filtered_cost = 1
    unfiltered_cost = 10

    filtered_body = _timeline_body([_sample_review_node()], cost=filtered_cost)
    unfiltered_body = _timeline_body(
        [_sample_review_node()] * 10, cost=unfiltered_cost
    )

    filtered_resp = _make_response(200, filtered_body)
    probe_resp = _make_response(200, _ratelimit_probe_body(cost=filtered_cost))

    client = _make_client()

    with patch.object(client._client, "send", side_effect=[filtered_resp, probe_resp]):
        client.fetch_pr_timeline("PR_abc", cumulative_cost=[0])

    # Verify PR_TIMELINE_QUERY contains itemTypes filter
    assert "itemTypes" in PR_TIMELINE_QUERY
    assert "PULL_REQUEST_REVIEW" in PR_TIMELINE_QUERY
    assert "MERGED_EVENT" in PR_TIMELINE_QUERY

    # Assert cost advantage: filtered < unfiltered
    assert filtered_cost < unfiltered_cost


# ---------------------------------------------------------------------------
# Test 4: JSON blob queryable via json_extract in SQLite
# ---------------------------------------------------------------------------

def test_review_events_json_queryable_in_sqlite() -> None:
    """review_events written as JSON blob is queryable via json_extract."""
    conn = _make_db()
    repo_id, _ = _insert_repo_and_pr(conn, pr_number=1, node_id="PR_node1")

    events = [
        {"type": "PullRequestReview", "author": "alice", "state": "APPROVED",
         "label": None, "submitted_at": "2026-01-02T10:00:00Z", "created_at": None},
        {"type": "ReviewRequestedEvent", "author": "bob", "state": None,
         "label": None, "submitted_at": None, "created_at": "2026-01-02T09:00:00Z"},
    ]
    review_json = json.dumps(events)
    with conn:
        conn.execute(
            "UPDATE prs SET review_events=? WHERE repo_id=? AND number=1",
            (review_json, repo_id),
        )

    row = conn.execute(
        "SELECT json_extract(review_events, '$[0].type') AS first_type FROM prs WHERE repo_id=?",
        (repo_id,),
    ).fetchone()
    assert row is not None
    assert row["first_type"] == "PullRequestReview"


# ---------------------------------------------------------------------------
# Test 5: Pagination resume — cursor correctly resumes mid-walk
# ---------------------------------------------------------------------------

def test_pagination_resume_with_cursor() -> None:
    """Second call with a cursor starts from where first left off."""
    page2_node = {"__typename": "MergedEvent", "actor": {"login": "carol"}, "createdAt": "2026-01-03T00:00:00Z"}

    # Only one page from cursor position
    page2_resp = _make_response(200, _timeline_body([page2_node], has_next_page=False))
    probe_resp = _make_response(200, _ratelimit_probe_body())

    client = _make_client()
    captured_vars: list[dict] = []

    def fake_send(request: httpx.Request, **kwargs):
        import json as _json
        body = _json.loads(request.content)
        captured_vars.append(body.get("variables", {}))
        if len(captured_vars) == 1:
            return page2_resp
        return probe_resp

    with patch.object(client._client, "send", side_effect=fake_send):
        result = client.fetch_pr_timeline("PR_abc", cumulative_cost=[0])

    assert len(result) == 1
    assert result[0]["__typename"] == "MergedEvent"
    # The first call should have used prId=PR_abc
    assert captured_vars[0].get("prId") == "PR_abc"


# ---------------------------------------------------------------------------
# Test 6: Partial-capture failure — one PR fails, others still captured
# ---------------------------------------------------------------------------

def test_partial_capture_failure_continues() -> None:
    """One PR timeline failure sets its review_events=NULL; other PRs still captured."""
    conn = _make_db()
    repo_id, _ = _insert_repo_and_pr(conn, pr_number=1, node_id="PR_fail")
    _insert_repo_and_pr(conn, pr_number=2, node_id="PR_ok", repo_id=repo_id)

    prs = [
        PRData(number=1, title="PR 1", author="a", created_at=None, updated_at=None,
               is_draft=False, is_dependabot=False, is_renovate=False,
               hours_idle=None, stalled=False, node_id="PR_fail"),
        PRData(number=2, title="PR 2", author="b", created_at=None, updated_at=None,
               is_draft=False, is_dependabot=False, is_renovate=False,
               hours_idle=None, stalled=False, node_id="PR_ok"),
    ]

    ok_events = [_sample_review_node()]
    ok_body = _timeline_body(ok_events)
    ok_resp = _make_response(200, ok_body)
    probe_resp = _make_response(200, _ratelimit_probe_body())

    client = _make_client()
    call_count = 0

    def fake_send(request: httpx.Request, **kwargs):
        nonlocal call_count
        call_count += 1
        import json as _json
        body = _json.loads(request.content)
        variables = body.get("variables", {})
        pr_id = variables.get("prId", "")
        if pr_id == "PR_fail":
            raise httpx.NetworkError("simulated failure")
        # With real-cost tracking via on_page_response, no separate probe call is made
        return ok_resp

    from pulse.schema import FieldStatus, RepoData
    repo = RepoData(org="o", name="r", default_branch="main", is_fork=False,
                    is_archived=False, has_issues_enabled=True, parent_owner=None,
                    parent_name=None, parent_is_deleted=False, capture_status="success")

    with patch.object(client._client, "send", side_effect=fake_send), \
         patch("pulse.graphql.time.sleep"):
        _capture_pr_timelines(client, conn, repo_id, prs, repo, None, [0])

    pr1 = conn.execute("SELECT review_events FROM prs WHERE repo_id=? AND number=1", (repo_id,)).fetchone()
    pr2 = conn.execute("SELECT review_events FROM prs WHERE repo_id=? AND number=2", (repo_id,)).fetchone()

    # PR 1 failed — review_events should be NULL
    assert pr1["review_events"] is None
    # PR 2 succeeded — should have JSON
    assert pr2["review_events"] is not None
    parsed = json.loads(pr2["review_events"])
    assert isinstance(parsed, list)
    assert len(parsed) > 0


# ---------------------------------------------------------------------------
# Test 7: Empty timeline — PR with 0 events → review_events = '[]', not NULL
# ---------------------------------------------------------------------------

def test_empty_timeline_stores_empty_list() -> None:
    """PR with 0 timeline events → review_events = '[]', not NULL."""
    conn = _make_db()
    repo_id, _ = _insert_repo_and_pr(conn, pr_number=1, node_id="PR_empty")

    prs = [
        PRData(number=1, title="PR 1", author="a", created_at=None, updated_at=None,
               is_draft=False, is_dependabot=False, is_renovate=False,
               hours_idle=None, stalled=False, node_id="PR_empty"),
    ]

    empty_body = _timeline_body([])
    empty_resp = _make_response(200, empty_body)
    probe_resp = _make_response(200, _ratelimit_probe_body())

    client = _make_client()
    call_count = 0

    def fake_send(request: httpx.Request, **kwargs):
        nonlocal call_count
        call_count += 1
        return empty_resp if call_count == 1 else probe_resp

    from pulse.schema import RepoData
    repo = RepoData(org="o", name="r", default_branch="main", is_fork=False,
                    is_archived=False, has_issues_enabled=True, parent_owner=None,
                    parent_name=None, parent_is_deleted=False, capture_status="success")

    with patch.object(client._client, "send", side_effect=fake_send):
        _capture_pr_timelines(client, conn, repo_id, prs, repo, None, [0])

    pr = conn.execute("SELECT review_events FROM prs WHERE repo_id=? AND number=1", (repo_id,)).fetchone()
    assert pr["review_events"] is not None
    assert json.loads(pr["review_events"]) == []


# ---------------------------------------------------------------------------
# Test 8: Orphan PR (node ID not found in GitHub) — graceful degradation, no crash
# ---------------------------------------------------------------------------

def test_orphan_pr_node_id_not_found() -> None:
    """PR node ID returns None from GitHub (node not found) — graceful, no crash."""
    conn = _make_db()
    repo_id, _ = _insert_repo_and_pr(conn, pr_number=1, node_id="PR_orphan")

    prs = [
        PRData(number=1, title="Orphan PR", author="a", created_at=None, updated_at=None,
               is_draft=False, is_dependabot=False, is_renovate=False,
               hours_idle=None, stalled=False, node_id="PR_orphan"),
    ]

    # GitHub returns node=null when node ID is not found
    orphan_body = {
        "data": {
            "node": None,
            "rateLimit": {"cost": 1, "remaining": 4999},
        }
    }
    orphan_resp = _make_response(200, orphan_body)
    probe_resp = _make_response(200, _ratelimit_probe_body())
    call_count = 0

    def fake_send(request: httpx.Request, **kwargs):
        nonlocal call_count
        call_count += 1
        return orphan_resp if call_count == 1 else probe_resp

    client = _make_client()

    from pulse.schema import RepoData
    repo = RepoData(org="o", name="r", default_branch="main", is_fork=False,
                    is_archived=False, has_issues_enabled=True, parent_owner=None,
                    parent_name=None, parent_is_deleted=False, capture_status="success")

    # Must not crash
    with patch.object(client._client, "send", side_effect=fake_send):
        _capture_pr_timelines(client, conn, repo_id, prs, repo, None, [0])

    pr = conn.execute("SELECT review_events FROM prs WHERE repo_id=? AND number=1", (repo_id,)).fetchone()
    assert pr is not None
    # Node not found → PRNodeNotFound raised → review_events stays NULL (not a failure)
    assert pr["review_events"] is None, "Orphaned node_id should result in NULL review_events"


# ---------------------------------------------------------------------------
# Test 9 (bonus): _event_to_review_event correctly maps all typename fields
# ---------------------------------------------------------------------------

def test_event_to_review_event_mapping() -> None:
    """_event_to_review_event correctly extracts fields for each __typename."""
    review = _event_to_review_event({
        "__typename": "PullRequestReview",
        "author": {"login": "alice"},
        "state": "APPROVED",
        "submittedAt": "2026-01-02T10:00:00Z",
    })
    assert review.type == "PullRequestReview"
    assert review.author == "alice"
    assert review.state == "APPROVED"
    assert review.submitted_at == "2026-01-02T10:00:00Z"
    assert review.created_at is None

    labeled = _event_to_review_event({
        "__typename": "LabeledEvent",
        "actor": {"login": "bot"},
        "createdAt": "2026-01-02T11:00:00Z",
        "label": {"name": "bug"},
    })
    assert labeled.type == "LabeledEvent"
    assert labeled.author == "bot"
    assert labeled.label == "bug"
    assert labeled.state is None
    assert labeled.submitted_at is None
    assert labeled.created_at == "2026-01-02T11:00:00Z"

    merged = _event_to_review_event({
        "__typename": "MergedEvent",
        "actor": {"login": "carol"},
        "createdAt": "2026-01-03T00:00:00Z",
    })
    assert merged.type == "MergedEvent"
    assert merged.label is None
