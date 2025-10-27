from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional


def log(msg: str, logfile: Optional[str] = None) -> None:
    """Log a message to stderr and optionally append to a file."""
    timestamped = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
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
