#!/bin/sh
# /wifi [on|off|status]
CMD="$1"
case "$CMD" in
on)
wifi up && echo "WiFi turned ON" || echo "Failed to turn WiFi on"
;;
off)
wifi down && echo "WiFi turned OFF" || echo "Failed to turn WiFi off"
;;
status|*)
iwinfo 2>/dev/null | sed -n '1,20p'
;;
esac
