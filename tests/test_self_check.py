"""Tests for pulse --self-check subcommand."""
from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from pulse.__main__ import main

MINIMAL_VALID_CONFIG = """\
schema_version: "1.0"
orgs:
  example-org:
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


def _mock_httpx_response(status_code: int = 200, scopes: str = "repo, read:org") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {"x-oauth-scopes": scopes}
    resp.json.return_value = {"data": {"rateLimit": {"remaining": 5000}, "viewer": {"login": "testuser"}}}
    return resp


@pytest.fixture()
def pulse_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up a minimal pulse environment in tmp_path."""
    pulse_dir = tmp_path / ".world" / "pulse"
    pulse_dir.mkdir(parents=True)
    pulse_dir.chmod(0o700)

    config_path = pulse_dir / "config.yml"
    config_path.write_text(MINIMAL_VALID_CONFIG)

    env_path = pulse_dir / "env"
    env_path.write_text("GH_TOKEN=ghp_test\n")
    env_path.chmod(0o600)

    monkeypatch.setenv("GH_TOKEN", "ghp_test_token")

    # Patch the default paths in __main__
    monkeypatch.setattr("pulse.__main__._DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr("pulse.__main__._DEFAULT_DB_PATH", pulse_dir / "pulse.db")

    # Patch Path.home() to return tmp_path
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    return pulse_dir


def test_self_check_token_scope_pass(pulse_env: Path):
    """Token with required scopes → all [OK], exit 0."""
    mock_resp = _mock_httpx_response(200, "repo, read:org, write:packages")

    runner = CliRunner()
    with patch("httpx.post", return_value=mock_resp):
        result = runner.invoke(main, ["--self-check"])

    assert result.exit_code == 0, result.output
    assert "[OK] token:" in result.output
    assert "[FAIL]" not in result.output


def test_self_check_token_scope_fail_missing(pulse_env: Path):
    """Token missing read:org → [FAIL], exit 1."""
    mock_resp = _mock_httpx_response(200, "repo")  # missing read:org

    runner = CliRunner(mix_stderr=False)
    with patch("httpx.post", return_value=mock_resp):
        result = runner.invoke(main, ["--self-check"])

    assert result.exit_code == 1
    combined = result.output + result.stderr
    assert "read:org" in combined or "missing" in combined.lower()


def test_self_check_no_token(pulse_env: Path, monkeypatch: pytest.MonkeyPatch):
    """No GH_TOKEN → [FAIL] token, exit 1."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    runner = CliRunner()
    result = runner.invoke(main, ["--self-check"])

    assert result.exit_code == 1


def test_self_check_config_parse_fail(pulse_env: Path, monkeypatch: pytest.MonkeyPatch):
    """Bad config YAML → [FAIL] config, exit 1."""
    config_path = pulse_env / "config.yml"
    config_path.write_text("this: is: not: valid: yaml: [unclosed")

    mock_resp = _mock_httpx_response(200, "repo, read:org")
    runner = CliRunner()
    with patch("httpx.post", return_value=mock_resp):
        result = runner.invoke(main, ["--self-check"])

    assert result.exit_code == 1


def test_self_check_sqlite_integrity_fail(pulse_env: Path):
    """Corrupt SQLite file → [FAIL] sqlite, exit 1."""
    db_path = pulse_env / "pulse.db"
    # Write garbage bytes — SQLite will reject this
    db_path.write_bytes(b"not a real sqlite database\x00\x01\x02")
    db_path.chmod(0o600)

    mock_resp = _mock_httpx_response(200, "repo, read:org")
    runner = CliRunner()
    with patch("httpx.post", return_value=mock_resp):
        result = runner.invoke(main, ["--self-check"])

    assert result.exit_code == 1


def test_self_check_env_wrong_perms(pulse_env: Path):
    """env file with 0644 perms → [FAIL] perms, exit 1."""
    env_path = pulse_env / "env"
    env_path.chmod(0o644)  # too permissive

    mock_resp = _mock_httpx_response(200, "repo, read:org")
    runner = CliRunner()
    with patch("httpx.post", return_value=mock_resp):
        result = runner.invoke(main, ["--self-check"])

    assert result.exit_code == 1


def test_self_check_fine_grained_token_no_scopes_header(pulse_env: Path):
    """GitHub App / fine-grained token: missing x-oauth-scopes header → [OK] informational."""
    mock_resp = _mock_httpx_response(200, "")
    mock_resp.headers = {}  # no x-oauth-scopes at all

    runner = CliRunner()
    with patch("httpx.post", return_value=mock_resp):
        result = runner.invoke(main, ["--self-check"])

    assert result.exit_code == 0
    assert "fine-grained" in result.output or "absent" in result.output
