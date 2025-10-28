#!/usr/bin/env python3
"""Entry point for the OpenWRT Telegram bot service."""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict

from config_manager import ConfigManager
from dispatcher import Dispatcher
from logger import log, log_exception
from router import RouterController
from telegram_api import TelegramAPI

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = BASE_DIR / "config" / "config.json"
RUNNING = True
SCHEDULE_PATTERN = re.compile(r"^(?P<hour>\d{1,2}):(?P<minute>\d{2})$")


class AuthenticationError(RuntimeError):
    """Raised when Telegram authentication fails."""


def handle_signal(signum, _frame):
    global RUNNING
    log(f"Received signal {signum}, shutting down…")
    RUNNING = False


def load_configuration(path: Path) -> Dict[str, Any]:
    manager = ConfigManager(path)
    return manager.load()


def create_dispatcher(
    cfg: Dict[str, Any], router: RouterController | None, enhanced_notifications: bool
) -> Dispatcher:
    plugins_dir = cfg.get("plugins_dir", str(BASE_DIR / "plugins"))
    default_chat = cfg.get("chat_id_default")
    log_file = cfg.get("log_file")

    def _logger(message: str) -> None:
        log(message, log_file)

    dispatcher = Dispatcher(
        plugins_dir=plugins_dir,
        logger=_logger,
        default_chat=default_chat,
        router=router,
        enhanced_notifications=enhanced_notifications,
    )
    if default_chat:
        log(f"Dispatcher restricted to chat {default_chat}", log_file)
    return dispatcher


def sync_bot_commands(api: TelegramAPI, dispatcher: Dispatcher, log_file: str | None) -> None:
    try:
        commands = dispatcher.telegram_commands()
    except AttributeError:
        commands = []
    if not commands:
        return
    try:
        api.set_my_commands(commands)
    except Exception as exc:  # pragma: no cover - network specific
        log_exception("Failed to register Telegram commands", exc, log_file)
    else:
        log(f"Registered {len(commands)} Telegram commands", log_file)


def configure_environment(cfg: Dict[str, Any]) -> None:
    log_file = cfg.get("log_file")
    if log_file:
        os.environ["TELEBOT_LOG_FILE"] = str(log_file)
    plugins_dir = cfg.get("plugins_dir")
    if plugins_dir:
        os.environ["TELEBOT_PLUGINS"] = str(plugins_dir)
    base_dir = cfg.get("base_dir") or str(BASE_DIR)
    os.environ.setdefault("TELEBOT_BASE", str(base_dir))
    config_path = cfg.get("config_path")
    if config_path:
        os.environ["TELEBOT_CONFIG"] = str(config_path)


def parse_schedule_entries(raw: Any) -> list[int]:
    if not raw:
        return []
    candidates: list[str] = []
    if isinstance(raw, str):
        candidates = [part.strip() for part in raw.replace(";", ",").split(",")]
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                candidates.extend(part.strip() for part in item.replace(";", ",").split(","))
            else:
                candidates.append(str(item))
    else:
        candidates = [str(raw)]
    slots: set[int] = set()
    entries: list[int] = []
    for candidate in candidates:
        if not candidate:
            continue
        match = SCHEDULE_PATTERN.match(candidate)
        if not match:
            continue
        try:
            hour = int(match.group("hour"))
            minute = int(match.group("minute"))
        except ValueError:
            continue
        hour = min(23, max(0, hour))
        minute = min(59, max(0, minute))
        total = hour * 60 + minute
        if total not in slots:
            slots.add(total)
            entries.append(total)
    return sorted(entries)


def format_slot(total: int) -> str:
    hour, minute = divmod(max(0, total), 60)
    hour %= 24
    minute %= 60
    return f"{hour:02d}:{minute:02d}"


def send_scheduled_digest(
    api: TelegramAPI,
    dispatcher: Dispatcher,
    router: RouterController | None,
    chat_id: int,
    log_file: str | None,
) -> bool:
    sections: list[str] = []
    try:
        status_messages = dispatcher._cmd_status(chat_id, chat_id, 0, [])
        if status_messages:
            sections.append(status_messages[0])
        if router:
            router_messages = dispatcher._cmd_router(chat_id, chat_id, 0, [])
            if router_messages:
                sections.append(router_messages[0])
    except Exception as exc:  # pragma: no cover - defensive
        log_exception("Failed to compose scheduled digest", exc, log_file)
        return False
    message = "\n\n".join(part.strip() for part in sections if part and part.strip())
    if not message:
        log("Scheduled digest skipped: empty content", log_file, level="DEBUG")
        return False
    try:
        api.send_message(
            chat_id,
            message,
            parse_mode="HTML" if dispatcher.uses_rich_text else None,
            disable_web_page_preview=True,
        )
        return True
    except Exception as exc:  # pragma: no cover - network
        log_exception("Failed to send scheduled digest", exc, log_file)
        return False


