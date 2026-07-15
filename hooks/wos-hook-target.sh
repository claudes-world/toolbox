# wos-hook-target.sh — resolve where a lifecycle hook should send its
# notification, and which bot speaks for it (world-os#247 hook-routing
# standard, companion to the group-onboarding + per-group-error-routing
# design, 2026-07-07/08).
#
# Source this the same way hooks already source common.sh:
#   . "$(dirname "$(readlink -f "$0")")/wos-hook-target.sh"
#
# Two entry points:
#
#   wos_hook_bot_token        Token-only resolution. Use this from hooks that
#                             must reply into the INBOUND MESSAGE's own chat
#                             (typing-hook, voice-hook derive chat_id from the
#                             message content itself, not from "home").
#                             Precedence: $WOS_BOT_ENV file (new standard) ->
#                             $TELEGRAM_STATE_DIR/.env (existing per-agent
#                             convention already used by voice-hook) -> legacy
#                             default (/home/claude/.claude/channels/telegram/.env,
#                             the OLD @claude_do_bot channel-plugin token).
#
#   wos_hook_target           Full chat+thread+token resolution for AGENT
#                             lifecycle hooks (SessionStart/PreCompact/
#                             PostCompact/Actions-menu). Sets globals
#                             TELEGRAM_CHAT_ID, WOS_HOOK_THREAD_ID, BOTTOKEN,
#                             WOS_HOOK_SOURCE ("env" | "fallback-dm").
#                             $WOS_HOME_CHAT_ID present -> use it (+
#                             $WOS_HOME_THREAD_ID, may be empty) and resolve
#                             the token the same way wos_hook_bot_token does.
#                             $WOS_HOME_CHAT_ID absent (today: every session
#                             NOT launched by lane_provisioner.py, e.g. the
#                             orchestrator's own interactive session) -> fall
#                             back to Liam's DM via claude-dobot. This is the
#                             approved 2026-07-07 design ("orchestrator's
#                             home = its DM... via the absent-env-var
#                             fallback").
#
# WOS_HOOK_TARGET_DM_CHAT_ID_OVERRIDE / WOS_HOOK_TARGET_DM_BOT_ENV_OVERRIDE
# exist ONLY so tests can point the fallback at fixtures instead of the real
# Liam DM / real secrets file. Production code must never set them.

wos_hook_bot_token() {
  local env_file=""
  if [ -n "${WOS_BOT_ENV:-}" ] && [ -f "$WOS_BOT_ENV" ]; then
    env_file="$WOS_BOT_ENV"
  elif [ -n "${TELEGRAM_STATE_DIR:-}" ] && [ -f "$TELEGRAM_STATE_DIR/.env" ]; then
    env_file="$TELEGRAM_STATE_DIR/.env"
  else
    env_file="/home/claude/.claude/channels/telegram/.env"
  fi
  grep '^TELEGRAM_BOT_TOKEN=' "$env_file" 2>/dev/null | head -1 | cut -d= -f2-
}

wos_hook_target() {
  local dm_chat_id="${WOS_HOOK_TARGET_DM_CHAT_ID_OVERRIDE:-1676859445}"
  local dm_bot_env="${WOS_HOOK_TARGET_DM_BOT_ENV_OVERRIDE:-/srv/world/secrets/agents/claude-dobot/.env}"

  if [ -n "${WOS_HOME_CHAT_ID:-}" ]; then
    TELEGRAM_CHAT_ID="$WOS_HOME_CHAT_ID"
    WOS_HOOK_THREAD_ID="${WOS_HOME_THREAD_ID:-}"
    BOTTOKEN="$(wos_hook_bot_token)"
    WOS_HOOK_SOURCE="env"
  else
    TELEGRAM_CHAT_ID="$dm_chat_id"
    WOS_HOOK_THREAD_ID=""
    BOTTOKEN="$(WOS_BOT_ENV="$dm_bot_env" TELEGRAM_STATE_DIR="" wos_hook_bot_token)"
    WOS_HOOK_SOURCE="fallback-dm"
  fi
}
