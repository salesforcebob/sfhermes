#!/usr/bin/env bash
# Wrapper for the hermes CLI that ensures the claude-cli-proxy is running.
#
# Replaces ~/.local/bin/hermes (the upstream Hermes launcher). When invoked:
#   1. If the proxy is not responding on http://127.0.0.1:8765/healthz, start it.
#   2. Wait briefly for the proxy to come up.
#   3. Exec the real hermes binary from the upstream venv.
#
# `hermes update` rewrites ~/.local/bin/hermes back to the upstream version.
# Re-apply this wrapper after an update by running:
#   ~/workspace/hermes/proxy/scripts/install-launcher.sh
set -euo pipefail

PROXY_PORT="${HERMES_PROXY_PORT:-8765}"
PROXY_HOST="${HERMES_PROXY_HOST:-127.0.0.1}"
PROXY_BIN="/Users/robert.ullery/workspace/hermes/proxy/.venv/bin/claude-cli-proxy"
PROXY_LOG_DIR="${HOME}/.hermes/logs"
PROXY_LOG="${PROXY_LOG_DIR}/proxy.log"
PROXY_PIDFILE="${PROXY_LOG_DIR}/proxy.pid"
HERMES_BIN="/Users/robert.ullery/.hermes/hermes-agent/venv/bin/hermes"

mkdir -p "$PROXY_LOG_DIR"

healthz_ok() {
    curl -sf --max-time 1 "http://${PROXY_HOST}:${PROXY_PORT}/healthz" >/dev/null 2>&1
}

start_proxy() {
    if [[ ! -x "$PROXY_BIN" ]]; then
        echo "claude-cli-proxy: $PROXY_BIN not found; running hermes without proxy" >&2
        return 1
    fi
    nohup "$PROXY_BIN" --host "$PROXY_HOST" --port "$PROXY_PORT" \
        >> "$PROXY_LOG" 2>&1 &
    echo $! > "$PROXY_PIDFILE"
    disown
    # Wait up to ~5s for healthz to come up
    for _ in $(seq 1 50); do
        if healthz_ok; then return 0; fi
        sleep 0.1
    done
    echo "claude-cli-proxy: failed to come up on ${PROXY_HOST}:${PROXY_PORT}; see $PROXY_LOG" >&2
    return 1
}

if ! healthz_ok; then
    start_proxy || true
fi

unset PYTHONPATH
unset PYTHONHOME
exec "$HERMES_BIN" "$@"
