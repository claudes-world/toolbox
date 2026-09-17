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
# DOCUMENTATION CARVE-OUT (same ruling): Write/Edit/MultiEdit/NotebookEdit of a
# document — a path under docs/, any *.md, or a lane workspace — is never
# denied. Writing a postmortem that names the incident is not the act the hook
# exists to stop, and 36 of the 123 denials logged on do-box between 2026-09-02
# and 2026-09-16 were exactly that.
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

# The cooldown key needs a session. Read it without jq so the degraded path
# below gets one too; an absent session falls back to a per-process key, which
# simply means the cooldown does not apply.
session_id="$(printf '%s' "$input" \
  | "${GREP_BIN:-grep}" -o '"session_id"[[:space:]]*:[[:space:]]*"[^"]*"' 2>/dev/null \
  | head -1 | sed -E 's/.*"([^"]*)"$/\1/')"
[ -n "$session_id" ] || session_id="no-session"

if ! command -v jq >/dev/null 2>&1; then
  # Degraded mode: no field scoping available, so scan the raw payload. The
  # patterns are specific enough that hook metadata (cwd, transcript path)
  # does not match them.
  echo "WARNING: block-google-imagegen: jq missing; scanning the raw payload" >&2
  if match_banned "$input"; then
    block degraded "$matched_pattern"
  fi
  exit 0
fi

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

# Extract only the content-bearing fields for tools with known schemas. Read
# cannot cause an API call, so it has no searchable content. Unknown tools
# retain coverage by scanning tool_input, never hook metadata.
# Documentation carve-out. A document that names a banned pattern is prose
# about the rule, not an API call: postmortems, tickets, ADRs, test fixtures
# written as markdown, and the lane workspaces those land in. Bash keeps its
# denials, because a Bash command naming the pattern can actually spend money.
is_documentation_write() {
  local path
  path="$(printf '%s' "$parsed" | jq -r '.tool_input.file_path // .tool_input.notebook_path // ""' 2>/dev/null)"
  [ -n "$path" ] || return 1
  case "$path" in
    *.md|*.mdx|*.markdown|*.txt|*.rst) return 0 ;;
    */docs/*|docs/*) return 0 ;;
    */.world/*/workspace/*|"${HOME:-/nonexistent}"/.world/*) return 0 ;;
  esac
  return 1
}

case "$tool_name" in
  Write|Edit|MultiEdit|NotebookEdit)
    if is_documentation_write; then
      echo "NOTE: $GUARD_NAME: documentation write; the pattern list is data here, not an API call" >&2
      exit 0
    fi
    ;;
esac

case "$tool_name" in
  Bash)
    haystack="$(printf '%s' "$parsed" | jq -er '
      if (.tool_input.command | type) == "string"
      then .tool_input.command else error("missing command") end
    ' 2>/dev/null)" || allow "Bash tool_input has no command string"
    ;;
  Edit)
    haystack="$(printf '%s' "$parsed" | jq -er '
      [.tool_input.old_string?, .tool_input.new_string?]
      | map(select(type == "string")) | join("\n")
    ' 2>/dev/null)" || allow "Edit tool_input has no edit strings"
    if [ -z "$haystack" ]; then
      echo "WARNING: block-google-imagegen: Edit tool_input has an unrecognised shape (no old_string/new_string); scanning the raw tool_input" >&2
      haystack="$(printf '%s' "$parsed" | jq -c '.tool_input' 2>/dev/null)" \
        || allow "could not serialise tool_input for Edit fallback"
    fi
    ;;
  Write)
    haystack="$(printf '%s' "$parsed" | jq -er '
      if (.tool_input.content | type) == "string"
      then .tool_input.content else error("missing content") end
    ' 2>/dev/null)" || allow "Write tool_input has no content string"
    ;;
  MultiEdit)
    haystack="$(printf '%s' "$parsed" | jq -er '
      if (.tool_input.edits | type) == "array"
      then [.tool_input.edits[]? | (.old_string?, .new_string?)]
           | map(select(type == "string")) | join("\n")
      else error("missing edits") end
    ' 2>/dev/null)" || allow "MultiEdit tool_input has no edits array"
    if [ -z "$haystack" ]; then
      echo "WARNING: block-google-imagegen: MultiEdit tool_input has an unrecognised shape (no edits[].old_string/new_string); scanning the raw tool_input" >&2
      haystack="$(printf '%s' "$parsed" | jq -c '.tool_input' 2>/dev/null)" \
        || allow "could not serialise tool_input for MultiEdit fallback"
    fi
    ;;
  NotebookEdit)
    haystack="$(printf '%s' "$parsed" | jq -er '
      if (.tool_input.new_source | type) == "string"
      then .tool_input.new_source else error("missing new_source") end
    ' 2>/dev/null)" || allow "NotebookEdit tool_input has no new_source string"
    ;;
  Read)
    haystack=""
    ;;
  *)
    haystack="$(printf '%s' "$parsed" | jq -c '.tool_input' 2>/dev/null)" \
      || allow "could not serialise tool_input for $tool_name"
    ;;
esac

if match_banned "$haystack"; then
  block "$tool_name" "$matched_pattern"
fi

exit 0
