# Shared config for Telegram hooks
# Source this at the top of hook scripts: . "$(dirname "$0")/common.sh"
#
# Reads BOTTOKEN and TELEGRAM_CHAT_ID with this priority:
# 1. .claude/.active-chat (written by typing-hook per session, project-scoped)
# 2. TELEGRAM_CHAT_ID env var (from settings.local.json env block)
# 3. ~/.secrets/telegram.env (global fallback)
# 4. First allowFrom entry in access.json (last resort)

# Bot token
BOTTOKEN=$(grep TELEGRAM_BOT_TOKEN /home/claude/.claude/channels/telegram/.env 2>/dev/null | cut -d= -f2)

# Chat ID — project-scoped active chat first
# Hooks receive cwd in their JSON input. Caller should extract it before sourcing.
# Fall back to pwd if not set.
HOOK_CWD="${HOOK_CWD:-$(pwd)}"
ACTIVE_CHAT_FILE="${HOOK_CWD}/.claude/.active-chat"

TELEGRAM_CHAT_ID=""

# 1. Project-scoped active chat (written by typing-hook)
if [ -f "$ACTIVE_CHAT_FILE" ]; then
  TELEGRAM_CHAT_ID=$(cat "$ACTIVE_CHAT_FILE" 2>/dev/null | tr -d '[:space:]')
fi

# 2. Env var (from settings.local.json)
if [ -z "$TELEGRAM_CHAT_ID" ] && [ -n "${TELEGRAM_CHAT_ID_ENV:-}" ]; then
  TELEGRAM_CHAT_ID="$TELEGRAM_CHAT_ID_ENV"
fi

# 3. Global secrets file
if [ -z "$TELEGRAM_CHAT_ID" ]; then
  TELEGRAM_CHAT_ID=$(grep TELEGRAM_CHAT_ID /home/claude/.secrets/telegram.env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')
fi

# 4. First allowed user from access.json
if [ -z "$TELEGRAM_CHAT_ID" ]; then
  TELEGRAM_CHAT_ID=$(jq -r '.allowFrom[0] // empty' /home/claude/.claude/channels/telegram/access.json 2>/dev/null || true)
fi
