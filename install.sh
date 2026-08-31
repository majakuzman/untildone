#!/bin/bash
# One-time install for untildone (macOS).
#   bash install.sh          interactive: writes config.json, registers launchd agent, starts it
#   bash install.sh remove   unregister the agent (keeps your data)
# Re-running is safe; it replaces the agent and keeps an existing config.json.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.$(id -un).untildone"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LEGACY_PLIST="$HOME/Library/LaunchAgents/com.$(id -un).reminder_daemon.plist"
UID_="$(id -u)"

if [ "$(uname)" != "Darwin" ]; then
  echo "untildone currently runs on macOS only (launchd + Apple Reminders)."; exit 1
fi

if [ "$1" = "remove" ]; then
  launchctl bootout "gui/$UID_" "$LEGACY_PLIST" 2>/dev/null || true
  rm -f "$LEGACY_PLIST"
  launchctl bootout "gui/$UID_" "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "removed $LABEL (data in $DIR untouched)"
  exit 0
fi

# Safety: never let this folder's parent be a git repo (personal files live there for some users).
if [ -d "$DIR/../.git" ]; then
  echo "Refusing: the PARENT of $DIR is a git repository. Only the untildone folder itself should be one."; exit 1
fi

# Use a system python, not a venv that might be deleted later.
if [ -n "$VIRTUAL_ENV" ]; then
  PATH="$(echo "$PATH" | tr ':' '\n' | grep -v "^$VIRTUAL_ENV" | paste -sd: -)"
fi
PY="$(command -v python3)"
"$PY" -c 'import sys; assert sys.version_info >= (3, 8), sys.version' \
  || { echo "python3 >= 3.8 needed, found $PY"; exit 1; }

# ---- config.json (only if missing) ----
if [ ! -f "$DIR/config.json" ]; then
  echo "== untildone setup =="
  echo "Optional: a Google Drive folder synced to this Mac, used as the mailbox for commands"
  echo "dropped from a phone. Leave empty to run laptop-only."
  read -r -p "Synced Drive folder path (or empty): " DRIVE
  read -r -p "Quiet hours start,end (24h, default 22,7): " QH
  QH="${QH:-22,7}"
  read -r -p "Nag every N minutes (default 30): " NAG
  NAG="${NAG:-30}"
  read -r -p "Apple Reminders list name (default Claude): " RL
  RL="${RL:-Claude}"
  "$PY" - "$DIR/config.json" "$DRIVE" "$QH" "$NAG" "$RL" <<'EOF'
import json, sys
p, drive, qh, nag, rl = sys.argv[1:]
a, b = [int(x) for x in qh.split(",")]
json.dump({"drive_folder": drive.strip(), "quiet_hours": [a, b], "nag_every_min": int(nag), "reminders_list": rl},
          open(p, "w"), indent=1)
print("wrote", p)
EOF
else
  echo "using existing $DIR/config.json"
fi

mkdir -p "$HOME/Library/LaunchAgents" "$DIR/inbox_local" "$DIR/exports"
"$PY" "$DIR/daemon.py" init >/dev/null
chmod +x "$DIR/untildone" "$DIR/daemonctl" 2>/dev/null || true

# git hook (only if this folder is a repo)
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" config core.hooksPath .githooks && echo "git pre-commit guard enabled"
fi

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$DIR/daemon.py</string>
    <string>run</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$DIR/launchd.err.log</string>
</dict>
</plist>
EOF

# retire the pre-rename agent if it exists, then (re)register ours
if [ -f "$LEGACY_PLIST" ]; then
  launchctl bootout "gui/$UID_" "$LEGACY_PLIST" 2>/dev/null || true
  rm -f "$LEGACY_PLIST"
  echo "retired legacy agent com.$(id -un).reminder_daemon"
fi
launchctl bootout "gui/$UID_" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$UID_" "$PLIST"
launchctl kickstart -k "gui/$UID_/$LABEL"
sleep 2
if launchctl print "gui/$UID_/$LABEL" 2>/dev/null | grep -q "state = running"; then
  echo "installed and running: $LABEL  (python: $PY)"
else
  echo "installed but not confirmed running — check $DIR/launchd.err.log"
fi
echo
echo "Next: run  python3 $DIR/daemon.py remtest  once, to grant Reminders access and see a test alert."
echo "Then try:  $DIR/untildone add \"buy milk\" --due 17:00"
echo "Control:   $DIR/daemonctl {status|stop|start|restart|log|tick}"