def poll_once(
    api: TelegramAPI,
    dispatcher: Dispatcher,
    poll_timeout: int,
    offset: int | None,
    log_file: str | None,
    router: RouterController | None,
    default_chat: int | None,
    enhanced_notifications: bool,
) -> int | None:
    log(f"Polling updates offset={offset} timeout={poll_timeout}", log_file)
    if router:
        try:
            refresh = router.refresh_clients()
        except Exception as exc:  # pragma: no cover - system specific
            log_exception("Client refresh failed", exc, log_file)
        else:
            for client in refresh.get("new_pending", []):
                notify_new_client(
                    api,
                    router,
                    client,
                    default_chat,
                    log_file,
                    enhanced_notifications,
                )
    try:
        updates = api.get_updates(offset=offset, timeout=poll_timeout)
    except Exception as exc:  # pragma: no cover - network/HTTP errors
        log(f"Polling error: {exc}", log_file)
        time.sleep(5)
        return offset

    if not isinstance(updates, dict):
        log("Telegram returned a non-dict response; retrying in 5s", log_file)
        time.sleep(5)
        return offset

    if not updates.get("ok"):
        log(
            "Telegram indicated failure: "
            + json.dumps({k: updates.get(k) for k in ("error_code", "description") if updates.get(k) is not None}),
            log_file,
        )
        if updates.get("error_code") == 401:
            log(
                "Telegram rejected the bot token (401). Verify the token in config.json and restart the service.",
                log_file,
                level="ERROR",
            )
            raise AuthenticationError("Telegram authentication failed (401)")
        time.sleep(5)
        return offset

    results = updates.get("result", [])
    log(f"Received {len(results)} updates from Telegram", log_file)

    for update in results:
        callback = update.get("callback_query")
        if callback:
            handle_callback_update(api, dispatcher, callback, log_file)
            offset = max(offset or 0, update.get("update_id", 0) + 1)
            continue
        offset = max(offset or 0, update.get("update_id", 0) + 1)
        message = update.get("message") or update.get("edited_message")
        if not message:
            continue
        text = message.get("text", "")
        chat_id = (message.get("chat") or {}).get("id")
        user_id = (message.get("from") or {}).get("id")
        message_id = message.get("message_id")
        if chat_id is None or user_id is None:
            continue
        log(f"<- {user_id}@{chat_id}: {text}", log_file)
        responses = dispatcher.handle(user_id, chat_id, message_id or 0, text)
        for response in responses:
            if isinstance(response, dict):
                payload = response
                message_text = str(payload.get("text", ""))
                reply_markup = payload.get("reply_markup")
                parse_mode = payload.get("parse_mode")
                disable_preview = payload.get("disable_web_page_preview")
            else:
                message_text = str(response)
                reply_markup = None
                parse_mode = None
                disable_preview = None
            if parse_mode is None:
                parse_mode = "HTML" if dispatcher.uses_rich_text else None
            if not message_text and not reply_markup:
                continue
            try:
                log(
                    f"-> sending to {chat_id} (reply={message_id}) {min(80, len(message_text))} chars",
                    log_file,
                )
                api.send_message(
                    chat_id,
                    message_text,
                    reply_to_message_id=message_id,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                    disable_web_page_preview=disable_preview,
                )
                log(f"-> sent to {chat_id}", log_file)
            except Exception as exc:  # pragma: no cover - network/HTTP errors
                log(f"Failed to send message: {exc}", log_file)
                time.sleep(1)
    return offset


