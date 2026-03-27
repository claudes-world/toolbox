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

### Tool Design Principles

- Each tool should be self-contained and runnable from `~/bin/` via symlink
- Support both argument input and stdin piping where appropriate
- Default to the cheapest provider/model that produces acceptable quality
- Output to stdout by default, `--out` flag for file output
- Print status/progress to stderr, results to stdout
