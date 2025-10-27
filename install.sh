#!/bin/sh
# Simple installer for the OpenWRT TeleBot package.

set -e

TARGET_BASE="${1:-/opt/openwrt-telebot}"
CUSTOM_ZIP="${2:-}"
REPO="sfdcai/openwrt-telegram"
LATEST_API="https://api.github.com/repos/$REPO/releases/latest"
DEFAULT_ASSET="https://github.com/$REPO/releases/latest/download/openwrt-telegram.zip"

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

if ! command_exists curl && ! command_exists wget; then
  echo "Error: curl or wget is required." >&2
  exit 1
fi

if ! command_exists unzip; then
  echo "Error: unzip is required." >&2
  exit 1
fi

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

ARCHIVE="$TMP_DIR/source.zip"
ZIP_URL="${CUSTOM_ZIP:-${ZIP_URL:-}}"

fetch_json() {
  if command_exists curl; then
    curl -fsSL "$1"
  else
    wget -qO- "$1"
  fi
}

probe_url() {
  if command_exists curl; then
    curl -sfIL "$1" >/dev/null 2>&1
  else
    wget --spider "$1" >/dev/null 2>&1
  fi
}

resolve_zip_url() {
  if [ -n "$ZIP_URL" ]; then
    return 0
  fi

  if probe_url "$DEFAULT_ASSET"; then
    ZIP_URL="$DEFAULT_ASSET"
    return 0
  fi

  RELEASE_JSON="$(fetch_json "$LATEST_API" 2>/dev/null || true)"
  if [ -n "$RELEASE_JSON" ]; then
    ZIP_URL="$(printf '%s' "$RELEASE_JSON" | grep -o 'https://[^"[:space:]]*\.zip' | head -n1)"
    if [ -z "$ZIP_URL" ]; then
      TAG="$(printf '%s' "$RELEASE_JSON" | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"[:space:]]*\)".*/\1/p' | head -n1)"
      if [ -n "$TAG" ]; then
        ZIP_URL="https://github.com/$REPO/archive/refs/tags/$TAG.zip"
      fi
    fi
  fi

  if [ -z "$ZIP_URL" ]; then
    ZIP_URL="https://github.com/$REPO/archive/refs/heads/main.zip"
  fi
}

resolve_zip_url

download_zip() {
  echo "Downloading release archive…"
  if command_exists curl; then
    curl -L "$ZIP_URL" -o "$ARCHIVE"
  else
    wget -O "$ARCHIVE" "$ZIP_URL"
  fi
}

download_zip

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
echo "Downloaded from: $ZIP_URL"
echo "Next steps:"
echo "  1. Edit $TARGET_BASE/config/config.json with your bot token and chat id."
echo "  2. Reload uhttpd so it serves the latest UI and CGI endpoint."
echo "  3. Enable and start the service: /etc/init.d/openwrt-telebot enable && /etc/init.d/openwrt-telebot start"
