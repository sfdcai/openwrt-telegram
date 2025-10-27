Each plugin is an **executable shell script** named after the command.


Examples:
- `/status` -> `status.sh`
- `/wifi on` -> `wifi.sh on`
- `/clients` -> `clients.sh`
- `/firewall list` -> `firewall.sh list`
- `/reboot` -> `reboot.sh`


Environment variables provided to plugins:
- `TELEBOT_USER_ID`, `TELEBOT_CHAT_ID`, `TELEBOT_MESSAGE_ID`
- `TELEBOT_COMMAND` e.g. `/status`
- `TELEBOT_ARGS` e.g. `on` or `list`
