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
- **Self-healing installer** that works from an extracted release folder or
  downloads the latest ZIP automatically, deploys to `/opt/openwrt-telebot`, and
  refreshes the uhttpd assets so stale UI files disappear.
- **Event helpers** for DHCP and WAN notifications via Telegram.
- **Router-aware access control** that watches LAN clients, blocks newcomers in
  nftables until you approve them, and lets you manage the allow/block lists
  from Telegram or the web dashboard.

## Requirements

- OpenWRT 24.x (or similar BusyBox-based firmware)
- Python 3 (`opkg update && opkg install python3`)
- `curl` or `wget` plus `unzip` (for the installer)
- uhttpd web server (stock on OpenWRT)
- `nftables` (`opkg install nftables`) for client isolation and approval


## Quick install

1. Download the latest release archive from the
   [GitHub Releases](https://github.com/sfdcai/openwrt-telegram/releases)
   page and extract it on the router (or a writable location).
2. Run the installer from inside the extracted directory:

   ```sh
   sh install.sh
   ```

   - Use `--target /custom/path` to override the installation prefix.
   - Use `--force-download` if you want the installer to fetch a fresh copy even
     though it is already running from an extracted archive.
   - Use `--source /path/to/archive.zip` to point the installer at a particular
     release bundle when operating offline.

3. Reload uhttpd so the refreshed assets and CGI script are active:

   ```sh
   /etc/init.d/uhttpd restart
   ```

The installer verifies tool availability, prepares nftables state, removes old
web assets before copying the new UI, preserves your existing `config.json`,
and reports the version that was installed.

To update in-place, run the same command again – the script detects its current
location and only downloads the release if needed.

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
- `client_state_file` – JSON file that stores discovered clients and their
  approval status (defaults to `state/clients.json`).
- `nft_table` / `nft_chain` – nftables objects that TeleBot manages to block
  unapproved MAC addresses on the forward hook.
- `nft_block_set` / `nft_allow_set` – nftables sets holding blocked and
  approved MAC addresses.
- `client_whitelist` – List of MAC addresses that bypass approval entirely.

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
- Review LAN devices, approve or reject new clients, and maintain a whitelist
  that is never blocked.

### Client approval workflow

- When a new MAC address appears on the LAN it is added to the `blocked`
  nftables set and shown as **Pending** in the dashboard.
- TeleBot sends a Telegram notification with inline buttons so you can approve,
  block, or whitelist the device directly from chat.
- The web UI mirrors the same controls and shows live connection/"last seen"
  data pulled from DHCP leases and `ip neigh`.
- Approving a client removes it from the block list, whitelisting marks it as
  always allowed, and forgetting a device clears it from the registry.

All operations are logged to the configured log file, and the CGI/UI layer will
report errors back to the browser while appending stack traces to the log for
easy troubleshooting.

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
- Preview the UI locally: `scripts/preview_server.py --port 8081` and browse to
  <http://127.0.0.1:8081/>.

Pull requests are welcome – see the source for additional details.
### Generate a release ZIP locally

If you want to create the same archive that GitHub serves (for testing or to
host it privately), use:

```sh
scripts/package_release.sh
```

The script writes `openwrt-telegram-<version>.zip` in the repository root. Pass
`--output /tmp/custom.zip` to change the destination.

Update the `VERSION` file before packaging so the installer and UI report the
correct release number.

