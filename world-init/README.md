# world-init

Bootstrap the `~/.world/` directory structure per ADR 0006.

## What it does

Creates the canonical `~/.world/` layout:

```
~/.world/
├── AGENTS.md               ← the index
├── reference/              (facts that don't change)
│   ├── ports.md
│   ├── services.md
│   ├── secrets.md
│   ├── shared-hosting.md
│   └── tooling-map.md
├── guides/                 (how to do tasks)
│   ├── deploy-cpc.md
│   ├── add-a-skill.md
│   ├── add-a-tool.md
│   ├── rebuild-vps.md
│   └── share-a-doc.md
├── conventions/            (rules that always apply)
│   ├── gitflow.md
│   ├── port-allocation.md
│   ├── tool-design-principles.md
│   ├── memory-system.md
│   └── changelog-discipline.md
└── host/                   (VPS-specific)
    ├── AGENTS.md
    └── manifest.yml
```

By default, each file is seeded as a stub with a TODO note. Use `--template` to copy pre-filled content from a directory (intended for CPC's `.world-templates/`).

## Usage

```bash
# Initialize with empty stubs
world-init

# Preview what would happen without writing
world-init --dry-run

# Overwrite existing files
world-init --force

# Seed from a template directory (e.g., CPC's .world-templates)
world-init --template ~/code/claude-pocket-console/.world-templates
```

## Idempotent

Safe to run multiple times. By default, existing files are skipped unless `--force` is used. Missing files and directories are created on each run.

## Environment

- `WORLD_DIR` — override the default `~/.world/` location (useful for testing)

## Status

Phase 1 proof of concept per ADR 0006. Phase 2 (CPC `.world-templates/` integration) and Phase 3 (per-project overlay support) are future work.

## Related

- ADR 0006: `.world/` directory bootstrap and config precedence
- ADR 0001: CPC + toolbox packaging (`.world-templates/` lives in CPC)
- `~/claudes-world/knowledge/adr/0006-world-directory-bootstrap-and-precedence.md`
