#!/usr/bin/env bash
set -euo pipefail

PLUGIN_NAME="hy_memory"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
TARGET_DIR="$HERMES_HOME/plugins/$PLUGIN_NAME"

mkdir -p "$(dirname "$TARGET_DIR")"
ln -sfn "$SOURCE_DIR" "$TARGET_DIR"

echo "Installed dev symlink: $TARGET_DIR -> $SOURCE_DIR"
echo "Enable with: hermes plugins enable hy_memory"
echo "Set provider with: hermes config set memory.provider hy_memory"
echo "Then restart/reset Hermes Agent so the provider and tools are loaded."
