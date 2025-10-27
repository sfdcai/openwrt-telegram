#!/usr/bin/env python3
"""CGI helper for managing the OpenWRT TeleBot."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict
from urllib.parse import parse_qs

BASE_DIR = Path(os.environ.get("TELEBOT_BASE", "/opt/openwrt-telebot"))
if not BASE_DIR.exists():
    BASE_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.environ.get("TELEBOT_CONFIG", BASE_DIR / "config" / "config.json"))
VERSION_PATH = BASE_DIR / "VERSION"

sys.path.insert(0, str(BASE_DIR / "bot"))

from config_manager import ConfigManager  # type: ignore  # pylint: disable=wrong-import-position
from dispatcher import Dispatcher  # type: ignore  # pylint: disable=wrong-import-position
from logger import log, log_exception  # type: ignore  # pylint: disable=wrong-import-position
from router import RouterController  # type: ignore  # pylint: disable=wrong-import-position
from telegram_api import TelegramAPI  # type: ignore  # pylint: disable=wrong-import-position


def respond(status: int, payload: Dict[str, Any]) -> None:
    message = "OK" if status == 200 else "Error"
    sys.stdout.write(f"Status: {status} {message}\r\n")
    sys.stdout.write("Content-Type: application/json\r\n\r\n")
    json.dump(payload, sys.stdout)
    sys.stdout.write("\n")


def read_version() -> str:
    try:
        with VERSION_PATH.open("r", encoding="utf-8") as handle:
            return handle.readline().strip() or "dev"
    except FileNotFoundError:
        return "dev"
    except Exception as exc:  # pragma: no cover - defensive
        log_exception("Failed to read VERSION file", exc, None)
        return "dev"


def read_body() -> Dict[str, Any]:
    length = int(os.environ.get("CONTENT_LENGTH", "0") or "0")
    if length <= 0:
        return {}
    raw = sys.stdin.read(length)
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def ensure_authenticated(cfg: Dict[str, Any], query: Dict[str, list[str]]) -> bool:
    expected = cfg.get("ui_api_token", "") or ""
    if not expected:
        return True
    provided = os.environ.get("HTTP_X_AUTH_TOKEN")
    if not provided:
        provided = (query.get("token") or [""])[0]
    if not provided:
        provided = os.environ.get("HTTP_AUTHORIZATION", "").removeprefix("Bearer ")
    return provided == expected


def unauthorized_response(cfg: Dict[str, Any]) -> Dict[str, Any]:
    hint_parts = [
        "Save the UI API token locally via the dashboard form and click ‘Save token’.",
    ]
    config_path = cfg.get("config_path") or str(CONFIG_PATH)
    hint_parts.append(f"Token source: {config_path}")
    if cfg.get("ui_api_token"):
        hint_parts.append("Ensure the browser is sending the same value in the X-Auth-Token header.")
    else:
        hint_parts.append("No UI token configured; leave the field blank or clear stored tokens.")
    return {
        "ok": False,
        "error": "Unauthorized",
        "hint": " ".join(hint_parts),
        "token_configured": bool(cfg.get("ui_api_token")),
    }


def get_dispatcher(cfg: Dict[str, Any], router: RouterController | None = None) -> Dispatcher:
    plugins_dir = cfg.get("plugins_dir", str(BASE_DIR / "plugins"))
    log_file = cfg.get("log_file")
    if log_file:
        os.environ["TELEBOT_LOG_FILE"] = str(log_file)
    dispatcher = Dispatcher(
        plugins_dir=plugins_dir,
        logger=lambda _m: None,
        default_chat=cfg.get("chat_id_default"),
        router=router,
    )
    return dispatcher


def bot_status() -> Dict[str, Any]:
    identifiers: list[int] = []
    try:
        output = subprocess.check_output(["pgrep", "-f", "openwrt-telebot/bot/main.py"], stderr=subprocess.DEVNULL)
        identifiers = [int(pid) for pid in output.decode().strip().split() if pid.strip().isdigit()]
    except Exception:
        try:
            output = subprocess.check_output(["ps", "w"], stderr=subprocess.DEVNULL)
            for line in output.decode().splitlines():
                if "openwrt-telebot/bot/main.py" in line:
                    parts = line.strip().split()
                    if parts and parts[0].isdigit():
                        identifiers.append(int(parts[0]))
        except Exception:
            identifiers = []
    return {"running": bool(identifiers), "pids": identifiers}


def system_info() -> str:
    sections: list[str] = []
    for command in (["uname", "-a"], ["uptime"], ["df", "-h", "/"]):
        try:
            output = subprocess.check_output(command, stderr=subprocess.STDOUT, timeout=10)
            sections.append(output.decode("utf-8", errors="ignore").strip())
        except Exception as exc:  # pragma: no cover
            sections.append(f"{' '.join(command)} failed: {exc}")
    return "\n\n".join(sections)


def read_logs(path: str | None, lines: int = 80) -> str:
    if not path:
        return ""
    log_path = Path(path)
    if not log_path.exists():
        return ""
    try:
        output = subprocess.check_output(["tail", f"-n{lines}", str(log_path)], stderr=subprocess.STDOUT)
        return output.decode("utf-8", errors="ignore")
    except Exception:
        try:
            with log_path.open("r", encoding="utf-8", errors="ignore") as handle:
                return handle.read()
        except Exception:
            return ""


def mask_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    masked = dict(cfg)
    token = masked.get("bot_token")
    masked["bot_token_masked"] = ConfigManager.mask_token(token)
    if "bot_token" in masked:
        del masked["bot_token"]
    return masked


def save_config(manager: ConfigManager, payload: Dict[str, Any]) -> Dict[str, Any]:
    current = manager.load()
    updated = dict(current)

    token_value = str(payload.get("bot_token", "")).strip()
    current_mask = ConfigManager.mask_token(current.get("bot_token"))
    if token_value and token_value != current_mask:
        updated["bot_token"] = token_value

    for key in (
        "plugins_dir",
        "log_file",
        "ui_api_token",
        "ui_base_url",
        "client_state_file",
        "nft_table",
        "nft_chain",
        "nft_block_set",
        "nft_allow_set",
    ):
        if key in payload and payload[key] is not None:
            updated[key] = str(payload[key]).strip()

    if payload.get("chat_id_default"):
        try:
            updated["chat_id_default"] = int(payload["chat_id_default"])
        except (TypeError, ValueError):
            updated["chat_id_default"] = None
    else:
        updated["chat_id_default"] = None

    whitelist_raw = payload.get("client_whitelist")
    if whitelist_raw is not None:
        if isinstance(whitelist_raw, list):
            updated["client_whitelist"] = [str(item).strip() for item in whitelist_raw if str(item).strip()]
        else:
            updated["client_whitelist"] = [
                item.strip()
                for item in str(whitelist_raw).replace(";", ",").split(",")
                if item.strip()
            ]

    try:
        updated["poll_timeout"] = max(5, int(payload.get("poll_timeout", current.get("poll_timeout", 25))))
    except (TypeError, ValueError):
        updated["poll_timeout"] = current.get("poll_timeout", 25)

    manager.save(updated)
    return updated


def send_message(cfg: Dict[str, Any], message: str, chat_id: int | None = None) -> None:
    token = cfg.get("bot_token")
    if not token:
        raise RuntimeError("Bot token not configured")
    chat = chat_id or cfg.get("chat_id_default")
    if not chat:
        raise RuntimeError("Chat ID is required")
    api = TelegramAPI(token)
    api.send_message(chat, message)


def run_plugin(cfg: Dict[str, Any], plugin: str, args: list[str]) -> str:
    dispatcher = get_dispatcher(cfg)
    result = dispatcher.execute_plugin(plugin, args, user=0, chat=cfg.get("chat_id_default") or 0, message=0)
    return "\n".join(result)


def control_service(command: str) -> None:
    service = Path("/etc/init.d/openwrt-telebot")
    if not service.exists():
        raise RuntimeError("Init script not installed")
    subprocess.check_call([str(service), command])


def main() -> None:
    manager = ConfigManager(CONFIG_PATH)
    try:
        cfg = manager.load()
    except FileNotFoundError:
        respond(500, {"ok": False, "error": "config.json not found"})
        return
    cfg.setdefault("base_dir", str(BASE_DIR))
    cfg.setdefault("config_path", str(CONFIG_PATH))
    log_file = cfg.get("log_file")

    query = parse_qs(os.environ.get("QUERY_STRING", ""))
    if not ensure_authenticated(cfg, query):
        remote = os.environ.get("REMOTE_ADDR", "?")
        token_present = bool(cfg.get("ui_api_token"))
        log(
            f"UI authentication failed from {remote} (token configured={'yes' if token_present else 'no'})",
            log_file,
            level="WARNING",
        )
        respond(401, unauthorized_response(cfg))
        return

    action = (query.get("action") or [""])[0]
    if not action:
        respond(400, {"ok": False, "error": "Missing action"})
        return

    method = os.environ.get("REQUEST_METHOD", "GET").upper()
    payload = read_body() if method == "POST" else {}

    router: RouterController | None = None
    remote = os.environ.get("REMOTE_ADDR", "?")
    log(f"UI {action} requested via {method} from {remote}", log_file, level="DEBUG")
    try:
        router = RouterController(
            cfg,
            logger=lambda message, logfile=None, level="INFO": log(message, log_file, level=level),
        )
        router.ensure_nft()
    except Exception as exc:  # pragma: no cover - defensive
        router = None
        log_exception("Router controller init failed in UI", exc, log_file)

    try:
        if action == "status":
            dispatcher = get_dispatcher(cfg, router)
            client_info = {"clients": [], "counts": {}}
            if router:
                refresh = router.refresh_clients()
                client_info["clients"] = refresh.get("clients", [])
                counts: Dict[str, int] = {}
                for item in client_info["clients"]:
                    status = item.get("status", "unknown")
                    counts[status] = counts.get(status, 0) + 1
                client_info["counts"] = counts
            response = {
                "ok": True,
                "bot": bot_status(),
                "system": {"info": system_info()},
                "config": mask_config(cfg),
                "plugins": dispatcher.available_plugins(),
                "log_tail": read_logs(cfg.get("log_file")),
                "clients": client_info,
                "version": {
                    "app": read_version(),
                    "base_dir": str(BASE_DIR),
                },
                "auth": {
                    "token_required": bool(cfg.get("ui_api_token")),
                    "config_path": cfg.get("config_path"),
                },
            }
            respond(200, response)
        elif action == "save_config":
            updated = save_config(manager, payload)
            respond(200, {"ok": True, "config": mask_config(updated)})
        elif action == "send_test":
            send_message(cfg, "✅ OpenWRT TeleBot test message", chat_id=None)
            respond(200, {"ok": True})
        elif action == "send_message":
            chat_id = payload.get("chat_id")
            chat = int(chat_id) if chat_id else None
            message = str(payload.get("message", "")).strip()
            if not message:
                raise RuntimeError("Message text required")
            send_message(cfg, message, chat_id=chat)
            respond(200, {"ok": True})
        elif action == "run_plugin":
            plugin = payload.get("plugin")
            if not plugin:
                raise RuntimeError("Plugin name required")
            args = shlex_split(payload.get("args", ""))
            output = run_plugin(cfg, plugin, args)
            respond(200, {"ok": True, "output": output})
        elif action == "logs":
            respond(200, {"ok": True, "log_tail": read_logs(cfg.get("log_file"))})
        elif action == "clients":
            if not router:
                raise RuntimeError("Router controller unavailable")
            refresh = router.refresh_clients()
            respond(200, {"ok": True, "clients": refresh.get("clients", [])})
        elif action == "client_action":
            if not router:
                raise RuntimeError("Router controller unavailable")
            client_action = payload.get("action")
            target = payload.get("target")
            if not client_action or not target:
                raise RuntimeError("Client action and target are required")
            if client_action == "approve":
                client = router.approve(str(target))
            elif client_action == "block":
                client = router.block(str(target))
            elif client_action == "pause":
                client = router.pause(str(target))
            elif client_action == "resume":
                client = router.resume(str(target))
            elif client_action == "whitelist":
                client = router.whitelist(str(target))
            elif client_action == "forget":
                router.forget(str(target))
                client = None
            else:
                raise RuntimeError("Unsupported client action")
            result = {"ok": True}
            if client:
                result["client"] = client
            respond(200, result)
        elif action == "control":
            command = payload.get("command")
            if command not in {"start", "stop", "restart", "reload"}:
                raise RuntimeError("Unsupported command")
            control_service(command)
            respond(200, {"ok": True})
        else:
            respond(400, {"ok": False, "error": f"Unknown action: {action}"})
    except Exception as exc:
        log_exception(f"UI action {action} failed", exc, log_file)
        respond(400, {"ok": False, "error": str(exc)})


def shlex_split(value: Any) -> list[str]:
    import shlex

    if not value:
        return []
    try:
        return shlex.split(str(value))
    except ValueError:
        return []


if __name__ == "__main__":
    main()

