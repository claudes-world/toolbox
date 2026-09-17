#!/bin/bash
# guard-cooldown.sh — deny-once-then-allow state for PreToolUse guard hooks.
#
# Liam, TapBack 2026-09-16: "Is there a way the hook could block then give a
# warning to the agent about the reason, like 'use ffmpeg on the dev-mac', then
# allow that agent to rerun the command and it won't fire again for 30 minutes?"
#
# The contract every guard implementing this shares:
#
#   1. First match for a (session, pattern) pair denies, and the stderr reason
#      says what to do instead and that a rerun will proceed.
#   2. The denial is recorded as one small file: {lane, session, pattern, tool,
#      timestamp}.
#   3. For COOLDOWN_SECONDS after that first denial, the same (session, pattern)
#      pair is allowed through without firing again. The window runs from the
#      first denial and is not refreshed by later calls.
#   4. After the window expires the next match denies again, once.
#
# The window is keyed on the pattern, not on the exact command text, so an agent
# that rewrites its command between the denial and the rerun still proceeds.
#
# Layout:
#   ${WORLDOS_GUARD_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/worldos/guard-cooldown}
#     /<guard-name>/<key-digest>
#
# Usage:
#   . "${CLAUDE_PLUGIN_ROOT}/hooks/lib/guard-cooldown.sh"
#   if wos_cooldown_active my-guard "$session_id" "$pattern"; then
#     exit 0                                  # inside the window: allow
#   fi
#   wos_cooldown_record my-guard "$session_id" "$pattern" "$tool_name"
#   exit 2                                    # first denial
#
# Every function here is best-effort: an unwritable or unreadable state
# directory degrades to "no cooldown recorded", which keeps the guard's
# pre-existing behaviour rather than opening or closing a new failure mode.

WOS_COOLDOWN_SECONDS="${WORLDOS_GUARD_COOLDOWN_SECONDS:-1800}"

wos_cooldown_dir() {
  # wos_cooldown_dir <guard-name>
  local base
  base="${WORLDOS_GUARD_STATE_DIR:-}"
  if [ -z "$base" ]; then
    base="${XDG_STATE_HOME:-${HOME:-/tmp}/.local/state}/worldos/guard-cooldown"
  fi
  printf '%s/%s' "$base" "$1"
}

wos_cooldown_key() {
  # wos_cooldown_key <session> <pattern> — a filesystem-safe digest.
  local raw="$1|$2" digest=""
  if command -v sha256sum >/dev/null 2>&1; then
    digest="$(printf '%s' "$raw" | sha256sum 2>/dev/null | cut -d' ' -f1)"
  elif command -v shasum >/dev/null 2>&1; then
    digest="$(printf '%s' "$raw" | shasum -a 256 2>/dev/null | cut -d' ' -f1)"
  fi
  if [ -z "$digest" ]; then
    # No hasher available. Hex-encode instead of sanitising, so two different
    # patterns can never collapse onto the same key.
    digest="$(printf '%s' "$raw" | od -An -v -tx1 2>/dev/null | tr -d ' \n')"
  fi
  [ -n "$digest" ] || digest="nodigest"
  printf '%s' "$digest"
}

wos_cooldown_json_escape() {
  # Backslashes and double quotes only: the recorded values are pattern
  # strings, ids and tool names, none of which carry control characters.
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  printf '%s' "$s"
}

wos_cooldown_active() {
  # wos_cooldown_active <guard-name> <session> <pattern>
  # Returns 0 when a denial for this pair is still inside its window.
  local file now recorded age
  file="$(wos_cooldown_dir "$1")/$(wos_cooldown_key "$2" "$3")"
  [ -r "$file" ] || return 1
  now="$(date -u '+%s' 2>/dev/null)" || return 1
  recorded="$(sed -n 's/.*"epoch"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$file" 2>/dev/null | head -1)"
  case "$recorded" in
    ''|*[!0-9]*) return 1 ;;
  esac
  age=$((now - recorded))
  [ "$age" -ge 0 ] || return 1
  [ "$age" -lt "$WOS_COOLDOWN_SECONDS" ]
}

wos_cooldown_record() {
  # wos_cooldown_record <guard-name> <session> <pattern> <tool>
  local dir file now iso lane session pattern tool
  dir="$(wos_cooldown_dir "$1")"
  file="$dir/$(wos_cooldown_key "$2" "$3")"
  now="$(date -u '+%s' 2>/dev/null || echo 0)"
  iso="$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || echo unknown)"
  lane="$(wos_cooldown_json_escape "${WORLDOS_LANE_ID:-${WOS_LANE_ID:-unknown}}")"
  session="$(wos_cooldown_json_escape "$2")"
  pattern="$(wos_cooldown_json_escape "$3")"
  tool="$(wos_cooldown_json_escape "$4")"
  mkdir -p "$dir" 2>/dev/null || return 0
  printf '{"lane":"%s","session":"%s","pattern":"%s","tool":"%s","denied_at":"%s","epoch":%s}\n' \
    "$lane" "$session" "$pattern" "$tool" "$iso" "$now" > "$file" 2>/dev/null || true
  return 0
}
