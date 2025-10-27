import json, time, urllib.request, urllib.parse


class TelegramAPI:
def __init__(self, token: str):
self.base = f"https://api.telegram.org/bot{token}"


def _post(self, method: str, params: dict):
data = urllib.parse.urlencode(params).encode()
req = urllib.request.Request(self.base + "/" + method, data=data)
with urllib.request.urlopen(req, timeout=30) as resp:
return json.loads(resp.read().decode())


def get_updates(self, offset=None, timeout=25):
params = {"timeout": timeout}
if offset is not None:
params["offset"] = offset
return self._post("getUpdates", params)


def send_message(self, chat_id, text, reply_to_message_id=None):
params = {"chat_id": chat_id, "text": text}
if reply_to_message_id:
params["reply_to_message_id"] = reply_to_message_id
return self._post("sendMessage", params)


def send_document(self, chat_id, caption, file_path):
# Fallback: send as text if file upload w/ stdlib is too complex; we avoid multipart deps.
# For now, we chunk long text output via send_message from caller.
return self.send_message(chat_id, f"[file] {caption}: {file_path}")
