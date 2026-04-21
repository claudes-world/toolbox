from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections import Counter as _Counter

import click
from datetime import datetime, timezone
from pathlib import Path

from pulse import __version__
from pulse import instrumentation as _instr
from pulse import otel as _otel
from pulse.config import PulseConfig
from pulse.gh_rest import GHRestClient
from pulse.graphql import CostBudgetExceeded, GraphQLClient, PRNodeNotFound, ScopeMissing, TIMELINE_CUMULATIVE_WARN
from pulse.schema import FieldStatus, IssueData, PRData, ReleaseData, RepoData, ReviewEvent
from pulse.storage import atomic_write_json

DEPENDABOT_AUTHORS = {"dependabot[bot]", "dependabot-preview[bot]", "app/dependabot"}
RENOVATE_AUTHORS = {"renovate[bot]", "renovate-bot[bot]", "app/renovate"}

# GraphQL queries — every query includes rateLimit

REPOS_QUERY = """
query($org: String!, $cursor: String) {
  rateLimit { cost remaining resetAt used }
  organization(login: $org) {
    repositories(first: 50, after: $cursor, orderBy: {field: UPDATED_AT, direction: DESC}) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        name
        defaultBranchRef { name }
        isFork
        isArchived
        hasIssuesEnabled
        pullRequests(first: 1) { totalCount }
        issues(first: 1) { totalCount }
        parent { owner { login } name defaultBranchRef { name } }
      }
    }
  }
}
"""

PRS_QUERY = """
query($org: String!, $repo: String!, $first: Int!, $cursor: String) {
  rateLimit { cost remaining resetAt used }
  repository(owner: $org, name: $repo) {
    pullRequests(first: $first, after: $cursor, states: OPEN, orderBy: {field: UPDATED_AT, direction: DESC}) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        id
        number title
        author { login }
        createdAt updatedAt
        isDraft
      }
    }
  }
}
"""

ISSUES_QUERY = """
query($org: String!, $repo: String!, $first: Int!, $cursor: String) {
  rateLimit { cost remaining resetAt used }
  repository(owner: $org, name: $repo) {
    issues(first: $first, after: $cursor, states: OPEN, orderBy: {field: UPDATED_AT, direction: DESC}) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        number title
        author { login }
        createdAt updatedAt
        labels(first: 10) { nodes { name } }
      }
    }
  }
}
"""

RELEASES_QUERY = """
query($org: String!, $repo: String!, $first: Int!) {
  rateLimit { cost remaining resetAt used }
  repository(owner: $org, name: $repo) {
    releases(first: $first, orderBy: {field: CREATED_AT, direction: DESC}) {
      totalCount
      nodes {
        tagName name createdAt isPrerelease
      }
    }
  }
}
"""


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _hours_idle(updated_at: str | None, now: datetime) -> float | None:
    dt = _parse_dt(updated_at)
    if dt is None:
        return None
    return (now - dt).total_seconds() / 3600.0


def _capture_prs(
    gql: GraphQLClient,
    org: str,
    repo_name: str,
    max_prs: int,
    stall_hours: float,
    now: datetime,
    deadline: float | None,
) -> tuple[list[PRData], FieldStatus]:
    try:
        # Single execute — clamp to 100 (GraphQL page limit)
        actual_first = min(max_prs, 100)
        body = gql.execute(
            PRS_QUERY,
            {"org": org, "repo": repo_name, "first": actual_first, "cursor": None},
            deadline_monotonic=deadline,
        )
        if body.get("data") is None:
            errors = body.get("errors") or []
            note = errors[0].get("message", "repository returned null data") if errors else "repository returned null data"
            return [], FieldStatus(status="failed", error_note=note[:200])
        repo_data = (body.get("data") or {}).get("repository")
        if repo_data is None:
            errors = body.get("errors") or []
            note = errors[0].get("message", "repository not found") if errors else "repository not found"
            return [], FieldStatus(status="failed", error_note=note[:200])
        pr_conn = repo_data.get("pullRequests") or {}
        total_count = pr_conn.get("totalCount", 0)
        nodes = pr_conn.get("nodes") or []

        field_status = FieldStatus(status="success")
        if total_count > actual_first:
            field_status = FieldStatus(
                status="partial",
                error_note=f"truncated, {total_count} total, fetched {len(nodes)}",
            )

        prs: list[PRData] = []
        for node in nodes:
            author_login = (node.get("author") or {}).get("login") or None
            idle = _hours_idle(node.get("updatedAt"), now)
            prs.append(
                PRData(
                    number=node["number"],
                    title=node.get("title", ""),
                    author=author_login,
                    created_at=node.get("createdAt"),
                    updated_at=node.get("updatedAt"),
                    is_draft=bool(node.get("isDraft")),
                    is_dependabot=author_login in DEPENDABOT_AUTHORS if author_login else False,
                    is_renovate=author_login in RENOVATE_AUTHORS if author_login else False,
                    hours_idle=idle,
                    stalled=idle is not None and idle > stall_hours,
                    node_id=node.get("id"),
                )
            )

        return prs, field_status

    except Exception as e:
        return [], FieldStatus(status="failed", error_note=str(e)[:200])


