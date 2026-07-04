# toolbox

Handmade CLI tools by a human-AI duo who share a Linux box. Built for the [do-box](https://github.com/claudes-world/do-box) VPS — a shared workspace where a human (Liam/Chaintail) and an AI (Claude) build things together.

All tools use a shared Python venv at `~/venvs/transcribe/` and are symlinked into `~/bin/` for easy access.

## Tools

| Tool | Description |
|------|-------------|
| [transcribe](#transcribe) | Speech-to-text with OpenAI + tech glossary |
| [speak](#speak) | Text-to-speech with OpenAI, Google, or ElevenLabs |
| [md-speak](#md-speak) | Read markdown documents aloud with multiple voices |
| [voice-hook](#voice-hook) | Claude Code hook: auto-transcribe Telegram voice notes |
| [pulse](pulse/README.md) | GitHub org activity snapshots — open PRs, issues, releases, Dependabot |
| [repair-telegram-mcp](#repair-telegram-mcp) | Repair Telegram MCP partial disconnects in a live Claude Code tmux session |

---

### transcribe

Transcribe audio files using OpenAI's `gpt-4o-mini-transcribe` API. Ships with a technical glossary that primes the model to correctly handle terms like `git`, `Docker`, `kubectl`, etc. Auto-converts unsupported formats (like Telegram's `.oga`) to mp3 via ffmpeg.

**Setup:**
- `OPENAI_API_KEY` in `~/.secrets/openai.env`
- ffmpeg installed (for format conversion)

**Usage:**
```
transcribe audio.ogg                        # mini model + glossary (default)
transcribe --full audio.ogg                 # full model (gpt-4o-transcribe)
transcribe --no-glossary audio.ogg          # skip glossary prompt
transcribe --glossary custom.txt audio.ogg  # custom glossary
```

**Files:**
- `transcribe/transcribe` — main script
- `transcribe/glossary.txt` — technical terminology glossary (editable)

**Cost:** ~$0.003/min (mini) or ~$0.006/min (full)

---

### speak

Text-to-speech with three provider options. Outputs ogg opus by default (Telegram voice notes) or mp3.

**Providers:**
| Provider | Default Voice | Cost | Native Opus |
|----------|--------------|------|-------------|
| `openai` (default) | onyx | $15/1M chars | Yes |
| `google` | en-US-Wavenet-J | $16/1M chars (1M free/mo) | Yes |
| `elevenlabs` | brian | $60-80/1M chars | No (ffmpeg) |

**Setup:**
- OpenAI: `OPENAI_API_KEY` in `~/.secrets/openai.env`
- Google: Service account JSON at `~/.secrets/gcp-tts-sa.json`
- ElevenLabs: `ELEVEN_API_KEY` in `~/.secrets/elevenlabs.env`

**Usage:**
```
speak "Hello, how are you?"                           # OpenAI onyx (default)
speak --voice nova "Warm and friendly."               # OpenAI nova
speak -p google --voice en-US-Wavenet-B "Deep male."  # Google
speak -p elevenlabs --voice brian "Premium quality."   # ElevenLabs
speak --out reply.ogg "Saved to file."                # explicit output
speak --mp3 "Force mp3 output."                       # mp3 instead of ogg
echo "Piped input" | speak                            # stdin
```

**OpenAI voices:** alloy, ash, ballad, coral, echo, fable, nova, onyx, sage, shimmer
**Google voices:** Any en-US voice (e.g. `en-US-Wavenet-J`, `en-US-Neural2-C`, `en-US-Chirp3-HD-Fenrir`)
**ElevenLabs voices:** eric, river, george, daniel, brian, alice, bella

**Files:**
- `speak/speak` — main script

---

### md-speak

Read markdown documents aloud with multiple voices, smart link/path handling, and AI-powered content descriptions. Uses Google Cloud TTS with SSML for natural-sounding output.

**Voice roles:**
| Role | Default Voice | Used For |
|------|--------------|----------|
| Heading | en-US-Wavenet-B | H1-H6 headings (deep male) |
| Body | en-US-Wavenet-J | Paragraphs, list items |
| Table | en-US-Neural2-A | Table content |
| Special | en-US-Neural2-C | Links, code descriptions (female) |
| Quote | en-US-Wavenet-D | Blockquotes |

All voices configurable via env vars (`MD_VOICE_HEADING`, `MD_VOICE_BODY`, etc).

**Smart processing:**
- GitHub URLs → "link to user's repo-name repo"
- File paths → "filename in repo-name"
- Code blocks → AI-described via Claude Haiku
- Tables → Auto-selects reading mode (headers-once vs repeat-headers)
- Consecutive same-voice segments batched into single SSML API calls

**Setup:**
- Service account JSON at `~/.secrets/gcp-tts-sa.json` (Google Cloud TTS API enabled)
- Optional: `ANTHROPIC_API_KEY` for AI descriptions of code blocks and smart table mode selection
- ffmpeg installed (for audio stitching)

**Usage:**
```
md-speak document.md                    # outputs document.mp3
md-speak --out output.mp3 document.md   # explicit output path
md-speak --no-describe document.md      # skip AI descriptions
```

**Files:**
- `md-speak/md-speak` — main script

**Cost:** Free for most usage (Google's 1M WaveNet chars/month free tier)

---

### pulse

GitHub org activity monitor. Periodic GraphQL snapshots — open PRs, issues, Dependabot PRs, releases — stored in SQLite, rendered to a markdown digest.

Full documentation: [`pulse/README.md`](pulse/README.md)

**Quick start:**
```bash
~/venvs/transcribe/bin/pip install -e ~/code/toolbox/
ln -sf ~/code/toolbox/bin/pulse ~/bin/pulse   # add to PATH
mkdir -p ~/.world/pulse && chmod 700 ~/.world/pulse
echo "GH_TOKEN=ghp_yourtoken" > ~/.world/pulse/env && chmod 600 ~/.world/pulse/env
cp ~/code/toolbox/systemd/user/config.yml ~/.world/pulse/config.yml
# edit ~/.world/pulse/config.yml — set your org name under `orgs:`
export $(grep -v '^#' ~/.world/pulse/env | xargs)
pulse --self-check    # validate token, config, storage
pulse --now           # run snapshot + render digest
```

**Files:**
- `pulse/` — Python package (config, storage, GraphQL, snapshot, digest)
- `bin/pulse` — executable entry point
- `systemd/user/` — service + timer units
- `tests/test_config.py`, `tests/test_storage.py` — unit tests

**Cost:** No paid API calls. GraphQL queries use GitHub PAT (free tier).

---

### repair-telegram-mcp

Repair a Telegram MCP partial disconnect in a live Claude Code tmux session by sending `/reload-plugins` through tmux after an input-stability guard. This is for the case where tmux and the Telegram bun plugin are alive, but MCP tools are missing; dead REPL zombies still need the separate full-restart procedure.

**Setup:**
- tmux session running Claude Code with Telegram plugin
- `agent-health-mcp-check` available beside this script or on `PATH`
- Optional symlinks:
  ```bash
  ln -sf ~/code/toolbox/bin/repair-telegram-mcp ~/bin/repair-telegram-mcp
  ln -sf ~/code/toolbox/bin/agent-health-mcp-check ~/bin/agent-health-mcp-check
  ```

**Usage:**
```bash
repair-telegram-mcp [SESSION]   # defaults to current tmux session
repair-telegram-mcp SESSION --dry-run
repair-telegram-mcp SESSION --force

agent-health-mcp-check SESSION  # MCP_OK, MCP_MISSING:<reason>, or MCP_UNKNOWN:<reason>
```

**Environment:**
- `REPAIR_POLL_SECONDS` — seconds between input captures, default `60`
- `REPAIR_MAX_WAIT_SECONDS` — total wait before timeout, default `180`
- `REPAIR_INPUT_LEN_THRESHOLD` — stable non-empty input length needed before auto-commit, default `40`

**Exit codes:**
| Code | Meaning |
| --- | --- |
| 0 | Sent successfully, dry-run completed, or MCP was already healthy |
| 2 | Bad args or no session |
| 3 | tmux session missing |
| 4 | Zombie: tmux exists but no live Claude REPL |
| 5 | Input never stabilized |
| 6 | Refused partial slash command |
| 7 | Another repair is running for the session |
| 8 | No visible `❯` prompt, capture failed, or stable input was below the safety threshold |
| 9 | Post-Enter verify failed |

**Files:**
- `bin/repair-telegram-mcp` — tmux repair command with input-stability guard
- `bin/agent-health-mcp-check` — best-effort transcript probe for Telegram MCP tool availability
- `~/.world/repair-telegram-mcp.log` — action log
- `~/.world/repair-telegram-mcp.<session>.lock` — per-session flock lock

**Cost:** No paid API calls.

---

## Setup

Clone the repo and symlink each tool into `~/bin/`:

```bash
git clone git@github.com:claudes-world/toolbox.git ~/code/toolbox

ln -sf ~/code/toolbox/transcribe/transcribe ~/bin/transcribe
ln -sf ~/code/toolbox/speak/speak ~/bin/speak
ln -sf ~/code/toolbox/md-speak/md-speak ~/bin/md-speak
ln -sf ~/code/toolbox/hooks/voice-hook ~/bin/voice-hook
```

Install the shared Python venv:

```bash
python3 -m venv ~/venvs/transcribe
~/venvs/transcribe/bin/pip install openai elevenlabs google-cloud-texttospeech mistune anthropic
```

## Additional tools

Standalone tools that don't have their own README section yet.

| Tool | Description | Docs |
|------|-------------|------|
| claude-cost | Estimate Claude API-equivalent cost from local session logs (cache-aware, per-model); human table or `--json` for CPC/agents | `claude-cost/` |
| gen-image | Generate images via Google Gemini/Imagen API | `gen-image/` |
| morning-brief | Gather system health, PR staleness, weather, and dependency alerts in parallel; output JSON or human-readable text | `morning-brief/` |
| openai-usage | Query OpenAI costs and usage; optionally send summary to Telegram | `openai-usage/` |
| ports | Dynamic port map of all listening TCP services on the VPS (port, process, PID, tunnel hostname, systemd status) | `ports/` |
| tag-mp3 | Tag an mp3 file with ID3 metadata and optional album art | `tag-mp3/` |
| tg-sanitize | Telegram MarkdownV2 sanitizer — escapes special characters for safe bot messages | `tg-sanitize/` |

## Hooks

Claude Code hooks that run automatically during sessions. Full hook registry and wiring guide: [`hooks/README.md`](hooks/README.md).

These live in `hooks/` and are wired up via `.claude/settings.local.json`.

### voice-hook

Auto-transcribes Telegram voice notes. Fires on `UserPromptSubmit` — when a message arrives with an `audio/ogg` attachment, it:

1. Downloads the audio via the Telegram Bot API
2. Transcribes it using `~/bin/transcribe` (OpenAI gpt-4o-mini-transcribe)
3. Sends the transcription back as a quoted reply to the original voice note
4. Injects the transcription into Claude's context via `additionalContext`

**Setup:** Add to `.claude/settings.local.json`:
```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/home/claude/bin/voice-hook",
            "timeout": 30,
            "statusMessage": "Transcribing voice note..."
          }
        ]
      }
    ]
  }
}
```

**Requires:**
- `OPENAI_API_KEY` in `~/.secrets/openai.env`
- Telegram bot token in `~/.claude/channels/telegram/.env`
- `~/bin/transcribe` symlinked and working
- ffmpeg installed

## License

MIT
