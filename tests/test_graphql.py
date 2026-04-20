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


def test_cost_budget_abort() -> None:
    """Query cost exceeding COST_ABORT_THRESHOLD must raise CostBudgetExceeded."""
    body = {"data": {"rateLimit": {"cost": 51, "remaining": 4900}}}
    ok_resp = _make_response(200, body)

    client = _make_client()

    with patch.object(client._client, "send", return_value=ok_resp):
        with pytest.raises(CostBudgetExceeded):
            client.execute("{ repository { ... } }")
