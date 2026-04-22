#!/usr/bin/env bash
# Validates the Alloy collector config parses without errors.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$SCRIPT_DIR/../collector/config.alloy"

if ! test -x "$HOME/bin/alloy" && ! command -v alloy &>/dev/null; then
  echo "SKIP: alloy binary not found — skipping config parse test"
  exit 0
fi

# Prefer the installed binary (matches what runs in production)
if test -x "$HOME/bin/alloy"; then
  ALLOY_BIN="$HOME/bin/alloy"
else
  ALLOY_BIN="$(command -v alloy)"
fi

echo "==> Checking Alloy config: $CONFIG"
# Validates config syntax via alloy fmt (alloy validate does not exist in 1.x)
"$ALLOY_BIN" fmt --write=false "$CONFIG" > /dev/null
echo "PASS: config.alloy is valid"
