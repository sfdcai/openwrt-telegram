from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Callable, List

MAX_MSG_LEN = 3900
ADMIN_PLUGIN_NAMES = {"reboot", "poweroff"}


class Dispatcher:
    """Translate incoming Telegram messages into actions."""

    def __init__(
        self,
        plugins_dir: str | os.PathLike[str],
        logger: Callable[[str], None],
        default_chat: int | None = None,
    ):
        self.plugins_dir = Path(plugins_dir)
        self.logger = logger
        self.default_chat = int(default_chat) if default_chat is not None else None
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
