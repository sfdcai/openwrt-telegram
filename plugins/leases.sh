#!/bin/sh
# Show current DHCP leases in a friendly table.

LEASES_FILE="${1:-/tmp/dhcp.leases}"

if [ ! -f "$LEASES_FILE" ]; then
    echo "Lease file not found: $LEASES_FILE"
    exit 1
fi

printf '%-20s %-18s %-16s\n' "Hostname" "MAC" "IP"
printf '%-20s %-18s %-16s\n' "--------------------" "------------------" "----------------"

while IFS=' ' read -r expires mac ip hostname _; do
    [ -n "$mac" ] || continue
    [ -n "$ip" ] || continue
    if [ -z "$hostname" ] || [ "$hostname" = '*' ]; then
        hostname="-"
    fi
    printf '%-20s %-18s %-16s\n' "$hostname" "$mac" "$ip"
done < "$LEASES_FILE"
