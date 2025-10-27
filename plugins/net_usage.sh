#!/bin/sh
# Display RX/TX counters for a network interface (default br-lan).

IFACE="${1:-br-lan}"

printf 'Interface: %s\n\n' "$IFACE"

if command -v ip >/dev/null 2>&1; then
    ip -s link show "$IFACE"
elif command -v ifconfig >/dev/null 2>&1; then
    ifconfig "$IFACE"
else
    echo "Neither 'ip' nor 'ifconfig' is available."
    exit 1
fi