def _capture_issues(
    gql: GraphQLClient,
    org: str,
    repo_name: str,
    max_issues: int,
    stall_hours: float,
    now: datetime,
    deadline: float | None,
) -> tuple[list[IssueData], FieldStatus]:
    try:
        # Single execute — clamp to 100 (GraphQL page limit)
        actual_first = min(max_issues, 100)
        body = gql.execute(
            ISSUES_QUERY,
            {"org": org, "repo": repo_name, "first": actual_first, "cursor": None},
            deadline_monotonic=deadline,
        )
        if body.get("data") is None:
            errors = body.get("errors") or []
            note = errors[0].get("message", "repository returned null data") if errors else "repository returned null data"
            return [], FieldStatus(status="failed", error_note=note[:200])
        repo_data = (body.get("data") or {}).get("repository")
        if repo_data is None:
            errors = body.get("errors") or []
            note = errors[0].get("message", "repository not found") if errors else "repository not found"
            return [], FieldStatus(status="failed", error_note=note[:200])
        issue_conn = repo_data.get("issues") or {}
        total_count = issue_conn.get("totalCount", 0)
        nodes = issue_conn.get("nodes") or []

        field_status = FieldStatus(status="success")
        if total_count > actual_first:
            field_status = FieldStatus(
                status="partial",
                error_note=f"truncated, {total_count} total, fetched {len(nodes)}",
            )

        issues: list[IssueData] = []
        for node in nodes:
            author_login = (node.get("author") or {}).get("login") or None
            idle = _hours_idle(node.get("updatedAt"), now)
            label_nodes = (node.get("labels") or {}).get("nodes") or []
            labels = [ln["name"] for ln in label_nodes if ln.get("name")]
            issues.append(
                IssueData(
                    number=node["number"],
                    title=node.get("title", ""),
                    author=author_login,
                    created_at=node.get("createdAt"),
                    updated_at=node.get("updatedAt"),
                    labels=labels,
                    hours_idle=idle,
                    stalled=idle is not None and idle > stall_hours,
                )
            )

        return issues, field_status

    except Exception as e:
        return [], FieldStatus(status="failed", error_note=str(e)[:200])


def _capture_releases(
    gql: GraphQLClient,
    org: str,
    repo_name: str,
    max_releases: int,
    deadline: float | None,
) -> tuple[list[ReleaseData], FieldStatus]:
    try:
        actual_first = min(max_releases, 100)
        body = gql.execute(
            RELEASES_QUERY,
            variables={"org": org, "repo": repo_name, "first": actual_first},
            deadline_monotonic=deadline,
        )
        if body.get("data") is None:
            errors = body.get("errors") or []
            note = errors[0].get("message", "repository returned null data") if errors else "repository returned null data"
            return [], FieldStatus(status="failed", error_note=note[:200])
        repo_data = (body.get("data") or {}).get("repository")
        if repo_data is None:
            errors = body.get("errors") or []
            note = errors[0].get("message", "repository not found") if errors else "repository not found"
            return [], FieldStatus(status="failed", error_note=note[:200])
        release_conn = repo_data.get("releases") or {}
        total_count = release_conn.get("totalCount", 0)
        nodes = release_conn.get("nodes") or []

        field_status = FieldStatus(status="success")
        if total_count > actual_first:
            field_status = FieldStatus(
                status="partial",
                error_note=f"truncated, {total_count} total, fetched {len(nodes)}",
            )

        releases = [
            ReleaseData(
                tag_name=n.get("tagName", ""),
                name=n.get("name"),
                created_at=n.get("createdAt"),
                is_prerelease=bool(n.get("isPrerelease")),
            )
            for n in nodes
        ]
        return releases, field_status
    except Exception as e:
        return [], FieldStatus(status="failed", error_note=str(e)[:200])



