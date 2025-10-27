from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from router import RouterController

MAX_MSG_LEN = 3900
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
        self.uses_rich_text = False
        extra_admin_plugins = os.environ.get("TELEBOT_ADMIN_PLUGINS", "")
        self.admin_only_plugins = {
            name.strip().lower() for name in extra_admin_plugins.split(",") if name.strip()
        } | ADMIN_PLUGIN_NAMES

        self.commands: dict[str, Callable[[int, int, int, list[str]], List[str]]] = {
            "/start": self._cmd_help,
            "/help": self._cmd_help,
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
        available = [
            "Commands:",
            "/ping - simple heartbeat",
            "/status - system information",
            "/plugins - list available shell plugins",
            "/run <plugin> [args] - execute a plugin",
            "/log [lines] - tail the bot log",
            "/whoami - display your identifiers",
            "/clients - list known devices",
            "/router - router guard summary",
            "/approve <id|mac|ip> - allow a device",
            "/block <id|mac|ip> - block a device",
            "/pause <id|mac|ip> - temporarily suspend a client",
            "/resume <id|mac|ip> - restore a paused client",
            "/whitelist <id|mac|ip> - always allow a device",
            "/forget <id|mac> - remove device from registry",
            "/diag - run deployment diagnostics",
        ]
        return ["\n".join(available + self._plugin_summary())]

    def _cmd_ping(self, user: int, chat: int, message: int, args: list[str]) -> List[str]:
        return ["pong"]

    def _cmd_status(self, user: int, chat: int, message: int, args: list[str]) -> List[str]:
        sections: list[str] = []
        sections.append(self._safe_command_output(["uname", "-a"]))
        sections.append(self._safe_command_output(["uptime"]))
        sections.append(self._safe_command_output(["df", "-h", "/"]))
        status = "\n\n".join(s for s in sections if s)
        return [status or "Status unavailable"]

    def _cmd_plugins(self, user: int, chat: int, message: int, args: list[str]) -> List[str]:
        lines = ["Available plugins:"]
        for plugin in self.available_plugins():
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
        return [self._safe_command_output(["tail", f"-n{lines}", str(log_path)])]

    def _cmd_whoami(self, user: int, chat: int, message: int, args: list[str]) -> List[str]:
        info = [
            f"User ID: {user}",
            f"Chat ID: {chat}",
            f"Message ID: {message}",
        ]
        if self.default_chat:
            info.append(f"Default chat ID: {self.default_chat}")
        return ["\n".join(info)]

    def _cmd_clients(self, user: int, chat: int, message: int, args: list[str]) -> List[str]:
        if not self.router:
            return ["Router controls are disabled in configuration."]
        clients = self.router.list_clients()
        if not clients:
            return ["No clients have been discovered yet."]
        if self.enhanced:
            header = "ID       MAC               Hostname                  IP              Status     Last Seen"
            lines = ["Known clients:", header, "-" * len(header)]
        else:
            lines = ["Known clients:"]
        for client in clients:
            lines.append(self._format_client_line(client))
        return ["\n".join(lines)]

    def _cmd_router(self, user: int, chat: int, message: int, args: list[str]) -> List[str]:
        if not self.router:
            return ["Router controls are disabled in configuration."]
        summary = self.router.summary()
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

    def _cmd_approve(self, user: int, chat: int, message: int, args: list[str]) -> List[str]:
        return self._client_action(args, self.router.approve if self.router else None, "Usage: /approve <id|mac|ip>", "approved")

    def _cmd_block(self, user: int, chat: int, message: int, args: list[str]) -> List[str]:
        return self._client_action(args, self.router.block if self.router else None, "Usage: /block <id|mac|ip>", "blocked")

    def _cmd_whitelist(self, user: int, chat: int, message: int, args: list[str]) -> List[str]:
        return self._client_action(args, self.router.whitelist if self.router else None, "Usage: /whitelist <id|mac|ip>", "whitelisted")

    def _cmd_pause(self, user: int, chat: int, message: int, args: list[str]) -> List[str]:
        return self._client_action(args, self.router.pause if self.router else None, "Usage: /pause <id|mac|ip>", "paused")

    def _cmd_resume(self, user: int, chat: int, message: int, args: list[str]) -> List[str]:
        return self._client_action(args, self.router.resume if self.router else None, "Usage: /resume <id|mac|ip>", "resumed")

    def _cmd_forget(self, user: int, chat: int, message: int, args: list[str]) -> List[str]:
        if not self.router:
            return ["Router controls are disabled."]
        if not args:
            return ["Usage: /forget <id|mac>"]
        target = args[0]
        try:
            self.router.forget(target)
            return [f"Removed {target} from registry."]
        except ValueError:
            return ["Unknown client identifier"]
        except Exception as exc:
            return [f"Failed to remove: {exc}"]

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

    def handle(self, user_id: int, chat_id: int, message_id: int, text: str) -> List[str]:
        if not self.authorize(user_id, chat_id):
            self.logger(f"ignored message from {user_id}@{chat_id}: unauthorized chat")
            return ["Unauthorized chat."]
        if not text:
            return []
        try:
            parts = shlex.split(text)
        except ValueError:
            return ["Could not parse command."]
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
                return ["Admin only."]
            response = self.execute_plugin(plugin_name, args, user_id, chat_id, message_id)
            if not response:
                response = [f"Unknown command: {cmd}\n\nTry /help for a list of commands."]
        return self._chunk_responses(response)

    def handle_callback(self, user_id: int, chat_id: int, message_id: int, data: str) -> dict[str, str]:
        if not data:
            return {"ack": "No action"}
        if not self.authorize(user_id, chat_id):
            return {"ack": "Unauthorized", "message": "Unauthorized chat."}
        if data.startswith("client:"):
            if not self.router:
                return {"ack": "Router disabled"}
            parts = data.split(":")
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
    ) -> List[str]:
        router = self.router
        if handler is None or router is None:
            return ["Router controls are disabled in configuration."]
        if not args:
            return [usage]
        target = args[0]
        try:
            client = handler(target)
        except ValueError:
            return ["Unknown client identifier"]
        except Exception as exc:  # pragma: no cover
            return [f"Failed to update client: {exc}"]
        return [f"{verb.capitalize()} {router.describe_client(client)}"]

    def _handle_client_callback(self, action: str, identifier: str) -> dict[str, str]:
        router = self.router
        handlers = {
            "approve": (router.approve if router else None, "✅ Approved"),
            "block": (router.block if router else None, "🚫 Blocked"),
            "whitelist": (router.whitelist if router else None, "⭐ Whitelisted"),
            "pause": (router.pause if router else None, "⏸ Paused"),
            "resume": (router.resume if router else None, "🟢 Resumed"),
        }
        handler, prefix = handlers.get(action, (None, ""))
        if handler is None:
            return {"ack": "Unsupported"}
        try:
            client = handler(identifier)
        except ValueError:
            return {"ack": "Invalid"}
        except Exception as exc:  # pragma: no cover
            return {"ack": "Failed", "message": f"Failed to update client: {exc}"}
        message = f"{prefix} {router.describe_client(client)}" if router else prefix.strip()
        return {"ack": prefix.strip() or "Done", "message": message}

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
                f"{badge} {ident:<8} {mac_display:<18} {hostname[:22]:<22} "
                f"{ip:<15} {status:<9} ({state})"
            )
        return f"{badge} {hostname} {ident} {mac_display} {ip} — {status} ({state})"

    @staticmethod
    def _status_badge(status: str | None) -> str:
        return {
            "pending": "🟡",
            "approved": "🟢",
            "blocked": "🔴",
            "paused": "⏸",
            "whitelist": "⭐",
        }.get(status or "", "•")

    def _render_counts_graph(self, counts: dict[str, int]) -> str:
        total = sum(int(value) for value in counts.values())
        if total <= 0:
            return ""
        order = ["pending", "blocked", "paused", "approved", "whitelist"]
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

    def _chunk_responses(self, responses: Iterable[str]) -> List[str]:
        chunks: List[str] = []
        for response in responses:
            text = response or ""
            while len(text) > MAX_MSG_LEN:
                chunks.append(text[:MAX_MSG_LEN])
                text = text[MAX_MSG_LEN:]
            chunks.append(text)
        return chunks

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
