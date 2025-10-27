#!/bin/sh
# Simple installer for the OpenWRT TeleBot package.

set -e

ZIP_URL="$1"
TARGET_BASE="${2:-/opt/openwrt-telebot}"

if [ -z "$ZIP_URL" ]; then
  cat <<USAGE
Usage: $0 <zip-url> [target-directory]

Example:
  $0 https://github.com/your-user/openwrt-telegram/archive/refs/heads/main.zip
USAGE
  exit 1
fi

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

ARCHIVE="$TMP_DIR/source.zip"

if echo "$ZIP_URL" | grep -qE '^https?://'; then
  echo "Downloading repository archive…"
  if command_exists curl; then
    curl -L "$ZIP_URL" -o "$ARCHIVE"
  elif command_exists wget; then
    wget -O "$ARCHIVE" "$ZIP_URL"
  else
    echo "Error: curl or wget required to download archive." >&2
    exit 1
  fi
else
  echo "Using local archive $ZIP_URL"
  cp "$ZIP_URL" "$ARCHIVE"
fi

if ! command_exists unzip; then
  echo "Error: unzip is required." >&2
  exit 1
fi

unzip -q "$ARCHIVE" -d "$TMP_DIR"
SRC_DIR="$(find "$TMP_DIR" -maxdepth 1 -type d -name 'openwrt-telebot*' -o -name 'openwrt-telegram*' | head -n1)"
if [ -z "$SRC_DIR" ]; then
  echo "Could not locate extracted source directory" >&2
  exit 1
fi

mkdir -p "$TARGET_BASE"
cp -a "$SRC_DIR"/* "$TARGET_BASE"/

# Ensure configuration directory exists
mkdir -p "$TARGET_BASE/config"
if [ ! -f "$TARGET_BASE/config/config.json" ] && [ -f "$SRC_DIR/config/config.json" ]; then
  cp "$SRC_DIR/config/config.json" "$TARGET_BASE/config/config.json"
fi

# Permissions
chmod +x "$TARGET_BASE/bot/main.py"
[ -d "$TARGET_BASE/plugins" ] && chmod +x "$TARGET_BASE"/plugins/*.sh || true
[ -f "$TARGET_BASE/helpers/tele-notify" ] && chmod +x "$TARGET_BASE/helpers/tele-notify"

# Install init script
if [ -f "$TARGET_BASE/init.d/openwrt-telebot" ]; then
  cp "$TARGET_BASE/init.d/openwrt-telebot" /etc/init.d/openwrt-telebot
  chmod +x /etc/init.d/openwrt-telebot
fi

# Prepare log file
LOG_FILE="$(sed -n 's/.*"log_file"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$TARGET_BASE/config/config.json" | head -n1)"
if [ -n "$LOG_FILE" ]; then
  mkdir -p "$(dirname "$LOG_FILE")"
  touch "$LOG_FILE"
  chmod 600 "$LOG_FILE"
fi

# Deploy web UI
if [ -d "$TARGET_BASE/www" ]; then
  mkdir -p /www/telebot
  cp -a "$TARGET_BASE/www"/assets /www/telebot/
  cp "$TARGET_BASE/www/index.html" /www/telebot/index.html
  mkdir -p /www/cgi-bin
  cp "$TARGET_BASE/www/cgi-bin/telebot.py" /www/cgi-bin/telebot.py
  chmod +x /www/cgi-bin/telebot.py
fi

echo "Installation complete!"
echo "Next steps:"
echo "  1. Edit $TARGET_BASE/config/config.json with your bot token and chat id."
echo "  2. Ensure uhttpd serves /www/telebot and /www/cgi-bin/telebot.py (reload uhttpd if necessary)."
echo "  3. Enable and start the service: /etc/init.d/openwrt-telebot enable && /etc/init.d/openwrt-telebot start"
