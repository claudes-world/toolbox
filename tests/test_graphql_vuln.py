"""tests/test_graphql_vuln.py — Vulnerability alerts GraphQL capture tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import httpx
import pytest

from pulse.graphql import GraphQLClient, ScopeMissing, VULN_ALERTS_QUERY
from pulse.schema import VulnerabilityAlert


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


def _make_client() -> GraphQLClient:
    with patch("pulse.graphql.apply_ipv4_patch"):
        return GraphQLClient(token="test-token", force_ipv4=False)


def _vuln_node(
    ghsa_id: str = "GHSA-1234-5678-abcd",
    package_name: str = "lodash",
    ecosystem: str = "NPM",
    severity: str = "HIGH",
    published_at: str | None = None,
    dep_pr_number: int | None = None,
) -> dict:
    if published_at is None:
        # Default: 10 days ago
        dt = datetime.now(timezone.utc) - timedelta(days=10)
        published_at = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    node: dict = {
        "securityVulnerability": {
            "severity": severity,
            "package": {"name": package_name, "ecosystem": ecosystem},
            "advisory": {"ghsaId": ghsa_id, "publishedAt": published_at},
        },
        "dependabotUpdate": None,
        "createdAt": published_at,
    }
    if dep_pr_number is not None:
        node["dependabotUpdate"] = {
            "pullRequest": {"number": dep_pr_number, "updatedAt": published_at}
        }
    return node


def _vuln_alerts_body(
    nodes: list[dict],
    has_next_page: bool = False,
    end_cursor: str | None = None,
    total_count: int | None = None,
    cost: int = 1,
    remaining: int = 4999,
) -> dict:
    return {
        "data": {
            "repository": {
                "vulnerabilityAlerts": {
                    "totalCount": total_count if total_count is not None else len(nodes),
                    "pageInfo": {"hasNextPage": has_next_page, "endCursor": end_cursor},
                    "nodes": nodes,
                }
            },
            "rateLimit": {"cost": cost, "remaining": remaining},
        }
    }


# ---------------------------------------------------------------------------
# Test 1: Single-page vuln alerts captured correctly
# ---------------------------------------------------------------------------

def test_single_page_vuln_alerts() -> None:
    """Single page of vuln alerts captured — fields mapped correctly."""
    node = _vuln_node(
        ghsa_id="GHSA-aaaa-bbbb-cccc",
        package_name="requests",
        ecosystem="PIP",
        severity="CRITICAL",
        dep_pr_number=42,
    )
    resp = _make_response(200, _vuln_alerts_body([node]))

    client = _make_client()
    with patch.object(client._client, "send", return_value=resp):
        alerts = client.fetch_vuln_alerts("myorg", "myrepo")

    assert len(alerts) == 1
    a = alerts[0]
    assert isinstance(a, VulnerabilityAlert)
    assert a.ghsa_id == "GHSA-aaaa-bbbb-cccc"
    assert a.package_name == "requests"
    assert a.ecosystem == "PIP"
    assert a.severity == "CRITICAL"
    assert a.dependabot_pr_number == 42
    assert a.age_days >= 0


# ---------------------------------------------------------------------------
# Test 2: Multi-page pagination — all alerts concatenated
# ---------------------------------------------------------------------------

def test_multi_page_vuln_alerts_concatenated() -> None:
    """Two pages of vuln alerts — both fetched, results concatenated."""
    node1 = _vuln_node(ghsa_id="GHSA-0001-0001-0001", package_name="axios")
    node2 = _vuln_node(ghsa_id="GHSA-0002-0002-0002", package_name="express")

    page1_resp = _make_response(
        200, _vuln_alerts_body([node1], has_next_page=True, end_cursor="cursor-p1")
    )
    page2_resp = _make_response(
        200, _vuln_alerts_body([node2], has_next_page=False)
    )

    client = _make_client()
    call_count = 0

    def fake_send(request: httpx.Request, **kwargs):
        nonlocal call_count
        call_count += 1
        return page1_resp if call_count == 1 else page2_resp

    with patch.object(client._client, "send", side_effect=fake_send):
        alerts = client.fetch_vuln_alerts("myorg", "myrepo")

    assert len(alerts) == 2
    ghsa_ids = {a.ghsa_id for a in alerts}
    assert "GHSA-0001-0001-0001" in ghsa_ids
    assert "GHSA-0002-0002-0002" in ghsa_ids


# ---------------------------------------------------------------------------
# Test 3: INSUFFICIENT_SCOPES → ScopeMissing raised, no crash
# ---------------------------------------------------------------------------

def test_insufficient_scopes_raises_scope_missing() -> None:
    """INSUFFICIENT_SCOPES error in GraphQL response → ScopeMissing raised."""
    scope_error_body = {
        "data": None,
        "errors": [
            {
                "type": "INSUFFICIENT_SCOPES",
                "message": "Token requires security_events scope",
            }
        ],
    }
    resp = _make_response(200, scope_error_body)

    client = _make_client()
    with patch.object(client._client, "send", return_value=resp):
        with pytest.raises(ScopeMissing):
            client.fetch_vuln_alerts("myorg", "myrepo")


# ---------------------------------------------------------------------------
# Test 4: Dedup — same ghsa_id different packages → 2 separate entries
# ---------------------------------------------------------------------------

def test_dedup_same_advisory_different_packages() -> None:
    """Same GHSA ID affecting different packages → 2 separate VulnerabilityAlert entries."""
    node1 = _vuln_node(ghsa_id="GHSA-shared-0001", package_name="pkg-a", ecosystem="NPM")
    node2 = _vuln_node(ghsa_id="GHSA-shared-0001", package_name="pkg-b", ecosystem="NPM")

    resp = _make_response(200, _vuln_alerts_body([node1, node2]))

    client = _make_client()
    with patch.object(client._client, "send", return_value=resp):
        alerts = client.fetch_vuln_alerts("myorg", "myrepo")

    assert len(alerts) == 2
    package_names = {a.package_name for a in alerts}
    assert "pkg-a" in package_names
    assert "pkg-b" in package_names


# ---------------------------------------------------------------------------
# Test 5: Same (ghsa_id, package_name) duplicate → deduplicated to 1 entry
# ---------------------------------------------------------------------------

def test_dedup_exact_duplicate_collapsed() -> None:
    """Exact same (ghsa_id, package_name) appearing twice → deduplicated to 1 entry."""
    node1 = _vuln_node(ghsa_id="GHSA-dup-dup-dup", package_name="same-pkg")
    node2 = _vuln_node(ghsa_id="GHSA-dup-dup-dup", package_name="same-pkg")

    resp = _make_response(200, _vuln_alerts_body([node1, node2]))

    client = _make_client()
    with patch.object(client._client, "send", return_value=resp):
        alerts = client.fetch_vuln_alerts("myorg", "myrepo")

    assert len(alerts) == 1
    assert alerts[0].ghsa_id == "GHSA-dup-dup-dup"
    assert alerts[0].package_name == "same-pkg"


# ---------------------------------------------------------------------------
# Test 6: Severity preserved verbatim — CRITICAL, HIGH, MODERATE, LOW
# ---------------------------------------------------------------------------

def test_severity_preserved_verbatim() -> None:
    """Severity values CRITICAL, HIGH, MODERATE, LOW preserved verbatim from GitHub."""
    severities = ["CRITICAL", "HIGH", "MODERATE", "LOW"]
    nodes = [
        _vuln_node(
            ghsa_id=f"GHSA-sev-{sev[:4].lower()}-0001",
            package_name=f"pkg-{i}",
            severity=sev,
        )
        for i, sev in enumerate(severities)
    ]
    resp = _make_response(200, _vuln_alerts_body(nodes))

    client = _make_client()
    with patch.object(client._client, "send", return_value=resp):
        alerts = client.fetch_vuln_alerts("myorg", "myrepo")

    assert len(alerts) == 4
    alert_severities = {a.severity for a in alerts}
    for sev in severities:
        assert sev in alert_severities, f"Severity {sev} not found in alerts"
