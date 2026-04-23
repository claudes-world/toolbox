#!/usr/bin/env bash
# Smoke test: otel_cli.py imports + setup + shutdown with no-op (empty endpoint)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="" /home/claude/venvs/transcribe/bin/python3 -c "
import sys; sys.path.insert(0, '$SCRIPT_DIR/../lib')
import otel_cli
otel_cli.setup('test-tool')
tracer = otel_cli.get_tracer('test')
with tracer.start_as_current_span('test.run'):
    pass
otel_cli.shutdown()
print('PASS: otel_cli setup + shutdown OK')
"
