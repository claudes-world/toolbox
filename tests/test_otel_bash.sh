#!/usr/bin/env bash
# Smoke test: otel.sh sourcing + otel_start_span/otel_end_span don't crash
# even when Alloy is unreachable (OTLP blocked by firewall or collector down)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/otel.sh"
otel_start_span "test-tool"
trap 'otel_end_span $?' EXIT
echo "PASS: otel.sh sourced and span started"
exit 0