def _capture_upstream(
    rest_client: GHRestClient,
    repo: RepoData,
    conn: sqlite3.Connection,
    repo_id: int,
) -> None:
    """Capture fork upstream compare delta and store as JSON in repos.upstream.

    Only runs if repo.is_fork == True and repo.parent is available.
    Gracefully degrades: exceptions stored as failed status, never abort caller.
    """
    if not repo.is_fork:
        return
    if not repo.parent_owner or not repo.parent_name:
        return

    fork_branch = repo.default_branch
    parent_branch = repo.parent_default_branch

    if not fork_branch or not parent_branch:
        upstream_blob = {
            "status": "failed",
            "error_note": "missing default_branch for fork or parent",
        }
        repo.field_statuses["upstream"] = FieldStatus(
            status="failed", error_note=upstream_blob["error_note"]
        )
    else:
        try:
            result = rest_client.compare_fork_upstream(
                fork_owner=repo.org,
                fork_repo=repo.name,
                fork_default_branch=fork_branch,
                parent_owner=repo.parent_owner,
                parent_default_branch=parent_branch,
            )
            upstream_blob = result
            repo.field_statuses["upstream"] = FieldStatus(
                status=result.get("status", "success")
                if result.get("status") in ("success", "parent_unavailable")
                else "failed"
            )
        except Exception as e:
            upstream_blob = {"status": "failed", "error_note": str(e)[:200]}
            repo.field_statuses["upstream"] = FieldStatus(
                status="failed", error_note=str(e)[:200]
            )

    try:
        with conn:
            conn.execute(
                "UPDATE repos SET upstream=? WHERE id=?",
                (json.dumps(upstream_blob), repo_id),
            )
    except Exception as e:
        logging.getLogger(__name__).warning(
            "Failed to write upstream for repo_id=%s: %s", repo_id, e
        )


def _capture_vuln_alerts(
    graphql_client: GraphQLClient,
    repo: RepoData,
    conn: sqlite3.Connection,
    repo_id: int,
    cumulative_cost: list[int] | None = None,
) -> list:
    """Capture open vulnerability alerts and store as JSON in repos.vulnerability_alerts.

    Handles ScopeMissing with a LOUD field_status alert. Other exceptions stored as failed.
    Gracefully degrades: never aborts caller.

    Returns the list of VulnerabilityAlert objects on success, or [] on any error.
    """
    alerts_blob: list | dict
    captured_alerts: list = []
    try:
        alerts = graphql_client.fetch_vuln_alerts(
            owner=repo.org,
            name=repo.name,
            cumulative_cost=cumulative_cost,
        )
        alerts_blob = [a.model_dump() for a in alerts]
        repo.field_statuses["vulnerability_alerts"] = FieldStatus(status="success")
        captured_alerts = alerts
    except ScopeMissing as e:
        alerts_blob = {"status": "scope_missing", "error_note": str(e)[:200]}
        repo.field_statuses["vulnerability_alerts"] = FieldStatus(
            status="scope_missing",
            error_note=f"ALERT: token lacks security_events scope — vuln alerts unavailable: {str(e)[:150]}",
        )
        logging.getLogger(__name__).warning(
            "SCOPE MISSING: vulnerability alerts require security_events scope on token (repo=%s/%s): %s",
            repo.org, repo.name, e,
        )
    except CostBudgetExceeded:
        alerts_blob = {"status": "partial", "error_note": "skipped: cumulative cost budget reached"}
        repo.field_statuses["vulnerability_alerts"] = FieldStatus(
            status="partial", error_note="skipped: cumulative cost budget reached"
        )
    except Exception as e:
        alerts_blob = {"status": "failed", "error_note": str(e)[:200]}
        repo.field_statuses["vulnerability_alerts"] = FieldStatus(
            status="failed", error_note=str(e)[:200]
        )

    try:
        with conn:
            conn.execute(
                "UPDATE repos SET vulnerability_alerts=? WHERE id=?",
                (json.dumps(alerts_blob), repo_id),
            )
    except Exception as e:
        logging.getLogger(__name__).warning(
            "Failed to write vulnerability_alerts for repo_id=%s: %s", repo_id, e
        )

    return captured_alerts


def _event_to_review_event(node: dict) -> ReviewEvent:
    """Convert a raw timeline event node to a ReviewEvent."""
    typename = node.get("__typename", "")
    actor = (node.get("actor") or {}).get("login") or None
    author_obj = (node.get("author") or {}).get("login") or None
    # PullRequestReview uses 'author', all others use 'actor'
    author = author_obj if typename == "PullRequestReview" else actor
    label_obj = node.get("label") or {}
    return ReviewEvent(
        type=typename,
        author=author,
        state=node.get("state") if typename == "PullRequestReview" else None,
        label=label_obj.get("name") if typename == "LabeledEvent" else None,
        submitted_at=node.get("submittedAt") if typename == "PullRequestReview" else None,
        created_at=node.get("createdAt") if typename != "PullRequestReview" else None,
    )


