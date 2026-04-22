#!/usr/bin/env bash
# Validates the Alloy collector config parses without errors.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$SCRIPT_DIR/../collector/config.alloy"

if ! command -v alloy &>/dev/null && ! test -f "$HOME/bin/alloy"; then
  echo "SKIP: alloy binary not found — skipping config parse test"
  exit 0
fi

ALLOY_BIN="${HOME}/bin/alloy"
if command -v alloy &>/dev/null; then
  ALLOY_BIN="alloy"
fi

echo "==> Checking Alloy config: $CONFIG"
# Use 'validate' (component-graph + type checking) when available; fall back to fmt.
if "$ALLOY_BIN" validate --help &>/dev/null 2>&1; then
  "$ALLOY_BIN" validate "$CONFIG"
else
  "$ALLOY_BIN" fmt --write=false "$CONFIG" > /dev/null
fi
echo "PASS: config.alloy is valid"
