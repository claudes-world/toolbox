#!/bin/bash
# test-context-threshold-patterns.sh
#
# Regression test for the 1M-context detection case statement in
# hooks/context-threshold-check.
#
# WHY THIS EXISTS
# ---------------
# 2026-04-19/20: a silent bug in the 1M-context detection was triaged.
# The original case used char-class globs of the form
#   *opus-4-[6-9]*|*sonnet-4-[6-9]*|...
# which only cover single digits 6..9. Bash globs do NOT span 10+ without
# an explicit second alternative, so a hypothetical "claude-opus-4-10"
# (and every model from 4.10 through 4.99) would silently route to the
# default 200k band and false-fire the 80% pre-compact advisory at ~175k
# used tokens (17.5% of their real 1M window).
#
# The fix (toolbox#34) added *opus-4-[1-9][0-9]*|*sonnet-4-[1-9][0-9]* so
# 4.10 through 4.99 stay routed to the 1M band. The Tier 1 swarm reviewer
# flagged that this class of bug is easy to re-introduce whenever a new
# model version lands, so this test pins the behavior across the full
# 4.6 .. 4.99 range plus the [1m]-tag and -1m-suffix fallbacks.
#
# HOW TO USE
# ----------
# Run this before shipping ANY edit to the `case` statement in
# hooks/context-threshold-check:
#
#   ./hooks/test-context-threshold-patterns.sh
#
# Exit 0 = all patterns behave as expected. Exit 1 = at least one
# model string was routed to the wrong context band.
#
# IMPORTANT — KEEP IN SYNC
# ------------------------
# The `classify()` function below MUST be a byte-for-byte copy of the
# case statement in hooks/context-threshold-check. When you edit the hook
# pattern, edit the copy here too, then extend EXPECTED_1M / EXPECTED_200K
# with the new cases you intend to cover. A diverging copy defeats the
# purpose of this regression test.

set -u

# ----- classifier (mirror of hooks/context-threshold-check case) -----
# Returns "1m" if the model string should route to the extended-context
# (1M) band, or "200k" otherwise.
classify() {
  local model="$1"
  case "$model" in
    *opus-4-[6-9]*|*opus-4-[1-9][0-9]*|*sonnet-4-[6-9]*|*sonnet-4-[1-9][0-9]*|*\[1m\]*|*-1m|*-1m-*)
      printf '1m'
      ;;
    *)
      printf '200k'
      ;;
  esac
}

# ----- test corpus -----
# Models that MUST classify as 1m.
EXPECTED_1M=(
  "claude-opus-4-6"
  "claude-opus-4-7"
  "claude-opus-4-10"
  "claude-opus-4-99"
  "claude-sonnet-4-6"
  "claude-sonnet-4-7"
  "claude-sonnet-4-23"
  "claude-opus-4-7[1m]"
  "claude-opus-3-0-1m"
  "some-model-[1m]-test"
  "claude-opus-4-7-foo-1m-bar"
)

# Models that MUST classify as 200k (default context).
EXPECTED_200K=(
  "claude-haiku-4-5-20251001"
  "claude-haiku-4-5"
  "claude-haiku-5-0"
  "claude-opus-4-5"
  "claude-opus-4-5-20260101"
  "claude-opus-3-7"
  "claude-sonnet-3-5"
  "claude-opus"
  "gpt-4o"
)

# ----- runner -----
pass=0
fail=0
total=0
rows=()

check() {
  local model="$1"
  local want="$2"
  local got
  got="$(classify "$model")"
  total=$((total + 1))
  if [ "$got" = "$want" ]; then
    pass=$((pass + 1))
    rows+=("PASS|${model}|${want}|${got}")
  else
    fail=$((fail + 1))
    rows+=("FAIL|${model}|${want}|${got}")
  fi
}

for m in "${EXPECTED_1M[@]}"; do
  check "$m" "1m"
done

for m in "${EXPECTED_200K[@]}"; do
  check "$m" "200k"
done

# ----- report -----
# Column widths: pad model to the longest model string in either array.
max_model_len=5
for m in "${EXPECTED_1M[@]}" "${EXPECTED_200K[@]}"; do
  if [ "${#m}" -gt "$max_model_len" ]; then
    max_model_len=${#m}
  fi
done

printf '%-4s  %-*s  %-8s  %-8s\n' "RES" "$max_model_len" "MODEL" "EXPECT" "ACTUAL"
printf '%-4s  %-*s  %-8s  %-8s\n' "----" "$max_model_len" "-----" "------" "------"
for row in "${rows[@]}"; do
  IFS='|' read -r res model want got <<<"$row"
  printf '%-4s  %-*s  %-8s  %-8s\n' "$res" "$max_model_len" "$model" "$want" "$got"
done

echo
echo "${pass} of ${total} patterns passed (${fail} failed)"

if [ "$fail" -gt 0 ]; then
  exit 1
fi
exit 0