def _capture_pr_timelines(
    gql: GraphQLClient,
    conn: sqlite3.Connection,
    repo_id: int,
    prs: list[PRData],
    repo: RepoData,
    deadline: float | None,
    cumulative_cost: list[int],
) -> None:
    """Fetch timeline events for each PR and write review_events JSON to the prs table.

    Graceful degradation: per-PR failures set review_events=NULL and update
    repo.field_statuses['review_events'] to partial. CostBudgetExceeded causes
    remaining PRs to be skipped (review_events stays NULL).
    """
    budget_exceeded = False
    any_failure = False
    skipped_no_node_id = 0

    for pr in prs:
        if budget_exceeded:
            # Skip remaining — leave review_events NULL
            continue

        if not pr.node_id:
            # No node_id available — skip silently
            skipped_no_node_id += 1
            continue

        try:
            raw_events = gql.fetch_pr_timeline(
                pr.node_id,
                deadline_monotonic=deadline,
                cumulative_cost=cumulative_cost,
            )
            events = [_event_to_review_event(n) for n in raw_events if n.get("__typename")]
            review_json = json.dumps(
                [
                    {
                        "type": e.type,
                        "author": e.author,
                        "state": e.state,
                        "label": e.label,
                        "submitted_at": e.submitted_at,
                        "created_at": e.created_at,
                    }
                    for e in events
                ]
            )
            with conn:
                conn.execute(
                    "UPDATE prs SET review_events=? WHERE repo_id=? AND number=?",
                    (review_json, repo_id, pr.number),
                )

        except PRNodeNotFound:
            # Node not found on GitHub — leave review_events NULL silently (not a failure)
            pass

        except CostBudgetExceeded as e:
            import sys as _sys
            print(f"WARNING: {e} — skipping remaining PR timelines", file=_sys.stderr)
            budget_exceeded = True
            any_failure = True

        except Exception as e:
            import sys as _sys
            print(
                f"WARNING: timeline capture failed for PR #{pr.number}: {e}",
                file=_sys.stderr,
            )
            any_failure = True
            # Leave review_events=NULL for this PR
            existing = repo.field_statuses.get("review_events")
            if existing is None or existing.status == "success":
                repo.field_statuses["review_events"] = FieldStatus(
                    status="partial",
                    error_note=f"timeline capture failed for PR #{pr.number}: {str(e)[:100]}",
                )

    if skipped_no_node_id > 0 and prs:
        if skipped_no_node_id == len(prs):
            logging.getLogger(__name__).warning(
                "All %d PRs for repo_id=%s lack node_id; timeline capture skipped entirely",
                len(prs), repo_id,
            )
        else:
            logging.getLogger(__name__).warning(
                "%d/%d PRs for repo_id=%s lack node_id; their timelines skipped",
                skipped_no_node_id, len(prs), repo_id,
            )
        # Treat as partial — we skipped work we should have done
        any_failure = True

    if budget_exceeded:
        repo.field_statuses["review_events"] = FieldStatus(
            status="partial",
            error_note=f"skipped: cumulative cost approached {TIMELINE_CUMULATIVE_WARN}/hr limit",
        )
    elif any_failure:
        # Set partial if not already set by the per-PR exception handler above
        if "review_events" not in repo.field_statuses:
            repo.field_statuses["review_events"] = FieldStatus(
                status="partial",
                error_note=f"{skipped_no_node_id}/{len(prs)} PRs lack node_id; timeline capture skipped",
            )
    else:
        repo.field_statuses["review_events"] = FieldStatus(status="success")


def _insert_snapshot_placeholder(
    db_conn: sqlite3.Connection,
    snapshot_id: str,
    captured_at_utc: str,
    captured_at_et: str,
    orgs_queried: list[str],
) -> None:
    """Insert the snapshot row early so repos can FK-reference it."""
    with db_conn:
        db_conn.execute(
            """
            INSERT OR IGNORE INTO snapshots
              (id, captured_at_utc, captured_at_et, duration_ms, orgs_queried,
               repos_succeeded, repos_failed, repos_partial, schema_version, capture_status)
            VALUES (?, ?, ?, 0, ?, 0, 0, 0, '1.0', 'in_progress')
            """,
            (
                snapshot_id,
                captured_at_utc,
                captured_at_et,
                json.dumps(orgs_queried),
            ),
        )


