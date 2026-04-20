# AGENTS.md

## Repository Purpose

This repo contains standalone CLI tools built for the do-box VPS. Each tool lives in its own directory with an executable script and any supporting files.

## Conventions

### Adding a New Tool

1. Create a directory: `<tool-name>/`
2. Add the main executable: `<tool-name>/<tool-name>` (no extension, with shebang)
3. Use the shared venv shebang: `#!/home/claude/venvs/transcribe/bin/python3`
4. Make it executable: `chmod +x <tool-name>/<tool-name>`
5. Symlink to ~/bin: `ln -sf ~/code/toolbox/<tool-name>/<tool-name> ~/bin/<tool-name>`
6. **Update README.md** — this is mandatory. Add:
   - Entry to the index table at the top
   - Full section with: description, setup, usage, files, and cost estimate
7. If new pip dependencies are needed, install into `~/venvs/transcribe/`

### README Maintenance

The README.md must stay in sync with the actual tools. When modifying a tool:
- Update usage examples if flags/arguments changed
- Update provider/voice tables if options changed
- Update cost estimates if pricing changed
- Update setup instructions if new credentials or dependencies added

### Secrets

Never commit secrets. Tools should read credentials from:
- `~/.secrets/openai.env` — OpenAI keys
- `~/.secrets/elevenlabs.env` — ElevenLabs key
- `~/.secrets/gcp-tts-sa.json` — Google Cloud service account
- Environment variables as fallback

### Adding a Python package tool

For tools that ship as a proper Python package (entry point in `pyproject.toml`) rather than a standalone script:

1. Create `<tool-name>/` directory with `__init__.py` and supporting modules
2. Add an entry point in `pyproject.toml` under `[project.scripts]`: `tool-name = "tool_name.__main__:main"`
3. Install into the shared venv: `pip install -e ~/code/toolbox/`
4. Add a `<tool-name>/README.md` documenting: overview, installation, configuration, CLI subcommands, storage, and troubleshooting
5. Update the root `README.md` with the new tool entry and a link to the package README
6. Update `AGENTS.md` with any ADR cross-references for architectural decisions the tool introduces

### Adding a hook

Hooks are event-driven scripts in `hooks/` wired via `.claude/settings.json`.

1. Create `hooks/<hook-name>` with a shebang and `set -euo pipefail`
2. Source `hooks/common.sh` if the hook needs to resolve a Telegram chat ID
3. Make it executable: `chmod +x hooks/<hook-name>`
4. Add a row to the hook registry table in `hooks/README.md` (name, event, purpose, status)
5. Wire in `.claude/settings.json` or project `settings.local.json` under the appropriate event key
6. Test with a real Claude Code session; verify output JSON shape matches the hook schema

### Tool Design Principles

- Each tool should be self-contained and runnable from `~/bin/` via symlink
- Support both argument input and stdin piping where appropriate
- Default to the cheapest provider/model that produces acceptable quality
- Output to stdout by default, `--out` flag for file output
- Print status/progress to stderr, results to stdout

## ADR cross-references

| ADR | Topic | Relevance |
|-----|-------|-----------|
| [ADR 0003](~/claudes-world/knowledge/adr/0003-port-allocation-v2-port-for.md) | Port allocation | Use `port-for` before binding any port; project-sharded 38xxx/58xxx bands |
| [ADR 0006](~/claudes-world/knowledge/adr/0006-world-bootstrap.md) | `.world/` bootstrap and precedence | Per-project `.world/` overlay pattern; where runtime state lives |
| [ADR 0014](~/claudes-world/knowledge/adr/0014-world-runtime-config.md) | `.world/` runtime config | `~/.world/pulse/` follows this convention for token, config, DB, snapshots |
| [ADR 0015](~/claudes-world/knowledge/adr/0015-org-pulse.md) | Org-pulse design | Decision record for the `pulse` package: GraphQL, SQLite, systemd timer, future extensibility |
