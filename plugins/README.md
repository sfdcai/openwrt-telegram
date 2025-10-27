Each plugin is an **executable shell script** named after the command exposed to
Telegram and the web UI.

The dispatcher automatically lists plugins in `/plugins` and uses the first
comment line as the human-readable description inside `/plugins` (bot command)
and the web control panel.

Examples:
- `/status` -> `status.sh`
- `/wifi on` -> `wifi.sh on`
- `/clients` -> `clients.sh`
- `/firewall list` -> `firewall.sh list`
- `/leases` -> `leases.sh`
- `/net_usage br-lan` -> `net_usage.sh br-lan`
- `/firewall_rules` -> `firewall_rules.sh`
- `/reboot` -> `reboot.sh`

Environment variables provided to plugins:
- `TELEBOT_USER_ID`, `TELEBOT_CHAT_ID`, `TELEBOT_MESSAGE_ID`
- `TELEBOT_COMMAND` e.g. `/status`
- `TELEBOT_ARGS` e.g. `on` or `list`

Return text from the plugin will be chunked automatically to fit Telegram’s
message limits and rendered verbatim in the web UI.
