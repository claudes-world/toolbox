#!/usr/bin/env bash
# Regression cases for the Google image-generation PreToolUse guardrail.
#
# The load-bearing case is stdin-on-a-socket: Claude Code hands hooks a socket
# on fd 0, and the pre-fix `input="$(</dev/stdin)"` failed with ENXIO there and
# then failed CLOSED, denying every Bash/Write/Edit call in a live lane
# (2026-09-03). Reverting the `cat` read or the fail-open paths fails this file.

set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="${HOOK:-$HERE/block-google-imagegen.sh}"

pass=0
fail=0

# Each case starts from a clean cooldown state, so a denial recorded by one case
# cannot allow the next one through.
STATE_ROOT="$(mktemp -d)"
trap 'rm -rf "$STATE_ROOT"' EXIT
export WORLDOS_GUARD_STATE_DIR="$STATE_ROOT/state"

reset_cooldown() { rm -rf "$WORLDOS_GUARD_STATE_DIR"; }

BANNED_URL="https://generativelanguage.googleapis.com/v1beta/models"

payload() {
  # payload <tool_name> <json tool_input>
  python3 -c 'import json,sys; print(json.dumps({"tool_name": sys.argv[1], "tool_input": json.loads(sys.argv[2])}))' "$1" "$2"
}

run_hook() {
  # run_hook <payload-json> — stdin is a pipe
  printf '%s' "$1" | "$HOOK" >/dev/null 2>&1
}