def run_bot(config_path: Path, once: bool = False) -> None:
    cfg = load_configuration(config_path)
    cfg.setdefault("base_dir", str(BASE_DIR))
    cfg.setdefault("config_path", str(config_path))
    log_file = cfg.get("log_file")
    poll_timeout = int(cfg.get("poll_timeout", 25))
    token = cfg.get("bot_token")
    if not token:
        raise RuntimeError("Telegram bot token missing from configuration")

    log(f"Loaded configuration from {config_path}", log_file)
    log(
        "Bot starting with settings "
        + json.dumps({
            "poll_timeout": poll_timeout,
            "chat_id_default": cfg.get("chat_id_default"),
            "plugins_dir": cfg.get("plugins_dir"),
        }),
        log_file,
    )

    log(
        "UI API token configured=" + ("yes" if cfg.get("ui_api_token") else "no"),
        log_file,
        level="DEBUG",
    )

    configure_environment(cfg)
    router: RouterController | None = None
    try:
        router = RouterController(
            cfg,
            logger=lambda message, logfile=None, level="INFO": log(message, log_file, level=level),
        )
        router.ensure_nft()
        log("Router controller initialised", log_file)
    except Exception as exc:  # pragma: no cover - defensive
        log_exception("Router controller unavailable", exc, log_file)
        router = None

    enhanced_notifications = bool(cfg.get("enhanced_notifications"))

    schedule_minutes = parse_schedule_entries(cfg.get("notification_schedule"))
    if schedule_minutes:
        slots = ", ".join(format_slot(slot) for slot in schedule_minutes)
        log(f"Scheduled digests configured at {slots}", log_file, level="INFO")

    dispatcher = create_dispatcher(cfg, router, enhanced_notifications)
    api = TelegramAPI(token)

    sent_today: set[int] = set()
    last_day: int | None = None

    def maybe_send_scheduled_notifications() -> None:
        nonlocal sent_today, last_day
        if not schedule_minutes:
            return
        chat_default = cfg.get("chat_id_default")
        try:
            chat_target = int(chat_default)
        except (TypeError, ValueError):
            chat_target = None
        if not chat_target:
            return
        now_struct = time.localtime()
        day_marker = now_struct.tm_yday
        if last_day != day_marker:
            sent_today.clear()
            last_day = day_marker
        current_minutes = now_struct.tm_hour * 60 + now_struct.tm_min
        for slot in schedule_minutes:
            if slot in sent_today or current_minutes < slot:
                continue
            sent_today.add(slot)
            success = send_scheduled_digest(api, dispatcher, router, chat_target, log_file)
            label = format_slot(slot)
            if success:
                log(f"Scheduled digest sent at {label}", log_file)
            else:
                log(f"Scheduled digest failed at {label}", log_file, level="WARNING")

    try:
        profile = api.get_me()
    except Exception as exc:  # pragma: no cover - network
        log_exception("Telegram handshake failed", exc, log_file)
        raise
    else:
        if isinstance(profile, dict) and profile.get("ok") and profile.get("result"):
            result = profile["result"]
            username = result.get("username") or result.get("first_name") or "unknown"
            identifier = result.get("id")
            log(f"Authenticated to Telegram as {username} (id {identifier})", log_file)
        else:
            log("Unexpected response from getMe(); continuing but please verify token", log_file, level="WARNING")

    sync_bot_commands(api, dispatcher, log_file)

    log("TeleBot starting…", log_file)
    offset: int | None = None

    global RUNNING
    RUNNING = True

    while RUNNING:
        try:
            offset = poll_once(
                api,
                dispatcher,
                poll_timeout,
                offset,
                log_file,
                router,
                cfg.get("chat_id_default"),
                enhanced_notifications,
            )
        except AuthenticationError:
            RUNNING = False
            raise
        except Exception as exc:  # pragma: no cover - defensive
            log_exception("Polling iteration failed", exc, log_file)
            time.sleep(5)
        maybe_send_scheduled_notifications()
        if once:
            break
    log("TeleBot stopped.", log_file)


