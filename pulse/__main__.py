from __future__ import annotations

import atexit
import dataclasses
import json
import logging
import os
import signal
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import click
import yaml
from opentelemetry import trace

from pulse import __version__
from pulse.ipv4 import apply_ipv4_patch


class _OtelTraceFilter(logging.Filter):
    """Inject OTEL trace_id and span_id into every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = trace.get_current_span().get_span_context()
        if ctx.is_valid:
            record.trace_id = format(ctx.trace_id, "032x")
            record.span_id = format(ctx.span_id, "016x")
        else:
            record.trace_id = ""
            record.span_id = ""
        return True

_DEFAULT_CONFIG_PATH = Path.home() / ".world" / "pulse" / "config.yml"
_DEFAULT_DB_PATH = Path.home() / ".world" / "pulse" / "pulse.db"

SCOPE_CHECK_QUERY = """
query {
  rateLimit { remaining }
  viewer { login }
}
"""


@click.group(invoke_without_command=True)
@click.option("--config-check", is_flag=True, default=False, help="Validate config and print effective config as YAML.")
@click.option("--self-check", is_flag=True, default=False, help="Run config + storage health checks.")
@click.option("--now", "run_now", is_flag=True, default=False, help="Run one snapshot and render digest.")
@click.option("--digest", "print_digest", is_flag=True, default=False, help="Print current digest to stdout.")
@click.option("--dry-run", "dry_run", is_flag=True, default=False, help="Snapshot a single repo to /tmp without touching production paths.")
@click.option("--service", "service", is_flag=True, default=False, help="Reserved for systemd ExecStart — runs without PulseLock (external flock handles concurrency).")
@click.option("--repo", "repo_override", default=None, help="OWNER/NAME override for --dry-run.")
@click.version_option(__version__, prog_name="pulse")
@click.pass_context
def main(
    ctx: click.Context,
    config_check: bool,
    self_check: bool,
    run_now: bool,
    print_digest: bool,
    dry_run: bool,
    service: bool,
    repo_override: str | None,
) -> None:
    """pulse — org health monitor."""
    apply_ipv4_patch()

    active_flags = sum([config_check, self_check, run_now, print_digest, dry_run, service])
    if active_flags > 1:
        click.echo("ERROR: --config-check, --self-check, --now, --digest, --dry-run, and --service are mutually exclusive", err=True)
        sys.exit(1)

    if repo_override and not dry_run:
        click.echo("ERROR: --repo requires --dry-run", err=True)
        sys.exit(1)

    from pulse import otel as _otel

    _otel.setup(service_name="pulse")

    # Ensure root logger has at least one handler before attaching the filter.
    # In the default CLI path root.handlers==[] until basicConfig is called,
    # so attaching to handlers only would silently no-op.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s trace=%(trace_id)s span=%(span_id)s %(message)s",
    )
    # Add filter to root logger so it runs for ALL records that propagate,
    # mutating the record before any handler formats it.
    _trace_filter = _OtelTraceFilter()
    _root_logger = logging.getLogger()
    _root_logger.addFilter(_trace_filter)
    # Belt-and-suspenders: also add to existing handlers
    for _h in _root_logger.handlers:
        _h.addFilter(_trace_filter)

    def _shutdown_handler(signum: int, frame: object) -> None:
        _otel.shutdown(timeout_ms=2000)
        sys.exit(0 if signum == signal.SIGTERM else 128 + signum)

    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)
    atexit.register(_otel.shutdown)

    if config_check:
        _run_config_check()
    elif self_check:
        _run_self_check()
    elif run_now:
        _run_now()
    elif print_digest:
        _run_digest()
    elif dry_run:
        _run_dry_run(repo_override)
    elif service:
        _run_now_no_lock()  # service path — external flock handles concurrency
    elif ctx.invoked_subcommand is None:
        click.echo(
            "ERROR: use `pulse --now` for manual runs or `pulse --service` is reserved for systemd ExecStart",
            err=True,
        )
        sys.exit(1)


def _run_config_check() -> None:
    from pulse.config import ConfigError, load_config

    config_path = _DEFAULT_CONFIG_PATH
    try:
        cfg = load_config(config_path)
    except ConfigError as e:
        click.echo(f"ERROR: {e}", err=True)
        sys.exit(1)

    click.echo(yaml.dump(cfg.model_dump(), sort_keys=False, allow_unicode=True), nl=False)
    sys.exit(0)


def _run_self_check() -> None:
    from pulse.config import ConfigError, load_config
    from pulse.storage import DBCorrupt, open_db

    errors: list[str] = []

    # 1. Config validation
    config_path = _DEFAULT_CONFIG_PATH
    cfg = None
    try:
        cfg = load_config(config_path)
        click.echo(f"[OK] config: {config_path}")
    except ConfigError as e:
        errors.append(f"config: {e}")
        click.echo(f"[FAIL] config: {e}", err=True)

    # 2. ~/.world/pulse/ dir writeable
    pulse_dir = Path.home() / ".world" / "pulse"
    write_test = pulse_dir / f".write_test.{os.getpid()}"
    try:
        pulse_dir.mkdir(parents=True, exist_ok=True)
        write_test.write_text("ok")
        click.echo(f"[OK] dir writable: {pulse_dir}")
    except Exception as e:
        errors.append(f"dir writeable: {e}")
        click.echo(f"[FAIL] dir writeable: {e}", err=True)
    finally:
        try:
            write_test.unlink()
        except FileNotFoundError:
            pass

    # 3. SQLite open + integrity_check
    db_path = _DEFAULT_DB_PATH
    try:
        conn = open_db(db_path)
        conn.close()
        click.echo(f"[OK] sqlite: {db_path}")
    except DBCorrupt as e:
        errors.append(f"sqlite corrupt: {e}")
        click.echo(f"[FAIL] sqlite corrupt: {e}", err=True)
    except Exception as e:
        errors.append(f"sqlite: {e}")
        click.echo(f"[FAIL] sqlite: {e}", err=True)

    # 4. Permission checks
    pulse_dir = Path.home() / ".world" / "pulse"
    if pulse_dir.exists():
        try:
            st = pulse_dir.stat()
            mode = stat.S_IMODE(st.st_mode)
            if mode == 0o700:
                click.echo(f"[OK] perms: {pulse_dir} is 0700")
            else:
                errors.append(f"perms: {pulse_dir} is {oct(mode)}, expected 0700")
                click.echo(f"[FAIL] perms: {pulse_dir} is {oct(mode)}, expected 0700", err=True)
        except Exception as e:
            errors.append(f"perms: cannot stat {pulse_dir}: {e}")
            click.echo(f"[FAIL] perms: cannot stat {pulse_dir}: {e}", err=True)

    env_path = pulse_dir / "env"
    if env_path.exists():
        try:
            st = env_path.stat()
            mode = stat.S_IMODE(st.st_mode)
            if mode == 0o600:
                click.echo(f"[OK] perms: {env_path} is 0600")
            else:
                errors.append(f"perms: {env_path} is {oct(mode)}, expected 0600")
                click.echo(f"[FAIL] perms: {env_path} is {oct(mode)}, expected 0600", err=True)
        except Exception as e:
            errors.append(f"perms: cannot stat {env_path}: {e}")
            click.echo(f"[FAIL] perms: cannot stat {env_path}: {e}", err=True)

    db_path_check = pulse_dir / "pulse.db"
    if db_path_check.exists():
        try:
            st = db_path_check.stat()
            mode = stat.S_IMODE(st.st_mode)
            if mode == 0o600:
                click.echo(f"[OK] perms: {db_path_check} is 0600")
            else:
                errors.append(f"perms: {db_path_check} is {oct(mode)}, expected 0600")
                click.echo(f"[FAIL] perms: {db_path_check} is {oct(mode)}, expected 0600", err=True)
        except Exception as e:
            errors.append(f"perms: cannot stat {db_path_check}: {e}")
            click.echo(f"[FAIL] perms: cannot stat {db_path_check}: {e}", err=True)

    # 5. Token scope verification
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        errors.append("token: GH_TOKEN not set")
        click.echo("[FAIL] token: GH_TOKEN not set", err=True)
    else:
        try:
            import httpx

            if cfg is None:
                click.echo("[WARN] token: skipping token check — config failed to load (fix config first)", err=True)
                # Don't append to errors — config error already recorded above
            else:
                _api_base = cfg.defaults.github_api_base
                _graphql_url = f"{_api_base.rstrip('/')}/graphql"

                resp = httpx.post(
                    _graphql_url,
                    json={"query": SCOPE_CHECK_QUERY},
                    headers={
                        "Authorization": f"bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    timeout=15.0,
                )
                if resp.status_code == 200:
                    scopes_header = resp.headers.get("x-oauth-scopes", "")
                    scopes = {s.strip() for s in scopes_header.split(",") if s.strip()}
                    required = {"read:org", "repo"}
                    missing = required - scopes
                    if not missing:
                        click.echo(f"[OK] token: scopes present ({', '.join(sorted(scopes))})")
                    elif not scopes_header:
                        # GitHub Apps / fine-grained tokens don't return x-oauth-scopes — probe with real queries
                        if not cfg.orgs:
                            click.echo("[WARN] token: fine-grained token — cannot verify scopes without configured orgs in config.yml", err=True)
                        else:
                            from pulse.snapshot import REPOS_QUERY
                            first_org = next(iter(cfg.orgs))
                            probe_resp = httpx.post(
                                _graphql_url,
                                json={"query": REPOS_QUERY, "variables": {"org": first_org, "cursor": None}},
                                headers={
                                    "Authorization": f"bearer {token}",
                                    "Accept": "application/vnd.github+json",
                                    "X-GitHub-Api-Version": "2022-11-28",
                                },
                                timeout=15.0,
                            )
                            if probe_resp.status_code == 200:
                                probe_body = probe_resp.json()
                                probe_errors = probe_body.get("errors") or []
                                scope_error = next(
                                    (
                                        e for e in probe_errors
                                        if "INSUFFICIENT_SCOPES" in str(e.get("type", "")).upper()
                                        or "Resource not accessible by integration" in str(e.get("message", ""))
                                    ),
                                    None,
                                )
                                if scope_error:
                                    err_msg = scope_error.get("message", str(scope_error))
                                    errors.append(f"token: fine-grained token lacks required scopes (read:org or repo): {err_msg}")
                                    click.echo(
                                        f"[FAIL] token: fine-grained token lacks required scopes (read:org or repo): {err_msg}",
                                        err=True,
                                    )
                                else:
                                    # Probe succeeds if data.organization.repositories.nodes is present (can be empty)
                                    probe_data = probe_body.get("data") or {}
                                    probe_org = probe_data.get("organization") or None
                                    if probe_org is None:
                                        errors.append("token: scope probe — org not accessible (null organization; token may lack read:org scope or org not found)")
                                        click.echo("[FAIL] token: scope probe — org not accessible (null organization; token may lack read:org scope or org not found)", err=True)
                                    else:
                                        repo_nodes = probe_org.get("repositories", {}).get("nodes")
                                        if repo_nodes is not None:
                                            click.echo(f"[OK] token: fine-grained token — scope probe passed for '{first_org}' (first configured org)")
                                        else:
                                            err_msg = "scope probe returned no data.organization.repositories.nodes"
                                            errors.append(f"token: fine-grained token scope probe failed: {err_msg}")
                                            click.echo(f"[FAIL] token: fine-grained token scope probe failed: {err_msg}", err=True)
                            else:
                                err_msg = f"scope probe returned HTTP {probe_resp.status_code}"
                                errors.append(f"token: fine-grained token scope probe failed: {err_msg}")
                                click.echo(f"[FAIL] token: fine-grained token scope probe failed: {err_msg}", err=True)
                    else:
                        errors.append(f"token: missing required scopes: {', '.join(sorted(missing))}")
                        click.echo(f"[FAIL] token: missing required scopes: {', '.join(sorted(missing))} (have: {scopes_header})", err=True)
                else:
                    errors.append(f"token: GraphQL health check returned {resp.status_code}")
                    click.echo(f"[FAIL] token: GraphQL health check returned {resp.status_code}", err=True)
        except Exception as e:
            errors.append(f"token: scope check failed: {e}")
            click.echo(f"[FAIL] token: scope check failed: {e}", err=True)

    if errors:
        sys.exit(1)
    sys.exit(0)


def _do_snapshot_and_digest() -> str:
    """Core snapshot + digest logic. Caller is responsible for concurrency guard."""
    from pulse.config import ConfigError, load_config
    from pulse.digest import atomic_write_text, render_digest
    from pulse.graphql import GraphQLClient, make_deadline
    from pulse.snapshot import run_snapshot
    from pulse.storage import open_db

    config_path = _DEFAULT_CONFIG_PATH
    db_path = _DEFAULT_DB_PATH
    output_dir = db_path.parent / "snapshots"

    try:
        cfg = load_config(config_path)
    except ConfigError as e:
        click.echo(f"ERROR: config: {e}", err=True)
        sys.exit(1)

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        click.echo("ERROR: GH_TOKEN environment variable not set", err=True)
        sys.exit(1)

    db_conn = open_db(db_path)
    try:
        with GraphQLClient(token=token, base_url=cfg.defaults.github_api_base) as gql:
            deadline = make_deadline()
            run_snapshot(
                cfg=cfg,
                db_conn=db_conn,
                gql=gql,
                deadline=deadline,
                output_dir=output_dir,
            )
        digest_text = render_digest(db_conn, cfg)
    finally:
        db_conn.close()

    digest_path = output_dir / "digest-latest.md"
    atomic_write_text(digest_path, digest_text)
    return str(digest_path)


def _run_now() -> None:
    """Manual invocation path — acquires PulseLock before running."""
    from pulse.locks import LockHeld, PulseLock

    try:
        with PulseLock():
            digest_path = _do_snapshot_and_digest()
        click.echo(digest_path)
    except LockHeld:
        click.echo("pulse already running", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"ERROR: {e}", err=True)
        sys.exit(1)


def _run_now_no_lock() -> None:
    """Invoked via `pulse --service` (systemd ExecStart path) — external flock handles concurrency, no PulseLock."""
    try:
        digest_path = _do_snapshot_and_digest()
        click.echo(digest_path)
    except Exception as e:
        click.echo(f"ERROR: {e}", err=True)
        sys.exit(1)


def _run_digest() -> None:
    from pulse.config import ConfigError, load_config
    from pulse.digest import render_digest
    from pulse.storage import open_db

    config_path = _DEFAULT_CONFIG_PATH
    db_path = _DEFAULT_DB_PATH

    try:
        cfg = load_config(config_path)
    except ConfigError as e:
        click.echo(f"ERROR: config: {e}", err=True)
        sys.exit(1)

    try:
        db_conn = open_db(db_path)
        try:
            digest_text = render_digest(db_conn, cfg)
        finally:
            db_conn.close()
    except Exception as e:
        click.echo(f"ERROR: {e}", err=True)
        sys.exit(1)

    click.echo(digest_text, nl=False)


def _run_dry_run(repo_override: str | None = None) -> None:
    """Dry-run: snapshot a single repo to /tmp. Touches no production paths."""
    from pulse.config import ConfigError, load_config
    from pulse.graphql import GraphQLClient, make_deadline

    config_path = _DEFAULT_CONFIG_PATH

    try:
        cfg = load_config(config_path)
    except ConfigError as e:
        click.echo(f"ERROR: config: {e}", err=True)
        sys.exit(1)

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        click.echo("ERROR: GH_TOKEN environment variable not set", err=True)
        sys.exit(1)

    # Determine target org/repo
    if repo_override:
        parts = repo_override.split("/", 1)
        if len(parts) != 2:
            click.echo(f"ERROR: --repo must be OWNER/NAME, got: {repo_override}", err=True)
            sys.exit(1)
        target_org, target_repo = parts
        if target_org not in cfg.orgs:
            click.echo(f"ERROR: org '{target_org}' not in config (known: {', '.join(cfg.orgs)})", err=True)
            sys.exit(1)
    else:
        if not cfg.orgs:
            click.echo("ERROR: no orgs in config", err=True)
            sys.exit(1)
        target_org = next(iter(cfg.orgs))
        target_repo = None  # will use first repo from API

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    try:
        with GraphQLClient(token=token, base_url=cfg.defaults.github_api_base) as gql:
            deadline = make_deadline()
            # Import snapshot queries directly
            from pulse.snapshot import (
                REPOS_QUERY,
                _capture_prs,
                _capture_issues,
                _capture_releases,
            )

            now = datetime.now(timezone.utc)

            # Find target repo
            if target_repo is None:
                body = gql.execute(
                    REPOS_QUERY,
                    {"org": target_org, "cursor": None},
                    deadline_monotonic=deadline,
                )
                repos_data = (body.get("data") or {}).get("organization", {}).get("repositories", {})
                nodes = repos_data.get("nodes") or []
                if not nodes:
                    click.echo(f"ERROR: no repositories found in org '{target_org}'", err=True, )
                    sys.exit(1)
                target_repo = nodes[0]["name"]
                click.echo(f"dry-run: using first repo: {target_org}/{target_repo}", err=True)

            org_cfg = cfg.orgs[target_org]
            defaults = cfg.defaults

            prs, pr_status = _capture_prs(
                gql=gql,
                org=target_org,
                repo_name=target_repo,
                max_prs=defaults.max_prs_per_repo,
                stall_hours=defaults.stall_pr_hours,
                now=now,
                deadline=deadline,
            )
            issues, issue_status = _capture_issues(
                gql=gql,
                org=target_org,
                repo_name=target_repo,
                max_issues=defaults.max_issues_per_repo,
                stall_hours=defaults.stall_issue_hours,
                now=now,
                deadline=deadline,
            )
            releases, release_status = _capture_releases(
                gql=gql,
                org=target_org,
                repo_name=target_repo,
                max_releases=defaults.max_releases_per_repo,
                deadline=deadline,
            )

    except Exception as e:
        click.echo(f"ERROR: {e}", err=True)
        sys.exit(1)

    result = {
        "dry_run": True,
        "timestamp": ts,
        "org": target_org,
        "repo": target_repo,
        "pr_count": len(prs),
        "issue_count": len(issues),
        "release_count": len(releases),
        "pr_status": dataclasses.asdict(pr_status),
        "issue_status": dataclasses.asdict(issue_status),
        "release_status": dataclasses.asdict(release_status),
        "prs": [dataclasses.asdict(p) for p in prs],
        "issues": [dataclasses.asdict(i) for i in issues],
        "releases": [dataclasses.asdict(r) for r in releases],
    }

    try:
        fd, tmp_str = tempfile.mkstemp(dir="/tmp", prefix=f"pulse-dry-run-{ts}-", suffix=".json")
        out_path = Path(tmp_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(result, indent=2, default=str))
        except Exception:
            out_path.unlink(missing_ok=True)
            raise
    except Exception as e:
        click.echo(f"ERROR: could not write dry-run output to /tmp: {e}", err=True)
        sys.exit(1)

    stalled_prs = sum(1 for p in prs if p.stalled)
    stalled_issues = sum(1 for i in issues if i.stalled)
    click.echo(
        f"dry-run: {target_org}/{target_repo} — "
        f"PRs: {len(prs)} ({stalled_prs} stalled), "
        f"issues: {len(issues)} ({stalled_issues} stalled), "
        f"releases: {len(releases)}",
        err=True,
    )
    click.echo(f"dry-run: pr_status={pr_status.status}, issue_status={issue_status.status}, release_status={release_status.status}", err=True)

    # stdout: output file path so scripts can capture it
    click.echo(str(out_path))


@main.command("migrate")
def cmd_migrate() -> None:
    """Migrate pulse.db from v0 to v1 (idempotent)."""
    import sqlite3 as _sqlite3

    from pulse.locks import LockHeld, PulseLock
    from pulse.migrate import run_migration

    db_path = _DEFAULT_DB_PATH
    try:
        with PulseLock():
            msg = run_migration(db_path)
            if msg == "no-op":
                click.echo("pulse.db already at v1 — no migration needed.")
            else:
                click.echo(f"Migration complete. Backup preserved at {db_path.parent}/pulse.db.pre-v1-*")
    except LockHeld as e:
        click.echo(f"Migration blocked: pulse is currently running. Stop the service first.\n  {e}", err=True)
        raise SystemExit(1)
    except RuntimeError as e:
        click.echo(f"Migration failed: {e}", err=True)
        raise SystemExit(1)
    except (_sqlite3.Error, OSError) as e:
        click.echo(f"Migration failed ({type(e).__name__}): {e}", err=True)
        click.echo(f"Check backup at: {db_path.parent}/pulse.db.pre-v1-* before retrying", err=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
