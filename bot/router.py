"""Router-oriented helpers for managing client access via nftables."""
from __future__ import annotations

import json
import re
import shutil
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
    client_id: str = ""

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
            "id": self.client_id,
        }


class RouterController:
    """Manage LAN clients and nftables state."""

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_BLOCKED = "blocked"
    STATUS_PAUSED = "paused"
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
        self.firewall_include_path = Path(
            cfg.get("firewall_include_path", "/etc/nftables.d/telebot.nft")
        )
        self.firewall_include_section = cfg.get("firewall_include_section", "telebot_include")
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
        self.state.setdefault("sequence", 999)
        self._ensure_client_ids()

    def _save_state(self) -> None:
        try:
            tmp_path = self.state_path.with_suffix(".tmp")
            with tmp_path.open("w", encoding="utf-8") as handle:
                json.dump(self.state, handle, indent=2, sort_keys=True)
                handle.write("\n")
            tmp_path.replace(self.state_path)
        except Exception as exc:  # pragma: no cover - defensive
            self._logger(f"Failed to persist client state: {exc}", self.log_file, "ERROR")

    def _ensure_client_ids(self) -> None:
        max_seq = int(self.state.get("sequence", 999))
        clients = self.state.get("clients", {})
        for entry in clients.values():
            current = entry.get("id")
            if current:
                seq = self._sequence_from_id(str(current))
                if seq is not None and seq > max_seq:
                    max_seq = seq
                continue
            entry["id"] = self._generate_id()
            seq = self._sequence_from_id(entry["id"])
            if seq is not None and seq > max_seq:
                max_seq = seq
        self.state["sequence"] = max_seq

    def _sequence_from_id(self, identifier: str | None) -> Optional[int]:
        if not identifier:
            return None
        cleaned = identifier.strip().upper()
        digits = "".join(ch for ch in cleaned if ch.isdigit())
        if not digits:
            return None
        try:
            return int(digits)
        except ValueError:
            return None

    def _generate_id(self) -> str:
        seq = int(self.state.get("sequence", 999)) + 1
        self.state["sequence"] = seq
        return f"C{seq:04d}"

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
                    "id": self._generate_id(),
                    "notified": False,
                }
            entry.update(
                {
                    "ip": info.get("ip") or entry.get("ip"),
                    "hostname": hostname,
                    "last_seen": now,
                }
            )
            entry.setdefault("id", self._generate_id())
            entry.setdefault("notified", False)
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
                client_id=entry.get("id", ""),
            )
            if status == self.STATUS_PENDING and not entry.get("notified"):
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
            record["id"] = entry.get("id")
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
            client_id=data.get("id", ""),
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
        if not self._nft_supported:
            return
        if self._nft_ready:
            self._ensure_firewall_include()
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
            self._ensure_firewall_include()
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

    def _ensure_firewall_include(self) -> None:
        include_path = self.firewall_include_path
        try:
            include_path.parent.mkdir(parents=True, exist_ok=True)
            content = (
                "# Autogenerated by openwrt-telegram RouterController\n"
                f"table inet {self.nft_table} {{\n"
                f"    set {self.nft_block_set} {{ type etheraddr; flags interval; }}\n"
                f"    set {self.nft_allow_set} {{ type etheraddr; flags interval; }}\n"
                f"    chain {self.nft_chain} {{\n"
                "        type filter hook forward priority 0; policy accept;\n"
                f"        ether saddr @{self.nft_block_set} drop\n"
                "    }\n"
                "}\n"
            )
            current = None
            if include_path.exists():
                current = include_path.read_text(encoding="utf-8")
            if current != content:
                include_path.write_text(content, encoding="utf-8")
        except Exception as exc:  # pragma: no cover - filesystem specifics
            self._logger(f"Failed to refresh firewall include: {exc}", self.log_file, "ERROR")
            return

        uci = shutil.which("uci")
        if not uci:
            self._reload_firewall()
            return
        section = self.firewall_include_section
        try:
            probe = subprocess.run(
                [uci, "-q", "get", f"firewall.{section}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if probe.returncode != 0:
                subprocess.run(
                    [uci, "set", f"firewall.{section}=include"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            subprocess.run(
                [uci, "set", f"firewall.{section}.path={include_path}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            subprocess.run(
                [uci, "set", f"firewall.{section}.type=script"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            subprocess.run(
                [uci, "set", f"firewall.{section}.reload=1"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            subprocess.run(
                [uci, "set", f"firewall.{section}.enabled=1"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            subprocess.run(
                [uci, "set", f"firewall.{section}.family=any"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            subprocess.run(
                [uci, "commit", "firewall"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception as exc:  # pragma: no cover
            self._logger(f"Failed to register firewall include: {exc}", self.log_file, "ERROR")
            return

        self._reload_firewall()

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

    def _reload_firewall(self) -> None:
        commands: list[tuple[list[str], str]] = []
        fw4 = shutil.which("fw4")
        if fw4:
            commands.append(([fw4, "reload"], "fw4 reload"))
        init_script = Path("/etc/init.d/firewall")
        if init_script.exists():
            commands.append(([str(init_script), "reload"], "/etc/init.d/firewall reload"))
        for cmd, label in commands:
            try:
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            except Exception as exc:  # pragma: no cover - best effort
                self._logger(f"Firewall reload command {label} failed: {exc}", self.log_file, "WARNING")
                continue
            if result.returncode == 0:
                self._logger(f"Reloaded firewall via {label}", self.log_file, "INFO")
                return

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
        if status in {self.STATUS_PENDING, self.STATUS_BLOCKED, self.STATUS_PAUSED}:
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
            record["id"] = data.get("id")
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
        firewall = {
            "include_path": str(self.firewall_include_path),
            "include_exists": self.firewall_include_path.exists(),
            "include_section": self.firewall_include_section,
        }
        return {
            "total_clients": len(clients),
            "online_clients": online,
            "counts": counts,
            "whitelist": sorted(self.whitelist),
            "state_file": str(self.state_path),
            "nft": nft_status,
            "firewall": firewall,
        }

    def resolve_identifier(self, identifier: str) -> str:
        ident = (identifier or "").strip().lower()
        mac = _normalize_mac(ident)
        clients = self.state.get("clients", {})
        if mac and mac in clients:
            return mac
        for key, value in clients.items():
            if str(value.get("id", "")).lower() == ident:
                return key
            if str(value.get("ip", "")).lower() == ident:
                return key
        raise ValueError("Client not found")

    def set_status(self, identifier: str, status: str) -> Dict[str, Any]:
        mac_normalized = _normalize_mac(identifier)
        if not mac_normalized:
            mac_normalized = self.resolve_identifier(identifier)
        if status not in {
            self.STATUS_PENDING,
            self.STATUS_APPROVED,
            self.STATUS_BLOCKED,
            self.STATUS_PAUSED,
            self.STATUS_WHITELIST,
        }:
            raise ValueError("Unsupported status")
        clients = self.state.setdefault("clients", {})
        entry = clients.setdefault(mac_normalized, {"first_seen": _now()})
        entry["status"] = status
        entry.setdefault("hostname", "")
        entry.setdefault("ip", "")
        entry["last_seen"] = entry.get("last_seen", _now())
        entry.setdefault("id", self._generate_id())
        if status != self.STATUS_PENDING:
            entry["notified"] = True
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

    def pause(self, mac: str) -> Dict[str, Any]:
        client = self.set_status(mac, self.STATUS_PAUSED)
        self._logger(f"Client {client['mac']} paused", self.log_file, "INFO")
        return client

    def resume(self, mac: str) -> Dict[str, Any]:
        client = self.set_status(mac, self.STATUS_APPROVED)
        self._logger(f"Client {client['mac']} resumed", self.log_file, "INFO")
        return client

    def whitelist(self, mac: str) -> Dict[str, Any]:
        client = self.set_status(mac, self.STATUS_WHITELIST)
        self._logger(f"Client {client['mac']} whitelisted", self.log_file, "INFO")
        return client

    def forget(self, mac: str) -> None:
        mac_normalized = _normalize_mac(mac)
        if not mac_normalized:
            mac_normalized = self.resolve_identifier(mac)
        clients = self.state.get("clients", {})
        if mac_normalized in clients:
            del clients[mac_normalized]
            self._nft_remove(self.nft_block_set, mac_normalized)
            self._nft_remove(self.nft_allow_set, mac_normalized)
            self._save_state()
            self._logger(f"Client {mac_normalized} removed from registry", self.log_file, "INFO")

    def mark_notified(self, identifier: str) -> None:
        try:
            mac = self.resolve_identifier(identifier)
        except ValueError:
            return
        clients = self.state.get("clients", {})
        entry = clients.get(mac)
        if not entry:
            return
        if not entry.get("notified"):
            entry["notified"] = True
            self._save_state()

    # ------------------------------------------------------------------
    # Formatting helpers

    def describe_client(self, client: Dict[str, Any]) -> str:
        hostname = client.get("hostname") or "Unknown"
        ip = client.get("ip") or "?"
        status = client.get("status")
        identifier = client.get("id")
        mac = client.get("mac")
        label = {
            self.STATUS_PENDING: "pending approval",
            self.STATUS_APPROVED: "approved",
            self.STATUS_BLOCKED: "blocked",
            self.STATUS_PAUSED: "paused",
            self.STATUS_WHITELIST: "whitelisted",
        }.get(status, status or "unknown")
        parts = []
        if identifier:
            parts.append(f"#{identifier}")
        if mac:
            parts.append(mac)
        ident_text = " / ".join(parts) if parts else "unknown"
        return f"{hostname} ({ident_text}) — {ip} — {label}"

    def _status_order(self, status: str | None) -> int:
        order = {
            self.STATUS_PENDING: 0,
            self.STATUS_BLOCKED: 1,
            self.STATUS_PAUSED: 1,
            self.STATUS_APPROVED: 2,
            self.STATUS_WHITELIST: 3,
        }
        return order.get(status or "", 99)

