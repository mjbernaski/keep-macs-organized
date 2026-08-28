#!/bin/sh
set -eu

LABEL="com.local.keep-macs-organized"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [ -f "$PLIST" ]; then
  rm "$PLIST"
fi
echo "Uninstalled $LABEL. Organized files and logs were not changed."

