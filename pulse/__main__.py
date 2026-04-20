from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import click
import yaml

from pulse import __version__
from pulse.ipv4 import apply_ipv4_patch

_DEFAULT_CONFIG_PATH = Path.home() / ".world" / "pulse" / "config.yml"
_DEFAULT_DB_PATH = Path.home() / ".world" / "pulse" / "pulse.db"


@click.group(invoke_without_command=True)
@click.option("--config-check", is_flag=True, default=False, help="Validate config and print effective config as YAML.")
@click.option("--self-check", is_flag=True, default=False, help="Run config + storage health checks.")
@click.option("--now", "run_now", is_flag=True, default=False, help="Run one snapshot and render digest.")
@click.option("--digest", "print_digest", is_flag=True, default=False, help="Print current digest to stdout.")
@click.version_option(__version__, prog_name="pulse")
@click.pass_context
def main(ctx: click.Context, config_check: bool, self_check: bool, run_now: bool, print_digest: bool) -> None:
    """pulse — org health monitor."""
    apply_ipv4_patch()

    active_flags = sum([config_check, self_check, run_now, print_digest])
    if active_flags > 1:
        click.echo("ERROR: --config-check, --self-check, --now, and --digest are mutually exclusive", err=True)
        sys.exit(1)

    if config_check:
        _run_config_check()
    elif self_check:
        _run_self_check()
    elif run_now:
        _run_now()
    elif print_digest:
        _run_digest()
    elif ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
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
    try:
        load_config(config_path)
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

    if errors:
        sys.exit(1)
    sys.exit(0)


def _run_now() -> None:
    from pulse.config import ConfigError, load_config
    from pulse.digest import atomic_write_text, render_digest
    from pulse.graphql import GraphQLClient, make_deadline
    from pulse.locks import LockHeld, PulseLock
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

    try:
        with PulseLock():
            db_conn = open_db(db_path)
            try:
                with GraphQLClient(token=token, base_url=cfg.defaults.github_api_base) as gql:
                    deadline = make_deadline()
                    snapshot_id = run_snapshot(
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
        click.echo(str(digest_path))
    except LockHeld:
        click.echo("pulse already running", err=True)
        sys.exit(1)
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


if __name__ == "__main__":
    main()
