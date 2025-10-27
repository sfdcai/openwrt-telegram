#!/bin/sh
# Hook for dnsmasq/odhcpd lease events (adjust to your environment)
# Example placement:
# /etc/hotplug.d/dhcp/10-telebot
# and point it to this script path or copy contents there.


BASE="/opt/openwrt-telebot/helpers/tele-notify"
if [ "$ACTION" = "add" ] || [ "$ACTION" = "update" ]; then
[ -n "$HOSTNAME" ] || HOSTNAME="$DNSMASQ_SUPPLIED_HOSTNAME"
MSG="DHCP: $HOSTNAME ($MACADDR) -> $IP"
$BASE "$MSG"
fi
