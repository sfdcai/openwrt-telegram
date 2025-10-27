# OpenWRT TeleBot

A modern Telegram automation toolkit for OpenWRT routers. The project ships a
lightweight Python bot, a responsive web control panel served through uhttpd,
and helper scripts for integrating with router events and shell plugins.

## Features

- **Robust Telegram bot** implemented in Python 3 with safe polling, logging and
  modular command dispatching.
- **Shell plugin framework** – drop executable `*.sh` files into the plugins
  directory to expose router functionality as Telegram commands.
- **Beautiful web UI** hosted from uhttpd for real-time status, configuration
  editing, log inspection, plugin execution and manual chat messaging.
- **Installer** script that downloads the latest release ZIP, deploys it to
  `/opt/openwrt-telebot`, sets permissions and copies the web assets to `/www`.
- **Event helpers** for DHCP and WAN notifications via Telegram.

## Requirements

- OpenWRT 24.x (or similar BusyBox-based firmware)
- Python 3 (`opkg update && opkg install python3`)
- `curl` or `wget` plus `unzip` (for the installer)
- uhttpd web server (stock on OpenWRT)

## Quick install

```sh
sh install.sh
```

Optionally pass a custom installation directory as the first argument
(defaults to `/opt/openwrt-telebot`). The installer performs the following
actions:

1. Resolves the most recent release from
   <https://github.com/sfdcai/openwrt-telegram/releases> and downloads the ZIP
   bundle.
2. Extracts the archive and copies the project into the target directory.
3. Installs the init script to `/etc/init.d/openwrt-telebot` and sets execute
   permissions on Python and shell helpers.
4. Creates the log file directory and deploys the web UI to `/www/telebot` with
   a CGI endpoint at `/www/cgi-bin/telebot.py`.

After installation, reload uhttpd so the new web assets and CGI script are
picked up:

```sh
/etc/init.d/uhttpd reload
```

## Manual setup

If you prefer manual deployment:

```sh
mkdir -p /opt/openwrt-telebot/{bot,config,plugins,helpers,events,init.d}
# copy files from this repo into /opt/openwrt-telebot with the same layout
chmod +x /opt/openwrt-telebot/bot/main.py
chmod +x /opt/openwrt-telebot/plugins/*.sh
chmod +x /opt/openwrt-telebot/helpers/tele-notify
cp /opt/openwrt-telebot/init.d/openwrt-telebot /etc/init.d/openwrt-telebot
chmod +x /etc/init.d/openwrt-telebot
```

Serve the UI by copying `www/index.html`, `www/assets`, and
`www/cgi-bin/telebot.py` into `/www/telebot` and `/www/cgi-bin` respectively.

## Configuration

Edit `/opt/openwrt-telebot/config/config.json`:

- `bot_token` – Telegram bot token from BotFather.
- `chat_id_default` – Default chat ID for outbound notifications.
- `poll_timeout` – Long polling timeout in seconds.
- `plugins_dir` – Directory containing executable shell plugins.
- `log_file` – Log output file for the bot.
- `ui_api_token` – Token required by the web UI API (store it locally in the
  browser via the UI access panel).
- `ui_base_url` – Preferred base URL for the UI (informational).

Use the built-in web UI to manage these fields securely – token values are
masked when displayed and only updated when explicitly changed. The bot accepts
messages only from the configured default chat ID, so make sure it matches your
personal conversation with the bot.

## Running the bot

```
/etc/init.d/openwrt-telebot enable
/etc/init.d/openwrt-telebot start
```

The service uses `procd` for supervision. Logs are written to the path defined
in `config.json`.

## Web UI

Visit `http://<router-ip>/telebot/` and enter the UI API token. The control
panel allows you to:

- Inspect bot process status, uptime and disk usage.
- Update Telegram credentials and bot configuration.
- Send test messages or arbitrary messages to specific chats.
- Run shell plugins and view their output instantly.
- Tail recent log entries.

## Helpers and events

- `helpers/tele-notify` – Shell helper to send quick messages using the bot
  configuration.
- `events/10-dhcp-notify.sh` – Example hook for DHCP lease notifications.
- `events/20-wan-iface.sh` – Example WAN state notification hook (edit for your
  environment).

## Development

- Run the bot once with a custom config file:
  `python3 bot/main.py --config ./config/config.json --once`
- Validate Python files: `python3 -m py_compile bot/*.py www/cgi-bin/telebot.py`

Pull requests are welcome – see the source for additional details.
