# Grafana Alloy — OTEL Unified Collector

Systemd user service that receives OTLP signals (traces, metrics, logs) from
pulse, dobot-server, inbox, and CPC, and writes them to a local file sink.
Cloud forwarding is wired in a later phase.

## Prerequisites

1. **Alloy binary** installed at `~/bin/alloy`:
   ```bash
   # Download from https://github.com/grafana/alloy/releases
   # Example (linux/amd64):
   curl -Lo /tmp/alloy.gz https://github.com/grafana/alloy/releases/download/v1.7.4/alloy-linux-amd64.gz
   gunzip /tmp/alloy.gz
   mkdir -p ~/bin
   install -m 0755 /tmp/alloy ~/bin/alloy
   ```

2. **Linger enabled** (service survives user logout):
   ```bash
   sudo loginctl enable-linger "$USER"
   ```

## Install

```bash
bash ~/code/toolbox/collector/install.sh
```

The script is idempotent. Re-running it deploys any updated unit file while
preserving your `~/.config/alloy/config.alloy` if it already exists.

> **Note:** The service unit passes `--stability.level=public-preview` to
> `alloy run`. This flag is required because `otelcol.exporter.file` is
> classified as public-preview in Alloy v1.15.1+. Without it, Alloy exits
> immediately. If you construct the `alloy run` command manually (e.g. for
> debugging), include this flag or the exporter will not load.

## Service Management

```bash
# Status
systemctl --user status alloy.service

# Stop / Start
systemctl --user stop alloy.service
systemctl --user start alloy.service

# Restart
systemctl --user restart alloy.service

# Tail logs
journalctl --user -u alloy -f

# Disable (removes auto-start on boot; service still runs until stopped)
systemctl --user disable alloy.service
```

## Smoke-Check Runbook

After install (or after restarting dependent services):

1. Verify service is running:
   ```bash
   systemctl --user is-active alloy.service   # should print "active"
   ```

2. Confirm OTLP ports are listening:
   ```bash
   ss -tlnp | grep -E '4317|4318'
   ```

3. Send a test trace span via grpcurl or otelcol-contrib:
   ```bash
   # Quick HTTP/JSON test (requires curl + jq):
   curl -s -X POST http://localhost:4318/v1/traces \
     -H 'Content-Type: application/json' \
     -d '{"resourceSpans":[]}' | jq .
   # Expected: {"partialSuccess":{}}
   ```

4. Verify output file is being written:
   ```bash
   tail -f ~/.local/share/alloy/traces.jsonl
   ```

5. Restart a connected service (e.g. pulse) and confirm spans appear in the file.

## Logrotate Config

Add to `/etc/logrotate.d/alloy-traces` (or `~/.config/logrotate/alloy-traces`):

```
/home/<user>/.local/share/alloy/traces.jsonl {
    weekly
    rotate 4
    compress
    missingok
    notifempty
    copytruncate
}
```

## Configuration

Default config lives at `~/.config/alloy/config.alloy` (deployed by install.sh,
never overwritten on re-install). Edit to change endpoints, add exporters, or
enable the Prometheus scraper block.

## Phase 2 Smoke-Test

Phase 2 will add an end-to-end test that:
- Starts alloy.service
- Sends synthetic OTLP spans from each instrumented service
- Asserts spans appear in `traces.jsonl` within 10 s
- Reports missing coverage per service

See `tests/test_alloy_config.sh` for the Phase 1 config-parse test.
