#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_USER="${USER:-$(id -un)}"

# Pre-flight: Alloy binary must be pre-installed and executable
if ! test -x "$HOME/bin/alloy"; then
    echo "ERROR: ~/bin/alloy not found or not executable." >&2
    echo "Download from: https://github.com/grafana/alloy/releases" >&2
    echo "Then: install -m 0755 alloy-linux-amd64 ~/bin/alloy" >&2
    exit 1
fi

# Pre-flight: linger must be enabled or service won't survive reboot
if ! test -e "/var/lib/systemd/linger/${_USER}"; then
    echo "ERROR: linger not enabled for user '${_USER}'." >&2
    echo "Run: sudo loginctl enable-linger ${_USER}" >&2
    exit 1
fi

# Ensure required dirs exist
mkdir -p "$HOME/.config/alloy"
mkdir -p "$HOME/.local/share/alloy"
mkdir -p "$HOME/.config/systemd/user"

# Idempotent config deploy — preserve user edits if config already exists
if [ ! -e "$HOME/.config/alloy/config.alloy" ]; then
    cp "$SCRIPT_DIR/config.alloy" "$HOME/.config/alloy/config.alloy"
    echo "Deployed config: ~/.config/alloy/config.alloy"
else
    echo "Existing config preserved: ~/.config/alloy/config.alloy"
fi

# Always deploy unit file (idempotent — source is canonical)
cp "$SCRIPT_DIR/systemd/user/alloy.service" "$HOME/.config/systemd/user/alloy.service"

systemctl --user daemon-reload

if systemctl --user is-active --quiet alloy.service; then
    systemctl --user restart alloy.service
    echo "alloy.service restarted."
else
    systemctl --user start alloy.service
    echo "alloy.service started."
fi

# Enable auto-start on boot only after a successful start/restart
systemctl --user enable alloy.service

echo "Tail logs: journalctl --user -u alloy -f"
