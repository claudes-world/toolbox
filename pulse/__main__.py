from __future__ import annotations

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
@click.version_option(__version__, prog_name="pulse")
@click.pass_context
def main(ctx: click.Context, config_check: bool, self_check: bool) -> None:
    """pulse — org health monitor."""
    apply_ipv4_patch()
    if config_check and self_check:
        click.echo("ERROR: --config-check and --self-check are mutually exclusive", err=True)
        sys.exit(1)
    if config_check:
        _run_config_check()
    elif self_check:
        _run_self_check()
    elif ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


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
    try:
        pulse_dir.mkdir(parents=True, exist_ok=True)
        test_file = pulse_dir / ".write_test"
        test_file.write_text("ok")
        test_file.unlink()
        click.echo(f"[OK] dir writable: {pulse_dir}")
    except Exception as e:
        errors.append(f"dir writeable: {e}")
        click.echo(f"[FAIL] dir writeable: {e}", err=True)

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


if __name__ == "__main__":
    main()
