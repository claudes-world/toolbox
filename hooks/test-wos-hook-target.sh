#!/bin/bash
# test-wos-hook-target.sh
#
# Regression test for hooks/wos-hook-target.sh (world-os#247 hook-routing
# standard). Run before shipping any edit to the resolver:
#
#   ./hooks/test-wos-hook-target.sh
#
# Exit 0 = all cases resolved as expected. Exit 1 = at least one case wrong.

set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMPDIR_TEST="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_TEST"' EXIT

pass=0
fail=0
check() {
  # check <label> <got> <want>
  if [ "$2" = "$3" ]; then
    pass=$((pass + 1))
  else
    fail=$((fail + 1))
    echo "FAIL: $1 — got '$2' want '$3'"
  fi
}

# ----- fixtures: three distinct bot-env files so precedence is unambiguous -----
mkdir -p "$TMPDIR_TEST/bot-env" "$TMPDIR_TEST/state-dir" "$TMPDIR_TEST/legacy"
echo "TELEGRAM_BOT_TOKEN=token-from-wos-bot-env" > "$TMPDIR_TEST/bot-env/.env"
echo "TELEGRAM_BOT_TOKEN=token-from-telegram-state-dir" > "$TMPDIR_TEST/state-dir/.env"
echo "TELEGRAM_BOT_TOKEN=token-from-legacy-default" > "$TMPDIR_TEST/legacy/.env"
echo "TELEGRAM_BOT_TOKEN=token-from-dm-fallback" > "$TMPDIR_TEST/bot-env/dm.env"

# --- wos_hook_bot_token precedence ---
(
  . "$HERE/wos-hook-target.sh"
  unset WOS_BOT_ENV TELEGRAM_STATE_DIR
  # Case A: only WOS_BOT_ENV set -> wins.
  WOS_BOT_ENV="$TMPDIR_TEST/bot-env/.env" TELEGRAM_STATE_DIR="$TMPDIR_TEST/state-dir" \
    got=$(WOS_BOT_ENV="$TMPDIR_TEST/bot-env/.env" TELEGRAM_STATE_DIR="$TMPDIR_TEST/state-dir" wos_hook_bot_token)
  echo "A|$got"
  # Case B: WOS_BOT_ENV unset, TELEGRAM_STATE_DIR set -> state-dir wins.
  got=$(unset WOS_BOT_ENV; TELEGRAM_STATE_DIR="$TMPDIR_TEST/state-dir" wos_hook_bot_token)
  echo "B|$got"
  # Case C: neither set -> legacy default path (won't exist in test sandbox,
  # so token resolves empty — proves the function doesn't crash and doesn't
  # silently pick up a stray file).
  got=$(unset WOS_BOT_ENV TELEGRAM_STATE_DIR; wos_hook_bot_token)
  echo "C|$got"
) > "$TMPDIR_TEST/token_cases.out"

check "wos_hook_bot_token: WOS_BOT_ENV wins" \
  "$(grep '^A|' "$TMPDIR_TEST/token_cases.out" | cut -d'|' -f2)" \
  "token-from-wos-bot-env"
check "wos_hook_bot_token: TELEGRAM_STATE_DIR fallback" \
  "$(grep '^B|' "$TMPDIR_TEST/token_cases.out" | cut -d'|' -f2)" \
  "token-from-telegram-state-dir"
check "wos_hook_bot_token: neither set -> empty (legacy default absent here)" \
  "$(grep '^C|' "$TMPDIR_TEST/token_cases.out" | cut -d'|' -f2)" \
  ""

# --- wos_hook_target: env vars present ---
(
  . "$HERE/wos-hook-target.sh"
  export WOS_HOME_CHAT_ID="-100555"
  export WOS_HOME_THREAD_ID="42"
  export WOS_BOT_ENV="$TMPDIR_TEST/bot-env/.env"
  wos_hook_target
  echo "CHAT|$TELEGRAM_CHAT_ID"
  echo "THREAD|$WOS_HOOK_THREAD_ID"
  echo "TOKEN|$BOTTOKEN"
  echo "SOURCE|$WOS_HOOK_SOURCE"
) > "$TMPDIR_TEST/env_case.out"

check "wos_hook_target(env): chat_id" "$(grep '^CHAT|' "$TMPDIR_TEST/env_case.out" | cut -d'|' -f2)" "-100555"
check "wos_hook_target(env): thread_id" "$(grep '^THREAD|' "$TMPDIR_TEST/env_case.out" | cut -d'|' -f2)" "42"
check "wos_hook_target(env): token" "$(grep '^TOKEN|' "$TMPDIR_TEST/env_case.out" | cut -d'|' -f2)" "token-from-wos-bot-env"
check "wos_hook_target(env): source" "$(grep '^SOURCE|' "$TMPDIR_TEST/env_case.out" | cut -d'|' -f2)" "env"

# --- wos_hook_target: no identity env vars -> DM fallback (overridden paths for the test) ---
(
  . "$HERE/wos-hook-target.sh"
  unset WOS_HOME_CHAT_ID WOS_HOME_THREAD_ID WOS_BOT_ENV
  export WOS_HOOK_TARGET_DM_CHAT_ID_OVERRIDE="1676859445"
  export WOS_HOOK_TARGET_DM_BOT_ENV_OVERRIDE="$TMPDIR_TEST/bot-env/dm.env"
  wos_hook_target
  echo "CHAT|$TELEGRAM_CHAT_ID"
  echo "THREAD|$WOS_HOOK_THREAD_ID"
  echo "TOKEN|$BOTTOKEN"
  echo "SOURCE|$WOS_HOOK_SOURCE"
) > "$TMPDIR_TEST/dm_case.out"

check "wos_hook_target(fallback): chat_id is Liam's DM" "$(grep '^CHAT|' "$TMPDIR_TEST/dm_case.out" | cut -d'|' -f2)" "1676859445"
check "wos_hook_target(fallback): thread_id empty" "$(grep '^THREAD|' "$TMPDIR_TEST/dm_case.out" | cut -d'|' -f2)" ""
check "wos_hook_target(fallback): token from claude-dobot env" "$(grep '^TOKEN|' "$TMPDIR_TEST/dm_case.out" | cut -d'|' -f2)" "token-from-dm-fallback"
check "wos_hook_target(fallback): source" "$(grep '^SOURCE|' "$TMPDIR_TEST/dm_case.out" | cut -d'|' -f2)" "fallback-dm"

total=$((pass + fail))
echo
echo "${pass} of ${total} cases passed (${fail} failed)"
[ "$fail" -gt 0 ] && exit 1
exit 0
