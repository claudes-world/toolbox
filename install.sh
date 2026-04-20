#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Pre-flight: linger must be enabled or user timer won't survive reboot
if ! test -e /var/lib/systemd/linger/claude; then
    echo "ERROR: linger not enabled for user 'claude'." >&2
    echo "Run: sudo loginctl enable-linger claude" >&2
    exit 1
fi

# Create pulse data directory with strict permissions
mkdir -p "$HOME/.world/pulse/snapshots"
chmod 0700 "$HOME/.world/pulse"

# Always copy env.example (idempotent — source never changes)
cp "$SCRIPT_DIR/systemd/user/env.example" "$HOME/.world/pulse/env.example"

# Create env file if missing, then bail so user fills in GH_TOKEN
if [ ! -e "$HOME/.world/pulse/env" ]; then
    install -m 0600 /dev/null "$HOME/.world/pulse/env"
    echo "Edit ~/.world/pulse/env with your GH_TOKEN then re-run install.sh" >&2
    exit 1
fi

# Copy config template only if not already present (preserve user edits)
if [ ! -e "$HOME/.world/pulse/config.yml" ]; then
    cp "$SCRIPT_DIR/systemd/user/config.yml" "$HOME/.world/pulse/config.yml"
fi

# Install systemd unit files (always copy — idempotent since source doesn't change)
mkdir -p "$HOME/.config/systemd/user"
cp "$SCRIPT_DIR/systemd/user/pulse.service" "$HOME/.config/systemd/user/pulse.service"
cp "$SCRIPT_DIR/systemd/user/pulse.timer" "$HOME/.config/systemd/user/pulse.timer"

systemctl --user daemon-reload

# Self-check before enabling timer
if ! pulse --self-check; then
    echo "ERROR: pulse --self-check failed. Fix issues above before enabling the timer." >&2
    exit 1
fi

systemctl --user enable --now pulse.timer

NEXT_FIRE="$(systemctl --user list-timers pulse.timer --no-legend 2>/dev/null | awk '{print $1, $2}' || echo 'unknown')"
echo "pulse.timer enabled. Next fire: ${NEXT_FIRE}. Watch logs: journalctl --user -u pulse.service -f"
