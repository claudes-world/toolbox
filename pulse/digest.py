from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pulse.config import PulseConfig


def md_escape(s: object, max_len: int = 120) -> str:
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    # COPY VERBATIM:
    escaped = (s.replace("\\", "\\\\")
                .replace("`", "\\`")
                .replace("*", "\\*")
                .replace("_", "\\_")
                .replace("[", "\\[")
                .replace("]", "\\]")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\n", " "))
    if len(escaped) > max_len:
        escaped = escaped[:max_len].rstrip() + "…"
    return escaped


def atomic_write_text(path: Path, text: str) -> None:
    """Write text to path atomically (crash-safe). Sets permissions to 0o600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    fd_open = True
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fd_open = False
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        if fd_open:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise


def _latest_snapshot(db_conn: sqlite3.Connection) -> sqlite3.Row | None:
    return db_conn.execute(
        "SELECT * FROM snapshots WHERE capture_status != 'in_progress'"
        " ORDER BY id DESC LIMIT 1"
    ).fetchone()


def render_digest(db_conn: sqlite3.Connection, cfg: PulseConfig) -> str:
    snap = _latest_snapshot(db_conn)
    if snap is None:
        return "# Org Pulse\n\nNo snapshots available.\n"

    snapshot_id = snap["id"]
    captured_at_et = snap["captured_at_et"] or snap["captured_at_utc"]
    duration_ms = snap["duration_ms"] or 0
    repos_succeeded = snap["repos_succeeded"] or 0
    repos_failed = snap["repos_failed"] or 0

    repo_rows = db_conn.execute(
        "SELECT * FROM repos WHERE snapshot_id=?", (snapshot_id,)
    ).fetchall()

    lines: list[str] = []
    lines.append(f"# Org Pulse — {captured_at_et}")
    lines.append("")

    # ---- Alerts section ----
    alert_lines: list[str] = []

    if snap["capture_status"] == "failed":
        alert_lines.append(
            "- ❌ **Snapshot failed**: could not enumerate any orgs"
            " — check `journalctl --user -u pulse.service`"
        )
    elif snap["capture_status"] == "partial":
        alert_lines.append(
            "- ⚠️ **Snapshot partial**: some repos or fields may be incomplete"
            " — check `journalctl --user -u pulse.service`"
        )

    if repos_failed > 0:
        alert_lines.append(f"- ❌ **snapshot**: {repos_failed} repo(s) failed to capture")

    for repo_row in repo_rows:
        org_escaped = md_escape(repo_row["org"])
        name_escaped_label = md_escape(repo_row["name"])
        repo_label = f"{org_escaped}/{name_escaped_label}"
        field_statuses: dict = json.loads(repo_row["field_statuses"] or "{}")

        for field_name, fs_data in field_statuses.items():
            status = fs_data.get("status", "success")
            error_note = fs_data.get("error_note") or ""
            error_note_escaped = md_escape(str(error_note))
            if status == "scope_missing":
                alert_lines.append(
                    f"- 🔴 **SCOPE MISSING — {repo_label}**: {field_name} — token lacks required permissions. "
                    f"Run `pulse --self-check` and ensure GH\\_TOKEN has `read:security\\_events` scope."
                )
            elif status == "failed":
                alert_lines.append(f"- ❌ **{repo_label}**: {field_name} failed — {error_note_escaped}")
            elif status == "disabled":
                alert_lines.append(f"- ⚠️ **{repo_label}**: {field_name} field disabled")
            elif status == "partial":
                alert_lines.append(f"- ℹ️ **{repo_label}**: {field_name} {error_note_escaped}")

    lines.append("## ⚠️ Alerts")
    lines.append("")
    if alert_lines:
        lines.extend(alert_lines)
    else:
        lines.append("No alerts — all repos captured successfully.")
    lines.append("")

    # ---- Open PRs ----
    all_prs: list[tuple[str, str, sqlite3.Row]] = []  # (repo_label, org/name, row)
    for repo_row in repo_rows:
        repo_label = f"{md_escape(repo_row['org'])}/{md_escape(repo_row['name'])}"
        pr_rows = db_conn.execute(
            "SELECT * FROM prs WHERE repo_id=?", (repo_row["id"],)
        ).fetchall()
        for pr in pr_rows:
            all_prs.append((repo_label, repo_row["name"], pr))

    # Stalled first
    stalled_prs = [(rl, rn, pr) for rl, rn, pr in all_prs if pr["stalled"]]
    normal_prs = [(rl, rn, pr) for rl, rn, pr in all_prs if not pr["stalled"]]
    ordered_prs = stalled_prs + normal_prs

    pr_repo_count = len({rl for rl, _rn, _ in ordered_prs})
    lines.append(f"## Open PRs  ({len(ordered_prs)} across {pr_repo_count} repos)")
    lines.append("")
    for repo_label, _rn, pr in ordered_prs:
        idle_h = pr["hours_idle"]
        idle_str = f"{idle_h:.1f}h idle" if idle_h is not None else "idle unknown"
        stall_tag = " [STALLED]" if pr["stalled"] else ""
        title_escaped = md_escape(pr["title"] or "")
        author = md_escape(pr["author"] or "unknown")
        lines.append(
            f"- [{repo_label}#{pr['number']}] {title_escaped}{stall_tag} — {author} ({idle_str})"
        )
    if not ordered_prs:
        lines.append("_No open PRs._")
    lines.append("")

    # ---- Open Issues ----
    all_issues: list[tuple[str, str, sqlite3.Row]] = []
    for repo_row in repo_rows:
        repo_label = f"{md_escape(repo_row['org'])}/{md_escape(repo_row['name'])}"
        issue_rows = db_conn.execute(
            "SELECT * FROM issues WHERE repo_id=?", (repo_row["id"],)
        ).fetchall()
        for issue in issue_rows:
            all_issues.append((repo_label, repo_row["name"], issue))

    stalled_issues = [(rl, rn, i) for rl, rn, i in all_issues if i["stalled"]]
    normal_issues = [(rl, rn, i) for rl, rn, i in all_issues if not i["stalled"]]
    ordered_issues = stalled_issues + normal_issues

    issue_repo_count = len({rl for rl, _rn, _ in ordered_issues})
    lines.append(f"## Open Issues  ({len(ordered_issues)} across {issue_repo_count} repos)")
    lines.append("")
    for repo_label, _rn, issue in ordered_issues:
        idle_h = issue["hours_idle"]
        idle_str = f"{idle_h:.1f}h idle" if idle_h is not None else "idle unknown"
        title_escaped = md_escape(issue["title"] or "")
        labels_raw = issue["labels"] or "[]"
        try:
            label_list = json.loads(labels_raw)
        except Exception:
            label_list = []
        labels_str = ", ".join(md_escape(l) for l in label_list) if label_list else "none"
        lines.append(
            f"- [{repo_label}#{issue['number']}] {title_escaped} — labels: {labels_str} ({idle_str})"
        )
    if not ordered_issues:
        lines.append("_No open issues._")
    lines.append("")

    # ---- Recent Releases ----
    all_releases: list[tuple[str, sqlite3.Row]] = []
    for repo_row in repo_rows:
        repo_label = f"{md_escape(repo_row['org'])}/{md_escape(repo_row['name'])}"
        release_rows = db_conn.execute(
            "SELECT * FROM releases WHERE repo_id=?", (repo_row["id"],)
        ).fetchall()
        for rel in release_rows:
            all_releases.append((repo_label, rel))

    lines.append(f"## Recent Releases  ({len(all_releases)})")
    lines.append("")
    for repo_label, rel in all_releases:
        name_escaped = md_escape(rel["name"] or "")
        created = rel["created_at"] or "unknown"
        lines.append(f"- {repo_label} {md_escape(rel['tag_name'])}: {name_escaped} — {created}")
    if not all_releases:
        lines.append("_No recent releases._")
    lines.append("")

    # ---- Dependabot ----
    # Derived from prs table (is_dependabot=True) — vulnerabilityAlerts is v1 scope
    repos_with_dependabot_prs: list[tuple[str, list[sqlite3.Row]]] = []
    total_dependabot_prs = 0
    for repo_row in repo_rows:
        repo_label = f"{md_escape(repo_row['org'])}/{md_escape(repo_row['name'])}"
        dep_pr_rows = db_conn.execute(
            "SELECT * FROM prs WHERE repo_id=? AND is_dependabot=1", (repo_row["id"],)
        ).fetchall()
        if dep_pr_rows:
            repos_with_dependabot_prs.append((repo_label, list(dep_pr_rows)))
            total_dependabot_prs += len(dep_pr_rows)

    lines.append(f"## Dependabot  ({total_dependabot_prs} open PRs across {len(repos_with_dependabot_prs)} repos)")
    lines.append("")
    for repo_label, dep_prs in repos_with_dependabot_prs:
        updated_ats = [p["updated_at"] for p in dep_prs if p["updated_at"] is not None]
        oldest_updated = min(updated_ats) if updated_ats else None
        oldest_str = oldest_updated or "unknown"
        lines.append(f"- {repo_label}: {len(dep_prs)} open Dependabot PRs (oldest updated: {oldest_str})")
    if not repos_with_dependabot_prs:
        lines.append("_No open Dependabot PRs._")
    lines.append("")

    # ---- Footer ----
    # Rate limit remaining: pull from latest snapshot via a stored value if available,
    # otherwise omit. We don't persist rate_limit_remaining in the schema, so skip for now.
    lines.append("---")
    lines.append(
        f"_Captured {captured_at_et} · {duration_ms}ms · {repos_succeeded} repos_"
    )
    lines.append("")

    return "\n".join(lines)
