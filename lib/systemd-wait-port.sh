#!/usr/bin/env bash
# Wait for a TCP port to be listening before ExecStartPost exits.
# Usage: systemd-wait-port.sh <port> [max_wait_secs]
# Exit 0 = port up; exit 1 = timed out (systemd marks unit failed).
set -euo pipefail

PORT="${1:?Usage: systemd-wait-port.sh <port> [max_wait_secs]}"
MAX="${2:-10}"

for ((i=1; i<=MAX; i++)); do
    if ss -tln | grep -Fq ":${PORT} "; then
        exit 0
    fi
    sleep 1
done

echo "systemd-wait-port: port ${PORT} not up after ${MAX}s" >&2
exit 1
