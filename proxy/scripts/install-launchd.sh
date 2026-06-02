#!/usr/bin/env bash
# Install the claude-cli-proxy launchd agent so it auto-starts at login.
#
# After install:
#   - The proxy runs in the background as long as your user is logged in.
#   - launchd restarts it if it crashes.
#   - The launcher wrapper at ~/.local/bin/hermes still works (its on-demand
#     start is a no-op when launchd has already started it).
#
# Uninstall with: scripts/uninstall-launchd.sh
set -euo pipefail

LABEL="com.salesforcebob.sfhermes.proxy"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="${SCRIPT_DIR}/${LABEL}.plist"
AGENTS_DIR="${HOME}/Library/LaunchAgents"
TARGET="${AGENTS_DIR}/${LABEL}.plist"

PROXY_BIN="$(cd "${SCRIPT_DIR}/.." && pwd)/.venv/bin/claude-cli-proxy"

if [[ ! -x "$PROXY_BIN" ]]; then
    echo "error: $PROXY_BIN not found or not executable" >&2
    echo "run \`uv pip install -e .\` in proxy/ first" >&2
    exit 1
fi

if [[ ! -f "$TEMPLATE" ]]; then
    echo "error: template $TEMPLATE not found" >&2
    exit 1
fi

mkdir -p "$AGENTS_DIR"
mkdir -p "${HOME}/.hermes/logs"

# Substitute placeholders into the plist
sed -e "s|__PROXY_BIN__|${PROXY_BIN}|g" \
    -e "s|__USER_HOME__|${HOME}|g" \
    "$TEMPLATE" > "$TARGET"

# If an old version is loaded, unload it first so launchd reads the new plist
if launchctl print "gui/$(id -u)/${LABEL}" >/dev/null 2>&1; then
    launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
fi

# Stop any manually-started proxy so launchd can take over the port
if lsof -ti :8765 >/dev/null 2>&1; then
    lsof -ti :8765 | xargs kill 2>/dev/null || true
    sleep 1
fi

launchctl bootstrap "gui/$(id -u)" "$TARGET"
echo "installed launchd agent → $TARGET"

# Wait briefly and confirm it came up
for _ in $(seq 1 30); do
    if curl -sf --max-time 1 http://127.0.0.1:8765/healthz >/dev/null 2>&1; then
        echo "proxy is healthy on http://127.0.0.1:8765"
        exit 0
    fi
    sleep 0.2
done

echo "warning: proxy did not respond on /healthz within 6s" >&2
echo "check ${HOME}/.hermes/logs/proxy.launchd.log" >&2
exit 1
