from __future__ import annotations

import html
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from router import RouterController

MAX_MSG_LEN = 3900
CLIENTS_PAGE_SIZE = 6

MessagePayload = Dict[str, Any]
ResponseType = MessagePayload | str
ADMIN_PLUGIN_NAMES = {"reboot", "poweroff"}


class Dispatcher:
    """Translate incoming Telegram messages into actions."""

    def __init__(
        self,
        plugins_dir: str | os.PathLike[str],
        logger: Callable[[str], None],
        default_chat: int | None = None,
        router: Optional[RouterController] = None,
        enhanced_notifications: bool = False,
    ):
        self.plugins_dir = Path(plugins_dir)
        self.logger = logger
        self.default_chat = int(default_chat) if default_chat is not None else None
        self.router = router
        self.enhanced = bool(enhanced_notifications)
        self.uses_rich_text = self.enhanced
        extra_admin_plugins = os.environ.get("TELEBOT_ADMIN_PLUGINS", "")
        self.admin_only_plugins = {
            name.strip().lower() for name in extra_admin_plugins.split(",") if name.strip()
        } | ADMIN_PLUGIN_NAMES

        self.commands: dict[str, Callable[[int, int, int, list[str]], List[ResponseType]]] = {
            "/start": self._cmd_help,
            "/help": self._cmd_help,
            "/menu": self._cmd_menu,
            "/dashboard": self._cmd_menu,
            "/ping": self._cmd_ping,
            "/status": self._cmd_status,
            "/plugins": self._cmd_plugins,
            "/run": self._cmd_run_plugin,
            "/log": self._cmd_log_tail,
            "/whoami": self._cmd_whoami,
            "/clients": self._cmd_clients,
            "/router": self._cmd_router,
            "/approve": self._cmd_approve,
            "/block": self._cmd_block,
            "/whitelist": self._cmd_whitelist,
            "/forget": self._cmd_forget,
            "/pause": self._cmd_pause,
            "/resume": self._cmd_resume,
            "/diag": self._cmd_diagnostics,
            "/diagnostics": self._cmd_diagnostics,
        }

    # ------------------------------------------------------------------
    # Command handlers

    def _cmd_help(self, user: int, chat: int, message: int, args: list[str]) -> List[str]:
        if self.enhanced:
            commands = [
                ("📋", "/menu", "Interactive control centre"),
                ("🏓", "/ping", "Heartbeat check"),
                ("📊", "/status", "System snapshot"),
                ("🧩", "/plugins", "List installed shell helpers"),
                ("▶️", "/run &lt;plugin&gt; [args]", "Execute a plugin"),
                ("🪵", "/log [lines]", "Tail the bot log"),
                ("🪪", "/whoami", "Show your identifiers"),
                ("🧑‍💻", "/clients", "Known devices"),
                ("🛡️", "/router", "Router guard summary"),
                ("✅", "/approve &lt;id|mac|ip&gt;", "Allow a device"),
                ("🚫", "/block &lt;id|mac|ip&gt; [internet|network]", "Block WAN-only or full network"),
                ("⏸", "/pause &lt;id|mac|ip&gt;", "Temporarily suspend"),
                ("▶️", "/resume &lt;id|mac|ip&gt;", "Resume a client"),
                ("⭐", "/whitelist &lt;id|mac|ip&gt;", "Always allow a device"),
                ("🧹", "/forget &lt;id|mac&gt;", "Remove from registry"),
                ("🩺", "/diag", "Deployment diagnostics"),
            ]
            lines = ["<b>🧭 Command navigator</b>"]
            for icon, command, description in commands:
                lines.append(f"{icon} <code>{command}</code> — {html.escape(description)}")
            plugins = self._plugin_summary()
            if plugins:
                lines.append("")
                lines.append("<b>🔌 Plugins</b>")
                for entry in plugins:
                    lines.append(f"• {html.escape(entry.strip())}")
            return ["\n".join(lines)]

        available = [
            "Commands:",
            "/menu - interactive control centre",
            "/ping - simple heartbeat",
            "/status - system information",
            "/plugins - list available shell plugins",
            "/run <plugin> [args] - execute a plugin",
            "/log [lines] - tail the bot log",
            "/whoami - display your identifiers",
            "/clients - list known devices",
            "/router - router guard summary",
            "/approve <id|mac|ip> - allow a device",
            "/block <id|mac|ip> [internet|network] - block WAN-only or the entire network",
            "/pause <id|mac|ip> - temporarily suspend a client",
            "/resume <id|mac|ip> - restore a paused client",
            "/whitelist <id|mac|ip> - always allow a device",
            "/forget <id|mac> - remove device from registry",
            "/diag - run deployment diagnostics",
        ]
        return ["\n".join(available + self._plugin_summary())]

    def _cmd_menu(self, user: int, chat: int, message: int, args: list[str]) -> List[ResponseType]:
        return [self._menu_payload()]

    def _cmd_ping(self, user: int, chat: int, message: int, args: list[str]) -> List[str]:
        if self.enhanced:
            return ["<b>🏓 Pong!</b> <code>latency-ok</code> ✅"]
        return ["pong"]

    def _cmd_status(self, user: int, chat: int, message: int, args: list[str]) -> List[str]:
        sections: list[str] = []
        sections.append(self._safe_command_output(["uname", "-a"]))
        sections.append(self._safe_command_output(["uptime"]))
        sections.append(self._safe_command_output(["df", "-h", "/"]))
        status = "\n\n".join(s for s in sections if s)
        if self.enhanced:
            if status:
                body = f"<b>🖥️ System snapshot</b>\n<pre>{html.escape(status)}</pre>"
            else:
                body = "<b>🖥️ System snapshot</b>\n<i>Status unavailable</i>"
            return [body]
        return [status or "Status unavailable"]

    def _cmd_plugins(self, user: int, chat: int, message: int, args: list[str]) -> List[str]:
        plugins = self.available_plugins()
        if self.enhanced:
            lines = ["<b>🧩 Available plugins</b>"]
            if not plugins:
                lines.append("<i>No executable *.sh files found in plugins directory.</i>")
            else:
                for plugin in plugins:
                    label = html.escape(plugin["command"])
                    desc = plugin.get("description")
                    if desc:
                        lines.append(f"• <code>{label}</code> — {html.escape(desc)}")
                    else:
                        lines.append(f"• <code>{label}</code>")
            return ["\n".join(lines)]
        lines = ["Available plugins:"]
        for plugin in plugins:
            desc = plugin.get("description")
            label = plugin["command"]
            if desc:
                lines.append(f"• {label} – {desc}")
            else:
                lines.append(f"• {label}")
        if len(lines) == 1:
            lines.append("(no executable *.sh files found in plugins directory)")
        return ["\n".join(lines)]

    def _cmd_run_plugin(self, user: int, chat: int, message: int, args: list[str]) -> List[str]:
        if not self.is_admin(user, chat):
            return ["Admin only."]
        if not args:
            return ["Usage: /run <plugin> [args]"]
        plugin, *plugin_args = args
        result = self.execute_plugin(plugin, plugin_args, user, chat, message)
        return result or ["Plugin returned no output"]

    def _cmd_log_tail(self, user: int, chat: int, message: int, args: list[str]) -> List[str]:
        lines = 40
        if args:
            try:
                lines = max(1, min(200, int(args[0])))
            except ValueError:
                pass
        log_path = Path(self._log_path())
        if not log_path.exists():
            return [f"Log file not found: {log_path}"]
        output = self._safe_command_output(["tail", f"-n{lines}", str(log_path)])
        if self.enhanced:
            heading = f"<b>🪵 Log tail</b> (<code>{lines}</code> lines)"
            if not output:
                return [heading + "\n<i>No log entries found.</i>"]
            return [heading + f"\n<pre>{html.escape(output)}</pre>"]
        return [output]

    def _cmd_whoami(self, user: int, chat: int, message: int, args: list[str]) -> List[str]:
        if self.enhanced:
            lines = [
                "<b>🪪 Identity</b>",
                f"• <b>User ID:</b> <code>{user}</code>",
                f"• <b>Chat ID:</b> <code>{chat}</code>",
                f"• <b>Message ID:</b> <code>{message}</code>",
            ]
            if self.default_chat:
                lines.append(f"• <b>Default chat ID:</b> <code>{self.default_chat}</code>")
            return ["\n".join(lines)]
        info = [
            f"User ID: {user}",
            f"Chat ID: {chat}",
            f"Message ID: {message}",
        ]
        if self.default_chat:
            info.append(f"Default chat ID: {self.default_chat}")
        return ["\n".join(info)]

    def _cmd_clients(self, user: int, chat: int, message: int, args: list[str]) -> List[ResponseType]:
        if not self.router:
            return ["Router controls are disabled in configuration."]
        if args:
            client = self._find_client(args[0])
            if client:
                return [self._client_detail_payload(client, include_back=True)]
        return [self._build_clients_overview_payload(0)]

    def _cmd_router(self, user: int, chat: int, message: int, args: list[str]) -> List[str]:
        if not self.router:
            return ["Router controls are disabled in configuration."]
        summary = self.router.summary()
        if self.enhanced:
            lines = ["<b>🛡️ Router guard summary</b>"]
            lines.append(
                "• <b>Clients discovered:</b> "
                + str(summary.get("total_clients", 0))
                + f" (online {summary.get('online_clients', 0)})"
            )
            counts = summary.get("counts", {})
            if counts:
                pretty = ", ".join(f"{html.escape(str(k))}={v}" for k, v in counts.items())
                lines.append(f"• <b>Status counts:</b> {pretty}")
                graph = self._render_counts_graph(counts)
                if graph:
                    lines.append("<pre>" + html.escape(graph) + "</pre>")
            nft = summary.get("nft") or {}
            if nft:
                parts = [
                    f"{html.escape(key)}={'✅' if value else '⚠️'}"
                    for key, value in nft.items()
                ]
                lines.append(f"• <b>nftables:</b> {', '.join(parts)}")
            whitelist = summary.get("whitelist", [])
            if whitelist:
                extra = ""
                if len(whitelist) > 10:
                    extra = f" (+{len(whitelist) - 10} more)"
                listing = ", ".join(html.escape(item) for item in whitelist[:10])
                lines.append(f"• <b>Whitelisted:</b> {listing}{extra}")
            state_path = summary.get("state_file")
            if state_path:
                lines.append(f"• State file: <code>{html.escape(str(state_path))}</code>")
            firewall = summary.get("firewall") or {}
            if firewall:
                include_path = firewall.get("include_path")
                include_exists = firewall.get("include_exists")
                label = "present" if include_exists else "missing"
                section = firewall.get("include_section")
                detail = f" <code>{html.escape(str(include_path))}</code>" if include_path else ""
                lines.append(
                    f"• Firewall include ({html.escape(str(section))}): {label}{detail}"
                )
            return ["\n".join(lines)]
        lines = ["Router guard summary:"]
        lines.append(
            " • Clients discovered: "
            + str(summary.get("total_clients", 0))
            + f" (online {summary.get('online_clients', 0)})"
        )
        counts = summary.get("counts", {})
        if counts:
            pretty = ", ".join(f"{key}={value}" for key, value in counts.items())
            lines.append(f" • Status counts: {pretty}")
            if self.enhanced:
                graph = self._render_counts_graph(counts)
                if graph:
                    lines.append(" • Distribution:\n" + graph)
        nft = summary.get("nft") or {}
        if nft:
            lines.append(
                " • nftables: "
                + ", ".join(f"{key}={'yes' if value else 'no'}" for key, value in nft.items())
            )
        whitelist = summary.get("whitelist", [])
        if whitelist:
            lines.append(" • Whitelisted: " + ", ".join(whitelist[:10]))
            if len(whitelist) > 10:
                lines[-1] += f" (+{len(whitelist) - 10} more)"
        state_path = summary.get("state_file")
        if state_path:
            lines.append(f"State file: {state_path}")
        firewall = summary.get("firewall") or {}
        if firewall:
            include_path = firewall.get("include_path")
            include_exists = firewall.get("include_exists")
            label = "present" if include_exists else "missing"
            lines.append(
                f" • Firewall include ({firewall.get('include_section')}): {label}"
                + (f" — {include_path}" if include_path else "")
            )
        return ["\n".join(lines)]

    # ------------------------------------------------------------------
    # High-level menus and navigation

    def _menu_payload(self) -> MessagePayload:
        summary = self.router.summary() if self.router else None
        total_clients = summary.get("total_clients", 0) if summary else 0
        pending_clients = (summary.get("counts") or {}).get("pending", 0) if summary else 0
        online_clients = summary.get("online_clients", 0) if summary else 0
        if self.enhanced:
            lines = ["<b>🏠 TeleBot control centre</b>"]
            lines.append("<i>Select a section to continue.</i>")
            if summary:
                lines.append(
                    "<b>Clients:</b> "
                    + f"{total_clients} total • {pending_clients} pending • {online_clients} recently online"
                )
        else:
            lines = ["TeleBot menu", "Select an option to continue."]
            if summary:
                lines.append(
                    f"Clients: {total_clients} total, {pending_clients} pending, {online_clients} recently online"
                )
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "🖥 System snapshot", "callback_data": "menu:section:system"},
                    {"text": "🧑‍💻 Manage clients", "callback_data": "menu:clients:refresh"},
                ],
                [
                    {"text": "🔌 Plugins", "callback_data": "menu:section:plugins"},
                    {"text": "🪵 Recent logs", "callback_data": "menu:section:logs"},
                ],
                [
                    {"text": "❓ Help", "callback_data": "menu:section:help"},
                ],
            ]
        }
        return self._make_message("\n".join(lines), reply_markup=keyboard)

    def _menu_back_keyboard(self) -> Dict[str, Any]:
        return {"inline_keyboard": [[{"text": "⬅ Menu", "callback_data": "menu:root"}]]}

    def _menu_section_payload(
        self, section: str, user: int, chat: int, message: int
    ) -> MessagePayload | None:
        if section == "system":
            parts: List[str] = []
            status = self._cmd_status(user, chat, message, [])
            if status:
                parts.append(status[0])
            if self.router:
                router_section = self._cmd_router(user, chat, message, [])
                if router_section:
                    parts.append(router_section[0])
            if not parts:
                parts = ["System information unavailable"]
            return self._make_message(
                "\n\n".join(parts),
                reply_markup=self._menu_back_keyboard(),
                parse_mode="HTML" if self.uses_rich_text else None,
                disable_web_page_preview=True,
            )
        if section == "plugins":
            plugins = self._cmd_plugins(user, chat, message, [])
            text = plugins[0] if plugins else "No plugins registered."
            keyboard = {
                "inline_keyboard": [
                    [{"text": "🔄 Refresh", "callback_data": "menu:section:plugins"}],
                    [{"text": "⬅ Menu", "callback_data": "menu:root"}],
                ]
            }
            return self._make_message(text, reply_markup=keyboard)
        if section == "logs":
            logs = self._cmd_log_tail(user, chat, message, ["40"])
            text = logs[0] if logs else "No log output available."
            keyboard = {
                "inline_keyboard": [
                    [{"text": "🔄 Refresh", "callback_data": "menu:section:logs"}],
                    [{"text": "⬅ Menu", "callback_data": "menu:root"}],
                ]
            }
            return self._make_message(text, reply_markup=keyboard)
        if section == "help":
            help_lines = self._cmd_help(user, chat, message, [])
            text = help_lines[0] if help_lines else "Use /help to list commands."
            return self._make_message(text, reply_markup=self._menu_back_keyboard())
        return None

    # ------------------------------------------------------------------
    # Client menus and flows

    def _build_clients_overview_payload(self, page: int) -> MessagePayload:
        router = self.router
        if not router:
            return self._make_message("Router controls are disabled.")
        clients = router.list_clients()
        summary = router.summary()
        counts = summary.get("counts", {}) if summary else {}
        total = len(clients)
        start = max(0, page * CLIENTS_PAGE_SIZE)
        end = start + CLIENTS_PAGE_SIZE
        subset = clients[start:end]
        if self.enhanced:
            lines = ["<b>🧑‍💻 Clients dashboard</b>"]
            if total:
                lines.append(
                    f"<b>Total:</b> {total} • <b>Pending:</b> {counts.get('pending', 0)} • <b>Online:</b> {summary.get('online_clients', 0) if summary else 0}"
                )
            else:
                lines.append("<i>No clients discovered yet.</i>")
            if subset:
                rows = []
                for client in subset:
                    rows.append(html.escape(self._format_client_line(client)))
                lines.append("<pre>" + "\n".join(rows) + "</pre>")
            else:
                lines.append("<i>No entries on this page.</i>")
            lines.append("Tap a client to see available actions.")
        else:
            lines = ["Clients dashboard"]
            if total:
                lines.append(
                    f"Total {total} • Pending {counts.get('pending', 0)} • Online {summary.get('online_clients', 0) if summary else 0}"
                )
            else:
                lines.append("No clients discovered yet.")
            if subset:
                for client in subset:
                    lines.append(self._format_client_line(client))
            else:
                lines.append("No entries on this page.")
            lines.append("Use the buttons to select a client or change page.")
        keyboard_rows: List[List[Dict[str, str]]] = []
        for client in subset:
            identifier = self._client_identifier(client)
            if not identifier:
                continue
            keyboard_rows.append(
                [
                    {
                        "text": self._client_button_label(client),
                        "callback_data": f"menu:client:{identifier}",
                    }
                ]
            )
        nav_row: List[Dict[str, str]] = []
        if start > 0:
            nav_row.append({"text": "⬅ Prev", "callback_data": f"menu:clients:page:{max(0, page - 1)}"})
        nav_row.append({"text": "🔄 Refresh", "callback_data": "menu:clients:refresh"})
        if end < total:
            nav_row.append({"text": "Next ➡", "callback_data": f"menu:clients:page:{page + 1}"})
        if nav_row:
            keyboard_rows.append(nav_row)
        keyboard_rows.append([{"text": "⬅ Menu", "callback_data": "menu:root"}])
        return self._make_message(
            "\n".join(lines),
            reply_markup={"inline_keyboard": keyboard_rows},
            parse_mode="HTML" if self.uses_rich_text else None,
        )

    def _client_button_label(self, client: Dict[str, Any]) -> str:
        name = client.get("hostname") or client.get("id") or client.get("mac") or "?"
        identifier = client.get("id") or client.get("mac") or "?"
        status = client.get("status") or "unknown"
        badge = self._status_badge(status)
        trimmed_name = name[:24] + ("…" if len(name) > 24 else "")
        return f"{badge} {trimmed_name} ({identifier})"[:60]

    def _client_identifier(self, client: Dict[str, Any]) -> str | None:
        identifier = client.get("id") or client.get("mac")
        if identifier:
            return str(identifier)
        return None

    def _find_client(self, identifier: str) -> Dict[str, Any] | None:
        if not self.router:
            return None
        try:
            mac = self.router.resolve_identifier(identifier)
        except ValueError:
            mac = None
        for client in self.router.list_clients():
            if mac and client.get("mac") == mac:
                return client
            values = {
                str(client.get("id") or "").lower(),
                str(client.get("mac") or "").lower(),
                str(client.get("ip") or "").lower(),
            }
            if identifier.lower() in values:
                return client
        return None

    def _client_detail_payload(self, client: Dict[str, Any], include_back: bool = False) -> MessagePayload:
        identifier = self._client_identifier(client) or "?"
        hostname = client.get("hostname") or "Unknown"
        ip = client.get("ip") or "?"
        mac = client.get("mac") or "?"
        status = client.get("status") or "unknown"
        last_seen = self._format_age(client.get("last_seen"))
        first_seen = self._format_age(client.get("first_seen"))
        interface = client.get("interface") or "?"
        if self.enhanced:
            lines = ["<b>🧑‍💻 Client details</b>"]
            lines.append(f"<b>Name:</b> {html.escape(hostname)}")
            lines.append(f"<b>Client ID:</b> <code>{html.escape(str(identifier))}</code>")
            lines.append(f"<b>MAC:</b> <code>{html.escape(mac)}</code>")
            lines.append(f"<b>IP:</b> <code>{html.escape(ip)}</code>")
            lines.append(f"<b>Status:</b> {self._status_badge(status)} {html.escape(self._status_label(status))}")
            lines.append(f"<b>Interface:</b> {html.escape(interface)}")
            lines.append(f"<b>Last seen:</b> {html.escape(last_seen)}")
            if first_seen != "unknown":
                lines.append(f"<b>First seen:</b> {html.escape(first_seen)}")
            lines.append("Use the buttons below to update this device.")
            text = "\n".join(lines)
        else:
            text = (
                "Client details:\n"
                f"Name: {hostname}\n"
                f"ID: {identifier}\n"
                f"MAC: {mac}\n"
                f"IP: {ip}\n"
                f"Status: {self._status_label(status)}\n"
                f"Interface: {interface}\n"
                f"Last seen: {last_seen}\n"
            )
        keyboard = self._client_actions_keyboard(client, include_back)
        return self._make_message(text, reply_markup=keyboard, parse_mode="HTML" if self.uses_rich_text else None)

    def _client_actions_keyboard(self, client: Dict[str, Any], include_back: bool) -> Dict[str, Any] | None:
        identifier = self._client_identifier(client)
        if not identifier:
            return None
        actions = self._client_available_actions(client.get("status"))
        buttons: List[List[Dict[str, str]]] = []
        row: List[Dict[str, str]] = []
        for index, action in enumerate(actions):
            row.append(
                {
                    "text": self._client_action_label(action),
                    "callback_data": f"client:{action}:{identifier}",
                }
            )
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        nav_row: List[Dict[str, str]] = []
        if include_back:
            nav_row.append({"text": "⬅ Clients", "callback_data": "menu:clients:refresh"})
        nav_row.append({"text": "🔄 Refresh", "callback_data": f"menu:client:{identifier}"})
        nav_row.append({"text": "⬅ Menu", "callback_data": "menu:root"})
        if nav_row:
            buttons.append(nav_row)
        if not buttons:
            return None
        return {"inline_keyboard": buttons}

    def _client_available_actions(self, status: str | None) -> List[str]:
        mapping = {
            "approved": ["pause", "block_internet", "block_network", "whitelist", "forget"],
            "paused": ["resume", "block_internet", "block_network", "forget"],
            "blocked": ["approve", "block_internet", "whitelist", "forget"],
            "internet_blocked": ["approve", "block_network", "whitelist", "forget"],
            "whitelist": ["block_internet", "block_network", "forget"],
            "pending": [
                "approve",
                "block_internet",
                "block_network",
                "whitelist",
                "pause",
                "forget",
            ],
        }
        return mapping.get(
            status or "",
            ["approve", "block_internet", "block_network", "whitelist", "pause", "forget"],
        )

    def _status_label(self, status: str | None) -> str:
        return {
            "pending": "pending approval",
            "approved": "approved",
            "internet_blocked": "internet access blocked",
            "blocked": "blocked",
            "paused": "paused",
            "whitelist": "whitelisted",
        }.get(status or "", status or "unknown")

    def _client_action_label(self, action: str) -> str:
        return {
            "approve": "✅ Approve",
            "block": "🚫 Block",
            "block_internet": "🌐🚫 Block internet",
            "block_network": "⛔ Block network",
            "whitelist": "⭐ Whitelist",
            "pause": "⏸ Pause",
            "resume": "▶ Resume",
            "forget": "🗑 Forget",
        }.get(action, action)

    def _block_mode_payload(self, client: Dict[str, Any]) -> MessagePayload | None:
        identifier = self._client_identifier(client)
        if not identifier:
            return None
        hostname = client.get("hostname") or "Unknown"
        status = client.get("status")
        status_label = self._status_label(status)
        badge = self._status_badge(status)
        if self.enhanced:
            text = (
                "<b>🚫 Block options</b>\n"
                f"<b>Client:</b> {html.escape(hostname)} <code>{html.escape(str(identifier))}</code>\n"
                f"<b>Current:</b> {badge} {html.escape(status_label)}\n"
                "<i>Choose how strictly to block this device.</i>"
            )
        else:
            text = (
                "Block options\n"
                f"Client {hostname} ({identifier})\n"
                f"Current status: {status_label}\n"
                "Choose whether to block internet only or the entire network."
            )
        buttons = [
            [
                {
                    "text": "🌐🚫 Internet only",
                    "callback_data": f"client:block_internet:{identifier}",
                },
                {
                    "text": "⛔ Full network",
                    "callback_data": f"client:block_network:{identifier}",
                },
            ]
        ]
        buttons.append(
            [
                {"text": "⬅ Clients", "callback_data": "menu:clients:refresh"},
                {"text": "⬅ Menu", "callback_data": "menu:root"},
            ]
        )
        return self._make_message(
            text,
            reply_markup={"inline_keyboard": buttons},
            parse_mode="HTML" if self.enhanced else None,
        )

    def _interactive_client_prompt(
        self,
        actions: List[str],
        title: str,
        statuses: Optional[Iterable[str]] = None,
    ) -> MessagePayload | None:
        if not actions:
            return None
        if not self.router:
            return None
        clients = self.router.list_clients()
        if statuses is not None:
            allowed = {status.lower() for status in statuses}
            clients = [client for client in clients if (client.get("status") or "").lower() in allowed]
        if not clients:
            message = "No matching clients found."
            if self.enhanced:
                message = "<i>No matching clients found.</i>"
            return self._make_message(message, parse_mode="HTML" if self.enhanced else None)
        keyboard_rows: List[List[Dict[str, str]]] = []
        for client in clients[:10]:
            identifier = self._client_identifier(client)
            if not identifier:
                continue
            keyboard_rows.append(
                [
                    {
                        "text": self._client_button_label(client),
                        "callback_data": f"client:{actions[0]}:{identifier}",
                    }
                ]
            )
        keyboard_rows.append([{"text": "View all", "callback_data": "menu:clients:refresh"}])
        if self.enhanced:
            text = f"<b>{html.escape(title)}</b>\n<i>Select a device below.</i>"
        else:
            text = f"{title}\nSelect a device below."
        return self._make_message(
            text,
            reply_markup={"inline_keyboard": keyboard_rows},
            parse_mode="HTML" if self.enhanced else None,
        )

    def _handle_menu_callback(
        self, user: int, chat: int, message: int, data: str
    ) -> dict[str, Any]:
        if data == "menu:root":
            return self._format_callback_payload("Menu", self._menu_payload())
        if data in {"menu:clients", "menu:clients:refresh"}:
            payload = self._build_clients_overview_payload(0)
            return self._format_callback_payload("Clients", payload)
        if data.startswith("menu:clients:page:"):
            try:
                page = max(0, int(data.split(":", 3)[3]))
            except (ValueError, IndexError):
                page = 0
            payload = self._build_clients_overview_payload(page)
            return self._format_callback_payload(f"Page {page + 1}", payload)
        if data.startswith("menu:client:"):
            identifier = data.split(":", 2)[2]
            client = self._find_client(identifier)
            if client:
                payload = self._client_detail_payload(client, include_back=True)
                return self._format_callback_payload("Client", payload)
            return {"ack": "Not found", "message": "Client not found."}
        if data.startswith("menu:section:"):
            section = data.split(":", 2)[2]
            payload = self._menu_section_payload(section, user, chat, message)
            if payload:
                return self._format_callback_payload("Section", payload)
            return {"ack": "Unavailable", "message": "Section unavailable."}
        return {"ack": "Unknown"}

    def _format_callback_payload(self, ack: str, payload: MessagePayload | None) -> dict[str, Any]:
        response: dict[str, Any] = {"ack": ack}
        if not payload:
            return response
        response["message"] = payload.get("text", "")
        if "reply_markup" in payload:
            response["reply_markup"] = payload["reply_markup"]
        if "parse_mode" in payload:
            response["parse_mode"] = payload["parse_mode"]
        if "disable_web_page_preview" in payload:
            response["disable_web_page_preview"] = payload["disable_web_page_preview"]
        return response

    def _cmd_approve(self, user: int, chat: int, message: int, args: list[str]) -> List[ResponseType]:
        return self._client_action(
            args,
            self.router.approve if self.router else None,
            "Usage: /approve <id|mac|ip>",
            "approved",
            "approve",
            eligible_statuses={"pending", "blocked", "paused"},
            prompt_title="Select a device to approve",
        )

    def _cmd_block(self, user: int, chat: int, message: int, args: list[str]) -> List[ResponseType]:
        router = self.router
        if not router:
            return ["Router controls are disabled in configuration."]
        usage = "Usage: /block <id|mac|ip> [internet|network]"
        if not args:
            prompt = self._interactive_client_prompt(
                ["block"],
                "Select a device to block",
                statuses={"approved", "paused", "pending", "whitelist", "internet_blocked"},
            )
            if prompt:
                return [prompt]
            return [usage]

        identifier, *mode_args = args
        if mode_args:
            mode = mode_args[0].lower()
            if mode in {"internet", "wan", "internetonly", "wanonly"}:
                return self._client_action(
                    [identifier],
                    router.block_internet,
                    usage,
                    "internet access blocked",
                    "block_internet",
                    eligible_statuses={"approved", "paused", "pending", "whitelist", "blocked"},
                )
            if mode in {"network", "lan", "all", "full"}:
                return self._client_action(
                    [identifier],
                    router.block,
                    usage,
                    "network access blocked",
                    "block_network",
                    eligible_statuses={"approved", "paused", "pending", "whitelist", "internet_blocked"},
                )
            message_text = f"Unknown block mode: {mode}. Use 'internet' or 'network'."
            if self.enhanced:
                return [
                    self._make_message(
                        f"<b>🚫 Block</b>\n<i>{html.escape(message_text)}</i>",
                        parse_mode="HTML",
                    )
                ]
            return [message_text]

        client = self._find_client(identifier)
        if not client:
            return ["Unknown client identifier"]
        payload = self._block_mode_payload(client)
        if payload:
            return [payload]
        return [usage]

    def _cmd_whitelist(self, user: int, chat: int, message: int, args: list[str]) -> List[ResponseType]:
        return self._client_action(
            args,
            self.router.whitelist if self.router else None,
            "Usage: /whitelist <id|mac|ip>",
            "whitelisted",
            "whitelist",
            prompt_title="Select a device to whitelist",
        )

    def _cmd_pause(self, user: int, chat: int, message: int, args: list[str]) -> List[ResponseType]:
        return self._client_action(
            args,
            self.router.pause if self.router else None,
            "Usage: /pause <id|mac|ip>",
            "paused",
            "pause",
            eligible_statuses={"approved", "pending"},
            prompt_title="Select a device to pause",
        )

    def _cmd_resume(self, user: int, chat: int, message: int, args: list[str]) -> List[ResponseType]:
        return self._client_action(
            args,
            self.router.resume if self.router else None,
            "Usage: /resume <id|mac|ip>",
            "resumed",
            "resume",
            eligible_statuses={"paused"},
            prompt_title="Select a device to resume",
        )

    def _cmd_forget(self, user: int, chat: int, message: int, args: list[str]) -> List[ResponseType]:
        handler = None
        if self.router:
            handler = lambda identifier: self.router.forget(identifier) or None  # type: ignore[arg-type]
        result = self._client_action(
            args,
            handler,
            "Usage: /forget <id|mac>",
            "removed",
            "forget",
            prompt_title="Select a device to forget",
        )
        return result

    def _cmd_diagnostics(self, user: int, chat: int, message: int, args: list[str]) -> List[str]:
        base_dir = Path(__file__).resolve().parents[1]
        script = base_dir / "scripts" / "diagnostics.py"
        if not script.exists():
            return ["Diagnostics script not found."]
        python = os.environ.get("TELEBOT_PYTHON", sys.executable or "python3")
        config = os.environ.get("TELEBOT_CONFIG")
        command = [python, str(script)]
        if config:
            command.extend(["--config", config])
        output = self._safe_command_output(command)
        return [output or "Diagnostics completed with no output."]

    # ------------------------------------------------------------------
    # Public helpers used by both the bot loop and the UI API

    def authorize(self, user_id: int, chat_id: int) -> bool:
        if self.default_chat is None:
            return True
        return chat_id == self.default_chat

    def is_admin(self, user_id: int, chat_id: int) -> bool:
        return self.authorize(user_id, chat_id)

    def handle(self, user_id: int, chat_id: int, message_id: int, text: str) -> List[MessagePayload]:
        if not self.authorize(user_id, chat_id):
            self.logger(f"ignored message from {user_id}@{chat_id}: unauthorized chat")
            return self._chunk_responses(["Unauthorized chat."])
        if not text:
            return []
        try:
            parts = shlex.split(text)
        except ValueError:
            return self._chunk_responses(["Could not parse command."])
        if not parts:
            return []
        cmd, *args = parts
        handler = self.commands.get(cmd)
        if handler:
            self.logger(f"command {cmd} from {user_id}")
            response = handler(user_id, chat_id, message_id, args)
        else:
            plugin_name = cmd.lstrip("/")
            self.logger(f"plugin {cmd} from {user_id}")
            if plugin_name.lower() in self.admin_only_plugins and not self.is_admin(user_id, chat_id):
                return self._chunk_responses(["Admin only."])
            response = self.execute_plugin(plugin_name, args, user_id, chat_id, message_id)
            if not response:
                response = [f"Unknown command: {cmd}\n\nTry /help for a list of commands."]
        return self._chunk_responses(response)

    def handle_callback(self, user_id: int, chat_id: int, message_id: int, data: str) -> dict[str, Any]:
        if not data:
            return {"ack": "No action"}
        if not self.authorize(user_id, chat_id):
            return {"ack": "Unauthorized", "message": "Unauthorized chat."}
        if data.startswith("menu:"):
            return self._handle_menu_callback(user_id, chat_id, message_id, data)
        if data.startswith("client:"):
            if not self.router:
                return {"ack": "Router disabled"}
            parts = data.split(":", 2)
            if len(parts) < 3:
                return {"ack": "Malformed"}
            action, identifier = parts[1], parts[2]
            return self._handle_client_callback(action, identifier)
        return {"ack": "Unknown action"}

    def available_plugins(self) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        if not self.plugins_dir.exists():
            return items
        for script in sorted(self.plugins_dir.glob("*.sh")):
            if not os.access(script, os.X_OK):
                continue
            command = f"/{script.stem}"
            description = self._extract_description(script)
            items.append({"command": command, "path": str(script), "description": description or ""})
        return items

    def execute_plugin(
        self,
        plugin: str,
        args: list[str],
        user: int,
        chat: int,
        message: int,
    ) -> List[str]:
        plugin_path = self._find_plugin(plugin)
        if not plugin_path:
            return [f"Plugin not found: {plugin}"]
        env = os.environ.copy()
        env.update(
            {
                "TELEBOT_USER_ID": str(user),
                "TELEBOT_CHAT_ID": str(chat),
                "TELEBOT_MESSAGE_ID": str(message),
                "TELEBOT_COMMAND": plugin,
                "TELEBOT_ARGS": " ".join(args),
            }
        )
        try:
            output = subprocess.check_output([plugin_path, *args], env=env, stderr=subprocess.STDOUT, timeout=60)
            text = output.decode("utf-8", errors="ignore")
            self.logger(f"plugin {plugin} completed for {user}")
            return self._chunk_responses([text]) or ["(no output)"]
        except subprocess.CalledProcessError as exc:
            self.logger(f"plugin {plugin} failed with code {exc.returncode}")
            data = exc.output.decode("utf-8", errors="ignore") if exc.output else "Command failed"
            return [data]
        except Exception as exc:  # pragma: no cover - defensive path
            self.logger(f"plugin {plugin} raised {exc}")
            return [f"Error executing plugin: {exc}"]

    # ------------------------------------------------------------------
    # Internal helpers

    def _find_plugin(self, name: str) -> str | None:
        candidate = self.plugins_dir / f"{name.lstrip('/')}.sh"
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
        return None

    def _plugin_summary(self) -> list[str]:
        summary: list[str] = []
        for plugin in self.available_plugins():
            label = plugin["command"]
            description = plugin.get("description")
            if description:
                summary.append(f"  {label} — {description}")
            else:
                summary.append(f"  {label}")
        return summary

    def _client_action(
        self,
        args: list[str],
        handler: Optional[Callable[[str], dict]],
        usage: str,
        verb: str,
        action_name: str,
        eligible_statuses: Optional[Iterable[str]] = None,
        prompt_title: str | None = None,
    ) -> List[ResponseType]:
        router = self.router
        if handler is None or router is None:
            return ["Router controls are disabled in configuration."]
        if not args:
            prompt = self._interactive_client_prompt(
                [action_name],
                prompt_title or f"Select a device to {action_name}",
                statuses=eligible_statuses,
            )
            if prompt:
                return [prompt]
            return [usage]
        target = args[0]
        try:
            client = handler(target)
        except ValueError:
            return ["Unknown client identifier"]
        except Exception as exc:  # pragma: no cover
            return [f"Failed to update client: {exc}"]
        description = router.describe_client(client) if client else target
        emoji_map = {
            "approve": "🟢",
            "block": "🚫",
            "block_internet": "🌐🚫",
            "block_network": "⛔",
            "pause": "⏸",
            "resume": "▶️",
            "whitelist": "⭐",
            "forget": "🗑",
        }
        emoji = emoji_map.get(action_name, "✅")
        if client:
            payload = self._client_detail_payload(client, include_back=True)
            text = payload.get("text", "")
            if self.enhanced:
                header = f"<b>{emoji} {verb.capitalize()}</b>"
                payload["text"] = header + "\n" + text
            else:
                header = f"{verb.capitalize()} {description}"
                payload["text"] = header + "\n\n" + text
            return [payload]
        if self.enhanced:
            return [
                self._make_message(
                    f"<b>{emoji} {verb.capitalize()}</b>\n<code>{html.escape(description)}</code>",
                    parse_mode="HTML",
                )
            ]
        return [self._make_message(f"{verb.capitalize()} {description}")]

    def _handle_client_callback(self, action: str, identifier: str) -> dict[str, Any]:
        router = self.router
        forget_handler = (lambda ident: router.forget(ident) or None) if router else None  # type: ignore[arg-type]
        if action == "block":
            client = self._find_client(identifier)
            if not client:
                return {"ack": "Invalid", "message": "Unknown client."}
            payload = self._block_mode_payload(client)
            if not payload:
                return {"ack": "Unavailable", "message": "Block options unavailable."}
            return self._format_callback_payload("Block", payload)
        handlers = {
            "approve": (router.approve if router else None, "✅ Approved"),
            "block_internet": (
                router.block_internet if router else None,
                "🌐🚫 Internet blocked",
            ),
            "block_network": (router.block if router else None, "⛔ Network blocked"),
            "whitelist": (router.whitelist if router else None, "⭐ Whitelisted"),
            "pause": (router.pause if router else None, "⏸ Paused"),
            "resume": (router.resume if router else None, "▶️ Resumed"),
            "forget": (forget_handler, "🗑 Removed"),
        }
        handler, prefix = handlers.get(action, (None, ""))
        if handler is None:
            return {"ack": "Unsupported"}
        try:
            client = handler(identifier)
        except ValueError:
            return {"ack": "Invalid", "message": "Unknown client."}
        except Exception as exc:  # pragma: no cover
            return {"ack": "Failed", "message": f"Failed to update client: {exc}"}
        ack = prefix.strip() or "Done"
        if not router:
            return {"ack": ack}
        if action == "forget" or not client:
            if self.enhanced:
                text = (
                    f"<b>{html.escape(ack)}</b>\n"
                    f"<code>{html.escape(identifier)}</code> removed from registry."
                )
                payload = self._make_message(text, parse_mode="HTML")
            else:
                payload = self._make_message(f"{ack} {identifier} removed from registry.")
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [{"text": "⬅ Clients", "callback_data": "menu:clients:refresh"}],
                    [{"text": "⬅ Menu", "callback_data": "menu:root"}],
                ]
            }
            return self._format_callback_payload(ack, payload)
        description = router.describe_client(client)
        payload = self._client_detail_payload(client, include_back=True)
        text = payload.get("text", "")
        if self.enhanced:
            payload["text"] = f"<b>{html.escape(ack)}</b>\n" + text
        else:
            payload["text"] = f"{ack}\n\n" + text
        return self._format_callback_payload(ack, payload)

    def _format_client_line(self, client: dict) -> str:
        status = client.get("status", "unknown")
        ip = client.get("ip") or "?"
        hostname = client.get("hostname") or "(unknown)"
        mac = client.get("mac")
        identifier = client.get("id")
        seen = client.get("last_seen") or 0
        online = client.get("online")
        badge = self._status_badge(status)
        age = self._format_age(seen)
        state = "online" if online else f"seen {age} ago"
        ident = f"#{identifier}" if identifier else "-"
        mac_display = mac or "?"
        if self.enhanced:
            return (
                f"{badge:<5} {ident:<8} {mac_display:<18} {hostname[:22]:<22} "
                f"{ip:<15} {status:<9} ({state})"
            )
        return f"{badge} {hostname} {ident} {mac_display} {ip} — {status} ({state})"

    @staticmethod
    def _status_badge(status: str | None) -> str:
        return {
            "pending": "🟡",
            "approved": "🟢",
            "internet_blocked": "🛑",
            "blocked": "🔴",
            "paused": "⏸",
            "whitelist": "⭐",
        }.get(status or "", "•")

    def _render_counts_graph(self, counts: dict[str, int]) -> str:
        total = sum(int(value) for value in counts.values())
        if total <= 0:
            return ""
        order = [
            "pending",
            "internet_blocked",
            "blocked",
            "paused",
            "approved",
            "whitelist",
        ]
        lines: list[str] = []
        for status in order:
            value = int(counts.get(status, 0) or 0)
            width = max(1, int(round((value / total) * 12))) if value else 0
            bar = "█" * width if width else ""
            lines.append(f"   {self._status_badge(status)} {status:<10} {bar} {value}")
        return "\n".join(lines)

    @staticmethod
    def _format_age(timestamp: int) -> str:
        if not timestamp:
            return "unknown"
        delta = max(0, int(time.time()) - int(timestamp))
        if delta < 60:
            return f"{delta}s"
        if delta < 3600:
            return f"{delta // 60}m"
        if delta < 86400:
            return f"{delta // 3600}h"
        return f"{delta // 86400}d"

    def _chunk_responses(self, responses: Iterable[ResponseType]) -> List[MessagePayload]:
        chunks: List[MessagePayload] = []
        default_parse_mode = "HTML" if self.uses_rich_text else None
        for response in responses:
            if isinstance(response, dict):
                text = str(response.get("text", ""))
                reply_markup = response.get("reply_markup")
                parse_mode = response.get("parse_mode", default_parse_mode)
            else:
                text = str(response or "")
                reply_markup = None
                parse_mode = default_parse_mode
            if not text:
                chunks.append(self._make_message("", reply_markup=reply_markup, parse_mode=parse_mode))
                continue
            remaining = text
            while len(remaining) > MAX_MSG_LEN:
                chunk = remaining[:MAX_MSG_LEN]
                chunks.append(self._make_message(chunk, parse_mode=parse_mode))
                remaining = remaining[MAX_MSG_LEN:]
            payload = self._make_message(remaining, reply_markup=reply_markup, parse_mode=parse_mode)
            chunks.append(payload)
        return chunks

    def _make_message(
        self,
        text: str,
        reply_markup: Dict[str, Any] | None = None,
        parse_mode: str | None = None,
        disable_web_page_preview: bool | None = None,
    ) -> MessagePayload:
        payload: MessagePayload = {"text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode
        if disable_web_page_preview is not None:
            payload["disable_web_page_preview"] = disable_web_page_preview
        return payload

    def _extract_description(self, path: Path) -> str | None:
        try:
            with path.open("r", encoding="utf-8") as fh:
                for _ in range(5):
                    line = fh.readline()
                    if not line:
                        break
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        stripped = stripped.lstrip("# ")
                        if stripped:
                            return stripped
                    elif stripped:
                        break
        except OSError:
            return None
        return None

    def _safe_command_output(self, command: list[str]) -> str:
        try:
            output = subprocess.check_output(command, stderr=subprocess.STDOUT, timeout=10)
            return output.decode("utf-8", errors="ignore").strip()
        except Exception as exc:  # pragma: no cover - defensive path
            return f"Command {' '.join(command)} failed: {exc}"

    def _log_path(self) -> str:
        env_path = os.environ.get("TELEBOT_LOG_FILE")
        if env_path:
            return env_path
        return str(self.plugins_dir.parent / "log" / "openwrt-telebot.log")
