#!/bin/sh
# Package the repository into a release-style ZIP.

set -e

ROOT_DIR="$(cd "$(dirname "$0")"/.. && pwd)"
OUTPUT=""

usage() {
  cat <<'USAGE'
Usage: scripts/package_release.sh [--output file.zip]

Creates a ZIP archive that mirrors the official GitHub release layout.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --output|-o)
      [ $# -lt 2 ] && { usage >&2; exit 1; }
      OUTPUT="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 1
      ;;
  esac
 done

VERSION="unknown"
if [ -f "$ROOT_DIR/VERSION" ]; then
  VERSION="$(head -n1 "$ROOT_DIR/VERSION" | tr -d '\r')"
fi

[ -n "$OUTPUT" ] || OUTPUT="$ROOT_DIR/openwrt-telegram-${VERSION}.zip"

if command -v git >/dev/null 2>&1 && git -C "$ROOT_DIR" rev-parse >/dev/null 2>&1; then
  git -C "$ROOT_DIR" archive --format=zip --output="$OUTPUT" HEAD
else
  TMP_DIR="$(mktemp -d)"
  cleanup() {
    rm -rf "$TMP_DIR"
  }
  trap cleanup EXIT
  cp -a "$ROOT_DIR" "$TMP_DIR/openwrt-telegram"
  rm -rf "$TMP_DIR/openwrt-telegram/.git"
  if command -v zip >/dev/null 2>&1; then
    (cd "$TMP_DIR" && zip -qr "$OUTPUT" openwrt-telegram)
  else
    python3 - "$TMP_DIR/openwrt-telegram" "$OUTPUT" <<'PY'
import os
import sys
import zipfile

src = sys.argv[1]
dest = sys.argv[2]
with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as archive:
    for root, _dirs, files in os.walk(src):
        for name in files:
            path = os.path.join(root, name)
            rel = os.path.relpath(path, os.path.dirname(src))
            archive.write(path, rel)
PY
  fi
fi

echo "Created $OUTPUT"
