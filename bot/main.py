#!/usr/bin/env python3
global RUNNING
RUNNING = False


signal.signal(signal.SIGINT, handle_sig)
signal.signal(signal.SIGTERM, handle_sig)


with open(CONF) as f:
cfg = json.load(f)


token = cfg["bot_token"]
allowed = set(cfg.get("allowed_user_ids", []))
admins = set(cfg.get("admin_user_ids", []))
chat_default = cfg.get("chat_id_default")
plugins_dir = cfg.get("plugins_dir", "/opt/openwrt-telebot/plugins")
log_file = cfg.get("log_file")
poll_timeout = int(cfg.get("poll_timeout", 25))


api = TelegramAPI(token)
dispatch = Dispatcher(plugins_dir, lambda m: log(m, log_file), allowed, admins)


offset = None
log("TeleBot starting…", log_file)


while RUNNING:
try:
updates = api.get_updates(offset=offset, timeout=poll_timeout)
if not updates.get("ok", False):
time.sleep(2)
continue
for upd in updates.get("result", []):
offset = max(offset or 0, upd.get("update_id", 0) + 1)
msg = upd.get("message") or upd.get("edited_message")
if not msg:
continue
text = msg.get("text", "")
chat = msg.get("chat", {}).get("id")
user = (msg.get("from") or {}).get("id")
mid = msg.get("message_id")
if not chat or not user:
continue
log(f"<- {user}@{chat}: {text}", log_file)
responses = dispatch.handle(user, chat, mid, text)
for r in responses:
api.send_message(chat, r, reply_to_message_id=mid)
log(f"-> {chat}: {min(60, len(r))} chars", log_file)
except Exception as e:
log(f"Loop error: {e}", log_file)
time.sleep(3)


log("TeleBot stopped.", log_file)
