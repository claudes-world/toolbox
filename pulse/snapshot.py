from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from pulse.config import PulseConfig
from pulse.graphql import GraphQLClient
from pulse.schema import FieldStatus, IssueData, PRData, ReleaseData, RepoData
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
        parent { owner { login } name }
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
        # Single execute — max_prs <= 100 so fits in one page
        body = gql.execute(
            PRS_QUERY,
            {"org": org, "repo": repo_name, "first": max_prs, "cursor": None},
            deadline_monotonic=deadline,
        )
        repo_data = (body.get("data") or {}).get("repository") or {}
        pr_conn = repo_data.get("pullRequests") or {}
        total_count = pr_conn.get("totalCount", 0)
        nodes = pr_conn.get("nodes") or []

        field_status = FieldStatus(status="success")
        if total_count > max_prs:
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
        # Single execute — max_issues <= 100 so fits in one page
        body = gql.execute(
            ISSUES_QUERY,
            {"org": org, "repo": repo_name, "first": max_issues, "cursor": None},
            deadline_monotonic=deadline,
        )
        repo_data = (body.get("data") or {}).get("repository") or {}
        issue_conn = repo_data.get("issues") or {}
        total_count = issue_conn.get("totalCount", 0)
        nodes = issue_conn.get("nodes") or []

        field_status = FieldStatus(status="success")
        if total_count > max_issues:
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
        body = gql.execute(
            RELEASES_QUERY,
            variables={"org": org, "repo": repo_name, "first": max_releases},
            deadline_monotonic=deadline,
        )
        nodes = (
            (body.get("data") or {})
            .get("repository", {})
            .get("releases", {})
            .get("nodes") or []
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
        return releases, FieldStatus(status="success")
    except Exception as e:
        return [], FieldStatus(status="failed", error_note=str(e)[:200])



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
) -> None:
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
                   is_draft, is_dependabot, is_renovate, hours_idle, stalled)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                "prs": [
                    {
                        **dict(r),
                        "is_draft": bool(r["is_draft"]),
                        "is_dependabot": bool(r["is_dependabot"]),
                        "is_renovate": bool(r["is_renovate"]),
                        "stalled": bool(r["stalled"]),
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
) -> str:
    """Run one full snapshot, persist to SQLite, write current/prev JSON. Returns snapshot_id."""
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

    org_errors: list[str] = []
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
            print(f"ERROR: {msg}", file=sys.stderr)
            org_errors.append(msg)
            continue

        for node in repo_nodes:
            repo_name = node.get("name", "")
            if not repo_name:
                continue
            if repo_name in (org_config.ignore or []):
                continue

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
                _persist_repo(db_conn, snapshot_id, repo, prs, issues, releases)
            except Exception as e:
                print(f"ERROR: failed to persist {org_name}/{repo_name}: {e}", file=sys.stderr)
                repos_failed += 1
                if counted_as == "partial":
                    repos_partial -= 1
                else:
                    repos_succeeded -= 1

    duration_ms = int((time.monotonic() - start_time) * 1000)

    if repos_succeeded == 0 and repos_partial == 0 and (repos_failed > 0 or org_errors):
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

    return snapshot_id
