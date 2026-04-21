"""Tests for pulse OTEL instrumentation — spans, metrics, log injection."""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

import pulse.otel as otel_mod
import pulse.instrumentation as instr_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_otel_module() -> None:
    """Reset otel module-level state between tests."""
    otel_mod._tracer_provider = None
    otel_mod._meter_provider = None
    otel_mod._shutdown_called = False


def _reset_instr_module() -> None:
    """Reset instrumentation module-level state between tests."""
    instr_mod._run_duration = None
    instr_mod._repos_succeeded = None
    instr_mod._repos_failed = None
    instr_mod._capture_errors = None
    instr_mod._rate_limit_used[0] = 0
    instr_mod._dependabot_alerts.clear()


def _make_db() -> sqlite3.Connection:
    from pulse.storage import create_schema
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    create_schema(conn)
    return conn


def _make_cfg(orgs: dict | None = None):
    from pulse.config import Defaults, OrgConfig, PulseConfig
    return PulseConfig(
        schema_version="1.0",
        orgs=orgs or {"testorg": OrgConfig()},
        defaults=Defaults(
            stall_pr_hours=12,
            stall_issue_hours=72,
            max_prs_per_repo=30,
            max_issues_per_repo=50,
            max_releases_per_repo=10,
        ),
    )


def _make_repo_nodes(names: list[str]) -> list[dict]:
    return [
        {
            "name": name,
            "defaultBranchRef": {"name": "main"},
            "isFork": False,
            "isArchived": False,
            "hasIssuesEnabled": True,
            "pullRequests": {"totalCount": 0},
            "issues": {"totalCount": 0},
            "parent": None,
        }
        for name in names
    ]


