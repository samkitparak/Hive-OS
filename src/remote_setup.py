"""Safe remote machine setup planning and connection probes."""

import socket
import ipaddress
from datetime import datetime, timezone
from pathlib import Path

import yaml


COMMON_MAESTRO_FOLDERS = [
    r"C:\SCM\Maestro\Logs",
    r"C:\ProgramData\SCM Group\Maestro\Logs",
    r"C:\Program Files\SCM Group\Maestro\Logs",
    r"D:\SCM\Maestro\Logs",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _machine(cfg_path: Path, machine_key: str) -> dict:
    cfg = yaml.safe_load(cfg_path.read_text()) or {}
    machine = next(
        (item for item in cfg.get("maestro_agents", [])
         if item.get("machine_key") == machine_key),
        None,
    )
    if not machine:
        raise ValueError(f"Unknown Maestro machine '{machine_key}'")
    return machine


def _validate_private_target(host: str) -> None:
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(host, None)
            }
        except OSError as error:
            raise ValueError(f"Could not resolve host '{host}': {error}") from error
    if not addresses or any(
        not (address.is_private or address.is_loopback or address.is_link_local)
        for address in addresses
    ):
        raise ValueError("Remote setup probes are limited to private factory-LAN addresses")


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def plan(cfg_path: Path, machine_key: str) -> dict:
    machine = _machine(cfg_path, machine_key)
    return {
        "generated_at": _now(),
        "machine_key": machine_key,
        "label": machine.get("label") or machine_key,
        "host": machine.get("host"),
        "transport": "ssh",
        "ssh_port": 22,
        "mode": "dry_run",
        "credentials_stored": False,
        "steps": [
            "Probe TCP port 22 on the machine PC",
            "Authenticate with a temporary SSH key or admin session",
            "Discover the local Maestro log and CNC folders",
            "Copy the HIVE agent package to C:\\HIVE-Agent",
            "Write the machine-local config and create its startup task",
            "Start the agent and verify its MQTT heartbeat centrally",
        ],
    }


def test_connection(payload: dict, timeout_s: float = 2.0) -> dict:
    host = str(payload.get("host") or "").strip()
    port = int(payload.get("port") or 22)
    if not host:
        raise ValueError("A machine host or IP is required")
    _validate_private_target(host)
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            pass
        return {
            "checked_at": _now(),
            "host": host,
            "port": port,
            "status": "reachable",
            "reachable": True,
            "detail": "SSH TCP port accepted a connection; authentication was not attempted",
        }
    except OSError as error:
        return {
            "checked_at": _now(),
            "host": host,
            "port": port,
            "status": "unreachable",
            "reachable": False,
            "detail": str(error),
        }


def detect_folders(cfg_path: Path, payload: dict) -> dict:
    machine = _machine(cfg_path, payload["machine_key"])
    configured = machine.get("log_folder")
    candidates = list(dict.fromkeys(
        [folder for folder in [configured, *COMMON_MAESTRO_FOLDERS] if folder]
    ))
    return {
        "generated_at": _now(),
        "machine_key": payload["machine_key"],
        "status": "remote_adapter_required",
        "mode": "dry_run",
        "candidates": [
            {"path": path, "exists": None, "source": "configured" if path == configured else "common"}
            for path in candidates
        ],
        "powershell_preview": (
            "$paths = @(" + ", ".join(_ps_quote(path) for path in candidates) + "); "
            "$paths | Where-Object { Test-Path $_ }"
        ),
    }


def install_agent(cfg_path: Path, payload: dict) -> dict:
    machine = _machine(cfg_path, payload["machine_key"])
    return {
        "generated_at": _now(),
        "machine_key": payload["machine_key"],
        "host": payload.get("host") or machine.get("host"),
        "status": "preview_ready",
        "mode": "dry_run",
        "will_execute": False,
        "install_dir": r"C:\HIVE-Agent",
        "log_folder": payload.get("log_folder") or machine.get("log_folder"),
        "scheduled_task": f"HIVE Agent - {payload['machine_key']}",
        "files": [
            "src/maestro_agent.py",
            "requirements.txt",
            "config/machines.yaml",
            "start-agent.cmd",
        ],
    }


def restart_agent(payload: dict) -> dict:
    machine_key = payload["machine_key"]
    return {
        "generated_at": _now(),
        "machine_key": machine_key,
        "status": "preview_ready",
        "mode": "dry_run",
        "will_execute": False,
        "powershell_preview": (
            f"Stop-ScheduledTask -TaskName 'HIVE Agent - {machine_key}'; "
            f"Start-ScheduledTask -TaskName 'HIVE Agent - {machine_key}'"
        ),
    }


def fetch_log(payload: dict) -> dict:
    return {
        "generated_at": _now(),
        "machine_key": payload["machine_key"],
        "status": "preview_ready",
        "mode": "dry_run",
        "will_execute": False,
        "remote_path": r"C:\HIVE-Agent\logs\agent.log",
        "powershell_preview": r"Get-Content C:\HIVE-Agent\logs\agent.log -Tail 200",
    }
