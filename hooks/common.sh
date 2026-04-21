# Shared config for Telegram hooks
# Source this at the top of hook scripts: . "$(dirname "$0")/common.sh"
#
# Sets: BOTTOKEN, TELEGRAM_CHAT_ID
# Caller should set HOOK_CWD before sourcing (from hook JSON .cwd field).
#
# If chat ID can't be resolved, sets HOOK_ERROR with a message that
# should be output as additionalContext so the model can fix it.

# Bot token — use agent's own token when TELEGRAM_STATE_DIR is set
_TOKEN_ENV="${TELEGRAM_STATE_DIR:+$TELEGRAM_STATE_DIR/.env}"
_TOKEN_ENV="${_TOKEN_ENV:-/home/claude/.claude/channels/telegram/.env}"
BOTTOKEN=$(grep TELEGRAM_BOT_TOKEN "$_TOKEN_ENV" 2>/dev/null | cut -d= -f2)

# Chat ID — project-scoped active chat first
HOOK_CWD="${HOOK_CWD:-$(pwd)}"
ACTIVE_CHAT_FILE="${HOOK_CWD}/.claude/.active-chat"

TELEGRAM_CHAT_ID=""
HOOK_ERROR=""

# 1. Project-scoped active chat (written by typing-hook)
if [ -f "$ACTIVE_CHAT_FILE" ]; then
  TELEGRAM_CHAT_ID=$(cat "$ACTIVE_CHAT_FILE" 2>/dev/null | tr -d '[:space:]')
fi

# 2. Env var (from settings.local.json env block)
if [ -z "$TELEGRAM_CHAT_ID" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
  TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID}"
fi

# 3. Global secrets file
if [ -z "$TELEGRAM_CHAT_ID" ]; then
  TELEGRAM_CHAT_ID=$(grep TELEGRAM_CHAT_ID /home/claude/.secrets/telegram.env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')
fi

# 4. First allowed user from access.json
if [ -z "$TELEGRAM_CHAT_ID" ]; then
  TELEGRAM_CHAT_ID=$(jq -r '.allowFrom[0] // empty' /home/claude/.claude/channels/telegram/access.json 2>/dev/null || true)
fi

# Error handling — if still empty, set error message for the model
if [ -z "$TELEGRAM_CHAT_ID" ]; then
  HOOK_ERROR="[Hook error] Could not resolve Telegram chat ID. Tried: ${ACTIVE_CHAT_FILE}, TELEGRAM_CHAT_ID env, ~/.secrets/telegram.env, access.json. The user needs to send a Telegram message first so typing-hook can write .claude/.active-chat, or set TELEGRAM_CHAT_ID in .claude/settings.local.json env block."
fi

# Helper: output error JSON and exit if chat ID is missing
# Call this in hooks that require chat ID: require_chat_id || exit 0
require_chat_id() {
  if [ -n "$HOOK_ERROR" ]; then
    jq -n --arg err "$HOOK_ERROR" '{
      hookSpecificOutput: {
        hookEventName: "UserPromptSubmit",
        additionalContext: $err
      }
    }'
    return 1
  fi
  return 0
}
