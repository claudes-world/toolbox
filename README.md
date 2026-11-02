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
| [share-doc](#share-doc) | Share Markdown through WorldOS share_document, legacy sender as fallback |
| [claude-was-wrong](#claude-was-wrong) | Tally Claude Code correction phrases and daily mistake frequency |

---

### share-doc

Publishes Markdown through the current lane's WorldOS `share_document` path
(`worldos-communications` `share_doc.py`). WorldOS owns publication, audio
rendering on its speech worker (counted toward the quota), Telegram delivery
and the auto-pin of the document message. The legacy calling convention keeps
working:

```bash
printf '# Title\n\nBody\n' | share-doc --to current
echo ~/claudes-world/tmp/report.md | share-doc --chat-id -1001 --thread-id 77 --no-audio
share-doc --to current --dry-run          # prints the resolved destination
share-doc --list-conversations            # share_doc.py arguments pass through
```

`--to current` uses `WORLDOS_CURRENT_CHAT_ID`/`WORLDOS_CURRENT_THREAD_ID` or
`WORLDOS_CURRENT_ENVELOPE_PATH` when present, otherwise the lane's single
conversation; no destination flag at all also means the lane's single
conversation. `--chat-id`/`--thread-id` map to `chat:<id>/thread:<id|root>`;
the lane's bound bot sends, so `--bot-env`/`--bot-username` only matter on the
fallback path.

**Degrade, never gate:** with no lane communications socket or client, an
unresolvable destination, or a broker refusal before acceptance, `share-doc`
prints one stderr line and runs `hooks/share-doc-legacy` (the direct Telegram
sender) with the same arguments and input. An uncertain broker response (the
request may have been accepted) also falls back, after one stderr line saying
the document may arrive twice: a duplicate beats a lost message.

**Files:** `hooks/share-doc` (wrapper), `hooks/share-doc-legacy` (fallback).
`WORLDOS_SHARE_DOC_CLIENT` overrides the client path; the default is
`/opt/worldos/current/...`, then `~/code/worldos/...`.

**Cost:** TTS through the WorldOS speech worker when audio is enabled.

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

### claude-was-wrong

Analyzes visible assistant replies in local Claude Code transcripts for admitted
mistakes, user vindication, corrections, and explicit apologies. The default TUI
features the combined "you were right and I was wrong" family, then shows
response-normalized rates, daily and seven-day trends, category/phrase totals,
main-agent versus sub-agent populations, context fullness, time-of-day patterns,
and roughest/cleanest active days. Dates and hours are reported in Eastern Time.

Exact, high-confidence matches drive the headline metrics. A separate probable
tier catches fuzzier language such as "I missed..." for auditing without silently
inflating the exact count. Standalone `sorry` is intentionally excluded; explicit
forms such as "I'm sorry," "I am sorry," "I apologize," and "sorry about/for/that"
are counted. One reply can contain several phrases, but counts as one admission
response in rates and timelines.

The first run builds a local SQLite index and may take a while on a large archive.
Later runs scan only appended or changed transcript data. Resolved paths and
copied message IDs are deduplicated.

**Setup:** No credentials or third-party packages are required. Symlink the
repository-owned executable into `~/bin`:

```bash
ln -sf ~/code/toolbox/claude-was-wrong/claude-was-wrong ~/bin/claude-was-wrong
```

**Usage:**

```bash
claude-was-wrong                  # last 30 ET calendar days
claude-was-wrong --days 7         # today and the previous 6 ET days
claude-was-wrong --all            # entire transcript archive
claude-was-wrong --days 90 --json # machine-readable output
claude-was-wrong --html report.html
claude-was-wrong --audit probable
claude-was-wrong --audit exact --include-snippets
claude-was-wrong --population main --model opus
claude-was-wrong --since 2026-01-01 --project toolbox
claude-was-wrong --context-window sonnet=1000000
claude-was-wrong --rebuild-index
```

The HTML report is self-contained, responsive, and includes interactive
population/model/project filters, a timeline, context and time heatmaps, phrase
charts, and an audit table. Snippets are omitted by default. When explicitly
enabled, matched clauses are locally redacted for common secrets, paths, and
email addresses, but the generated report should still be treated as private.

Set `CLAUDE_TRANSCRIPTS_DIR` or pass `--transcripts-dir PATH` for another archive.
Set `CLAUDE_WAS_WRONG_STATE_DIR` or pass `--state-dir PATH` to move the index.
The default state is `~/.world/claude-was-wrong/index.sqlite3` with private
directory/file permissions.

Context fullness is an estimate: usage is input plus cache-read plus
cache-creation tokens divided by a model-family context-window mapping. Use
repeatable `--context-window MODEL=TOKENS` overrides when the mapping is wrong.
Results describe language in replies, not independently verified model errors,
and sparse rates or correlations should not be read as causal.

**Files:**

- `claude-was-wrong/claude-was-wrong` — command-line entry point
- `claude-was-wrong/cww_core.py` — index, classifier, and metrics
- `claude-was-wrong/cww_render.py` — TUI, JSON, audit, and HTML renderers
- `tests/test_claude_was_wrong.py` — classifier, index, privacy, and report tests
- `~/.claude/projects/**/*.jsonl` — read-only input transcripts
- `~/.world/claude-was-wrong/index.sqlite3` — private incremental index

**Cost:** No paid API calls and no network requests; it only reads local files.

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
| codex-cost | Estimate OpenAI API-equivalent cost from local Codex rollout logs (cache-aware, per-model/project); human table or `--json` | `codex-cost/` |
| gen-image | Generate images via Google Gemini/Imagen API | `gen-image/` |
| morning-brief | Gather system health, PR staleness, weather, and dependency alerts in parallel; output JSON or human-readable text | `morning-brief/` |
| openai-usage | Query OpenAI costs and usage; optionally send summary to Telegram | `openai-usage/` |
| ports | Dynamic port map of all listening TCP services on the VPS (port, process, PID, tunnel hostname, systemd status) | `ports/` |
| tag-mp3 | Tag an mp3 file with ID3 metadata and optional album art | `tag-mp3/` |
| tg-sanitize | Telegram MarkdownV2 sanitizer — escapes special characters for safe bot messages | `tg-sanitize/` |

### claude-cost

Estimates current Claude API token costs from `~/.claude/projects/**/*.jsonl`,
including subagents. Reads, 5-minute writes, 1-hour writes, and ordinary input
are billed separately. Completed usage replaces partial streaming usage for
the same message ID, including copies in other transcripts. Nested iteration
and thinking counts are not added to the already aggregated token totals.

```bash
claude-cost                      # last 3 days
claude-cost --days 7 --by-project
claude-cost --model fable-5-1 --json
claude-cost --all
claude-cost --no-progress         # suppress progress on stderr
```

No credentials or dependencies are needed. Files: `claude-cost/claude-cost`
(symlinked from `~/bin/claude-cost`) and the shared `cost_accounting.py` at the
repository root. Keep the shared module with the repository when installing.
The tool makes no API calls and costs nothing to run.

Progress uses the same `cost_progress.py` renderer as Codex: terminals show a
progress bar with file/byte counts, elapsed time, and ETA, followed by cost
calculation status. Redirected stderr receives occasional plain-text updates.
Progress stays on stderr, so `--json` remains clean; `--no-progress` hides it.

Current standard/global prices, checked **2026-09-12**, in USD per million tokens:

| Model | Ordinary input | Cache read | 5m write | 1h write | Output |
|---|---:|---:|---:|---:|---:|
| Fable 5.1 / Mythos 5.1 | 10 | 0.25 | 12.50 | 20 | 50 |
| Fable 5 / Mythos 5 | 10 | 1 | 12.50 | 20 | 50 |
| Opus 5 | 5 | 0.50 | 6.25 | 10 | 25 |
| Sonnet 5 | 2 | 0.20 | 2.50 | 4 | 10 |
| Haiku 4.5 | 1 | 0.10 | 1.25 | 2 | 5 |

[Official Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing).
Sonnet 5's former introductory price is now permanent. Claude 5 has no
long-context premium. Opus 5/4.8 Fast doubles standard token rates; Batch
halves rates. Supported US-only inference multiplies all token rates by 1.1.
Recorded billing metadata determines these modifiers. Existing explicit
Opus 4.5–4.8 and Sonnet 4.5/4.6 models remain supported.

JSON preserves `cache_write` as the combined write count and adds
`cache_write_5m`, `cache_write_1h`, and `cache_write_unknown` per model.
`total_cost_intro_usd` is deprecated and now equals `total_cost_usd`.
The common reporting conventions below apply to both tools.

### codex-cost

Estimates the metered OpenAI API equivalent of local Codex usage. It reads
`~/.codex/sessions/**/*.jsonl`, uses each turn's input, cached-input, and output
token counts, and groups results by model. These are list-price estimates, not
the amount billed for a ChatGPT or Codex subscription. Human output includes
aggregate input/output token totals and cache percentage; JSON includes the
exact integer totals.

Astra, Sol, Terra, Luna, GPT-5.5, and GPT-5.4 have two context subrows:
`<=272k` and `>272k` total input tokens (including cache reads and writes).
Their percentages show each band's share of its model's input-plus-output
tokens and cost. They subdivide the model total; they do not add charges.
JSON exposes these as `by_model.<model>.context_bands.le_272k` and `gt_272k`.

An output breakdown for every model and context band separates reasoning from
non-reasoning output, with token counts, output-token percentages, attributed
output costs, and output-cost percentages. Reasoning is already included in
output tokens. Non-reasoning output includes tool calls and formatting, not
just final answers. Missing or invalid reasoning counts are shown as
`unclassified`. JSON's `output_breakdown` contains `reasoning`, `non_reasoning`,
and `unclassified` entries with `tokens`, `token_pct`, `cost_usd`, and `cost_pct`.

Setup: no credentials or third-party packages are required. The executable is
`codex-cost/codex-cost`, symlinked as `~/bin/codex-cost`, with its sibling
`codex_cost_core.py` providing parsing, caching, and progress. Shared verified
rates and reporting helpers live in the repository-root `cost_accounting.py`;
`cost_progress.py` provides the shared stderr progress display.

```bash
codex-cost                     # last 3 days
codex-cost --days 7
codex-cost --since 2026-07-01
codex-cost --by-project
codex-cost --model gpt-5.6-sol
codex-cost --model gpt-6-astra
codex-cost --json
codex-cost --workers 1          # sequential parsing; default caps workers at 4
codex-cost --no-cache           # bypass persistent cache reads and writes
codex-cost --full-scan          # ignore transcript modification times
codex-cost --no-progress       # suppress progress messages
```

Bounded reports skip transcripts whose modification time predates the cutoff,
then check timestamps inside the remaining files. Recently resumed sessions
are included even when stored in old date directories. This assumes normal
local log timestamps are consistent with file modification times; use
`--full-scan` for imported or timestamp-altered logs. `--all` disables the time
filter while retaining cache benefits.

The disposable SQLite cache lives under
`${XDG_CACHE_HOME:-~/.cache}/codex-cost/<sessions-directory-hash>.sqlite3`.
It stores normalized usage counts and metadata, not transcript text or prices.
Unchanged files are reused across date/model filters; changed files are fully
reparsed. Each canonical sessions directory has its own cache, and cache errors
fall back to reading logs. New or changed files are parsed in parallel when at
least two files and 16 MiB of data need reading.

Progress goes to stderr, keeping JSON on stdout clean. Terminals show an updating
bar with file/byte counts, elapsed time, and ETA. Redirected stderr receives
plain updates at most every five seconds, plus initial and final status.

The tool itself makes no API calls and costs nothing to run. Current prices,
checked **2026-09-12**, in USD per million tokens (Standard, <=272k input):

| Model | Ordinary input | Cache read | Cache write | Output |
|---|---:|---:|---:|---:|
| GPT-6 Astra | 10 | 1 | 12.50 | 50 |
| GPT-5.6 Sol | 4 | 0.40 | 5 | 20 |
| GPT-5.6 Terra | 2 | 0.20 | 2.50 | 12 |
| GPT-5.6 Luna | 0.20 | 0.02 | 0.25 | 1.20 |
| GPT-5.6 Cyber | 12.50 | 1.25 | 15.625 | 75 |

[Official OpenAI pricing](https://developers.openai.com/api/docs/pricing).
For Astra/Sol/Terra/Luna, requests above 272,000 input tokens use 2×
input/read/write rates and 1.5× output rates for the entire request.
Fast/Priority doubles these rates; Batch/Flex halves them. Cyber has only
verified Standard short-context pricing; other combinations are unpriced.
Sol's promotional rates are available at least through **November 21, 2026**;
recheck the official page then instead of assuming a replacement price.
Existing GPT-5.5, GPT-5.4, GPT-5.4-mini, and GPT-5 rates remain supported,
including their own documented context/tier rules.

Ordinary input is `input_tokens - cached_input_tokens - cache_write_input_tokens`.
Writes use their own rate, not an additional full-rate input charge. The parser
also accepts `input_tokens_details.cached_tokens` and `.cache_write_tokens`.
Repeated notifications with unchanged cumulative usage are counted once;
identical-sized requests with growing totals are retained, as are requests
after cumulative counter resets. Raw usage is repriced each run. Cache schema
changes trigger an automatic rebuild without changing the source transcripts.

JSON adds `cache_write_input_tokens` and `unclassified_input_tokens` at the top
level, and `cache_write`/`cache_unknown` per model. `fresh_input_tokens` and
`fresh_in` now mean known ordinary input, excluding writes and unknown splits.

### Cost reporting conventions (both tools)

- Prices are today's API-equivalent **token** prices even for older sessions.
  Subscription invoices and separate hosted-tool fees are excluded.
- Billing metadata on usage takes precedence over requested turn settings.
  Missing processing tier/geography defaults to Standard/global and is disclosed;
  current machine settings are never used to infer historical billing.
- Exact model IDs, supported dated snapshots, and documented aliases are resolved.
  `gpt-5.6` and `gpt-daybreak-blue-latest` currently resolve to Sol;
  `gpt-daybreak-red-latest` resolves to Cyber. Unknown models, unsupported tiers,
  and invalid counters are flagged instead of assigned guessed prices.
- `pricing_complete`, `pricing_assumptions` (counts by assumption),
  `pricing_issues`, `unpriced_calls`, `invalid_usage_records`, and
  `uncertain_cache_calls` describe coverage at report and model level.
  Missing metadata also makes `pricing_complete` false.
- Explicit zero writes remain zero. Missing Codex write counts produce a range
  from ordinary input to all non-read input being cache-written. Missing Claude
  write durations range from 5-minute to 1-hour rates; partial duration breakdowns
  preserve known writes and bound only the unresolved remainder.
- `total_cost_usd` and per-model `cost_usd` stay numeric: lower cache estimates
  under disclosed tier assumptions, excluding wholly unpriced/invalid usage.
  `cost_range_usd` provides `min` and `max`; `max: null` means excluded usage
  prevents a finite total. This range is conditional on the stated tier/geography,
  not a bound on possible speed tiers. Unpriced usage tokens remain in model rows.
  Project costs remain numeric subtotals, with coverage in the report/model rows.
- `input_breakdown` contains integer tokens and USD costs for each input category;
  `output_cost_usd` exposes the output subtotal. Breakdown costs retain six decimal
  places, while legacy totals retain cents. Small rounding differences are expected.
  Cache savings compare the same priced requests against ordinary-input pricing;
  with unknown cache writes, the displayed savings are the upper estimate.
- `pricing_checked_on`, `pricing_source`, and `pricing_basis` document provenance.
  Update `cost_accounting.py`, its independent pricing fixtures, and this README
  when prices change. No prices are fetched at runtime.

## Hooks

Claude Code hooks that run automatically during sessions. Full hook registry and wiring guide: [`hooks/README.md`](hooks/README.md).

These live in `hooks/` and are wired up via `.claude/settings.local.json`.

### block-google-imagegen.sh

The PreToolUse Google image-generation guard scans only `Bash` invocations.
All other tools, including `Write`, `Edit`, and `MultiEdit`, pass without pattern
scanning. A matching Bash invocation is denied once per session/pattern, then
retries are allowed for 30 minutes through the shared cooldown state. If `jq`
is unavailable, the hook fails open because it cannot identify the tool safely.
Run its 60-case regression suite through a temporary bin symlink with
`bash hooks/test-block-google-imagegen.sh`. To test the deployed entry point, run
`HOOK=/home/claude/bin/block-google-imagegen.sh bash hooks/test-block-google-imagegen.sh`.
The loader resolves the script symlink before finding the cooldown library,
including on systems whose `readlink` lacks `-f`.

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
