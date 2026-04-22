#!/usr/bin/env bash
# Validates the Alloy collector config parses without errors.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$SCRIPT_DIR/../collector/config.alloy"

ALLOY_BIN="$HOME/bin/alloy"
if [ ! -x "$ALLOY_BIN" ]; then
  echo "SKIP: $ALLOY_BIN not found or not executable — install Alloy first"
  exit 0
fi

echo "==> Checking Alloy config: $CONFIG"
# Validates config syntax via alloy fmt (alloy validate does not exist in 1.x)
"$ALLOY_BIN" fmt --write=false "$CONFIG" > /dev/null
echo "PASS: config.alloy is valid"