def _finalize_snapshot(
    db_conn: sqlite3.Connection,
    snapshot_id: str,
    duration_ms: int,
    repos_succeeded: int,
    repos_failed: int,
    repos_partial: int,
    capture_status: str = "success",
) -> None:
    with db_conn:
        db_conn.execute(
            """
            UPDATE snapshots
            SET duration_ms=?, repos_succeeded=?, repos_failed=?, repos_partial=?,
                capture_status=?
            WHERE id=?
            """,
            (duration_ms, repos_succeeded, repos_failed, repos_partial, capture_status, snapshot_id),
        )


def _persist_repo(
    db_conn: sqlite3.Connection,
    snapshot_id: str,
    repo: RepoData,
    prs: list[PRData],
    issues: list[IssueData],
    releases: list[ReleaseData],
) -> int:
    field_statuses_json = json.dumps(
        {k: {"status": v.status, "error_note": v.error_note} for k, v in repo.field_statuses.items()}
    )
    with db_conn:
        cur = db_conn.execute(
            """
            INSERT INTO repos
              (snapshot_id, org, name, default_branch, is_fork, is_archived,
               parent_owner, parent_name, parent_is_deleted, capture_status, field_statuses)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                repo.org,
                repo.name,
                repo.default_branch,
                int(repo.is_fork),
                int(repo.is_archived),
                repo.parent_owner,
                repo.parent_name,
                int(repo.parent_is_deleted),
                repo.capture_status,
                field_statuses_json,
            ),
        )
        repo_id = cur.lastrowid

        for pr in prs:
            db_conn.execute(
                """
                INSERT OR REPLACE INTO prs
                  (repo_id, number, title, author, created_at, updated_at,
                   is_draft, is_dependabot, is_renovate, hours_idle, stalled,
                   node_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    repo_id,
                    pr.number,
                    pr.title,
                    pr.author,
                    pr.created_at,
                    pr.updated_at,
                    int(pr.is_draft),
                    int(pr.is_dependabot),
                    int(pr.is_renovate),
                    pr.hours_idle,
                    int(pr.stalled),
                    pr.node_id,
                ),
            )

        for issue in issues:
            db_conn.execute(
                """
                INSERT OR REPLACE INTO issues
                  (repo_id, number, title, author, created_at, updated_at,
                   labels, hours_idle, stalled)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    repo_id,
                    issue.number,
                    issue.title,
                    issue.author,
                    issue.created_at,
                    issue.updated_at,
                    json.dumps(issue.labels),
                    issue.hours_idle,
                    int(issue.stalled),
                ),
            )

        for release in releases:
            db_conn.execute(
                """
                INSERT OR REPLACE INTO releases
                  (repo_id, tag_name, name, created_at, is_prerelease)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    repo_id,
                    release.tag_name,
                    release.name,
                    release.created_at,
                    int(release.is_prerelease),
                ),
            )

    return repo_id


def _prune_old_snapshots(
    db_conn: sqlite3.Connection,
    history_days: int,
) -> None:
    cutoff = datetime.now(timezone.utc)
    cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    # Use SQLite datetime arithmetic for the cutoff
    with db_conn:
        db_conn.execute(
            "DELETE FROM snapshots WHERE datetime(captured_at_utc) < datetime(?, '-' || ? || ' days')",
            (cutoff_str, str(history_days)),
        )


