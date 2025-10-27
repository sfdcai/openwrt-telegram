#!/usr/bin/env python3
"""Run a lightweight HTTP server for developing the TeleBot UI locally."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from http.server import CGIHTTPRequestHandler, ThreadingHTTPServer


def main() -> int:
    parser = argparse.ArgumentParser(description="Preview the TeleBot web UI")
    parser.add_argument(
        "--port", type=int, default=8081, help="Port to bind the HTTP server to"
    )
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parents[1] / "www"),
        help="Directory that contains index.html and assets",
    )
    parser.add_argument(
        "--base",
        default=str(Path(__file__).resolve().parents[1]),
        help="TeleBot base directory for imports",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    base_dir = Path(args.base).resolve()

    if not root.exists():
        parser.error(f"Root directory not found: {root}")

    os.environ.setdefault("TELEBOT_BASE", str(base_dir))
    os.environ.setdefault("TELEBOT_CONFIG", str(base_dir / "config" / "config.json"))

    os.chdir(root)

    handler = CGIHTTPRequestHandler
    handler.cgi_directories = ["/cgi-bin"]

    with ThreadingHTTPServer(("0.0.0.0", args.port), handler) as httpd:
        print(f"Serving {root} via http://127.0.0.1:{args.port}/ (CTRL+C to stop)")
        httpd.serve_forever()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