def notify_new_client(
    api: TelegramAPI,
    router: RouterController,
    client: Any,
    chat_id: int | None,
    log_file: str | None,
    enhanced_notifications: bool,
) -> None:
    if not chat_id:
        return
    client_data = client if isinstance(client, dict) else client.to_dict()
    details = router.describe_client(client_data)
    client_id = client_data.get("id") or client_data.get("mac")
    keyboard = {
        "inline_keyboard": [
            [
                {
                    "text": "✅ Approve",
                    "callback_data": f"client:approve:{client_id}",
                },
                {
                    "text": "🚫 Block",
                    "callback_data": f"client:block:{client_id}",
                },
            ],
            [
                {
                    "text": "⭐ Whitelist",
                    "callback_data": f"client:whitelist:{client_id}",
                },
            ],
        ]
    }
    parse_mode = None
    if enhanced_notifications:
        summary = router.summary()
        counts = summary.get("counts", {})
        graph = _render_status_graph(counts)
        name = html.escape(client_data.get("hostname") or "Unknown")
        mac = html.escape(client_data.get("mac") or "?")
        ip = html.escape(client_data.get("ip") or "?")
        identifier = html.escape(str(client_id or "?"))
        details_lines = [
            "<b>🆕 New device detected</b>",
            f"<b>Name:</b> {name}",
            f"<b>Client ID:</b> {identifier}",
            f"<b>MAC:</b> {mac}",
            f"<b>IP:</b> {ip}",
            f"<b>Status:</b> pending approval",
        ]
        if graph:
            details_lines.append("<b>Current client mix:</b>")
            details_lines.append(f"<pre>{html.escape(graph)}</pre>")
        details_lines.append(
            "Use the buttons below or /menu, /approve, /block, /whitelist commands to manage this device."
        )
        text = "\n".join(details_lines)
        parse_mode = "HTML"
    else:
        text = (
            "🆕 New device detected\n"
            f"{details}\n\n"
            "Approve, block, or whitelist the device using the buttons below or the /menu command."
        )
    try:
        api.send_message(chat_id, text, reply_markup=keyboard, parse_mode=parse_mode)
        if client_id:
            router.mark_notified(client_id)
    except Exception as exc:  # pragma: no cover
        log(f"Failed to notify new client: {exc}", log_file, level="ERROR")


def _render_status_graph(counts: Dict[str, Any]) -> str:
    total = sum(int(value) for value in counts.values() if isinstance(value, int))
    if total <= 0:
        return ""
    order = [
        ("pending", "🟡"),
        ("blocked", "🔴"),
        ("paused", "⏸"),
        ("approved", "🟢"),
        ("whitelist", "⭐"),
    ]
    lines: list[str] = []
    for status, icon in order:
        value = int(counts.get(status, 0) or 0)
        if value < 0:
            value = 0
        if total:
            width = max(1, int(round((value / total) * 12))) if value else 0
        else:
            width = 0
        bar = "█" * width if width else ""
        label = status.capitalize()
        lines.append(f"{icon} {label:<10} {bar} {value}")
    return "\n".join(lines)


def handle_callback_update(api: TelegramAPI, dispatcher: Dispatcher, callback: dict, log_file: str | None) -> None:
    callback_id = callback.get("id")
    data = callback.get("data") or ""
    from_user = (callback.get("from") or {}).get("id") or 0
    message = callback.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    result = dispatcher.handle_callback(from_user, chat_id or 0, message_id or 0, data)
    ack_text = result.get("ack")
    try:
        if callback_id:
            api.answer_callback_query(callback_id, text=ack_text)
    except Exception as exc:  # pragma: no cover
        log(f"Failed answering callback: {exc}", log_file, level="ERROR")
    message_text = result.get("message")
    reply_markup = result.get("reply_markup")
    parse_mode = result.get("parse_mode")
    if parse_mode is None and dispatcher.uses_rich_text:
        parse_mode = "HTML"
    disable_preview = result.get("disable_web_page_preview")
    if message_text and chat_id:
        try:
            if message_id:
                api.edit_message_text(
                    chat_id,
                    message_id,
                    message_text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                )
            else:
                api.send_message(
                    chat_id,
                    message_text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                    disable_web_page_preview=disable_preview,
                )
        except Exception as exc:  # pragma: no cover
            log(f"Failed updating message: {exc}", log_file, level="ERROR")
            try:
                api.send_message(
                    chat_id,
                    message_text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                    disable_web_page_preview=disable_preview,
                )
            except Exception:
                pass



def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the OpenWRT Telegram bot")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to config.json")
    parser.add_argument("--once", action="store_true", help="Poll for a single iteration and exit")
    args = parser.parse_args(argv)

    config_path = Path(args.config).resolve()
    if not config_path.exists():
        parser.error(f"Configuration file not found: {config_path}")

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        run_bot(config_path, once=args.once)
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # pragma: no cover
        log(f"Fatal error: {exc}")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
