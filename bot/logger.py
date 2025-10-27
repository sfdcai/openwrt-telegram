import sys, time


def log(msg: str, logfile: str | None = None):
line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
try:
sys.stderr.write(line + "\n")
except Exception:
pass
if logfile:
try:
with open(logfile, "a") as f:
f.write(line + "\n")
except Exception:
pass
