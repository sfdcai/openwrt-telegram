#!/bin/sh
# List DHCP leases / associated clients
LEASES=/tmp/dhcp.leases
if [ -f "$LEASES" ]; then
echo "DHCP Leases:"
awk '{print $3"\t"$4"\t"$2}' "$LEASES" | sed '1iIP\tHOST\tMAC'
else
echo "No DHCP leases file found"
fi


# Associated WiFi clients
echo "\nWiFi Stations:"
for dev in $(ls /sys/class/net | grep -E 'wlan|ath|ra'); do
echo "== $dev =="
iw dev "$dev" station dump 2>/dev/null | sed 's/^/ /'
done