def _build_current_json(
    db_conn: sqlite3.Connection,
    snapshot_id: str,
) -> dict:
    """Build current.json payload from the given snapshot."""
    snap_row = db_conn.execute(
        "SELECT * FROM snapshots WHERE id=?", (snapshot_id,)
    ).fetchone()
    if snap_row is None:
        return {}

    result: dict = {
        "snapshot_id": snap_row["id"],
        "captured_at_utc": snap_row["captured_at_utc"],
        "captured_at_et": snap_row["captured_at_et"],
        "duration_ms": snap_row["duration_ms"],
        "capture_status": snap_row["capture_status"],
        "repos_succeeded": snap_row["repos_succeeded"],
        "repos_failed": snap_row["repos_failed"],
        "repos_partial": snap_row["repos_partial"],
        "orgs": json.loads(snap_row["orgs_queried"]),
        "repos": [],
    }

    repo_rows = db_conn.execute(
        "SELECT * FROM repos WHERE snapshot_id=?", (snapshot_id,)
    ).fetchall()

    for repo_row in repo_rows:
        repo_id = repo_row["id"]
        pr_rows = db_conn.execute("SELECT * FROM prs WHERE repo_id=?", (repo_id,)).fetchall()
        issue_rows = db_conn.execute("SELECT * FROM issues WHERE repo_id=?", (repo_id,)).fetchall()
        release_rows = db_conn.execute("SELECT * FROM releases WHERE repo_id=?", (repo_id,)).fetchall()

        result["repos"].append(
            {
                "org": repo_row["org"],
                "name": repo_row["name"],
                "default_branch": repo_row["default_branch"],
                "is_fork": bool(repo_row["is_fork"]),
                "is_archived": bool(repo_row["is_archived"]),
                "parent_owner": repo_row["parent_owner"],
                "parent_name": repo_row["parent_name"],
                "parent_is_deleted": bool(repo_row["parent_is_deleted"]),
                "capture_status": repo_row["capture_status"],
                "field_statuses": json.loads(repo_row["field_statuses"] or "{}"),
                "upstream": json.loads(repo_row["upstream"]) if repo_row["upstream"] else None,
                "vulnerability_alerts": json.loads(repo_row["vulnerability_alerts"]) if repo_row["vulnerability_alerts"] else None,
                "prs": [
                    {
                        **dict(r),
                        "is_draft": bool(r["is_draft"]),
                        "is_dependabot": bool(r["is_dependabot"]),
                        "is_renovate": bool(r["is_renovate"]),
                        "stalled": bool(r["stalled"]),
                        "review_events": json.loads(r["review_events"]) if r["review_events"] else None,
                    }
                    for r in pr_rows
                ],
                "issues": [
                    {
                        **dict(r),
                        "labels": json.loads(r["labels"] or "[]"),
                        "stalled": bool(r["stalled"]),
                    }
                    for r in issue_rows
                ],
                "releases": [
                    {**dict(r), "is_prerelease": bool(r["is_prerelease"])}
                    for r in release_rows
                ],
            }
        )

    return result


