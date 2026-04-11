#!/bin/bash
# test-worktree-hooks.sh
#
# End-to-end test for the worktree-add-port-init and
# worktree-remove-port-release Claude Code hooks.
#
# Skips cleanly if `port-for` is not installed yet.

set -u

HOOK_DIR="$(cd "$(dirname "$0")" && pwd)"
ADD_HOOK="$HOOK_DIR/worktree-add-port-init"
REMOVE_HOOK="$HOOK_DIR/worktree-remove-port-release"

PASS=0
FAIL=0

fail() {
  printf '  FAIL: %s\n' "$*" >&2
  FAIL=$((FAIL + 1))
}
pass() {
  printf '  ok: %s\n' "$*"
  PASS=$((PASS + 1))
}

if ! command -v jq >/dev/null 2>&1; then
  printf 'jq not installed, cannot run hook tests\n' >&2
  exit 0
fi

if ! command -v port-for >/dev/null 2>&1 && [ ! -x /home/claude/bin/port-for ]; then
  printf 'port-for not found, skipping integration test\n'
  exit 0
fi

TMPROOT="$(mktemp -d -t worktree-hooks-test.XXXXXX)"
trap 'rm -rf "$TMPROOT"' EXIT

# Pick a band/canonical pair that is unlikely to collide with existing
# allocations. test-frontend sub-band = 58600-58899.
# NOTE: under the ADR 0003 Option D amendment (2026-04-11), port-for honors
# the canonical port only if it falls inside the project's assigned sharding
# slot. For test purposes we only assert that the port name appears in the
# hook's allocation context; the numeric port is whatever port-for decides.
CANONICAL_WEB=58701
CANONICAL_SRV=38701

WT_PATH="$TMPROOT/fake-wt"
mkdir -p "$WT_PATH/.world"
cat > "$WT_PATH/.world/ports.yml" <<EOF
repo: hooks-test-repo
purposes:
  - name: hooks-test-web
    band: test-frontend
    canonical: $CANONICAL_WEB
  - name: hooks-test-server
    band: test-backend
    canonical: $CANONICAL_SRV
EOF

# Make sure we start clean in case of a prior aborted run.
port-for --release-worktree "$WT_PATH" >/dev/null 2>&1 || true

printf 'Test 1: non-matching Bash command is a silent no-op\n'
out="$(printf '{"tool_name":"Bash","tool_input":{"command":"ls -la"},"tool_response":{"exit_code":0}}' | "$ADD_HOOK" 2>&1)"
if [ "$out" = "{}" ]; then
  pass "no-op returns empty object"
else
  fail "expected {}, got: $out"
fi

printf 'Test 2: non-Bash tool is a silent no-op\n'
out="$(printf '{"tool_name":"Read","tool_input":{"file_path":"/tmp/x"}}' | "$ADD_HOOK" 2>&1)"
if [ "$out" = "{}" ]; then
  pass "non-Bash no-op"
else
  fail "expected {}, got: $out"
fi

printf 'Test 3: git worktree add with missing .world/ports.yml is a no-op\n'
NO_PORTS_WT="$TMPROOT/no-ports-wt"
mkdir -p "$NO_PORTS_WT"
out="$(jq -n --arg cmd "git worktree add $NO_PORTS_WT main" \
  '{tool_name:"Bash",tool_input:{command:$cmd},tool_response:{exit_code:0}}' \
  | "$ADD_HOOK" 2>&1)"
if [ "$out" = "{}" ]; then
  pass "no .world/ports.yml → silent no-op"
else
  fail "expected {}, got: $out"
fi

printf 'Test 4: git worktree add with ports.yml triggers allocation\n'
out="$(jq -n --arg cmd "git worktree add $WT_PATH main" \
  '{tool_name:"Bash",tool_input:{command:$cmd},tool_response:{exit_code:0}}' \
  | "$ADD_HOOK" 2>&1)"
ctx="$(printf '%s' "$out" | jq -r '.hookSpecificOutput.additionalContext // ""' 2>/dev/null)"
if printf '%s' "$ctx" | grep -q "Ports allocated for worktree $WT_PATH"; then
  pass "allocation context emitted"
else
  fail "expected allocation context, got: $out"
fi
if printf '%s' "$ctx" | grep -qE "hooks-test-web=[0-9]+"; then
  pass "web port included in summary"
else
  fail "web port missing: $ctx"
fi
if [ -f "$WT_PATH/.world/ports.lock" ]; then
  pass "lock file created"
else
  fail "lock file missing at $WT_PATH/.world/ports.lock"
fi
if port-for --list 2>/dev/null | grep -q "hooks-test-web"; then
  pass "global registry updated"
else
  fail "global registry missing hooks-test-web"
fi

printf 'Test 5: git worktree add with -b <branch> <path> form parses correctly\n'
WT_PATH2="$TMPROOT/fake-wt2"
mkdir -p "$WT_PATH2/.world"
cat > "$WT_PATH2/.world/ports.yml" <<EOF
repo: hooks-test-repo-2
purposes:
  - name: hooks-test-web2
    band: test-frontend
    canonical: 58702
