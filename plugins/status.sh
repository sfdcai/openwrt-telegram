#!/bin/sh
# System status: uptime, load, memory, IP
UPTIME=$(uptime 2>/dev/null)
FREE=$(free -h 2>/dev/null | sed -n '2p')
WAN=$(ubus call network.interface.wan status 2>/dev/null | sed -n 's/.*"ipv4-address":\s*\[\{"address":"\([0-9.]*\)".*/WAN IP: \1/p')
[ -z "$WAN" ] && WAN="WAN IP: (unknown)"


echo "$(hostname) — Status\n$UPTIME\nMEM: $FREE\n$WAN"
