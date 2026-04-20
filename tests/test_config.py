from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from pulse.config import ConfigError, Defaults, OrgConfig, PulseConfig, StallOverride, load_config

MINIMAL_VALID = """\
schema_version: "1.0"
orgs:
  claudes-world:
    ignore: []
    stall_overrides: {}
defaults:
  stall_pr_hours: 12
  stall_issue_hours: 72
  history_days: 7
  cadence_minutes: 30
  github_api_base: "https://api.github.com"
  max_prs_per_repo: 30
  max_issues_per_repo: 50
  max_releases_per_repo: 10
"""

WITH_STALL_OVERRIDES = """\
schema_version: "1.0"
orgs:
  claudes-world:
    ignore: [some-repo]
    stall_overrides:
      my-repo:
        pr_hours: 24
        issue_hours: 48
defaults:
  stall_pr_hours: 12
  stall_issue_hours: 72
  history_days: 7
  cadence_minutes: 30
  github_api_base: "https://api.github.com"
  max_prs_per_repo: 30
  max_issues_per_repo: 50
  max_releases_per_repo: 10
"""


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "config.yml"
    p.write_text(content)
    return p


def test_valid_minimal_loads(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, MINIMAL_VALID))
    assert cfg.schema_version == "1.0"
    assert "claudes-world" in cfg.orgs
    assert cfg.defaults.stall_pr_hours == 12


def test_valid_with_stall_overrides(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, WITH_STALL_OVERRIDES))
    override = cfg.orgs["claudes-world"].stall_overrides["my-repo"]
    assert override.pr_hours == 24
    assert override.issue_hours == 48


def test_extra_field_raises(tmp_path: Path) -> None:
    bad = MINIMAL_VALID + "extra_field: oops\n"
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, bad))


def test_missing_defaults_raises(tmp_path: Path) -> None:
    no_defaults = """\
schema_version: "1.0"
orgs:
  claudes-world: {}
"""
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, no_defaults))


def test_invalid_type_stall_pr_hours(tmp_path: Path) -> None:
    bad = MINIMAL_VALID.replace("stall_pr_hours: 12", "stall_pr_hours: not-a-number")
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, bad))


def test_stall_pr_hours_zero_raises() -> None:
    with pytest.raises(ValidationError):
        Defaults(
            stall_pr_hours=0,
            stall_issue_hours=72,
            history_days=7,
            cadence_minutes=30,
            github_api_base="https://api.github.com",
            max_prs_per_repo=30,
            max_issues_per_repo=50,
            max_releases_per_repo=10,
        )


def test_stall_pr_hours_negative_raises() -> None:
    with pytest.raises(ValidationError):
        Defaults(
            stall_pr_hours=-1,
            stall_issue_hours=72,
            history_days=7,
            cadence_minutes=30,
            github_api_base="https://api.github.com",
            max_prs_per_repo=30,
            max_issues_per_repo=50,
            max_releases_per_repo=10,
        )


def test_history_days_out_of_range_raises() -> None:
    with pytest.raises(ValidationError):
        Defaults(
            stall_pr_hours=12,
            stall_issue_hours=72,
            history_days=400,
            cadence_minutes=30,
            github_api_base="https://api.github.com",
            max_prs_per_repo=30,
            max_issues_per_repo=50,
            max_releases_per_repo=10,
        )


def test_load_config_nonexistent_path(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "does_not_exist.yml")
