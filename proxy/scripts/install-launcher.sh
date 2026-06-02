#!/usr/bin/env bash
# Install the claude-cli-proxy-aware hermes launcher at ~/.local/bin/hermes.
#
# Run this once after install, and again after any `hermes update` that
# rewrites the launcher back to the upstream version.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="${SCRIPT_DIR}/hermes-launcher.sh"
TARGET="${HOME}/.local/bin/hermes"

if [[ ! -f "$SOURCE" ]]; then
    echo "error: $SOURCE not found" >&2
    exit 1
fi

# Back up the upstream launcher once so we can restore it if needed
if [[ -f "$TARGET" ]] && [[ ! -f "${TARGET}.upstream" ]]; then
    cp "$TARGET" "${TARGET}.upstream"
    echo "backed up upstream launcher → ${TARGET}.upstream"
fi

cp "$SOURCE" "$TARGET"
chmod +x "$TARGET"
echo "installed proxy-aware launcher → $TARGET"
