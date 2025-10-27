#!/bin/sh
# /firewall [list|restart]
case "$1" in
restart)
/etc/init.d/firewall restart && echo "Firewall restarted" || echo "Firewall restart failed"
;;
list|*)
iptables -L -n -v 2>/dev/null || echo "iptables not available"
;;
esac
