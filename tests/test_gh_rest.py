"""tests/test_gh_rest.py — GHRestClient tests for fork upstream compare."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from pulse.gh_rest import GHRestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_rest_response(
    status_code: int,
    body: dict | None = None,
    text: str = "",
    headers: dict | None = None,
) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = body or {}
    resp.text = text or str(body or {})
    resp.headers = headers or {}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=MagicMock(), response=resp
        )
    return resp


def _make_client() -> GHRestClient:
    with patch("pulse.gh_rest.apply_ipv4_patch"):
        return GHRestClient(token="test-token", force_ipv4=False)


# ---------------------------------------------------------------------------
# Test 1: Happy path — fork compare returns commits_behind and commits_ahead
# ---------------------------------------------------------------------------

def test_compare_fork_upstream_happy_path() -> None:
    """Happy path: compare endpoint returns behind_by and ahead_by correctly."""
    compare_body = {
        "status": "diverged",
        "behind_by": 5,
        "ahead_by": 2,
        "commits": [],
    }
    resp = _make_rest_response(200, compare_body)

    client = _make_client()
    with patch.object(client._client, "send", return_value=resp):
        result = client.compare_fork_upstream(
            fork_owner="myorg",
            fork_repo="myfork",
            fork_default_branch="develop",
            parent_owner="upstream-org",
            parent_default_branch="main",
        )

    assert result["status"] == "success"
    assert result["commits_behind"] == 5
    assert result["commits_ahead"] == 2
    assert "recent_upstream_releases" in result


# ---------------------------------------------------------------------------
# Test 2: 404 on deleted/missing upstream → parent_unavailable, no crash
# ---------------------------------------------------------------------------

def test_compare_fork_upstream_404_parent_unavailable() -> None:
    """404 response → status=parent_unavailable, no exception raised."""
    resp = _make_rest_response(
        404, text="Not Found: upstream repo deleted"
    )
    # 404 should NOT trigger raise_for_status
    resp.raise_for_status.side_effect = None

    client = _make_client()
    with patch.object(client._client, "send", return_value=resp):
        result = client.compare_fork_upstream(
            fork_owner="myorg",
            fork_repo="myfork",
            fork_default_branch="main",
            parent_owner="deleted-org",
            parent_default_branch="main",
        )

    assert result["status"] == "parent_unavailable"
    assert "error_note" in result


# ---------------------------------------------------------------------------
# Test 3: Branch names from captured data appear in the request URL — not hardcoded "main"
# ---------------------------------------------------------------------------

def test_compare_uses_captured_branch_names_not_hardcoded() -> None:
    """Branch names from caller args appear in the request URL, not hardcoded 'main'."""
    compare_body = {"behind_by": 0, "ahead_by": 0, "status": "identical"}
    resp = _make_rest_response(200, compare_body)

    client = _make_client()
    captured_requests: list[httpx.Request] = []

    def fake_send(request: httpx.Request, **kwargs):
        captured_requests.append(request)
        return resp

    with patch.object(client._client, "send", side_effect=fake_send):
        client.compare_fork_upstream(
            fork_owner="myorg",
            fork_repo="myfork",
            fork_default_branch="feature-branch",
            parent_owner="upstream",
            parent_default_branch="trunk",
        )

    assert len(captured_requests) == 1
    url = str(captured_requests[0].url)
    assert "feature-branch" in url, f"fork branch not in URL: {url}"
    assert "trunk" in url, f"parent branch not in URL: {url}"
    # Confirm 'main' is NOT hardcoded in this request (neither branch is 'main')
    # The URL structure: compare/{parent}:{parent_branch}...{fork}:{fork_branch}
    assert "upstream:trunk" in url
    assert "myorg:feature-branch" in url


# ---------------------------------------------------------------------------
# Test 4: Rate limit header warning when remaining < 10
# ---------------------------------------------------------------------------

def test_ratelimit_warning_when_remaining_low(caplog) -> None:
    """Logger warning emitted when x-ratelimit-remaining < 10."""
    import logging

    compare_body = {"behind_by": 1, "ahead_by": 0, "status": "behind"}
    resp = _make_rest_response(
        200, compare_body, headers={"x-ratelimit-remaining": "3"}
    )

    client = _make_client()
    with patch.object(client._client, "send", return_value=resp):
        with caplog.at_level(logging.WARNING, logger="pulse.gh_rest"):
            client.compare_fork_upstream(
                fork_owner="o", fork_repo="r",
                fork_default_branch="main",
                parent_owner="upstream", parent_default_branch="main",
            )

    assert any("rate limit" in rec.message.lower() or "remaining" in rec.message.lower()
                for rec in caplog.records), \
        f"Expected rate limit warning, got: {[r.message for r in caplog.records]}"


# ---------------------------------------------------------------------------
# Test 5: Non-404 HTTP errors (401, 403) raise an exception
# ---------------------------------------------------------------------------

def test_compare_fork_upstream_401_raises() -> None:
    """401 Unauthorized triggers raise_for_status → exception propagates."""
    resp = _make_rest_response(401, text="Unauthorized")

    client = _make_client()
    with patch.object(client._client, "send", return_value=resp):
        with pytest.raises(httpx.HTTPStatusError):
            client.compare_fork_upstream(
                fork_owner="o", fork_repo="r",
                fork_default_branch="main",
                parent_owner="upstream", parent_default_branch="main",
            )


def test_compare_fork_upstream_403_raises() -> None:
    """403 Forbidden triggers raise_for_status → exception propagates."""
    resp = _make_rest_response(403, text="Forbidden")

    client = _make_client()
    with patch.object(client._client, "send", return_value=resp):
        with pytest.raises(httpx.HTTPStatusError):
            client.compare_fork_upstream(
                fork_owner="o", fork_repo="r",
                fork_default_branch="main",
                parent_owner="upstream", parent_default_branch="main",
            )
