from __future__ import annotations

import sqlite3
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from pulse.graphql import (
    CostBudgetExceeded,
    GraphQLClient,
    RateLimitExhausted,
    RetriesExhausted,
    RunDeadlineExceeded,
)


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
    """Return a GraphQLClient with ipv4 patching disabled for unit tests."""
    with patch("pulse.graphql.apply_ipv4_patch"):
        return GraphQLClient(token="test-token", force_ipv4=force_ipv4)


def _pagination_state_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pagination_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            org TEXT NOT NULL,
            repo TEXT NOT NULL,
            field TEXT NOT NULL,
            last_cursor TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            UNIQUE(org, repo, field)
        )
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_execute_429_retry() -> None:
    """Mock 429 secondary-rate-limit for 2 attempts then 200 on 3rd."""
    rate_limited_resp = _make_response(
        429,
        {"message": "secondary rate limit exceeded"},
        {"retry-after": "2"},
    )
    rate_limited_resp.text = "secondary rate limit exceeded"

    ok_resp = _make_response(200, {"data": {"rateLimit": {"cost": 1}}})

    client = _make_client()
    call_seq = [rate_limited_resp, rate_limited_resp, ok_resp]

    with patch.object(client._client, "send", side_effect=call_seq), \
         patch("pulse.graphql.time.sleep"):
        result = client.execute("{ viewer { login } }")

    assert result == {"data": {"rateLimit": {"cost": 1}}}


def test_execute_network_error_retry() -> None:
    """Mock NetworkError on first attempt, then 200 on second."""
    ok_resp = _make_response(200, {"data": {"viewer": {"login": "user"}}})

    client = _make_client()

    with patch.object(client._client, "send", side_effect=[httpx.NetworkError("timeout"), ok_resp]), \
         patch("pulse.graphql.time.sleep"):
        result = client.execute("{ viewer { login } }")

    assert result["data"]["viewer"]["login"] == "user"


def test_execute_partial_data() -> None:
    """Partial data response — both data and errors must be returned as-is."""
    body = {
        "data": {
            "repository": {
                "pullRequests": {"nodes": [{"number": 1}]},
                "issues": None,
            }
        },
        "errors": [{"message": "issues disabled", "path": ["repository", "issues"]}],
    }
    ok_resp = _make_response(200, body)

    client = _make_client()

    with patch.object(client._client, "send", return_value=ok_resp):
        result = client.execute("{ repository { pullRequests { nodes { number } } } }")

    assert result["data"]["repository"]["pullRequests"]["nodes"] == [{"number": 1}]
    assert result["errors"][0]["message"] == "issues disabled"


