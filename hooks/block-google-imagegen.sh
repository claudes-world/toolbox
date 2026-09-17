#!/bin/bash
# block-google-imagegen.sh
#
# PreToolUse hook: best-effort text guardrail against accidental use of
# Google's image-generation APIs by our own agents. Under the single-operator
# prompt-injection YAGNI doctrine, this is not a bypass-proof security boundary:
# the API key's spend cap is the hard limit. Egress-level blocking is not used
# because the same Google host serves the comms translation path.
#
# Founder incident 2026-09-02: an agent burned ~$40 through
# generativelanguage.googleapis.com to the monthly cap. Second time in a
# month. The operational rule is to use codex image_gen instead.
#
# FAIL-OPEN CONTRACT (incident 2026-09-03): this hook blocks ONLY on a positive
# match against the banned-pattern list. Every internal error — unreadable
# stdin, unparseable JSON, a tool_input whose shape we do not recognise — warns
# on stderr and exits 0. An earlier revision failed CLOSED and bricked a lane:
# `input="$(</dev/stdin)"` re-opens fd 0 *by path*, which fails with ENXIO when
# Claude Code hands the hook a socket, and the resulting parse failure denied
# every Bash/Write/Edit call in that session. Read stdin with `cat`, which reads
# the descriptor directly and works on sockets and pipes alike.
#
# COOLDOWN CONTRACT (Liam, TapBack 2026-09-16): a match denies ONCE per
# (session, pattern) pair, with a reason that says what to do instead. The same
# pair is then allowed through for thirty minutes, so an agent that has read the
# reason can rerun and proceed. See hooks/lib/guard-cooldown.sh.
#
# BASH-ONLY CONTRACT (Liam, TapBack 2026-09-17, PL-B1): only Bash
# invocations are scanned. Every other tool is allowed, regardless of its
# content or destination; mentioning a pattern in a file is not an invocation.
# Without a JSON parser we cannot establish tool identity, so fail open.
#
# Exit 2 + stderr = PreToolUse block (per Claude Code hook contract).
# Exit 0 = allow (default, for everything that doesn't positively match).

set -u

LOG_FILE="${HOME:-/tmp}/.claude/hook-blocks.log"
GUARD_NAME="block-google-imagegen"

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || HOOK_DIR=""
if [ -n "$HOOK_DIR" ] && [ -r "$HOOK_DIR/lib/guard-cooldown.sh" ]; then
  # shellcheck source=lib/guard-cooldown.sh
  . "$HOOK_DIR/lib/guard-cooldown.sh"
fi
if ! command -v wos_cooldown_active >/dev/null 2>&1; then
  # The library is the cooldown, not the ban. Without it the hook keeps its
  # previous deny-every-time behaviour rather than failing the tool call.
  wos_cooldown_active() { return 1; }
  wos_cooldown_record() { return 0; }
fi

# Banned patterns, matched case-insensitively. Order = first match wins.
PATTERNS=(
  'generativelanguage\.googleapis\.com'
  'aiplatform\.googleapis\.com.*imagen'
  'imagen.*aiplatform\.googleapis\.com'
  'GOOGLE_AI_API_KEY'
  'google-ai\.env'
  'imagen-[a-zA-Z0-9._-]*'
  'gemini-[a-zA-Z0-9._-]*-image'
  'nano.?banana'
)

allow() {
  # allow <reason> — every internal error path lands here.
  echo "WARNING: block-google-imagegen: $1; allowing the tool call (fail-open)" >&2
  exit 0
}

block() {
  # block <tool_name> <pattern> — deny once, then allow the rerun for the
  # length of the cooldown window.
  local ts
  if wos_cooldown_active "$GUARD_NAME" "$session_id" "$2"; then
    echo "NOTE: $GUARD_NAME: '$2' matched again within the cooldown window; allowing the rerun" >&2
    exit 0
  fi
  ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || echo unknown)"
  mkdir -p "${LOG_FILE%/*}" 2>/dev/null
  printf '%s\ttool=%s\tpattern=%s\n' "$ts" "$1" "$2" >> "$LOG_FILE" 2>/dev/null
  wos_cooldown_record "$GUARD_NAME" "$session_id" "$2" "$1"
  cat >&2 <<EOF
BLOCKED by $GUARD_NAME: this tool call matches a banned Google image-generation
pattern. Founder rule 2026-09-02: an agent burned ~\$40 through Google's image
API to the monthly cap, twice in one month, and the key on this box is revoked.

Do this instead: generate images with codex image_gen, not a Google image API.

If you already meant to proceed — you are writing about the incident, naming the
pattern in a test, or auditing the guard — rerun this exact command and it will
go through. This pattern will not fire again in this session for 30 minutes.
EOF
  exit 2
}

# WOS2-909: Bash commands are matched on their INVOCATION shape, not on
# whatever text they happen to contain. A heredoc report that mentions the
# Imagen spend, or an echo of a postmortem line, is prose — not a call to any
# API. Three passes reduce a command to only the parts that could actually
# invoke something, before the PATTERNS list ever sees it:
#   1. drop the body of any heredoc whose delimiter is QUOTED (<<'EOF' /
#      <<"EOF") — quoting means no expansion, which in this codebase always
#      means literal text being written somewhere (a report, a doc), never
#      code being executed. An unquoted heredoc (<<EOF) is left alone.
#   2. drop shell comments (# to end of line).
#   3. drop any ; / && / || / | separated segment whose leading command is a
#      pure text sink (echo, printf, cat, tee, ...) rather than a network
#      client, CLI, or interpreter — that is the "file-writing context whose
#      content is prose" case (`cat <<... > file`, `tee file <<<"..."`,
#      `echo "..." >> file`).
# A real invocation — curl/wget hitting the host, an agy/gemini CLI call, a
# python/node one-liner importing the genai client — keeps its leading
# command (curl, wget, agy, gemini, python, python3, node, ...) and is
# matched exactly as before.
PROSE_SINK_CMDS='^(echo|printf|cat|tee|true|:|read|yes)$'

