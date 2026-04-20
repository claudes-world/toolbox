from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConfigError(Exception):
    """Raised when config loading or validation fails."""


class StallOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pr_hours: int = Field(gt=0)
    issue_hours: int = Field(gt=0)


class OrgConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ignore: list[str] = []
    stall_overrides: dict[str, StallOverride] = {}


class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stall_pr_hours: int = Field(default=12)
    stall_issue_hours: int = Field(default=72)
    history_days: int = Field(default=7)
    cadence_minutes: int = Field(default=30, gt=0)
    github_api_base: str = Field(default="https://api.github.com")
    max_prs_per_repo: int = Field(default=30)
    max_issues_per_repo: int = Field(default=50)
    max_releases_per_repo: int = Field(default=10)

    @field_validator("stall_pr_hours", "stall_issue_hours")
    @classmethod
    def stall_hours_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("stall hours must be > 0")
        return v

    @field_validator("history_days")
    @classmethod
    def history_days_range(cls, v: int) -> int:
        if not (1 <= v <= 365):
            raise ValueError("history_days must be between 1 and 365")
        return v

    @field_validator("max_prs_per_repo", "max_issues_per_repo", "max_releases_per_repo")
    @classmethod
    def max_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("max_* values must be > 0")
        return v


class PulseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    orgs: dict[str, OrgConfig]
    defaults: Defaults


def load_config(path: Path) -> PulseConfig:
    """Read YAML config from path, validate via Pydantic, raise ConfigError on failure."""
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(f"cannot read config file {path}: {e}") from e
    try:
        raw: Any = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ConfigError(f"YAML parse error in {path}: {e}") from e
    try:
        return PulseConfig.model_validate(raw)
    except Exception as e:
        raise ConfigError(f"Config validation failed: {e}") from e
