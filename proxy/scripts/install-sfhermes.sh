#!/usr/bin/env bash
# Install the sfhermes CLI shim at ~/.local/bin/sfhermes.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="${SCRIPT_DIR}/sfhermes"
TARGET_DIR="${HOME}/.local/bin"
TARGET="${TARGET_DIR}/sfhermes"

mkdir -p "$TARGET_DIR"
ln -sf "$SOURCE" "$TARGET"
echo "installed sfhermes → $TARGET (symlink to $SOURCE)"