EOF
out="$(jq -n --arg cmd "git worktree add -b feature/foo $WT_PATH2 main" \
  '{tool_name:"Bash",tool_input:{command:$cmd},tool_response:{exit_code:0}}' \
  | "$ADD_HOOK" 2>&1)"
ctx="$(printf '%s' "$out" | jq -r '.hookSpecificOutput.additionalContext // ""' 2>/dev/null)"
if printf '%s' "$ctx" | grep -qE "hooks-test-web2=[0-9]+"; then
  pass "-b <branch> <path> form parsed"
else
  fail "expected hooks-test-web2=<port> in: $ctx"
fi

printf 'Test 6: tool failure (non-zero exit_code) is a no-op\n'
WT_PATH3="$TMPROOT/fake-wt3"
mkdir -p "$WT_PATH3/.world"
cat > "$WT_PATH3/.world/ports.yml" <<EOF
repo: hooks-test-repo-3
purposes:
  - name: hooks-test-web3
    band: test-frontend
    canonical: 58703
EOF
out="$(jq -n --arg cmd "git worktree add $WT_PATH3 main" \
  '{tool_name:"Bash",tool_input:{command:$cmd},tool_response:{exit_code:1}}' \
  | "$ADD_HOOK" 2>&1)"
if [ "$out" = "{}" ]; then
  pass "failed tool call is no-op"
else
  fail "expected {}, got: $out"
fi
# Should NOT have allocated anything
if ! port-for --list 2>/dev/null | grep -q "hooks-test-web3"; then
  pass "no allocation on failed tool call"
else
  fail "should not allocate on failed tool call"
fi

printf 'Test 7: git worktree remove releases ports\n'
out="$(jq -n --arg cmd "git worktree remove $WT_PATH" \
  '{tool_name:"Bash",tool_input:{command:$cmd}}' \
  | "$REMOVE_HOOK" 2>&1)"
ctx="$(printf '%s' "$out" | jq -r '.hookSpecificOutput.additionalContext // ""' 2>/dev/null)"
if printf '%s' "$ctx" | grep -q "Released ports for worktree $WT_PATH"; then
  pass "release context emitted"
else
  fail "expected release context, got: $out"
fi
if ! port-for --list 2>/dev/null | grep -q "hooks-test-web$"; then
  pass "registry entry removed"
else
  fail "registry still has hooks-test-web"
fi

printf 'Test 8: git worktree remove --force form parses correctly\n'
out="$(jq -n --arg cmd "git worktree remove --force $WT_PATH2" \
  '{tool_name:"Bash",tool_input:{command:$cmd}}' \
  | "$REMOVE_HOOK" 2>&1)"
ctx="$(printf '%s' "$out" | jq -r '.hookSpecificOutput.additionalContext // ""' 2>/dev/null)"
if printf '%s' "$ctx" | grep -q "Released ports for worktree $WT_PATH2"; then
  pass "--force form parsed + released"
else
  fail "expected release, got: $out"
fi

printf 'Test 9: remove of unrelated bash command is a no-op\n'
out="$(printf '{"tool_name":"Bash","tool_input":{"command":"git status"}}' | "$REMOVE_HOOK" 2>&1)"
if [ "$out" = "{}" ]; then
  pass "unrelated command no-op"
else
  fail "expected {}, got: $out"
fi

printf 'Test 10: idempotent re-run of add hook on already-allocated worktree\n'
# Re-create worktree 3 path so the hook doesn't reject on missing dir
out="$(jq -n --arg cmd "git worktree add $WT_PATH3 main" \
  '{tool_name:"Bash",tool_input:{command:$cmd},tool_response:{exit_code:0}}' \
  | "$ADD_HOOK" 2>&1)"
# First call allocates
out="$(jq -n --arg cmd "git worktree add $WT_PATH3 main" \
  '{tool_name:"Bash",tool_input:{command:$cmd},tool_response:{exit_code:0}}' \
  | "$ADD_HOOK" 2>&1)"
ctx="$(printf '%s' "$out" | jq -r '.hookSpecificOutput.additionalContext // ""' 2>/dev/null)"
if printf '%s' "$ctx" | grep -q "hooks-test-web3"; then
  pass "second init is idempotent and still reports allocation"
else
  fail "idempotent re-run did not emit context: $out"
fi

# Cleanup
port-for --release-worktree "$WT_PATH3" >/dev/null 2>&1 || true
port-for --release-worktree "$WT_PATH" >/dev/null 2>&1 || true
port-for --release-worktree "$WT_PATH2" >/dev/null 2>&1 || true

# Also drop the sticky project slot entries from the global registry so
# re-runs start clean. Option D sharding persists project→slot mapping
# across releases.
if [ -f /home/claude/.world/ports/active.json ] && command -v python3 >/dev/null 2>&1; then
  python3 - <<'PYCLEAN' >/dev/null 2>&1 || true
import json
p = '/home/claude/.world/ports/active.json'
with open(p) as f:
    d = json.load(f)
projects = d.get('__projects__', {})
for k in list(projects.keys()):
    if k.startswith('hooks-test-repo'):
        del projects[k]
with open(p, 'w') as f:
    json.dump(d, f, indent=2)
PYCLEAN
fi

printf '\nResult: %d passed, %d failed\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
exit 0
