#!/bin/sh
# Simple installer for the OpenWRT TeleBot package.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET_BASE="/opt/openwrt-telebot"
SOURCE_SPEC=""
FORCE_DOWNLOAD=0

usage() {
  cat <<'EOF'
Usage: ./install.sh [--target /path] [--source /path-or-zip] [--force-download]

Without arguments the installer copies files from the extracted release
directory that contains this script. If the script is not part of a release
tree it will fall back to downloading the latest GitHub release archive.

Positional compatibility: ./install.sh [/custom/target] [zip-or-dir]
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --target)
      [ $# -lt 2 ] && { usage >&2; exit 1; }
      TARGET_BASE="$2"
      shift 2
      ;;
    --source|--zip)
      [ $# -lt 2 ] && { usage >&2; exit 1; }
      SOURCE_SPEC="$2"
      shift 2
      ;;
    --force-download)
      FORCE_DOWNLOAD=1
      shift 1
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      if [ "${TARGET_OVERRIDE:-}" = "" ]; then
        TARGET_BASE="$1"
        TARGET_OVERRIDE=1
      elif [ "${SOURCE_OVERRIDE:-}" = "" ]; then
        SOURCE_SPEC="$1"
        SOURCE_OVERRIDE=1
      else
        usage >&2
        exit 1
      fi
      shift 1
      ;;
  esac
done

REPO="sfdcai/openwrt-telegram"
LATEST_API="https://api.github.com/repos/$REPO/releases/latest"
DEFAULT_ASSET="https://github.com/$REPO/releases/latest/download/openwrt-telegram.zip"

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

if [ "$(id -u 2>/dev/null || echo 0)" -ne 0 ]; then
  echo "Warning: installer should be run as root to deploy services." >&2
fi

if ! command_exists curl && ! command_exists wget; then
  echo "Error: curl or wget is required." >&2
  exit 1
fi

if ! command_exists unzip; then
  echo "Error: unzip is required." >&2
  exit 1
fi

if ! command_exists nft; then
  echo "Warning: nft command not found – install 'nftables' for client approvals." >&2
fi

TMP_DIR=""
ARCHIVE=""
ZIP_URL="${ZIP_URL:-}"
SOURCE_DIR=""

if [ -n "$SOURCE_SPEC" ]; then
  if [ -d "$SOURCE_SPEC" ]; then
    SOURCE_DIR="$(cd "$SOURCE_SPEC" && pwd)"
  elif [ -f "$SOURCE_SPEC" ]; then
    ARCHIVE="$(cd "$(dirname "$SOURCE_SPEC")" && pwd)/$(basename "$SOURCE_SPEC")"
  else
    echo "Error: --source path not found: $SOURCE_SPEC" >&2
    exit 1
  fi
fi

if [ "$FORCE_DOWNLOAD" -ne 1 ] && [ -z "$SOURCE_DIR" ] && [ -z "$ARCHIVE" ]; then
  if [ -d "$SCRIPT_DIR/bot" ] && [ -d "$SCRIPT_DIR/www" ]; then
    SOURCE_DIR="$SCRIPT_DIR"
  fi
fi

ensure_tmp() {
  if [ -z "$TMP_DIR" ]; then
    TMP_DIR="$(mktemp -d)"
    cleanup() {
      [ -n "$TMP_DIR" ] && rm -rf "$TMP_DIR"
    }
    trap cleanup EXIT
  fi
}

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

prepare_source_from_archive() {
  ensure_tmp
  if [ -z "$ARCHIVE" ]; then
    ARCHIVE="$TMP_DIR/source.zip"
  fi
  if [ ! -f "$ARCHIVE" ]; then
    resolve_zip_url
    download_zip
  fi
  unzip -q "$ARCHIVE" -d "$TMP_DIR"
  SOURCE_DIR="$(find "$TMP_DIR" -maxdepth 1 -type d \( -name 'openwrt-telebot*' -o -name 'openwrt-telegram*' \) | head -n1)"
  if [ -z "$SOURCE_DIR" ]; then
    echo "Could not locate extracted source directory" >&2
    exit 1
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

download_zip() {
  echo "Downloading release archive…"
  if command_exists curl; then
    curl -L "$ZIP_URL" -o "$ARCHIVE"
  else
    wget -O "$ARCHIVE" "$ZIP_URL"
  fi
}

if [ -z "$SOURCE_DIR" ]; then
  prepare_source_from_archive
else
  echo "Using local source directory: $SOURCE_DIR"
fi

VERSION=""
if [ -f "$SOURCE_DIR/VERSION" ]; then
  VERSION="$(head -n1 "$SOURCE_DIR/VERSION" | tr -d '\r')"
fi

mkdir -p "$TARGET_BASE"

copy_tree() {
  for item in "$SOURCE_DIR"/*; do
    name="$(basename "$item")"
    case "$name" in
      config)
        mkdir -p "$TARGET_BASE/config"
        for cfg in "$item"/*; do
          basecfg="$(basename "$cfg")"
          if [ "$basecfg" = "config.json" ] && [ -f "$TARGET_BASE/config/config.json" ]; then
            continue
          fi
          cp -a "$cfg" "$TARGET_BASE/config/"
        done
        ;;
      .git|.github|.gitignore)
        ;;
      *)
        cp -a "$item" "$TARGET_BASE/"
        ;;
    esac
  done
}

copy_tree

# Prepare state directory
mkdir -p "$TARGET_BASE/state"
STATE_FILE=""
if [ -f "$TARGET_BASE/config/config.json" ]; then
  STATE_FILE="$(sed -n 's/.*"client_state_file"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$TARGET_BASE/config/config.json" | head -n1)"
fi
if [ -z "$STATE_FILE" ]; then
  STATE_FILE="$TARGET_BASE/state/clients.json"
fi
STATE_DIR="$(dirname "$STATE_FILE")"
mkdir -p "$STATE_DIR"
if [ ! -f "$STATE_FILE" ]; then
  printf '{"clients": {}}\n' >"$STATE_FILE"
fi
chmod 600 "$STATE_FILE"

# Permissions
[ -f "$TARGET_BASE/bot/main.py" ] && chmod +x "$TARGET_BASE/bot/main.py"
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
  mkdir -p /www
  rm -rf /www/telebot
  mkdir -p /www/telebot
  cp -a "$TARGET_BASE/www"/assets /www/telebot/
  cp "$TARGET_BASE/www/index.html" /www/telebot/index.html
  mkdir -p /www/cgi-bin
  cp "$TARGET_BASE/www/cgi-bin/telebot.py" /www/cgi-bin/telebot.py
  chmod +x /www/cgi-bin/telebot.py
fi

echo "Installation complete!"
if [ -n "$ZIP_URL" ]; then
  echo "Downloaded from: $ZIP_URL"
fi
if [ -n "$VERSION" ]; then
  echo "Installed version: $VERSION"
fi
echo "Next steps:"
echo "  1. Edit $TARGET_BASE/config/config.json with your bot token and chat id."
echo "  2. Reload uhttpd so it serves the latest UI and CGI endpoint (or run '/etc/init.d/uhttpd restart')."
echo "  3. Enable and start the service: /etc/init.d/openwrt-telebot enable && /etc/init.d/openwrt-telebot start"
