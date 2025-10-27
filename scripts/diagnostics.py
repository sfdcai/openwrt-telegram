#!/usr/bin/env python3
"""Environment diagnostic helper for OpenWRT TeleBot deployments."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = BASE_DIR / "config" / "config.json"


def run_command(cmd: Iterable[str]) -> tuple[int, str]:
    """Run a command and capture its exit status and output."""
    try:
        output = subprocess.check_output(list(cmd), stderr=subprocess.STDOUT, timeout=10)
        return 0, output.decode("utf-8", errors="ignore").strip()
    except subprocess.CalledProcessError as exc:  # pragma: no cover - diagnostic utility
        return exc.returncode, exc.output.decode("utf-8", errors="ignore").strip()
    except FileNotFoundError:
        return 127, "command not found"
    except Exception as exc:  # pragma: no cover - defensive
        return 1, f"{type(exc).__name__}: {exc}"


def load_config(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def check_paths(cfg: Dict[str, Any]) -> list[str]:
    checks: list[str] = []
    log_file = cfg.get("log_file")
    if log_file:
        lf = Path(log_file)
        checks.append(f"Log file: {lf} ({'present' if lf.exists() else 'missing'})")
    state_file = cfg.get("client_state_file")
    if state_file:
        sf = Path(state_file)
        checks.append(f"Client state file: {sf} ({'present' if sf.exists() else 'missing'})")
    plugins_dir = cfg.get("plugins_dir")
    if plugins_dir:
        pd = Path(plugins_dir)
        checks.append(f"Plugins directory: {pd} ({'present' if pd.exists() else 'missing'})")
    return checks


def check_services() -> list[str]:
    checks: list[str] = []
    init_script = Path("/etc/init.d/openwrt-telebot")
    if init_script.exists():
        code, output = run_command([str(init_script), "status"])
        state = "running" if code == 0 else "stopped or unknown"
        checks.append(f"Service status: {state}\n{output}".strip())
    else:
        checks.append("Service status: init script not installed")
    code, output = run_command(["ps", "w"])
    if code == 0:
        running = [line for line in output.splitlines() if "openwrt-telebot/bot/main.py" in line]
        checks.append(f"Bot process entries: {len(running)}")
        checks.extend(running[:5])
    else:
        checks.append(f"Process list unavailable: {output}")
    return checks


def check_telegram(cfg: Dict[str, Any]) -> list[str]:
    checks: list[str] = []
    token = cfg.get("bot_token")
    chat_id = cfg.get("chat_id_default")
    checks.append(f"Telegram token configured: {'yes' if token and token != '123456789:YOUR_TELEGRAM_BOT_TOKEN' else 'no'}")
    checks.append(f"Default chat id set: {'yes' if chat_id else 'no'}")
    return checks


def check_nft(cfg: Dict[str, Any]) -> list[str]:
    checks: list[str] = []
    nft = shutil.which("nft")
    if not nft:
        checks.append("nftables binary not found – client approval will not work")
        return checks
    table = cfg.get("nft_table")
    chain = cfg.get("nft_chain")
    block_set = cfg.get("nft_block_set")
    allow_set = cfg.get("nft_allow_set")
    if not table or not chain:
        checks.append("NFT table/chain not configured")
        return checks
    code, output = run_command([nft, "list", "table", table])
    checks.append(f"nft table '{table}': {'ok' if code == 0 else 'missing'}")
    if code != 0:
        checks.append(output)
    if block_set:
        code, output = run_command([nft, "list", "set", table, block_set])
        checks.append(f"block set '{block_set}': {'ok' if code == 0 else 'missing'}")
        if code != 0:
            checks.append(output)
    if allow_set:
        code, output = run_command([nft, "list", "set", table, allow_set])
        checks.append(f"allow set '{allow_set}': {'ok' if code == 0 else 'missing'}")
        if code != 0:
            checks.append(output)
    return checks


def check_uhttpd() -> list[str]:
    checks: list[str] = []
    init_script = Path("/etc/init.d/uhttpd")
    if init_script.exists():
        code, output = run_command([str(init_script), "status"])
        state = "running" if code == 0 else "stopped or unknown"
        checks.append(f"uhttpd status: {state}")
        if output:
            checks.append(output)
    else:
        checks.append("uhttpd init script not found")
    web_root = Path("/www/telebot/index.html")
    checks.append(f"Web UI deployed: {'yes' if web_root.exists() else 'no'}")
    return checks


def check_ui(cfg: Dict[str, Any]) -> list[str]:
    checks: list[str] = []
    base = cfg.get("ui_base_url", "/telebot")
    checks.append(f"UI base URL: {base}")
    checks.append(f"UI API token configured: {'yes' if cfg.get('ui_api_token') else 'no'}")
    cgi_fs = Path("/www/cgi-bin/telebot.py")
    checks.append(f"CGI script deployed: {'yes' if cgi_fs.exists() else 'no'}")
    assets_root = Path("/www") / base.strip("/") / "index.html"
    checks.append(f"Dashboard index present: {'yes' if assets_root.exists() else 'no'}")
    url = f"http://127.0.0.1/cgi-bin/telebot.py?action=status"
    request = urllib.request.Request(url)
    token = cfg.get("ui_api_token")
    if token:
        request.add_header("X-Auth-Token", token)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            checks.append(f"Local CGI status: HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        checks.append(f"Local CGI status: HTTP {exc.code}")
        body = exc.read().decode("utf-8", errors="ignore")
        if body:
            checks.append(f"CGI response: {body[:200]}")
    except Exception as exc:  # pragma: no cover - diagnostics
        checks.append(f"Local CGI status: {type(exc).__name__}: {exc}")
    return checks


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose an OpenWRT TeleBot installation")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to config.json")
    args = parser.parse_args()

    config_path = Path(args.config)
    print(f"==> Base directory: {BASE_DIR}")
    print(f"==> Config path: {config_path}")
    if not config_path.exists():
        print("Configuration file not found")
        sys.exit(1)

    try:
        cfg = load_config(config_path)
    except Exception as exc:  # pragma: no cover - diagnostic helper
        print(f"Failed to read configuration: {exc}")
        sys.exit(1)

    print("\n== Configuration summary ==")
    print(json.dumps({k: v for k, v in cfg.items() if k != "bot_token"}, indent=2))
    print("Telegram token masked:", "yes" if cfg.get("bot_token") else "no")

    print("\n== Path checks ==")
    for line in check_paths(cfg):
        print(line)

    print("\n== Telegram configuration ==")
    for line in check_telegram(cfg):
        print(line)

    print("\n== Service status ==")
    for line in check_services():
        print(line)

    print("\n== nftables status ==")
    for line in check_nft(cfg):
        print(line)

    print("\n== uhttpd status ==")
    for line in check_uhttpd():
        print(line)

    print("\n== Web UI check ==")
    for line in check_ui(cfg):
        print(line)


if __name__ == "__main__":  # pragma: no cover - script entrypoint
    main()
