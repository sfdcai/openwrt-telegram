#!/usr/bin/env python3
"""Entry point for the OpenWRT Telegram bot service."""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict

from config_manager import ConfigManager
from dispatcher import Dispatcher
from logger import log
from telegram_api import TelegramAPI

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = BASE_DIR / "config" / "config.json"
RUNNING = True


def handle_signal(signum, _frame):
    global RUNNING
    log(f"Received signal {signum}, shutting down…")
    RUNNING = False


def load_configuration(path: Path) -> Dict[str, Any]:
    manager = ConfigManager(path)
    return manager.load()


def create_dispatcher(cfg: Dict[str, Any]) -> Dispatcher:
    plugins_dir = cfg.get("plugins_dir", str(BASE_DIR / "plugins"))
    allowed = cfg.get("allowed_user_ids", [])
    admins = cfg.get("admin_user_ids", [])
    default_chat = cfg.get("chat_id_default")
    log_file = cfg.get("log_file")

    def _logger(message: str) -> None:
        log(message, log_file)

    dispatcher = Dispatcher(
        plugins_dir=plugins_dir,
        logger=_logger,
        allowed_ids=allowed,
        admin_ids=admins,
        default_chat=default_chat,
    )
    return dispatcher


def configure_environment(cfg: Dict[str, Any]) -> None:
    log_file = cfg.get("log_file")
    if log_file:
        os.environ["TELEBOT_LOG_FILE"] = str(log_file)
    plugins_dir = cfg.get("plugins_dir")
    if plugins_dir:
        os.environ["TELEBOT_PLUGINS"] = str(plugins_dir)


def poll_once(api: TelegramAPI, dispatcher: Dispatcher, poll_timeout: int, offset: int | None, log_file: str | None) -> int | None:
    try:
        updates = api.get_updates(offset=offset, timeout=poll_timeout)
    except Exception as exc:  # pragma: no cover - network/HTTP errors
        log(f"Polling error: {exc}", log_file)
        time.sleep(5)
        return offset

    if not isinstance(updates, dict) or not updates.get("ok"):
        log("Telegram returned an unexpected response; retrying in 5s", log_file)
        time.sleep(5)
        return offset

    for update in updates.get("result", []):
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
            try:
                api.send_message(chat_id, response, reply_to_message_id=message_id)
                log(f"-> {chat_id}: {min(80, len(response))} chars", log_file)
            except Exception as exc:  # pragma: no cover - network/HTTP errors
                log(f"Failed to send message: {exc}", log_file)
                time.sleep(1)
    return offset


def run_bot(config_path: Path, once: bool = False) -> None:
    cfg = load_configuration(config_path)
    log_file = cfg.get("log_file")
    poll_timeout = int(cfg.get("poll_timeout", 25))
    token = cfg.get("bot_token")
    if not token:
        raise RuntimeError("Telegram bot token missing from configuration")

    configure_environment(cfg)
    dispatcher = create_dispatcher(cfg)
    api = TelegramAPI(token)

    log("TeleBot starting…", log_file)
    offset: int | None = None

    global RUNNING
    RUNNING = True

    while RUNNING:
        offset = poll_once(api, dispatcher, poll_timeout, offset, log_file)
        if once:
            break
    log("TeleBot stopped.", log_file)



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
