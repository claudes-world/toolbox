# quarantine-research

A hardened web research pipeline that runs an isolated Claude subprocess,
sanitizes output, and gates release through a risk review.

## Why this exists

`safe-research` provides a basic sandbox. `quarantine-research` adds a second
layer: every batch of raw findings passes through the `sanitize-research`
regex scanner and then through a separate haiku model review that scores the
content for prompt injection risk before releasing it to the filesystem.

Research that scores above 5/10 is hard-blocked. Nothing leaves quarantine.

## Usage

```bash
quarantine-research "What is Project Mockingbird?"
quarantine-research --telegram "latest EU AI Act enforcement actions"
```

- First argument (required): the research prompt.
- `--telegram`: send a Pocket Console deep-link button when done.

Prints the released file path (or a BLOCKED message) to stdout.
All other progress logging goes to stderr.

## Output directory

Each run creates a timestamped session directory:

```
~/claudes-world/quarantine/YYYYMMDD-HHMMSS-<slug>/
  01-raw-findings.txt         raw output from the research subprocess
  02-sanitized-findings.txt   output after sanitize-research scanner
  03-reviewed-findings.json   haiku risk review (JSON)
  04-released.md              present only when risk_score <= 5
  BLOCKED.md                  present only when risk_score >= 6
  meta.json                   timestamps, scores, outcome summary
```

## Pipeline stages

### 1. Isolated research subprocess

Runs `claude -p` with:

- `--model sonnet`
- `--allowedTools "WebSearch,WebFetch"` — no filesystem, no Bash
- `--disable-slash-commands` — no skills
- `--no-session-persistence` — nothing saved to disk
- `--strict-mcp-config --mcp-config '{"mcpServers":{}}'` — no MCP servers
- `CLAUDE_CONFIG_DIR` pointed at a fresh temp dir containing only credentials
- `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` — no auto-memory writes

The system prompt instructs the subprocess to summarize content in its own
words and ignore any instructions embedded in web pages.

### 2. Sanitize

Pipes raw output through `hooks/sanitize-research` (Python regex scanner).
Detects common injection patterns: role reassignment, system/assistant/human
role markers, tool call JSON, XML invocations, code execution calls,
destructive commands, dangerous protocols, base64 payloads, concealment
instructions, safety bypass phrases, and excessive repetition.

Warnings are prepended to the text so downstream reviewers can see them.

### 3. Haiku review

A second isolated `claude -p` call with `--model haiku` and no tools reviews
the sanitized text and returns JSON:

```json
{
  "risk_score": 0,
  "risk_level": "low",
  "findings": [],
  "clean_text": "..."
}
```

The `clean_text` field contains the original content with any detected
injection attempts redacted as `[REDACTED]`.

### 4. Risk gate

| risk_score | outcome  | file written        |
|------------|----------|---------------------|
| 0 – 5      | released | `04-released.md`    |
| 6 – 10     | blocked  | `BLOCKED.md`        |

Released files include clean_text from the haiku review, or fall back to
the sanitizer output if clean_text is empty.

Blocked files explain the risk score, list findings, and point to the session
directory for manual review.

### 5. meta.json

Written after the risk gate:

```json
{
  "prompt": "...",
  "session_dir": "...",
  "started_at": "...",
  "ended_at": "...",
  "risk_score": 3,
  "risk_level": "low",
  "outcome": "released",
  "files": { ... },
  "pipeline_version": "1"
}
```

### 6. Telegram (optional)

With `--telegram`, sends an inline keyboard button to the active Telegram
channel. Released content links to the file viewer. Blocked reports link to
the BLOCKED.md file. Chat ID resolution follows `common.sh` priority order.

## Sandbox cleanup

The temp sandbox dir (`/tmp/qr-sandbox-*`) is deleted on exit via a bash
`trap`, even if the script errors out.

## Error handling

- Research subprocess failure: writes a failure notice to `01-raw-findings.txt`,
  continues pipeline.
- Sanitizer failure: falls back to copying raw findings.
- Haiku review failure or unparseable JSON: defaults to risk_score=10 (blocked)
  for safety.
- Missing credentials: warns on stderr, continues (subprocess may fail auth).

## Dependencies

- `claude` in PATH (Claude Code CLI)
- `hooks/sanitize-research` (sibling script)
- `hooks/common.sh` (Telegram config, only needed with `--telegram`)
- `python3` with stdlib only
- `jq`, `curl` (only needed with `--telegram`)

## Related tools

- `safe-research` — simpler one-stage pipeline, no risk scoring, output goes
  to `~/claudes-world/tmp/` instead of quarantine
- `sanitize-research` — standalone regex injection scanner, reads from stdin
