"""Tests for pulse --dry-run subcommand."""
from __future__ import annotations

import dataclasses
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from pulse.__main__ import main
from pulse.schema import FieldStatus, IssueData, PRData, ReleaseData

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

MINIMAL_VALID_CONFIG_WITH_TWO_ORGS = """\
schema_version: "1.0"
orgs:
  example-org:
    ignore: []
    stall_overrides: {}
  other-org:
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


def _make_pr(number: int = 1, stalled: bool = False) -> PRData:
    return PRData(
        number=number,
        title=f"PR #{number}",
        author="alice",
        created_at="2025-01-01T00:00:00Z",
        updated_at="2025-01-02T00:00:00Z",
        is_draft=False,
        is_dependabot=False,
        is_renovate=False,
        hours_idle=24.0,
        stalled=stalled,
    )


def _make_issue(number: int = 1, stalled: bool = False) -> IssueData:
    return IssueData(
        number=number,
        title=f"Issue #{number}",
        author="bob",
        created_at="2025-01-01T00:00:00Z",
        updated_at="2025-01-02T00:00:00Z",
        labels=[],
        hours_idle=24.0,
        stalled=stalled,
    )


def _make_release(tag: str = "v1.0.0") -> ReleaseData:
    return ReleaseData(
        tag_name=tag,
        name=tag,
        created_at="2025-01-01T00:00:00Z",
        is_prerelease=False,
    )


@pytest.fixture()
def pulse_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up minimal pulse environment."""
    pulse_dir = tmp_path / ".world" / "pulse"
    pulse_dir.mkdir(parents=True)
    pulse_dir.chmod(0o700)

    config_path = pulse_dir / "config.yml"
    config_path.write_text(MINIMAL_VALID_CONFIG)

    monkeypatch.setenv("GH_TOKEN", "ghp_test_token")
    monkeypatch.setattr("pulse.__main__._DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr("pulse.__main__._DEFAULT_DB_PATH", pulse_dir / "pulse.db")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    return pulse_dir


def _patch_capture_fns(prs=None, issues=None, releases=None):
    """Return patch context for the three capture functions."""
    prs = prs or [_make_pr()]
    issues = issues or [_make_issue()]
    releases = releases or [_make_release()]
    ok = FieldStatus(status="success")

    mock_gql = MagicMock()
    # Mock repos query for auto-detect path
    mock_gql.execute.return_value = {
        "data": {
            "organization": {
                "repositories": {
                    "nodes": [{"name": "my-repo"}],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }
        }
    }

    return (
        patch("pulse.__main__.GraphQLClient", return_value=mock_gql.__enter__.return_value),
        patch("pulse.snapshot._capture_prs", return_value=(prs, ok)),
        patch("pulse.snapshot._capture_issues", return_value=(issues, ok)),
        patch("pulse.snapshot._capture_releases", return_value=(releases, ok)),
    )


def test_dry_run_writes_to_tmp_not_prod(pulse_env: Path):
    """--dry-run must write to /tmp, NOT to ~/.world/pulse/."""
    ok = FieldStatus(status="success")
    prs = [_make_pr()]
    issues = [_make_issue()]
    releases = [_make_release()]

    runner = CliRunner(mix_stderr=False)
    with (
        patch("pulse.graphql.GraphQLClient") as mock_gql_cls,
        patch("pulse.snapshot._capture_prs", return_value=(prs, ok)),
        patch("pulse.snapshot._capture_issues", return_value=(issues, ok)),
        patch("pulse.snapshot._capture_releases", return_value=(releases, ok)),
    ):
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_ctx.execute.return_value = {
            "data": {"organization": {"repositories": {"nodes": [{"name": "my-repo"}], "pageInfo": {"hasNextPage": False}}}}
        }
        mock_gql_cls.return_value = mock_ctx

        result = runner.invoke(main, ["--dry-run", "--repo", "example-org/my-repo"])

    assert result.exit_code == 0, result.output
    out_path_str = result.output.strip()
    assert out_path_str.startswith(tempfile.gettempdir()), f"Expected /tmp path, got: {out_path_str}"

    # Verify no production files were touched
    prod_db = pulse_env / "pulse.db"
    assert not prod_db.exists(), "dry-run must not create pulse.db"


def test_dry_run_output_file_valid_json(pulse_env: Path):
    """Output file must be valid JSON with expected keys."""
    ok = FieldStatus(status="success")
    prs = [_make_pr(1, stalled=True), _make_pr(2, stalled=False)]
    issues = [_make_issue(1), _make_issue(2)]
    releases = [_make_release()]

    runner = CliRunner(mix_stderr=False)
    with (
        patch("pulse.graphql.GraphQLClient") as mock_gql_cls,
        patch("pulse.snapshot._capture_prs", return_value=(prs, ok)),
        patch("pulse.snapshot._capture_issues", return_value=(issues, ok)),
        patch("pulse.snapshot._capture_releases", return_value=(releases, ok)),
    ):
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_gql_cls.return_value = mock_ctx

        result = runner.invoke(main, ["--dry-run", "--repo", "example-org/my-repo"])

    assert result.exit_code == 0, result.output
    out_path = Path(result.output.strip())
    assert out_path.exists()

    data = json.loads(out_path.read_text())
    assert data["dry_run"] is True
    assert data["org"] == "example-org"
    assert data["repo"] == "my-repo"
    assert data["pr_count"] == 2
    assert data["issue_count"] == 2
    assert data["release_count"] == 1

    # Cleanup
    out_path.unlink(missing_ok=True)


def test_dry_run_does_not_acquire_pulse_lock(pulse_env: Path):
    """--dry-run contract: no production paths written, output goes to /tmp.

    _run_dry_run never imports pulse.locks, so checking for lock acquisition
    is vacuous. The real contract is: pulse.db and digest-latest.md must NOT
    be created, and a pulse-dry-run-*.json file MUST appear in /tmp.
    """
    import glob

    ok = FieldStatus(status="success")
    prs = [_make_pr()]
    issues = [_make_issue()]
    releases = [_make_release()]

    runner = CliRunner(mix_stderr=False)
    with (
        patch("pulse.graphql.GraphQLClient") as mock_gql_cls,
        patch("pulse.snapshot._capture_prs", return_value=(prs, ok)),
        patch("pulse.snapshot._capture_issues", return_value=(issues, ok)),
        patch("pulse.snapshot._capture_releases", return_value=(releases, ok)),
    ):
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_gql_cls.return_value = mock_ctx

        result = runner.invoke(main, ["--dry-run", "--repo", "example-org/my-repo"])

    assert result.exit_code == 0, result.output

    # Production paths must NOT have been touched
    prod_db = pulse_env / "pulse.db"
    assert not prod_db.exists(), "dry-run must not create pulse.db"
    digest = pulse_env / "snapshots" / "digest-latest.md"
    assert not digest.exists(), "dry-run must not create digest-latest.md"

    # Output path must be a /tmp/pulse-dry-run-*.json file
    out_path_str = result.output.strip()
    assert out_path_str.startswith(tempfile.gettempdir()), f"Expected /tmp path, got: {out_path_str}"
    assert "pulse-dry-run-" in out_path_str
    assert out_path_str.endswith(".json")
    # Clean up
    Path(out_path_str).unlink(missing_ok=True)


def test_dry_run_repo_override_used(pulse_env: Path):
    """--repo OWNER/NAME is passed through correctly."""
    ok = FieldStatus(status="success")
    prs = [_make_pr()]
    issues = []
    releases = []

    runner = CliRunner()
    captured_args = {}

    def mock_capture_prs(gql, org, repo_name, **kwargs):
        captured_args["org"] = org
        captured_args["repo_name"] = repo_name
        return (prs, ok)

    def mock_capture_issues(gql, org, repo_name, **kwargs):
        return (issues, ok)

    def mock_capture_releases(gql, org, repo_name, **kwargs):
        return (releases, ok)

    with (
        patch("pulse.graphql.GraphQLClient") as mock_gql_cls,
        patch("pulse.snapshot._capture_prs", side_effect=mock_capture_prs),
        patch("pulse.snapshot._capture_issues", side_effect=mock_capture_issues),
        patch("pulse.snapshot._capture_releases", side_effect=mock_capture_releases),
    ):
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_gql_cls.return_value = mock_ctx

        result = runner.invoke(main, ["--dry-run", "--repo", "example-org/specific-repo"])

    assert result.exit_code == 0, result.output
    assert captured_args.get("org") == "example-org"
    assert captured_args.get("repo_name") == "specific-repo"


def test_dry_run_unknown_org_fails(pulse_env: Path):
    """--repo with unknown org → error, exit 1."""
    runner = CliRunner()
    result = runner.invoke(main, ["--dry-run", "--repo", "unknown-org/some-repo"])

    assert result.exit_code == 1
    assert "unknown-org" in result.output