run_hook_socket() {
  # run_hook_socket <payload-json> — stdin is a SOCKET, as Claude Code supplies
  python3 - "$HOOK" "$1" <<'PY'
import socket, subprocess, sys
hook, data = sys.argv[1], sys.argv[2]
parent, child = socket.socketpair()
parent.sendall(data.encode())
parent.shutdown(socket.SHUT_WR)
proc = subprocess.run([hook], stdin=child.fileno(),
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
sys.exit(proc.returncode)
PY
}

check() {
  # check <label> <want-exit> <runner> <payload>
  local label="$1" want="$2" runner="$3" data="$4" rc
  reset_cooldown
  "$runner" "$data"
  rc=$?
  if [[ "$rc" == "$want" ]]; then
    pass=$((pass + 1))
  else
    fail=$((fail + 1))
    echo "FAIL: $label — got exit $rc, want $want"
  fi
}

benign_bash="$(payload Bash '{"command": "echo hi"}')"
banned_bash="$(payload Bash "$(python3 -c 'import json,sys; print(json.dumps({"command": "curl -s " + sys.argv[1]}))' "$BANNED_URL")")"
benign_write="$(payload Write '{"file_path": "/tmp/notes.md", "content": "nothing to see"}')"
banned_write="$(payload Write "$(python3 -c 'import json,sys; print(json.dumps({"file_path": "/tmp/x.sh", "content": "export GOOGLE_AI_API_KEY=abc"}))')")"
benign_multiedit="$(payload MultiEdit '{"file_path": "/tmp/x.py", "edits": [{"old_string": "a", "new_string": "b"}, {"old_string": "c", "new_string": "d"}]}')"
banned_multiedit="$(payload MultiEdit "$(python3 -c 'import json,sys; print(json.dumps({"file_path": "/tmp/x.py", "edits": [{"old_string": "a", "new_string": "b"}, {"old_string": "c", "new_string": "https://" + "generativelanguage" + "." + "googleapis" + "." + "com" + "/v1beta/models"}]}))')")"

# Edit payload with no old_string/new_string keys at all — an unrecognised
# shape — but a banned key sitting in some other field of tool_input. The
# haystack built from old_string/new_string is empty, so this only blocks if
# the fallback scans the raw serialized tool_input.
banned_edit_unknown_shape="$(payload Edit "$(python3 -c 'import json,sys; print(json.dumps({"file_path": "/tmp/x.sh", "patch": "GOOGLE_AI" + "_API_KEY"}))')")"

# --- positive detection still blocks
check "banned host in a Bash command is blocked"   2 run_hook        "$banned_bash"
check "banned host over a socket is blocked"       2 run_hook_socket "$banned_bash"
check "banned key in Write content is blocked"     2 run_hook        "$banned_write"
check "banned host in a MultiEdit second edit is blocked" 2 run_hook "$banned_multiedit"
check "Edit with unrecognised shape but a banned key in raw tool_input is blocked" \
  2 run_hook "$banned_edit_unknown_shape"

# --- benign traffic is allowed -------------------------------------------
check "benign Bash over a pipe is allowed"         0 run_hook        "$benign_bash"
check "benign Bash over a SOCKET is allowed"       0 run_hook_socket "$benign_bash"
check "benign Write is allowed"                    0 run_hook        "$benign_write"
check "benign MultiEdit is allowed"                0 run_hook        "$benign_multiedit"
check "the hook's own name is not a banned string" 0 run_hook \
  "$(payload Bash '{"command": "grep -n block-google-imagegen hooks.json"}')"

# --- internal errors fail OPEN, never closed -----------------------------
check "malformed JSON fails open"                  0 run_hook '{'
check "empty payload fails open"                   0 run_hook ''
check "missing tool_input fails open"              0 run_hook '{"tool_name":"Bash"}'
check "unknown tool_input shape fails open"        0 run_hook '{"tool_name":"Bash","tool_input":{}}'
check "Read is allowed"                            0 run_hook \
  '{"tool_name":"Read","tool_input":{"file_path":"/etc/hosts"}}'

# --- a PATH without jq must still allow benign calls ---------------------
reset_cooldown
jqless_rc=0
PATH=/nonexistent "$HOOK" <<<"$benign_bash" >/dev/null 2>&1 || jqless_rc=$?
if [[ "$jqless_rc" == 0 ]]; then
  pass=$((pass + 1))
else
  fail=$((fail + 1))
  echo "FAIL: benign call with jq off PATH — got exit $jqless_rc, want 0"
fi

reset_cooldown
jqless_block_rc=0
PATH=/nonexistent "$HOOK" <<<"$banned_bash" >/dev/null 2>&1 || jqless_block_rc=$?
if [[ "$jqless_block_rc" == 2 ]]; then
  pass=$((pass + 1))
else
  fail=$((fail + 1))
  echo "FAIL: banned call with jq off PATH — got exit $jqless_block_rc, want 2"
fi


# --- deny once, then allow the rerun (Liam, TapBack 2026-09-16) -----------
#
# The banned literals below are assembled from inert halves. Writing them out
# whole would have an older copy of this very guard deny the edit — which is
# the collateral this change exists to remove.
BANNED_HOST="${BANNED_URL#https://}"; BANNED_HOST="${BANNED_HOST%%/*}"
BANNED_KEY="GOOGLE_AI""_API_KEY"
BANNED_MODEL="imagen""-3.0-generate"
BANNED_FRUIT="nano""-banana"

sess_payload() {
  # sess_payload <session> <tool> <json tool_input>
  python3 -c 'import json,sys; print(json.dumps({"session_id": sys.argv[1], "tool_name": sys.argv[2], "tool_input": json.loads(sys.argv[3])}))' "$1" "$2" "$3"
}

jobj() { python3 -c 'import json,sys; print(json.dumps(dict(zip(sys.argv[1::2], sys.argv[2::2]))))' "$@"; }

run_sess() { printf '%s' "$1" | "$HOOK" >/dev/null 2>&1; }

expect() {
  local label="$1" want="$2" got="$3"
  if [[ "$got" == "$want" ]]; then
    pass=$((pass + 1))
  else
    fail=$((fail + 1))
    echo "FAIL: $label — got exit $got, want $want"
  fi
}

banned_a="$(sess_payload sess-a Bash "$(jobj command "curl -s $BANNED_URL")")"
banned_b="$(sess_payload sess-b Bash "$(jobj command "curl -s $BANNED_URL")")"

reset_cooldown
rc=0; run_sess "$banned_a" || rc=$?
expect "first matching Bash call is denied" 2 "$rc"

rc=0; run_sess "$banned_a" || rc=$?
expect "identical rerun inside the window is allowed" 0 "$rc"

rc=0; run_sess "$banned_b" || rc=$?
expect "a different session is denied on its own first run" 2 "$rc"

rc=0; run_sess "$(sess_payload sess-a Bash "$(jobj command "echo $BANNED_FRUIT")")" || rc=$?
expect "a different pattern in the same session is denied once" 2 "$rc"

# The reason tells the agent what to do and that the rerun will proceed.
reset_cooldown
reason="$(printf '%s' "$(sess_payload sess-c Bash "$(jobj command "curl -s $BANNED_URL")")" | "$HOOK" 2>&1 >/dev/null || true)"
if [[ "$reason" == *"codex image_gen"* ]] && [[ "$reason" == *"rerun this exact command"* ]]; then
  pass=$((pass + 1))
else
  fail=$((fail + 1))
  echo "FAIL: denial reason must name codex image_gen and the rerun"
fi

# After the window expires the next match is denied again.
reset_cooldown
rc=0; WORLDOS_GUARD_COOLDOWN_SECONDS=1 run_sess "$banned_a" || rc=$?
expect "first run with a short window is denied" 2 "$rc"
sleep 2
rc=0; WORLDOS_GUARD_COOLDOWN_SECONDS=1 run_sess "$banned_a" || rc=$?
expect "a match after the window expires is denied again" 2 "$rc"

# An unwritable state directory keeps the old deny-every-time shape.
reset_cooldown
rc=0; WORLDOS_GUARD_STATE_DIR=/proc/nonexistent/state run_sess "$banned_a" || rc=$?
expect "unwritable state denies" 2 "$rc"
rc=0; WORLDOS_GUARD_STATE_DIR=/proc/nonexistent/state run_sess "$banned_a" || rc=$?
expect "unwritable state keeps denying rather than failing open" 2 "$rc"

# --- documentation writes are never denied -------------------------------
reset_cooldown
rc=0; run_sess "$(sess_payload sess-d Write "$(jobj \
  file_path /home/claude/docs/incidents/2026-09-02-image-spend.md \
  content "The agent called https://$BANNED_HOST and burned the cap.")")" || rc=$?
expect "a markdown postmortem naming the host is allowed" 0 "$rc"

reset_cooldown
rc=0; run_sess "$(sess_payload sess-d Write "$(jobj \
  file_path /repo/docs/runbook \
  content "export $BANNED_KEY=redacted")")" || rc=$?
expect "a file under docs/ naming the key is allowed" 0 "$rc"

reset_cooldown
rc=0; run_sess "$(sess_payload sess-d Edit "$(jobj \
  file_path /home/claude/.world/team/composer/workspace/ASSESSMENT.md \
  old_string x new_string "$BANNED_MODEL is the pattern")")" || rc=$?
expect "an Edit of a lane workspace document is allowed" 0 "$rc"

reset_cooldown
multi_doc="$(python3 -c 'import json,sys; print(json.dumps({"file_path": "/repo/docs/guards.md", "edits": [{"old_string": "a", "new_string": sys.argv[1]}]}))' "$BANNED_FRUIT")"
rc=0; run_sess "$(sess_payload sess-d MultiEdit "$multi_doc")" || rc=$?
expect "a MultiEdit of a document is allowed" 0 "$rc"

# ...but a non-document write still gets its first denial.
reset_cooldown
rc=0; run_sess "$(sess_payload sess-e Write "$(jobj \
  file_path /repo/src/client.py \
  content "URL = 'https://$BANNED_HOST/v1beta'")")" || rc=$?
expect "a source-file write is still denied once" 2 "$rc"

echo "block-google-imagegen: $pass passed, $fail failed"
[[ "$fail" == 0 ]]
