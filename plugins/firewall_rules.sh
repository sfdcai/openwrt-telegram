#!/bin/sh
# Show the current telebot nftables table for quick inspection.

NFT_BIN="${NFT_BIN:-nft}"
TABLE="${TABLE:-inet telebot}"

if ! command -v "$NFT_BIN" >/dev/null 2>&1; then
  echo "nft command not found"
  exit 1
fi

# shellcheck disable=SC2086 -- TABLE is expected to include family and name
$NFT_BIN list table $TABLE 2>&1 |
  sed 's/^/    /'
