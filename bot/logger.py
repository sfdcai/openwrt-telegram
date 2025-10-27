from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path
from typing import Optional


def _format(level: str, msg: str) -> str:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    return f"{timestamp} [{level.upper()}] {msg}"


def log(msg: str, logfile: Optional[str] = None, level: str = "INFO") -> None:
    """Log a message to stderr and optionally append to a file."""
    timestamped = _format(level, msg)
    try:
        sys.stderr.write(timestamped + "\n")
    except Exception:
        pass
    if logfile:
        try:
            Path(logfile).parent.mkdir(parents=True, exist_ok=True)
            with open(logfile, "a", encoding="utf-8") as handle:
                handle.write(timestamped + "\n")
        except Exception:
            pass


def log_exception(msg: str, exc: BaseException, logfile: Optional[str] = None) -> None:
    """Log an error with traceback details."""
    formatted = _format("ERROR", f"{msg}: {exc}")
    try:
        sys.stderr.write(formatted + "\n")
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
    except Exception:
        pass
    if logfile:
        try:
            Path(logfile).parent.mkdir(parents=True, exist_ok=True)
            with open(logfile, "a", encoding="utf-8") as handle:
                handle.write(formatted + "\n")
                traceback.print_exception(type(exc), exc, exc.__traceback__, file=handle)
        except Exception:
            pass
