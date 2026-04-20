# hooks

Claude Code event-driven scripts. Each hook fires on a specific Claude Code lifecycle event and receives JSON on stdin (where applicable). Hooks live in `hooks/` and are wired via `.claude/settings.json` or `settings.local.json`.

## Hook registry

| Hook Name | Event | Purpose | Status |
|-----------|-------|---------|--------|
| `actions-hook` | `UserPromptSubmit` | Handle Actions keyboard button taps; show one-time reply keyboard, execute action, restore launcher | Active |
| `agent-start-hook` | `SubagentStart` | Notify Telegram and track active agent in JSON state file | Active |
| `agent-stop-hook` | `SubagentStop` | Notify Telegram (threaded) and update agent tracker | Active |
| `compact-hook` | `PreCompact` / `PostCompact` | Notify user on Telegram before and after context compaction | Active |
| `context-threshold-check` | `UserPromptSubmit` | Estimate context utilization from transcript JSONL; nudge pre-compact preparation | Active |
| `launcher-hook` | `SessionStart` | Restore the web_app keyboard launcher; initialize `.active-chat` if absent | Active |
| `quarantine-research` | manual / Bash | Isolated Claude subprocess research pipeline with sanitizer + Haiku review + risk-gated release | Active |
| `safe-research` | manual / Bash | Sandboxed web research via restricted `claude -p`; pipes through sanitizer | Active |
| `sanitize-research` | called by quarantine-research | Scan research output for prompt injection patterns; prepend warnings | Active |
| `share-doc` | manual / Bash | Write markdown to timestamped file in `~/claudes-world/tmp/` and send Telegram deep-link button | Active |
| `typing-hook` | `UserPromptSubmit` | Send typing indicator on every inbound Telegram message; write `.active-chat` | Active |
| `voice-hook` | `UserPromptSubmit` | Auto-transcribe Telegram voice notes; reply with transcription; inject into context | Active |
| `worktree-add-port-init` | `PostToolUse` (Bash) | On `git worktree add`, run `port-for --init` for new worktree | Active |
| `worktree-remove-port-release` | `PostToolUse` (Bash) | On `git worktree remove`, run `port-for --release-worktree` | Active |

## Shared utilities

### `hooks/common.sh`

Source at the top of any hook that needs to send Telegram messages:

```bash
. "$(dirname "$0")/common.sh"
# Sets: BOTTOKEN, TELEGRAM_CHAT_ID (or HOOK_ERROR if unresolvable)
```

**Chat ID priority chain** (first non-empty wins):

1. Project-scoped `.claude/.active-chat` (written by `typing-hook` on each inbound message)
2. `TELEGRAM_CHAT_ID` env var (from `settings.local.json` env block)
3. `~/.secrets/telegram.env` global secrets file
4. First entry in `~/.claude/channels/telegram/access.json` allowlist

If all four fail, `HOOK_ERROR` is set with a diagnostic message. Use `require_chat_id || exit 0` to emit this as `additionalContext` for the model.

### `hooks/lib/launcher-keyboard.sh`

Keyboard state management for the Telegram web_app launcher button. Sourced by `launcher-hook` and `actions-hook`. Handles: keyboard creation, state persistence, and restoration after action execution.

## Hook I/O schema

Hook scripts receive event JSON on stdin (where applicable). Output must be JSON with a `hookSpecificOutput` field:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "Transcription: hello world"
  }
}
```

For hooks that block the event pipeline (`UserPromptSubmit`): exit code 2 cancels the action and surfaces the hook's stderr to the model; exit code 0 continues normally; other non-zero exit codes are treated as non-blocking errors. See [Claude Code hooks docs](https://docs.anthropic.com/en/docs/claude-code/hooks) for the full schema per event type.

## Adding a hook

1. Create the script in `hooks/<hook-name>` with a shebang (`#!/bin/bash` or `#!/path/to/python3`)
2. Make it executable: `chmod +x hooks/<hook-name>`
3. Source `common.sh` if it needs a Telegram chat ID
4. Add a row to the registry table above
5. Wire it in `.claude/settings.json` (or the project's `.claude/settings.local.json`):
   ```json
   {
     "hooks": {
       "UserPromptSubmit": [{
         "hooks": [{
           "type": "command",
           "command": "/home/claude/code/toolbox/hooks/<hook-name>",
           "timeout": 30
         }]
       }]
     }
   }
   ```
6. Test with a real Claude Code session; check stderr for errors
