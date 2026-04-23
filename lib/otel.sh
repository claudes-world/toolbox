#!/usr/bin/env bash
# otel.sh — minimal OTEL tracing for bash CLI tools
# Source this file, then call otel_start_span at the top of your script
# and set a trap to otel_end_span at the bottom.
#
# Usage:
#   OTEL_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../lib/otel.sh"
#   [ -f "$OTEL_LIB" ] && source "$OTEL_LIB" && otel_start_span "${0##*/}" || true
#   trap 'otel_end_span $?' EXIT
#
# OTEL instrumentation NEVER breaks the tool — all errors are suppressed with || true.
# When Alloy/collector is unreachable, the background curl fails silently.

_OTEL_SERVICE_NAME=""
_OTEL_START_NS=""
_OTEL_TRACE_ID=""
_OTEL_SPAN_ID=""

otel_start_span() {
  local service_name="${1:-${0##*/}}"
  _OTEL_SERVICE_NAME="$service_name"
  _OTEL_START_NS=$(date +%s%N)
  # Generate 16-byte trace ID and 8-byte span ID as hex
  _OTEL_TRACE_ID=$(cat /proc/sys/kernel/random/uuid 2>/dev/null | tr -d '-' || od -An -tx1 -N16 /dev/urandom | tr -d ' \n')
  _OTEL_SPAN_ID=$(od -An -tx1 -N8 /dev/urandom | tr -d ' \n')
}

otel_end_span() {
  [ -z "$_OTEL_START_NS" ] && return 0
  local exit_code="${1:-0}"
  local end_ns
  end_ns=$(date +%s%N)
  local caller_agent="${AGENT_NAME:-cli-direct}"
  # JSON-escape double-quotes and backslashes in string values
  local safe_svc="${_OTEL_SERVICE_NAME//\\/\\\\}"
  safe_svc="${safe_svc//\"/\\\"}"
  local safe_agent="${caller_agent//\\/\\\\}"
  safe_agent="${safe_agent//\"/\\\"}"
  local status_code=0  # OK
  local status_msg=""
  if [ "$exit_code" -ne 0 ]; then
    status_code=2  # ERROR
    status_msg="exited with code $exit_code"
  fi

  # Build minimal OTLP JSON and POST to Alloy — fire-and-forget, never blocks the tool
  local payload
  payload=$(printf '{
    "resourceSpans": [{
      "resource": {
        "attributes": [
          {"key": "service.name", "value": {"stringValue": "%s"}},
          {"key": "caller.agent", "value": {"stringValue": "%s"}}
        ]
      },
      "scopeSpans": [{
        "scope": {"name": "otel.sh"},
        "spans": [{
          "traceId": "%s",
          "spanId": "%s",
          "name": "%s",
          "kind": 1,
          "startTimeUnixNano": "%s",
          "endTimeUnixNano": "%s",
          "attributes": [
            {"key": "process.exit_code", "value": {"intValue": %d}}
          ],
          "status": {"code": %d, "message": "%s"}
        }]
      }]
    }]
  }' \
    "$safe_svc" \
    "$safe_agent" \
    "$_OTEL_TRACE_ID" \
    "$_OTEL_SPAN_ID" \
    "$safe_svc" \
    "$_OTEL_START_NS" \
    "$end_ns" \
    "$exit_code" \
    "$status_code" \
    "$status_msg")

  # Background curl — || true ensures failure never propagates
  curl -s --max-time 1 \
    -H 'Content-Type: application/json' \
    -d "$payload" \
    'http://localhost:4318/v1/traces' \
    >/dev/null 2>&1 &
  # Don't wait on the background curl — let the tool exit cleanly
}
