# pulse

GitHub org health monitor. Periodic GraphQL snapshots across configured orgs, stored in SQLite, rendered to a markdown digest. Tracks open PRs (including Dependabot bot PRs), issues, and recent releases.

## Overview

`pulse` runs as a systemd user timer (every 30 minutes by default) and on demand via `pulse --now`. Each snapshot queries the GitHub GraphQL API, writes results to `~/.world/pulse/pulse.db`, and renders a human-readable markdown digest at `~/.world/pulse/snapshots/digest-latest.md`. Stalled PRs (idle beyond threshold), Dependabot PRs, and recent releases are all surfaced in one place without manual polling.

**Note (v0 scope):** The `alerts` table in the schema is a stub for future Dependabot security-alert integration (GitHub GraphQL `vulnerabilityAlerts`). In v0 the Dependabot section of the digest shows open Dependabot bot PRs (derived from PR author), not raw security alerts.

## Installation

```bash
pip install -e ~/code/toolbox/
```

This installs the `pulse` CLI entry point (defined in `pyproject.toml`). The systemd unit files live in `~/code/toolbox/systemd/`.

GH_TOKEN must be set in `~/.world/pulse/env` (0o600):

```bash
mkdir -p ~/.world/pulse && chmod 700 ~/.world/pulse
echo "GH_TOKEN=ghp_yourtoken" > ~/.world/pulse/env
chmod 600 ~/.world/pulse/env
```

The systemd service automatically loads this file via `EnvironmentFile=`. For manual CLI use, export the token into your shell:

```bash
export $(grep -v '^#' ~/.world/pulse/env | xargs)
pulse --now
```

Note: `source ~/.world/pulse/env` assigns variables but does not export them to child processes unless you follow with `export GH_TOKEN`.

Token needs `read:org` + `repo` scopes (classic PAT) or equivalent fine-grained permissions.

## Configuration

Config lives at `~/.world/pulse/config.yml`. Validated on load via Pydantic (`pulse/config.py`).

```yaml
schema_version: "1.0"

orgs:
  claudes-world:            # GitHub org name
    ignore: []              # repo names to skip entirely
    stall_overrides:        # per-repo stall threshold overrides (optional)
      some-repo:
        pr_hours: 24        # override stall_pr_hours for this repo
        issue_hours: 168    # override stall_issue_hours for this repo

defaults:
  stall_pr_hours: 12        # flag PRs idle > this many hours
  stall_issue_hours: 72     # flag issues idle > this many hours
  history_days: 7           # SQLite retention window (1-365)
  cadence_minutes: 30       # informational; systemd timer governs actual schedule
  github_api_base: "https://api.github.com"
  max_prs_per_repo: 30      # max PRs fetched per repo (single capped query, not cursor pagination)
  max_issues_per_repo: 50
  max_releases_per_repo: 10
```

Key Pydantic model classes (see `pulse/config.py`):

| Class | Fields |
|-------|--------|
| `PulseConfig` | `schema_version`, `orgs`, `defaults` |
| `OrgConfig` | `ignore` (list), `stall_overrides` (dict of repo → `StallOverride`) |
| `StallOverride` | `pr_hours`, `issue_hours` |
| `Defaults` | all defaults fields above |

Unknown keys are rejected (`extra="forbid"`).

## CLI subcommands

| Command | Description |
|---------|-------------|
| `pulse --now` | Run snapshot + render digest. Manual path — acquires file lock, blocks until complete. |
| `pulse --service` | Run snapshot without lock. Used by systemd `ExecStart`. |
| `pulse --digest` | Print current digest to stdout. No new snapshot. |
| `pulse --self-check` | Diagnostic: verify token scope, config parse, directory permissions, SQLite integrity. |
| `pulse --dry-run [--repo OWNER/NAME]` | Test against a single repo, write to `/tmp`. No DB writes. |
| `pulse --config-check` | Validate config + print effective YAML. Exits non-zero on error. |
| `pulse --version` | Print version. |

## SQLite storage

Location: `~/.world/pulse/pulse.db` (0o600). WAL mode, `busy_timeout=5000ms`.

| Table | Key columns | Purpose |
|-------|-------------|---------|
| `snapshots` | `id`, `captured_at_utc`, `captured_at_et`, `duration_ms`, `orgs_queried`, `repos_succeeded`, `repos_failed`, `repos_partial`, `capture_status` | One row per snapshot run |
| `repos` | `snapshot_id`, `org`, `name`, `default_branch`, `is_fork`, `is_archived`, `capture_status`, `field_statuses` | Repos queried in each snapshot |
| `prs` | `repo_id`, `number`, `title`, `author`, `created_at`, `updated_at`, `is_draft`, `is_dependabot`, `is_renovate`, `hours_idle`, `stalled` | Open PRs at snapshot time |
| `issues` | `repo_id`, `number`, `title`, `author`, `created_at`, `updated_at`, `labels`, `hours_idle`, `stalled` | Open issues at snapshot time |
| `releases` | `repo_id`, `tag_name`, `name`, `created_at`, `is_prerelease` | Latest N releases per repo (`max_releases_per_repo`); `history_days` governs snapshot retention, not release filtering |
| `alerts` | `repo_id`, `severity`, `ghsa_id`, `package_name`, `ecosystem`, `age_days`, `dependabot_pr_number` | Dependabot security alerts (schema stub — not populated in v0) |
| `pagination_state` | `org`, `repo`, `field`, `last_cursor`, `timestamp` | Cursor state for org-level repo enumeration (not per-repo collections) |

Full schema: `pulse/storage.py` → `create_schema()`.

## GraphQL design notes

- GitHub GraphQL v4 API (`https://api.github.com/graphql`)
- **Org-level repo enumeration** uses cursor pagination (`gql.paginate`) to page through all repos in an org (50 per page).
- **Per-repo collections** (PRs, issues, releases) use a single capped query per collection (`gql.execute` with `first: max_*_per_repo`, clamped to 100). If the real count exceeds the cap, the repo is marked `partial` — data shown is real but may be incomplete.
- `pagination_state` table stores org-level repo cursors (not per-repo PR/issue cursors).
- Stale or truncated data surfaced as `partial` capture_status on the repo row — never silently dropped.
- IPv4 monkey-patch applied at startup (same pattern as `smart-speak`) — VPS Happy Eyeballs stall workaround.

## GH_TOKEN scope requirements

| PAT type | Scopes needed |
|----------|--------------|
| Classic PAT | `read:org`, `repo` |
| Fine-grained PAT | `Contents: read`, `Issues: read`, `Pull requests: read`, `Metadata: read`, `Members: read` (per org) |

Verify with: `pulse --self-check`

## Troubleshooting

**Run the built-in diagnostic first:**
```bash
pulse --self-check
```

**Check service logs:**
```bash
journalctl --user -u pulse.service -n 50
journalctl --user -u pulse.timer
```

**Test config validity:**
```bash
pulse --config-check
```

**Dry run against a specific repo:**
```bash
pulse --dry-run --repo claudes-world/toolbox
```

For full operational procedures (token rotation, adding orgs, security guidance), see the SOP at `~/claudes-world/knowledge/sop/org-pulse.md`.
