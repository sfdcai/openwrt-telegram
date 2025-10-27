from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict


class TelegramAPI:
    """Minimal Telegram Bot API client that only relies on the stdlib."""

    def __init__(self, token: str):
        if not token:
            raise ValueError("Token must not be empty")
        self.base = f"https://api.telegram.org/bot{token}"

    def _post(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        encoded = urllib.parse.urlencode(params).encode("utf-8")
        request = urllib.request.Request(self.base + "/" + method, data=encoded)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:  # pragma: no cover - network specific
            try:
                details = exc.read().decode("utf-8", errors="ignore")
            except Exception:  # pragma: no cover - defensive
                details = ""
            message = f"Telegram API error during {method}: HTTP {exc.code}"
            if details:
                message += f" — {details.strip()}"
            raise RuntimeError(message) from exc
        except urllib.error.URLError as exc:  # pragma: no cover
            raise RuntimeError(f"Telegram API unreachable: {exc.reason}") from exc
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:  # pragma: no cover
            raise RuntimeError("Failed to decode Telegram response") from exc

    def get_updates(self, offset: int | None = None, timeout: int = 25) -> Dict[str, Any]:
        params: Dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        return self._post("getUpdates", params)

    def send_message(self, chat_id: int | str, text: str, reply_to_message_id: int | None = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_to_message_id:
            params["reply_to_message_id"] = reply_to_message_id
        response = self._post("sendMessage", params)
        if not isinstance(response, dict) or not response.get("ok"):
            raise RuntimeError(f"sendMessage failed: {response}")
        return response

    def send_document(self, chat_id: int | str, caption: str, file_path: str) -> Dict[str, Any]:
        # Stdlib-only upload is cumbersome; instead, notify the user where the file lives.
        message = f"[file] {caption}: {file_path}"
        return self.send_message(chat_id, message)
