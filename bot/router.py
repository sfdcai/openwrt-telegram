"""Router-oriented helpers for managing client access via nftables."""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from logger import log


MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


def _normalize_mac(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip().lower()
    if cleaned.count("-") == 5:
        cleaned = cleaned.replace("-", ":")
    if cleaned.count(".") == 2 and len(cleaned) == 14:
        cleaned = ":".join([cleaned[i : i + 2] for i in range(0, len(cleaned), 2)])
    if MAC_RE.match(cleaned):
        return cleaned
    return None


def _safe_hostname(value: str | None) -> str:
    if not value or value in {"*", "-"}:
        return ""
    return value.strip()


def _now() -> int:
    return int(time.time())


@dataclass
class Client:
    mac: str
    ip: str
    hostname: str
    status: str
    first_seen: int
    last_seen: int
    online: bool = False
    interface: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mac": self.mac,
            "ip": self.ip,
            "hostname": self.hostname,
            "status": self.status,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "online": self.online,
            "interface": self.interface,
        }


class RouterController:
    """Manage LAN clients and nftables state."""

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_BLOCKED = "blocked"
    STATUS_WHITELIST = "whitelist"

    def __init__(self, cfg: Dict[str, Any], logger: Callable[[str, Optional[str], str], None] | None = None):
        self.cfg = cfg
        self.log_file = cfg.get("log_file")
        self._logger = logger or (lambda message, logfile=None, level="INFO": log(message, logfile, level=level))
        base_dir = Path(cfg.get("state_dir") or Path(cfg.get("base_dir", "."))).resolve()
        if not base_dir:
            base_dir = Path("/opt/openwrt-telebot")
        self.state_path = Path(cfg.get("client_state_file") or (base_dir / "state" / "clients.json"))
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.nft_binary = cfg.get("nft_binary", "nft")
        self.nft_table = cfg.get("nft_table", "telebot")
        self.nft_chain = cfg.get("nft_chain", "client_guard")
        self.nft_block_set = cfg.get("nft_block_set", "blocked_clients")
        self.nft_allow_set = cfg.get("nft_allow_set", "approved_clients")
        self.whitelist = {_normalize_mac(mac) for mac in cfg.get("client_whitelist", []) if _normalize_mac(mac)}
        self.state: Dict[str, Any] = {"clients": {}}
        self._nft_ready = False
        self._nft_supported = True
        self._load_state()

    # ------------------------------------------------------------------
    # Persistence

    def _load_state(self) -> None:
        if self.state_path.exists():
            try:
                with self.state_path.open("r", encoding="utf-8") as handle:
                    self.state = json.load(handle)
            except Exception as exc:  # pragma: no cover - defensive
                self._logger(f"Failed to load client state: {exc}", self.log_file, "ERROR")
                self.state = {"clients": {}}
        if "clients" not in self.state or not isinstance(self.state["clients"], dict):
            self.state = {"clients": {}}

    def _save_state(self) -> None:
        try:
            tmp_path = self.state_path.with_suffix(".tmp")
            with tmp_path.open("w", encoding="utf-8") as handle:
                json.dump(self.state, handle, indent=2, sort_keys=True)
                handle.write("\n")
            tmp_path.replace(self.state_path)
        except Exception as exc:  # pragma: no cover - defensive
            self._logger(f"Failed to persist client state: {exc}", self.log_file, "ERROR")

    # ------------------------------------------------------------------
    # Discovery

    def refresh_clients(self) -> Dict[str, Any]:
        now = _now()
        discovered = self._discover_clients()
        clients_state: Dict[str, Any] = self.state.setdefault("clients", {})
        new_pending: List[Client] = []

        for mac, info in discovered.items():
            entry = clients_state.get(mac)
            is_new = entry is None
            hostname = info.get("hostname") or (entry or {}).get("hostname") or ""
            status = (entry or {}).get("status")
            if not status:
                if mac in self.whitelist:
                    status = self.STATUS_WHITELIST
                else:
                    status = self.STATUS_PENDING
            if entry is None:
                entry = {
                    "status": status,
                    "first_seen": now,
                }
            entry.update(
                {
                    "ip": info.get("ip") or entry.get("ip"),
                    "hostname": hostname,
                    "last_seen": now,
                }
            )
            clients_state[mac] = entry

            client_obj = Client(
                mac=mac,
                ip=entry.get("ip") or "",
                hostname=hostname,
                status=status,
                first_seen=entry.get("first_seen", now),
                last_seen=now,
                online=True,
                interface=info.get("interface", ""),
            )
            if status == self.STATUS_PENDING and is_new:
                new_pending.append(client_obj)
            self._apply_nft_status(mac, status)

        # mark offline clients
        results: List[Dict[str, Any]] = []
        for mac, entry in clients_state.items():
            if mac not in discovered:
                entry.setdefault("first_seen", now)
                entry.setdefault("last_seen", now)
            record = self._client_from_state(mac, entry)
            record["status"] = entry.get("status", self.STATUS_PENDING)
            record["ip"] = entry.get("ip", "")
            record["hostname"] = entry.get("hostname", "")
            record["last_seen"] = entry.get("last_seen")
            record["first_seen"] = entry.get("first_seen")
            record["online"] = mac in discovered
            record["interface"] = discovered.get(mac, {}).get("interface", "")
            results.append(record)

        results.sort(key=lambda item: (self._status_order(item.get("status")), -(item.get("last_seen") or 0)))

        self._save_state()
        return {
            "clients": results,
            "new_pending": new_pending,
        }

    def _client_from_state(self, mac: str, data: Dict[str, Any]) -> Dict[str, Any]:
        return Client(
            mac=mac,
            ip=data.get("ip", ""),
            hostname=data.get("hostname", ""),
            status=data.get("status", self.STATUS_PENDING),
            first_seen=int(data.get("first_seen", 0) or 0),
            last_seen=int(data.get("last_seen", 0) or 0),
            online=False,
        ).to_dict()

    def _discover_clients(self) -> Dict[str, Dict[str, Any]]:
        clients: Dict[str, Dict[str, Any]] = {}
        leases = self._read_dhcp_leases()
        neigh = self._read_ip_neighbors()

        for mac, info in leases.items():
            clients.setdefault(mac, {}).update(info)
        for mac, info in neigh.items():
            record = clients.setdefault(mac, {})
            for key, value in info.items():
                if value:
                    record[key] = value
        return clients

    def _read_dhcp_leases(self) -> Dict[str, Dict[str, Any]]:
        paths = [
            Path(self.cfg.get("dhcp_leases_path", "/tmp/dhcp.leases")),
            Path("/var/dhcp.leases"),
        ]
        clients: Dict[str, Dict[str, Any]] = {}
        for path in paths:
            if not path.exists():
                continue
            try:
                with path.open("r", encoding="utf-8", errors="ignore") as handle:
                    for line in handle:
                        parts = line.split()
                        if len(parts) < 5:
                            continue
                        _expires, mac, ip, hostname = parts[:4]
                        normalized = _normalize_mac(mac)
                        if not normalized:
                            continue
                        clients[normalized] = {
                            "ip": ip.strip(),
                            "hostname": _safe_hostname(hostname),
                        }
            except Exception as exc:  # pragma: no cover
                self._logger(f"Failed reading {path}: {exc}", self.log_file, "ERROR")
        return clients

    def _read_ip_neighbors(self) -> Dict[str, Dict[str, Any]]:
        clients: Dict[str, Dict[str, Any]] = {}
        command = self.cfg.get("ip_neigh_command", ["ip", "neigh", "show", "dev", "br-lan"])
        if isinstance(command, str):
            command = command.split()
        fallback = ["ip", "neigh", "show"]
        for cmd in (command, fallback):
            try:
                output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=5)
                text = output.decode("utf-8", errors="ignore")
            except Exception:
                continue
            for line in text.splitlines():
                parts = line.split()
                if len(parts) < 5:
                    continue
                ip = parts[0]
                if "lladdr" not in parts:
                    continue
                try:
                    idx = parts.index("lladdr")
                except ValueError:
                    continue
                mac = _normalize_mac(parts[idx + 1] if len(parts) > idx + 1 else None)
                if not mac:
                    continue
                interface = parts[2] if len(parts) > 2 else ""
                clients[mac] = {
                    "ip": ip,
                    "interface": interface,
                }
            if clients:
                break
        return clients

    # ------------------------------------------------------------------
    # nftables integration

    def ensure_nft(self) -> None:
        if not self._nft_supported or self._nft_ready:
            return
        try:
            subprocess.run(
                [self.nft_binary, "list", "table", "inet", self.nft_table],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            subprocess.run(
                [self.nft_binary, "add", "table", "inet", self.nft_table],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )

            self._ensure_set(self.nft_block_set)
            self._ensure_set(self.nft_allow_set)
            self._ensure_chain()
            self._ensure_drop_rule()
            self._nft_ready = True
        except FileNotFoundError:
            self._nft_supported = False
            self._logger("nft binary not found; client enforcement disabled", self.log_file, "WARNING")
        except Exception as exc:  # pragma: no cover
            self._nft_supported = False
            self._logger(f"Failed to initialise nftables: {exc}", self.log_file, "ERROR")

    def _ensure_set(self, name: str) -> None:
        script = f"add set inet {self.nft_table} {name} {{ type etheraddr; size 65535; }}"
        self._run_nft(script)

    def _ensure_chain(self) -> None:
        script = (
            f"add chain inet {self.nft_table} {self.nft_chain} "
            "{ type filter hook forward priority 0; policy accept; }"
        )
        self._run_nft(script)

    def _ensure_drop_rule(self) -> None:
        script = (
            f"add rule inet {self.nft_table} {self.nft_chain} "
            f"ether saddr @${self.nft_block_set} drop"
        )
        self._run_nft(script)

    def _run_nft(self, script: str) -> None:
        if not self._nft_supported:
            return
        try:
            subprocess.run(
                [self.nft_binary, "-f", "-"],
                input=(script + "\n").encode("utf-8"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception as exc:  # pragma: no cover
            self._logger(f"nft command failed: {exc}", self.log_file, "ERROR")

    def _apply_nft_status(self, mac: str, status: str) -> None:
        if not mac:
            return
        if status == self.STATUS_WHITELIST:
            self._nft_remove(self.nft_block_set, mac)
            return
        if status == self.STATUS_APPROVED:
            self._nft_remove(self.nft_block_set, mac)
            self._nft_add(self.nft_allow_set, mac)
            return
        if status in {self.STATUS_PENDING, self.STATUS_BLOCKED}:
            self._nft_add(self.nft_block_set, mac)
            self._nft_remove(self.nft_allow_set, mac)

    def _nft_add(self, set_name: str, mac: str) -> None:
        if not set_name:
            return
        self.ensure_nft()
        script = f"add element inet {self.nft_table} {set_name} {{ {mac} }}"
        self._run_nft(script)

    def _nft_remove(self, set_name: str, mac: str) -> None:
        if not set_name:
            return
        self.ensure_nft()
        script = f"delete element inet {self.nft_table} {set_name} {{ {mac} }}"
        self._run_nft(script)

    def _nft_resource_exists(self, kind: str, name: str | None = None) -> bool:
        if not self._nft_supported or not self.nft_binary:
            return False
        if kind == "table":
            if not self.nft_table:
                return False
            command = [self.nft_binary, "list", "table", "inet", self.nft_table]
        elif kind == "set":
            if not self.nft_table or not name:
                return False
            command = [self.nft_binary, "list", "set", "inet", self.nft_table, name]
        else:
            return False
        try:
            subprocess.check_output(command, stderr=subprocess.DEVNULL, timeout=5)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Client manipulation

    def list_clients(self) -> List[Dict[str, Any]]:
        clients = []
        for mac, data in self.state.get("clients", {}).items():
            record = self._client_from_state(mac, data)
            record["status"] = data.get("status", self.STATUS_PENDING)
            record["ip"] = data.get("ip", "")
            record["hostname"] = data.get("hostname", "")
            record["last_seen"] = data.get("last_seen")
            record["first_seen"] = data.get("first_seen")
            record["online"] = False
            clients.append(record)
        clients.sort(key=lambda item: (self._status_order(item.get("status")), -(item.get("last_seen") or 0)))
        return clients

    def summary(self) -> Dict[str, Any]:
        clients = self.list_clients()
        counts: Dict[str, int] = {}
        online = 0
        now = _now()
        for client in clients:
            status = client.get("status", "unknown")
            counts[status] = counts.get(status, 0) + 1
            last_seen = int(client.get("last_seen") or 0)
            if last_seen and now - last_seen < 120:
                online += 1
        nft_status = {
            "supported": self._nft_supported,
            "ready": self._nft_ready,
            "table_exists": self._nft_resource_exists("table"),
            "block_set_exists": self._nft_resource_exists("set", self.nft_block_set),
            "allow_set_exists": self._nft_resource_exists("set", self.nft_allow_set),
        }
        return {
            "total_clients": len(clients),
            "online_clients": online,
            "counts": counts,
            "whitelist": sorted(self.whitelist),
            "state_file": str(self.state_path),
            "nft": nft_status,
        }

    def find_client(self, identifier: str) -> Optional[str]:
        ident = identifier.strip().lower()
        mac = _normalize_mac(ident)
        if mac and mac in self.state.get("clients", {}):
            return mac
        for key, value in self.state.get("clients", {}).items():
            if value.get("ip") == ident:
                return key
        return None

    def set_status(self, mac: str, status: str) -> Dict[str, Any]:
        mac_normalized = _normalize_mac(mac)
        if not mac_normalized:
            raise ValueError("Invalid MAC address")
        if status not in {
            self.STATUS_PENDING,
            self.STATUS_APPROVED,
            self.STATUS_BLOCKED,
            self.STATUS_WHITELIST,
        }:
            raise ValueError("Unsupported status")
        clients = self.state.setdefault("clients", {})
        entry = clients.setdefault(mac_normalized, {"first_seen": _now()})
        entry["status"] = status
        entry.setdefault("hostname", "")
        entry.setdefault("ip", "")
        entry["last_seen"] = entry.get("last_seen", _now())
        self._apply_nft_status(mac_normalized, status)
        self._save_state()
        return self._client_from_state(mac_normalized, entry)

    def approve(self, mac: str) -> Dict[str, Any]:
        client = self.set_status(mac, self.STATUS_APPROVED)
        self._logger(f"Client {client['mac']} approved", self.log_file, "INFO")
        return client

    def block(self, mac: str) -> Dict[str, Any]:
        client = self.set_status(mac, self.STATUS_BLOCKED)
        self._logger(f"Client {client['mac']} blocked", self.log_file, "WARNING")
        return client

    def whitelist(self, mac: str) -> Dict[str, Any]:
        client = self.set_status(mac, self.STATUS_WHITELIST)
        self._logger(f"Client {client['mac']} whitelisted", self.log_file, "INFO")
        return client

    def forget(self, mac: str) -> None:
        mac_normalized = _normalize_mac(mac)
        if not mac_normalized:
            raise ValueError("Invalid MAC address")
        clients = self.state.get("clients", {})
        if mac_normalized in clients:
            del clients[mac_normalized]
            self._nft_remove(self.nft_block_set, mac_normalized)
            self._nft_remove(self.nft_allow_set, mac_normalized)
            self._save_state()
            self._logger(f"Client {mac_normalized} removed from registry", self.log_file, "INFO")

    # ------------------------------------------------------------------
    # Formatting helpers

    def describe_client(self, client: Dict[str, Any]) -> str:
        hostname = client.get("hostname") or "Unknown"
        ip = client.get("ip") or "?"
        status = client.get("status")
        label = {
            self.STATUS_PENDING: "pending approval",
            self.STATUS_APPROVED: "approved",
            self.STATUS_BLOCKED: "blocked",
            self.STATUS_WHITELIST: "whitelisted",
        }.get(status, status or "unknown")
        return f"{hostname} ({client.get('mac')}) — {ip} — {label}"

    def _status_order(self, status: str | None) -> int:
        order = {
            self.STATUS_PENDING: 0,
            self.STATUS_BLOCKED: 1,
            self.STATUS_APPROVED: 2,
            self.STATUS_WHITELIST: 3,
        }
        return order.get(status or "", 99)

