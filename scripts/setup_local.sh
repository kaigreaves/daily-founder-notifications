#!/bin/bash
# One-time local setup. Prompts for the two credentials, stores them in a
# private file, and schedules the daily 6:00 PM run.
#
# Nothing you type here is echoed to the screen or written anywhere except
# ~/.config/daily-deals/env, which is readable only by you.
#
#   bash scripts/setup_local.sh

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="$HOME/.config/daily-deals"
ENV_FILE="$CONFIG_DIR/env"
PLIST="$HOME/Library/LaunchAgents/com.kaigreaves.dailydeals.plist"
LABEL="com.kaigreaves.dailydeals"

echo "Daily Founder Notifications - local setup"
echo "========================================="
echo

mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR"

# ---------------------------------------------------------------- credentials
echo "1) Gemini API key   (from https://aistudio.google.com/apikey)"
printf "   Paste it and press Enter (input is hidden): "
read -rs GEMINI_API_KEY
echo
if [ -z "$GEMINI_API_KEY" ]; then
  echo "   ERROR: empty. Run this again." >&2
  exit 1
fi

echo
echo "2) Gmail App Password  (from https://myaccount.google.com/apppasswords)"
echo "   16 characters. Spaces are fine - they get stripped."
printf "   Paste it and press Enter (input is hidden): "
read -rs GMAIL_APP_PASSWORD
echo
GMAIL_APP_PASSWORD="${GMAIL_APP_PASSWORD// /}"
if [ ${#GMAIL_APP_PASSWORD} -ne 16 ]; then
  echo "   ERROR: expected 16 characters, got ${#GMAIL_APP_PASSWORD}. Run this again." >&2
  exit 1
fi

echo
printf "3) Email address to send to [kaigreaves18@gmail.com]: "
read -r NOTIFY_TO
NOTIFY_TO="${NOTIFY_TO:-kaigreaves18@gmail.com}"

# Written with a restrictive umask so the file is never briefly world-readable.
(
  umask 077
  cat > "$ENV_FILE" <<EOF
export GEMINI_API_KEY='$GEMINI_API_KEY'
export GMAIL_ADDRESS='$NOTIFY_TO'
export GMAIL_APP_PASSWORD='$GMAIL_APP_PASSWORD'
export NOTIFY_TO='$NOTIFY_TO'
EOF
)
chmod 600 "$ENV_FILE"

unset GEMINI_API_KEY GMAIL_APP_PASSWORD

echo
echo "   Saved to $ENV_FILE (readable only by you)"

# ------------------------------------------------------------------- schedule
mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$REPO/scripts/run_daily.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>18</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>$HOME/Library/Logs/daily-deals.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/Library/Logs/daily-deals.log</string>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo "   Scheduled for 6:00 PM daily."
echo
echo "Done. Test it right now with:"
echo
echo "    bash $REPO/scripts/run_daily.sh"
echo
