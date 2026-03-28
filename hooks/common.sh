# Shared config for Telegram hooks
# Source this at the top of hook scripts: . "$(dirname "$0")/common.sh"

BOTTOKEN=$(grep TELEGRAM_BOT_TOKEN /home/claude/.claude/channels/telegram/.env 2>/dev/null | cut -d= -f2)
TELEGRAM_CHAT_ID=$(grep TELEGRAM_CHAT_ID /home/claude/.secrets/telegram.env 2>/dev/null | cut -d= -f2)

# Fallback: read first allowed user from access.json
if [ -z "$TELEGRAM_CHAT_ID" ]; then
  TELEGRAM_CHAT_ID=$(jq -r '.allowFrom[0] // empty' /home/claude/.claude/channels/telegram/access.json 2>/dev/null || true)
fi
