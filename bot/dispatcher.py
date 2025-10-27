import os, subprocess, shlex
self.allowed = allowed_ids
self.admins = admin_ids


def authorize(self, user_id: int) -> bool:
return user_id in self.allowed or user_id in self.admins


def is_admin(self, user_id: int) -> bool:
return user_id in self.admins


def handle(self, user_id: int, chat_id: int, message_id: int, text: str) -> list[str]:
if not self.authorize(user_id):
return ["Unauthorized user."]
if not text:
return []
parts = shlex.split(text)
if not parts:
return []
cmd, *args = parts
if cmd in ("/start", "/help"):
return [HELP_TEXT]
if cmd == "/ping":
return ["pong"]


# Map commands to plugin scripts by filename prefix
plugin = self._find_plugin(cmd)
if not plugin:
return [f"Unknown command: {cmd}\n\n{HELP_TEXT}"]


# Admin-gate risky commands
if cmd == "/reboot" and not self.is_admin(user_id):
return ["Admin only."]


env = os.environ.copy()
env.update({
"TELEBOT_USER_ID": str(user_id),
"TELEBOT_CHAT_ID": str(chat_id),
"TELEBOT_MESSAGE_ID": str(message_id),
"TELEBOT_COMMAND": cmd,
"TELEBOT_ARGS": " ".join(args),
})


try:
out = subprocess.check_output([plugin] + args, env=env, stderr=subprocess.STDOUT, timeout=25)
text = out.decode(errors="ignore")
except subprocess.CalledProcessError as e:
text = (e.output or b"Command failed").decode(errors="ignore")
except Exception as e:
text = f"Error: {e}"


# chunking for Telegram
chunks = []
while text:
chunks.append(text[:MAX_MSG_LEN])
text = text[MAX_MSG_LEN:]
return chunks


def _find_plugin(self, cmd: str):
name = cmd.lstrip("/")
candidate = os.path.join(self.plugins_dir, f"{name}.sh")
return candidate if os.path.isfile(candidate) and os.access(candidate, os.X_OK) else None
