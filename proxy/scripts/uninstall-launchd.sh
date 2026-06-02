#!/usr/bin/env bash
# Uninstall the claude-cli-proxy launchd agent.
set -euo pipefail

LABEL="com.salesforcebob.sfhermes.proxy"
TARGET="${HOME}/Library/LaunchAgents/${LABEL}.plist"

if launchctl print "gui/$(id -u)/${LABEL}" >/dev/null 2>&1; then
    launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
    echo "unloaded launchd agent ${LABEL}"
fi

if [[ -f "$TARGET" ]]; then
    rm -f "$TARGET"
    echo "removed $TARGET"
fi
