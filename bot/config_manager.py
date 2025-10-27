"""Configuration utilities for OpenWRT TeleBot."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict


class ConfigManager:
    """Load and persist configuration for the Telegram bot and UI."""

    def __init__(self, path: os.PathLike[str] | str):
        self.path = Path(path)
        if not self.path.parent.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            raise FileNotFoundError(f"Configuration file not found: {self.path}")
        with self.path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    def save(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_name = tempfile.mkstemp(prefix="telebot-config-", dir=str(self.path.parent))
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
                fh.write("\n")
            shutil.move(tmp_name, self.path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def update(self, **updates: Any) -> Dict[str, Any]:
        cfg = self.load()
        cfg.update(updates)
        self.save(cfg)
        return cfg

    @staticmethod
    def mask_token(token: str | None) -> str:
        if not token:
            return ""
        if len(token) <= 6:
            return "***" + token[-2:]
        return token[:3] + "…" + token[-4:]

    def ensure_defaults(self, defaults: Dict[str, Any]) -> Dict[str, Any]:
        if self.path.exists():
            data = self.load()
        else:
            data = {}
        changed = False
        for key, value in defaults.items():
            if key not in data:
                data[key] = value
                changed = True
        if changed:
            self.save(data)
        return data
