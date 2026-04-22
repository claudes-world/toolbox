#!/usr/bin/env bash
# Validates the Alloy collector config parses without errors.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$SCRIPT_DIR/../collector/config.alloy"

ALLOY_BIN="$HOME/bin/alloy"
if [ ! -x "$ALLOY_BIN" ]; then
  if [ -n "${CI:-}" ]; then
    echo "ERROR: $ALLOY_BIN not found — cannot skip in CI"
    exit 1
  fi
  echo "SKIP: $ALLOY_BIN not found or not executable — install Alloy first"
  exit 77
fi

echo "==> Validating Alloy config: $CONFIG"
"$ALLOY_BIN" validate --stability.level=public-preview "$CONFIG"
echo "PASS: config.alloy is valid (component graph + syntax)"
