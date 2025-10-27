#!/bin/sh
# Admin-only (enforced in dispatcher)
logger -t telebot "Reboot requested by $TELEBOT_USER_ID"
/sbin/reboot || echo "Failed to reboot"
