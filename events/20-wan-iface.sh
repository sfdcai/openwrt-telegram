#!/bin/sh
# Hook for WAN interface up/down
# Place at: /etc/hotplug.d/iface/20-telebot


IFNAME="$INTERFACE"
ACTION="$ACTION"
BASE="/opt/openwrt-telebot/helpers/tele-notify"


if [ "$IFNAME" = "wan" ]; then
case "$ACTION" in
ifup)
IP=$(ubus call network.interface.wan status 2>/dev/null | sed -n 's/.*"ipv4-address":\s*\[\{"address":"\([0-9.]*\)".*/\1/p')
[ -z "$IP" ] && IP="(unknown)"
$BASE "WAN is UP — $IP"
;;
ifdown)
$BASE "WAN is DOWN"
;;
esac
fi