strip_quoted_heredocs() {
  local cmd="$1" out="" line delim="" in_heredoc=0 strip_leading=0 check_line
  while IFS= read -r line || [ -n "$line" ]; do
    if [ "$in_heredoc" = 1 ]; then
      check_line="$line"
      if [ "$strip_leading" = 1 ]; then
        check_line="${check_line#"${check_line%%[![:space:]]*}"}"
      fi
      if [ "$check_line" = "$delim" ]; then
        in_heredoc=0
      fi
      continue
    fi
    if [[ "$line" =~ \<\<(-)?[[:space:]]*\'([A-Za-z_][A-Za-z0-9_]*)\' ]] \
      || [[ "$line" =~ \<\<(-)?[[:space:]]*\"([A-Za-z_][A-Za-z0-9_]*)\" ]]; then
      delim="${BASH_REMATCH[2]}"
      if [ "${BASH_REMATCH[1]}" = "-" ]; then strip_leading=1; else strip_leading=0; fi
      in_heredoc=1
    fi
    out="${out}${line}"$'\n'
  done <<<"$cmd"
  printf '%s' "$out"
}

strip_line_comments() {
  # A '#' preceded by start-of-line or whitespace starts a shell comment; our
  # patterns never contain '#', so this cannot eat a real match.
  sed -E 's/(^|[[:space:]])#.*$//'
}

bash_invocation_haystack() {
  local cmd="$1" stripped seg rest leading kept=""
  stripped="$(strip_quoted_heredocs "$cmd" | strip_line_comments)"
  stripped="${stripped//;/$'\n'}"
  stripped="${stripped//&&/$'\n'}"
  stripped="${stripped//||/$'\n'}"
  stripped="${stripped//|/$'\n'}"
  while IFS= read -r seg || [ -n "$seg" ]; do
    rest="${seg#"${seg%%[![:space:]]*}"}"
    while [[ "$rest" =~ ^([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*)[[:space:]]+(.*)$ ]] \
      || [[ "$rest" =~ ^(sudo|env|time|nohup|nice|!)[[:space:]]+(.*)$ ]]; do
      rest="${BASH_REMATCH[2]}"
      rest="${rest#"${rest%%[![:space:]]*}"}"
    done
    leading="${rest%%[[:space:]]*}"
    leading="$(printf '%s' "$leading" | tr 'A-Z' 'a-z')"
    if [[ "$leading" =~ $PROSE_SINK_CMDS ]]; then
      continue
    fi
    kept="${kept}${seg}"$'\n'
  done <<<"$stripped"
  printf '%s' "$kept"
}

# Returns 0 and sets $matched_pattern when $1 contains a banned pattern.
matched_pattern=""
GREP_BIN="$(command -v grep 2>/dev/null || true)"
[ -n "$GREP_BIN" ] || { [ -x /bin/grep ] && GREP_BIN=/bin/grep; }
[ -n "$GREP_BIN" ] || { [ -x /usr/bin/grep ] && GREP_BIN=/usr/bin/grep; }

match_banned() {
  local haystack="$1" pat
  [ -n "$haystack" ] || return 1
  [ -n "$GREP_BIN" ] || allow "grep not found"
  for pat in "${PATTERNS[@]}"; do
    if printf '%s' "$haystack" | "$GREP_BIN" -Eqi -- "$pat"; then
      matched_pattern="$pat"
      return 0
    fi
  done
  return 1
}

# Read fd 0 with a bash builtin: it needs no external binary and works when fd 0
# is a socket. Do NOT use $(</dev/stdin) — that opens the path and fails with
# ENXIO on a socket, which is how this hook bricked a lane. `read -d ''` returns
# non-zero at EOF while still populating the variable, so the status is ignored.
input=""
IFS= read -r -d '' input || true
[ -n "$input" ] || allow "empty or unreadable PreToolUse payload on stdin"

command -v jq >/dev/null 2>&1 || allow "jq missing; cannot identify the tool"

parsed="$(printf '%s' "$input" | jq -ce '
  if type != "object"
    or (.tool_name | type) != "string"
    or (.tool_name | length) == 0
    or (.tool_input | type) != "object"
  then error("invalid PreToolUse payload")
  else .
  end
' 2>/dev/null)" || allow "PreToolUse payload is not valid hook JSON"

tool_name="$(printf '%s' "$parsed" | jq -r '.tool_name' 2>/dev/null)" \
  || allow "could not read tool_name"

# Exit before inspecting tool_input or touching cooldown state for other tools.
[ "$tool_name" = "Bash" ] || exit 0

session_id="$(printf '%s' "$parsed" | jq -r '.session_id // "no-session"' 2>/dev/null)" \
  || allow "could not read session_id"
[ -n "$session_id" ] || session_id="no-session"

haystack="$(printf '%s' "$parsed" | jq -er '
  if (.tool_input.command | type) == "string"
  then .tool_input.command else error("missing command") end
' 2>/dev/null)" || allow "Bash tool_input has no command string"
haystack="$(bash_invocation_haystack "$haystack")"

if match_banned "$haystack"; then
  block "$tool_name" "$matched_pattern"
fi

exit 0
