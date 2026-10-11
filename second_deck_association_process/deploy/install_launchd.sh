#!/bin/bash
# Usage: deploy/install_launchd.sh <season>   (installs the daily 06:00 pipeline LaunchAgent)
set -euo pipefail
SEASON="${1:?season required, e.g. 2027}"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
TARGET="$HOME/Library/LaunchAgents/com.sda.pipeline.plist"
sed -e "s|__REPO__|$REPO|g" -e "s|__SEASON__|$SEASON|g" "$HERE/com.sda.pipeline.plist.template" > "$TARGET"
launchctl bootout "gui/$(id -u)" "$TARGET" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$TARGET"
echo "Installed $TARGET (runs daily at 06:00 while the Mac is awake)."