def test_paginate_resume() -> None:
    """paginate() loads saved cursor, uses it in first request, deletes row on completion."""
    conn = sqlite3.connect(":memory:")
    _pagination_state_schema(conn)

    conn.execute(
        "INSERT INTO pagination_state (org, repo, field, last_cursor, timestamp)"
        " VALUES ('org', 'repo', 'prs', 'cursor-abc', datetime('now'))"
    )
    conn.commit()

    page_body = {
        "data": {
            "rateLimit": {"cost": 1, "remaining": 4999, "resetAt": "2026-04-20T00:00:00Z", "used": 1},
            "repository": {
                "prs": {
                    "nodes": [{"number": 2}],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            },
        }
    }
    ok_resp = _make_response(200, page_body)

    client = _make_client()

    captured_requests: list[dict] = []

    def fake_send(request: httpx.Request, **kwargs):  # type: ignore[override]
        import json
        body = json.loads(request.content)
        captured_requests.append(body)
        return ok_resp

    with patch.object(client._client, "send", side_effect=fake_send):
        nodes = client.paginate(
            query="query($cursor: String) { ... }",
            variables={"cursor": None},
            page_info_path=["data", "repository", "prs", "pageInfo"],
            nodes_path=["data", "repository", "prs", "nodes"],
            db_conn=conn,
            org="org",
            repo="repo",
            field="prs",
            cursor_var="cursor",
        )

    assert nodes == [{"number": 2}]
    assert captured_requests[0]["variables"]["cursor"] == "cursor-abc"

    row = conn.execute(
        "SELECT * FROM pagination_state WHERE org='org' AND repo='repo' AND field='prs'"
    ).fetchone()
    assert row is None


def test_execute_null_data_errors_only() -> None:
    """200 response with null data and errors array must be returned as-is."""
    body = {
        "data": None,
        "errors": [{"message": "Could not resolve to a Repository", "type": "NOT_FOUND"}],
    }
    ok_resp = _make_response(200, body)

    client = _make_client()

    with patch.object(client._client, "send", return_value=ok_resp):
        result = client.execute("{ repository(owner: \"x\", name: \"y\") { name } }")

    assert result["data"] is None
    assert result["errors"][0]["type"] == "NOT_FOUND"


def test_cost_budget_abort() -> None:
    """Query cost exceeding COST_ABORT_THRESHOLD must raise CostBudgetExceeded."""
    body = {"data": {"rateLimit": {"cost": 51, "remaining": 4900}}}
    ok_resp = _make_response(200, body)

    client = _make_client()

    with patch.object(client._client, "send", return_value=ok_resp):
        with pytest.raises(CostBudgetExceeded):
            client.execute("{ repository { ... } }")


def test_execute_deadline_exceeded() -> None:
    """RunDeadlineExceeded raised when deadline is in the past at loop entry."""
    client = _make_client()
    past_deadline = time.monotonic() - 1.0  # already expired

    with pytest.raises(RunDeadlineExceeded):
        client.execute("{ viewer { login } }", deadline_monotonic=past_deadline)


def test_execute_non_secondary_429_raises() -> None:
    """Non-secondary-rate-limit 429 raises RuntimeError, not retried."""
    resp = _make_response(429, {"message": "abuse"}, {})
    resp.text = "abuse detection"

    client = _make_client()

    with patch.object(client._client, "send", return_value=resp):
        with pytest.raises(RuntimeError, match="auth or unexpected 4xx"):
            client.execute("{ viewer { login } }")


def test_execute_500_retry() -> None:
    """HTTP 500 retries with backoff; succeeds on next attempt."""
    server_error_resp = _make_response(500, {}, {})
    server_error_resp.text = "Internal Server Error"
    ok_resp = _make_response(200, {"data": {"viewer": {"login": "user"}}})

    client = _make_client()

    with patch.object(client._client, "send", side_effect=[server_error_resp, ok_resp]), \
         patch("pulse.graphql.time.sleep"):
        result = client.execute("{ viewer { login } }")

    assert result["data"]["viewer"]["login"] == "user"


def test_paginate_interrupt_saves_checkpoint() -> None:
    """RetriesExhausted mid-walk saves last_cursor to pagination_state."""
    conn = sqlite3.connect(":memory:")
    _pagination_state_schema(conn)

    # First page succeeds, second page raises RetriesExhausted
    page1_body = {
        "data": {
            "rateLimit": {"cost": 1, "remaining": 4999, "resetAt": "2026-04-20T00:00:00Z", "used": 1},
            "repository": {
                "prs": {
                    "nodes": [{"number": 1}],
                    "pageInfo": {"hasNextPage": True, "endCursor": "cursor-page1"},
                }
            },
        }
    }
    ok_resp = _make_response(200, page1_body)

    client = _make_client()
    call_count = 0

    def fake_send(request, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return ok_resp
        raise httpx.NetworkError("rate limit")  # will exhaust retries

    with patch.object(client._client, "send", side_effect=fake_send), \
         patch("pulse.graphql.time.sleep"):
        with pytest.raises(RetriesExhausted):
            client.paginate(
                query="query($cursor: String) { ... }",
                variables={"cursor": None},
                page_info_path=["data", "repository", "prs", "pageInfo"],
                nodes_path=["data", "repository", "prs", "nodes"],
                db_conn=conn,
                org="org",
                repo="repo",
                field="prs",
            )

    # Checkpoint should be saved with the cursor from the completed page
    row = conn.execute(
        "SELECT last_cursor FROM pagination_state WHERE org='org' AND repo='repo' AND field='prs'"
    ).fetchone()
    assert row is not None
    assert row[0] == "cursor-page1"


def test_paginate_bad_cursor_deletes_checkpoint() -> None:
    """Path traversal failure on response with errors deletes the checkpoint."""
    conn = sqlite3.connect(":memory:")
    _pagination_state_schema(conn)

    # Pre-insert a stale checkpoint
    conn.execute(
        "INSERT INTO pagination_state (org, repo, field, last_cursor, timestamp)"
        " VALUES ('org', 'repo', 'prs', 'stale-cursor', datetime('now'))"
    )
    conn.commit()

    # Response with null data + errors (bad cursor scenario)
    error_body = {
        "data": None,
        "errors": [{"message": "invalid cursor", "type": "INVALID_CURSOR_ARGUMENTS"}],
    }
    ok_resp = _make_response(200, error_body)

    client = _make_client()

    with patch.object(client._client, "send", return_value=ok_resp):
        nodes = client.paginate(
            query="query($cursor: String) { ... }",
            variables={"cursor": None},
            page_info_path=["data", "repository", "prs", "pageInfo"],
            nodes_path=["data", "repository", "prs", "nodes"],
            db_conn=conn,
            org="org",
            repo="repo",
            field="prs",
        )

    assert nodes == []
    # Stale checkpoint should be deleted (bad-cursor loop guard)
    row = conn.execute(
        "SELECT * FROM pagination_state WHERE org='org' AND repo='repo' AND field='prs'"
    ).fetchone()
    assert row is None


def test_paginate_mid_walk_traversal_failure_deletes_checkpoint() -> None:
    """Path traversal failure after page 1 (pages_advanced=1) still deletes checkpoint."""
    conn = sqlite3.connect(":memory:")
    _pagination_state_schema(conn)

    # Pre-insert a checkpoint
    conn.execute(
        "INSERT INTO pagination_state (org, repo, field, last_cursor, timestamp)"
        " VALUES ('org', 'repo', 'prs', 'old-cursor', datetime('now'))"
    )
    conn.commit()

    page1_body = {
        "data": {
            "rateLimit": {"cost": 1, "remaining": 4999, "resetAt": "2026-04-20T00:00:00Z", "used": 1},
            "repository": {
                "prs": {
                    "nodes": [{"number": 1}],
                    "pageInfo": {"hasNextPage": True, "endCursor": "cursor-page1"},
                }
            },
        }
    }
    page2_error_body = {
        "data": None,
        "errors": [{"message": "internal error", "type": "INTERNAL"}],
    }

    page1_resp = _make_response(200, page1_body)
    page2_resp = _make_response(200, page2_error_body)

    client = _make_client()
    call_count = 0

    def fake_send(request, **kwargs):
        nonlocal call_count
        call_count += 1
        return page1_resp if call_count == 1 else page2_resp

    with patch.object(client._client, "send", side_effect=fake_send):
        nodes = client.paginate(
            query="query($cursor: String) { ... }",
            variables={"cursor": None},
            page_info_path=["data", "repository", "prs", "pageInfo"],
            nodes_path=["data", "repository", "prs", "nodes"],
            db_conn=conn,
            org="org",
            repo="repo",
            field="prs",
        )

    # Page 1 nodes returned before failure
    assert nodes == [{"number": 1}]
    # Checkpoint must be deleted — not preserved despite pages_advanced=1
    row = conn.execute(
        "SELECT * FROM pagination_state WHERE org='org' AND repo='repo' AND field='prs'"
    ).fetchone()
    assert row is None