def run_snapshot(
    cfg: PulseConfig,
    db_conn: sqlite3.Connection,
    gql: GraphQLClient,
    deadline: float | None,
    output_dir: Path | None = None,
    rest_client: GHRestClient | None = None,
) -> str:
    """Run one full snapshot, persist to SQLite, write current/prev JSON. Returns snapshot_id."""
    _instr.init_instruments()

    start_time = time.monotonic()
    now_utc = datetime.now(timezone.utc)
    snapshot_id = now_utc.strftime("%Y%m%dT%H%M%S.%fZ")

    # ET offset: simple approach using environment or fixed offset
    try:
        import zoneinfo
        et_zone = zoneinfo.ZoneInfo("America/New_York")
        now_et = now_utc.astimezone(et_zone)
    except Exception:
        # Fallback: UTC-4 (EDT) approximation
        from datetime import timedelta
        now_et = now_utc.replace(tzinfo=None) - timedelta(hours=4)
    captured_at_et = now_et.strftime("%Y-%m-%d %H:%M ET")

    repos_succeeded = 0
    repos_failed = 0
    repos_partial = 0

    orgs_queried = list(cfg.orgs.keys())
    captured_at_utc_str = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Insert placeholder so repos can FK-reference it immediately
    _insert_snapshot_placeholder(
        db_conn,
        snapshot_id=snapshot_id,
        captured_at_utc=captured_at_utc_str,
        captured_at_et=captured_at_et,
        orgs_queried=orgs_queried,
    )

    # Set up REST client (caller may pass one in for testing; otherwise create from env)
    _rest_client_owner = rest_client is None
    if rest_client is None:
        import os as _os
        _token = _os.environ.get("GH_TOKEN") or _os.environ.get("GITHUB_TOKEN")
        if _token:
            rest_client = GHRestClient(token=_token)
    _rest_client = rest_client

    org_errors: list[str] = []
    orgs_succeeded = 0
    # Cumulative rateLimit.cost tracker across all timeline queries in this run
    cumulative_timeline_cost: list[int] = [0]
    # Accumulate vuln alert counts across all repos for dependabot gauge
    _vuln_sev_counts: dict[str, int] = {}

    tracer = _otel.get_tracer("pulse")
    with tracer.start_as_current_span("pulse.run") as run_span:
        run_span.set_attribute("pulse.snapshot_id", snapshot_id)
        run_span.set_attribute("pulse.version", __version__)

        for org_name, org_config in cfg.orgs.items():
            # Enumerate repos
            try:
                repo_nodes = gql.paginate(
                    REPOS_QUERY,
                    variables={"org": org_name},
                    page_info_path=["data", "organization", "repositories", "pageInfo"],
                    nodes_path=["data", "organization", "repositories", "nodes"],
                    deadline_monotonic=deadline,
                    db_conn=db_conn,
                    org=org_name,
                    repo="__repos__",
                    field="repos",
                )
            except Exception as e:
                msg = f"failed to enumerate repos for {org_name}: {e}"
                click.echo(f"ERROR: {msg}", err=True)
                org_errors.append(msg)
                continue

            orgs_succeeded += 1
            for node in repo_nodes:
                repo_name = node.get("name", "")
                if not repo_name:
                    continue
                if repo_name in (org_config.ignore or []):
                    continue

                with tracer.start_as_current_span("pulse.repo.collect") as repo_span:
                    repo_span.set_attribute("repo.name", f"{org_name}/{repo_name}")

                    # Get stall thresholds
                    stall_override = (org_config.stall_overrides or {}).get(repo_name)
                    stall_pr_hours = (
                        stall_override.pr_hours if stall_override else cfg.defaults.stall_pr_hours
                    )
                    stall_issue_hours = (
                        stall_override.issue_hours if stall_override else cfg.defaults.stall_issue_hours
                    )

                    parent = node.get("parent") or {}
                    parent_owner = (parent.get("owner") or {}).get("login")
                    parent_name = parent.get("name")
                    parent_default_branch = (parent.get("defaultBranchRef") or {}).get("name")

                    has_issues = bool(node.get("hasIssuesEnabled", True))
                    default_branch_ref = node.get("defaultBranchRef") or {}

                    repo = RepoData(
                        org=org_name,
                        name=repo_name,
                        default_branch=default_branch_ref.get("name"),
                        is_fork=bool(node.get("isFork")),
                        is_archived=bool(node.get("isArchived")),
                        has_issues_enabled=has_issues,
                        parent_owner=parent_owner,
                        parent_name=parent_name,
                        parent_is_deleted=False,
                        capture_status="success",
                        field_statuses={},
                        parent_default_branch=parent_default_branch,
                    )

                    # Capture PRs
                    prs, pr_status = _capture_prs(
                        gql, org_name, repo_name,
                        cfg.defaults.max_prs_per_repo, stall_pr_hours,
                        now_utc, deadline,
                    )
                    repo.field_statuses["prs"] = pr_status

                    # Capture issues
                    if has_issues:
                        issues, issue_status = _capture_issues(
                            gql, org_name, repo_name,
                            cfg.defaults.max_issues_per_repo, stall_issue_hours,
                            now_utc, deadline,
                        )
                        repo.field_statuses["issues"] = issue_status
                    else:
                        issues = []
                        repo.field_statuses["issues"] = FieldStatus(status="disabled")

                    # Capture releases
                    releases, release_status = _capture_releases(
                        gql, org_name, repo_name,
                        cfg.defaults.max_releases_per_repo, deadline,
                    )
                    repo.field_statuses["releases"] = release_status

                    # Determine overall repo status
                    field_statuses = list(repo.field_statuses.values())
                    failed_fields = [fs for fs in field_statuses if fs.status == "failed"]
                    partial_fields = [fs for fs in field_statuses if fs.status == "partial"]

                    counted_as = "partial" if (failed_fields or partial_fields) else "success"
                    if counted_as == "partial":
                        repo.capture_status = "partial"
                        repos_partial += 1
                    else:
                        repo.capture_status = "success"
                        repos_succeeded += 1

                    # Persist to SQLite
                    try:
                        repo_id = _persist_repo(db_conn, snapshot_id, repo, prs, issues, releases)
                    except Exception as e:
                        click.echo(f"ERROR: failed to persist {org_name}/{repo_name}: {e}", err=True)
                        repos_failed += 1
                        if counted_as == "partial":
                            repos_partial -= 1
                        else:
                            repos_succeeded -= 1
                        _instr.get_repos_failed().add(1, {"org": org_name, "reason": "failed"})
                        continue

                    # Capture PR timelines (after persist so we have repo_id)
                    if repo_id is not None and prs:
                        _capture_pr_timelines(
                            gql, db_conn, repo_id, prs, repo,
                            deadline, cumulative_timeline_cost,
                        )
                        # Re-evaluate repo status after timeline capture
                        if "review_events" in repo.field_statuses:
                            rev_status = repo.field_statuses["review_events"]
                            if rev_status.status in ("partial", "failed"):
                                if counted_as == "success":
                                    repos_succeeded -= 1
                                    repos_partial += 1
                                    repo.capture_status = "partial"
                                    counted_as = "partial"

                    # Capture fork upstream delta (REST, graceful)
                    if repo_id is not None and _rest_client is not None:
                        _capture_upstream(_rest_client, repo, db_conn, repo_id)
                        up_status = repo.field_statuses.get("upstream")
                        if up_status and up_status.status in ("partial", "failed"):
                            if counted_as == "success":
                                repos_succeeded -= 1
                                repos_partial += 1
                                repo.capture_status = "partial"
                                counted_as = "partial"

                    # Capture vulnerability alerts (GraphQL, graceful)
                    if repo_id is not None:
                        _repo_vuln_alerts = _capture_vuln_alerts(gql, repo, db_conn, repo_id, cumulative_timeline_cost)
                        if _repo_vuln_alerts:
                            for _v in _repo_vuln_alerts:
                                _vuln_sev_counts[_v.severity] = _vuln_sev_counts.get(_v.severity, 0) + 1
                        va_status = repo.field_statuses.get("vulnerability_alerts")
                        if va_status and va_status.status in ("partial", "failed", "scope_missing"):
                            if counted_as == "success":
                                repos_succeeded -= 1
                                repos_partial += 1
                                repo.capture_status = "partial"
                                counted_as = "partial"

                    # Record field-level capture errors after ALL captures complete
                    # (timelines, upstream, vuln alerts may have added new failed/partial statuses)
                    for field_name, fstatus in repo.field_statuses.items():
                        if fstatus.status in ("failed", "partial"):
                            _instr.get_capture_errors().add(1, {"field_name": field_name})

                    # Emit counters after final status is determined (all captures complete)
                    if counted_as == "partial":
                        _instr.get_repos_failed().add(1, {"org": org_name, "reason": "partial"})
                    else:
                        _instr.get_repos_succeeded().add(1, {"org": org_name})

                    # Update field_statuses in the DB row after all captures
                    if repo_id is not None:
                        try:
                            field_statuses_json = json.dumps(
                                {k: {"status": v.status, "error_note": v.error_note}
                                 for k, v in repo.field_statuses.items()}
                            )
                            with db_conn:
                                db_conn.execute(
                                    "UPDATE repos SET field_statuses=?, capture_status=? WHERE id=?",
                                    (field_statuses_json, repo.capture_status, repo_id),
                                )
                        except Exception as e:
                            click.echo(f"WARNING: failed to update field_statuses for {org_name}/{repo_name}: {e}", err=True)

        # Update dependabot gauge with cumulative counts across all repos
        # Always call — empty dict clears stale gauge values from prior run
        _instr.set_dependabot_alerts(_vuln_sev_counts)

        # Record run-level metrics after all orgs processed
        elapsed = time.monotonic() - start_time
        _instr.get_run_duration().record(elapsed, {})

    duration_ms = int((time.monotonic() - start_time) * 1000)

    if orgs_succeeded == 0 and repos_succeeded == 0 and repos_partial == 0:
        snapshot_capture_status = "failed"
    elif repos_partial > 0 or repos_failed > 0 or org_errors:
        snapshot_capture_status = "partial"
    else:
        snapshot_capture_status = "success"
    _finalize_snapshot(
        db_conn,
        snapshot_id=snapshot_id,
        duration_ms=duration_ms,
        repos_succeeded=repos_succeeded,
        repos_failed=repos_failed,
        repos_partial=repos_partial,
        capture_status=snapshot_capture_status,
    )

    # Compute and cache 7-day reviewer activity rollup
    # Local import to avoid circular dependency (rollup imports DEPENDABOT_AUTHORS from this module)
    try:
        from pulse.rollup import compute_reviewer_activity_7d
        rollup = compute_reviewer_activity_7d(db_conn)
        with db_conn:
            db_conn.execute(
                "UPDATE snapshots SET reviewer_activity_7d=? WHERE id=?",
                (json.dumps(rollup), snapshot_id),
            )
    except Exception as e:
        logging.getLogger(__name__).warning(
            "reviewer_activity_7d rollup failed for snapshot %s: %s", snapshot_id, e
        )

    # Write JSON artifacts
    if output_dir is not None:
        current_data = _build_current_json(db_conn, snapshot_id)
        atomic_write_json(output_dir / "current.json", current_data)

        # prev.json = second-latest snapshot
        prev_row = db_conn.execute(
            "SELECT id FROM snapshots WHERE id != ? AND capture_status != 'in_progress'"
            " ORDER BY id DESC LIMIT 1",
            (snapshot_id,),
        ).fetchone()
        if prev_row is not None:
            prev_data = _build_current_json(db_conn, prev_row["id"])
            atomic_write_json(output_dir / "prev.json", prev_data)

    # Prune old snapshots
    _prune_old_snapshots(db_conn, cfg.defaults.history_days)

    # Close REST client if we created it
    if _rest_client_owner and _rest_client is not None:
        try:
            _rest_client.close()
        except Exception:
            pass

    return snapshot_id