def _make_in_memory_tracer() -> tuple[TracerProvider, InMemorySpanExporter]:
    """Create an isolated in-memory OTEL tracer provider."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


# ---------------------------------------------------------------------------
# Test 1: span hierarchy — pulse.run > pulse.repo.collect
# ---------------------------------------------------------------------------

def test_span_hierarchy(monkeypatch: pytest.MonkeyPatch) -> None:
    """pulse.run span wraps two pulse.repo.collect children."""
    _reset_otel_module()
    _reset_instr_module()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "")

    provider, exporter = _make_in_memory_tracer()
    monkeypatch.setattr(otel_mod, "get_tracer", lambda name: provider.get_tracer(name))

    from pulse.schema import FieldStatus
    success_fs = FieldStatus(status="success")

    gql = MagicMock()
    gql.paginate.return_value = _make_repo_nodes(["repo1", "repo2"])

    with (
        patch("pulse.snapshot._capture_prs", return_value=([], success_fs)),
        patch("pulse.snapshot._capture_issues", return_value=([], success_fs)),
        patch("pulse.snapshot._capture_releases", return_value=([], success_fs)),
        patch("pulse.snapshot._capture_pr_timelines"),
        patch("pulse.snapshot._capture_upstream"),
        patch("pulse.snapshot._capture_vuln_alerts", return_value=[]),
    ):
        from pulse.snapshot import run_snapshot
        db_conn = _make_db()
        snapshot_id = run_snapshot(_make_cfg(), db_conn, gql, deadline=None)

    spans = exporter.get_finished_spans()
    span_names = [s.name for s in spans]

    run_spans = [s for s in spans if s.name == "pulse.run"]
    repo_spans = [s for s in spans if s.name == "pulse.repo.collect"]

    assert len(run_spans) == 1, f"Expected 1 pulse.run span, got {len(run_spans)}; all={span_names}"
    assert len(repo_spans) == 2, f"Expected 2 pulse.repo.collect spans, got {len(repo_spans)}; all={span_names}"

    run_span = run_spans[0]
    # snapshot_id attribute present on run span
    assert run_span.attributes.get("pulse.snapshot_id") == snapshot_id

    # Each repo span is a child of the run span
    for repo_span in repo_spans:
        assert repo_span.parent is not None, "pulse.repo.collect span has no parent"
        assert repo_span.parent.span_id == run_span.context.span_id, (
            f"pulse.repo.collect parent span_id {repo_span.parent.span_id!r} "
            f"!= pulse.run span_id {run_span.context.span_id!r}"
        )


# ---------------------------------------------------------------------------
# Test 2: no repo in metric labels
# ---------------------------------------------------------------------------

def test_cardinality_no_repo_in_metric_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    """No metric data point carries a 'repo' or 'repo_name' attribute key."""
    _reset_otel_module()
    _reset_instr_module()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "")

    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry import metrics

    reader = InMemoryMetricReader()
    mp = MeterProvider(metric_readers=[reader])
    # Patch get_meter so instruments go to our isolated provider
    monkeypatch.setattr(otel_mod, "get_meter", lambda name: mp.get_meter(name))

    provider, _ = _make_in_memory_tracer()
    monkeypatch.setattr(otel_mod, "get_tracer", lambda name: provider.get_tracer(name))

    from pulse.schema import FieldStatus
    success_fs = FieldStatus(status="success")

    gql = MagicMock()
    gql.paginate.return_value = _make_repo_nodes(["repo1", "repo2", "repo3"])

    with (
        patch("pulse.snapshot._capture_prs", return_value=([], success_fs)),
        patch("pulse.snapshot._capture_issues", return_value=([], success_fs)),
        patch("pulse.snapshot._capture_releases", return_value=([], success_fs)),
        patch("pulse.snapshot._capture_pr_timelines"),
        patch("pulse.snapshot._capture_upstream"),
        patch("pulse.snapshot._capture_vuln_alerts", return_value=[]),
    ):
        from pulse.snapshot import run_snapshot
        db_conn = _make_db()
        run_snapshot(_make_cfg(), db_conn, gql, deadline=None)

    data = reader.get_metrics_data()
    forbidden_keys = {"repo", "repo_name"}
    for resource_metric in data.resource_metrics:
        for scope_metric in resource_metric.scope_metrics:
            for metric in scope_metric.metrics:
                for dp in getattr(metric.data, "data_points", []):
                    attrs = dict(getattr(dp, "attributes", {}) or {})
                    overlap = forbidden_keys & set(attrs.keys())
                    assert not overlap, (
                        f"Metric '{metric.name}' has forbidden label key(s) {overlap}: {attrs}"
                    )


# ---------------------------------------------------------------------------
# Test 3: no-op mode — no spans emitted
# ---------------------------------------------------------------------------

def test_noop_mode_no_spans(monkeypatch: pytest.MonkeyPatch) -> None:
    """No-op mode: TracerProvider has zero processors so spans are dropped."""
    _reset_otel_module()
    _reset_instr_module()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "")

    # A no-op TracerProvider has no span processors
    noop_provider = TracerProvider()  # no processors added
    exporter = InMemorySpanExporter()
    # Do NOT add any processor — this is the no-op scenario

    monkeypatch.setattr(otel_mod, "get_tracer", lambda name: noop_provider.get_tracer(name))

    from pulse.schema import FieldStatus
    success_fs = FieldStatus(status="success")
    gql = MagicMock()
    gql.paginate.return_value = _make_repo_nodes(["repo1"])

    with (
        patch("pulse.snapshot._capture_prs", return_value=([], success_fs)),
        patch("pulse.snapshot._capture_issues", return_value=([], success_fs)),
        patch("pulse.snapshot._capture_releases", return_value=([], success_fs)),
        patch("pulse.snapshot._capture_pr_timelines"),
        patch("pulse.snapshot._capture_upstream"),
        patch("pulse.snapshot._capture_vuln_alerts", return_value=[]),
    ):
        from pulse.snapshot import run_snapshot
        db_conn = _make_db()
        run_snapshot(_make_cfg(), db_conn, gql, deadline=None)

    # Exporter was never attached to the provider, so it captures nothing
    assert len(exporter.get_finished_spans()) == 0


# ---------------------------------------------------------------------------
# Test 4: pulse.gql.request span is emitted with query.name attribute
# ---------------------------------------------------------------------------

def test_gql_execute_span(monkeypatch: pytest.MonkeyPatch) -> None:
    """execute() emits a pulse.gql.request span with query.name attribute."""
    _reset_otel_module()
    _reset_instr_module()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "")

    provider, exporter = _make_in_memory_tracer()
    import pulse.graphql as gql_mod
    monkeypatch.setattr(gql_mod, "_otel", MagicMock(get_tracer=lambda name: provider.get_tracer(name)))

    # Patch httpx client to return a fake 200 response
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "data": {
            "rateLimit": {"cost": 1, "remaining": 5000},
            "repository": {"pullRequests": {"totalCount": 0, "nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}},
        }
    }
    fake_resp.headers = {}

    from pulse.graphql import GraphQLClient
    client = GraphQLClient.__new__(GraphQLClient)
    client._client = MagicMock()
    client._client.post.return_value = fake_resp

    test_query = """
    query GetPullRequests($org: String!, $repo: String!) {
      rateLimit { cost remaining }
      repository(owner: $org, name: $repo) {
        pullRequests(first: 10) { totalCount nodes { number } pageInfo { hasNextPage endCursor } }
      }
    }
    """

    result = client.execute(test_query, {"org": "myorg", "repo": "myrepo"})

    spans = exporter.get_finished_spans()
    gql_spans = [s for s in spans if s.name == "pulse.gql.request"]

    assert len(gql_spans) >= 1, f"Expected at least one pulse.gql.request span; got: {[s.name for s in spans]}"
    span = gql_spans[0]
    assert span.attributes.get("query.name") == "GetPullRequests", (
        f"Expected query.name='GetPullRequests', got {span.attributes.get('query.name')!r}"
    )
