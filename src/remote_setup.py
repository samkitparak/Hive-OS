"""Secure, auditable SSH commissioning for Windows machine PCs."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

import config_editor
import mqtt_security


ROOT = Path(__file__).parent.parent
COMMON_MAESTRO_FOLDERS = [
    r"C:\SCM\Maestro\Logs",
    r"C:\ProgramData\SCM Group\Maestro\Logs",
    r"C:\Program Files\SCM Group\Maestro\Logs",
    r"D:\SCM\Maestro\Logs",
]
COMMON_CNC_FOLDERS = [
    r"C:\SCM\Maestro\CncPrograms",
    r"C:\ProgramData\SCM Group\Maestro\CncPrograms",
    r"D:\SCM\Maestro\CncPrograms",
]
HOST_KEY_TYPES = {"ssh-ed25519", "ecdsa-sha2-nistp256", "ssh-rsa"}
USERNAME = re.compile(r"^[^\s\x00-\x1f]{1,120}$")
OUTPUT_LIMIT = 20_000
HEARTBEAT_FRESH_SECONDS = 300


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


def _machine_row(conn: sqlite3.Connection, machine_key: str) -> dict:
    row = conn.execute(
        "SELECT id,machine_key,name FROM machines WHERE machine_key=?", (machine_key,)
    ).fetchone()
    if not row:
        raise ValueError(f"Unknown machine '{machine_key}'")
    return dict(row)


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
        raise ValueError("Remote setup is limited to private factory-LAN addresses")


def _validate_username(username: Optional[str]) -> str:
    value = str(username or "").strip()
    if not value:
        raise ValueError("A Windows administrator username is required")
    if value.startswith("-") or not USERNAME.fullmatch(value):
        raise ValueError("The SSH username contains unsupported characters")
    return value


def _target(cfg_path: Path, payload: dict) -> dict:
    machine = _machine(cfg_path, payload["machine_key"])
    host = str(payload.get("host") or machine.get("host") or "").strip()
    if not host:
        raise ValueError("A machine host or IP is required")
    _validate_private_target(host)
    return {
        "machine_key": payload["machine_key"],
        "host": host,
        "port": int(payload.get("port") or 22),
        "username": str(payload.get("username") or "").strip() or None,
        "log_folder": payload.get("log_folder") or machine.get("log_folder"),
        "cnc_folder": payload.get("cnc_folder") or machine.get("cnc_folder"),
    }


def _ssh_dir() -> Path:
    return Path(os.environ.get("HIVE_SSH_DIR") or ROOT / "data" / "ssh")


def _identity_path() -> Path:
    return Path(os.environ.get("HIVE_SSH_IDENTITY_FILE") or _ssh_dir() / "id_ed25519")


def _known_hosts_path() -> Path:
    return _ssh_dir() / "known_hosts"


def _tool_status() -> dict:
    return {name: shutil.which(name) for name in ("ssh", "scp", "ssh-keyscan", "ssh-keygen")}


def identity_status() -> dict:
    identity = _identity_path()
    public_path = Path(f"{identity}.pub")
    public_key = public_path.read_text().strip() if public_path.exists() else None
    tools = _tool_status()
    return {
        "status": "ready" if identity.is_file() and public_key and all(tools.values()) else "missing",
        "identity_file": str(identity),
        "public_key_file": str(public_path),
        "public_key": public_key,
        "known_hosts_file": str(_known_hosts_path()),
        "tools": {key: bool(value) for key, value in tools.items()},
        "private_key_stored_in_database": False,
    }


def _run_process(args: list[str], timeout_s: float = 30) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=timeout_s, check=False,
        )
    except FileNotFoundError as error:
        raise ValueError(f"Required OpenSSH command is unavailable: {args[0]}") from error
    except subprocess.TimeoutExpired as error:
        raise ValueError(f"Remote command timed out after {timeout_s:g} seconds") from error


def generate_identity() -> dict:
    current = identity_status()
    if current["status"] == "ready":
        return current
    tool = shutil.which("ssh-keygen")
    if not tool:
        raise ValueError("OpenSSH Client and ssh-keygen must be installed first")
    identity = _identity_path()
    identity.parent.mkdir(parents=True, exist_ok=True)
    try:
        identity.parent.chmod(0o700)
    except OSError:
        pass
    if identity.exists() and not Path(f"{identity}.pub").exists():
        raise ValueError("An incomplete SSH identity exists; restore its public key or remove it manually")
    if identity.exists():
        missing = [name for name, available in current["tools"].items() if not available]
        raise ValueError(f"The SSH identity exists, but OpenSSH tools are missing: {', '.join(missing)}")
    result = _run_process([
        tool, "-q", "-t", "ed25519", "-N", "", "-C", "HIVE OS deployment",
        "-f", str(identity),
    ])
    if result.returncode:
        raise ValueError((result.stderr or result.stdout or "SSH key generation failed").strip())
    try:
        identity.chmod(0o600)
        Path(f"{identity}.pub").chmod(0o644)
    except OSError:
        pass
    return identity_status()


def _fingerprint(key_blob: str) -> str:
    try:
        raw = base64.b64decode(key_blob.encode("ascii"), validate=True)
    except (ValueError, UnicodeError) as error:
        raise ValueError("The SSH host returned an invalid public key") from error
    digest = base64.b64encode(hashlib.sha256(raw).digest()).decode("ascii").rstrip("=")
    return f"SHA256:{digest}"


def _known_host_token(host: str, port: int) -> str:
    return host if port == 22 else f"[{host}]:{port}"


def _scan_keys(host: str, port: int, timeout_s: float = 5) -> list[dict]:
    _validate_private_target(host)
    tool = shutil.which("ssh-keyscan")
    if not tool:
        raise ValueError("OpenSSH ssh-keyscan is not installed")
    result = _run_process([
        tool, "-T", str(max(1, int(timeout_s))), "-p", str(port),
        "-t", "ed25519,ecdsa,rsa", host,
    ], timeout_s=timeout_s + 2)
    keys = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 3 or fields[1] not in HOST_KEY_TYPES:
            continue
        keys.append({
            "key_type": fields[1],
            "fingerprint": _fingerprint(fields[2]),
            "known_hosts_line": f"{_known_host_token(host, port)} {fields[1]} {fields[2]}",
        })
    if not keys:
        detail = (result.stderr or "No supported SSH host key was returned").strip()
        raise ValueError(detail[-1000:])
    return keys


def scan_host_key(conn: sqlite3.Connection, cfg_path: Path, payload: dict) -> dict:
    target = _target(cfg_path, payload)
    keys = _scan_keys(target["host"], target["port"])
    trusted = conn.execute(
        """SELECT host_key_sha256,status,version FROM remote_setup_hosts rsh
           JOIN machines m ON m.id=rsh.machine_id WHERE m.machine_key=?""",
        (target["machine_key"],),
    ).fetchone()
    return {
        "scanned_at": _now(), **target, "status": "fingerprint_verification_required",
        "keys": [{key: value for key, value in item.items() if key != "known_hosts_line"} for item in keys],
        "trusted": dict(trusted) if trusted else None,
        "instruction": "Compare one fingerprint with the fingerprint printed on the machine PC before trusting it.",
    }


def _write_known_hosts(conn: sqlite3.Connection) -> Path:
    path = _known_hosts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [row["known_hosts_line"] for row in conn.execute(
        "SELECT known_hosts_line FROM remote_setup_hosts WHERE status='trusted' ORDER BY id"
    ).fetchall()]
    temporary = path.with_suffix(".tmp")
    temporary.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    os.replace(temporary, path)
    try:
        path.parent.chmod(0o700)
        path.chmod(0o600)
    except OSError:
        pass
    return path


def trust_host(conn: sqlite3.Connection, cfg_path: Path, payload: dict, actor: str) -> dict:
    target = _target(cfg_path, payload)
    username = _validate_username(payload.get("username"))
    expected = str(payload.get("fingerprint") or "").strip()
    if not expected.startswith("SHA256:"):
        raise ValueError("A verified SHA256 host-key fingerprint is required")
    keys = _scan_keys(target["host"], target["port"])
    selected = next((item for item in keys if item["fingerprint"] == expected), None)
    if not selected:
        raise ValueError("The machine's current SSH host key does not match the approved fingerprint")
    machine = _machine_row(conn, target["machine_key"])
    current = conn.execute(
        "SELECT version FROM remote_setup_hosts WHERE machine_id=?", (machine["id"],)
    ).fetchone()
    expected_version = payload.get("expected_version")
    if expected_version is not None and (not current or current["version"] != expected_version):
        raise ValueError("Remote host trust changed since it was loaded; scan again")
    now = _now()
    conn.execute(
        """INSERT INTO remote_setup_hosts
           (machine_id,host,port,username,host_key_type,host_key_sha256,known_hosts_line,
            status,trusted_by,trusted_at,updated_at)
           VALUES (?,?,?,?,?,?,?,'trusted',?,?,?)
           ON CONFLICT(machine_id) DO UPDATE SET host=excluded.host,port=excluded.port,
             username=excluded.username,host_key_type=excluded.host_key_type,
             host_key_sha256=excluded.host_key_sha256,known_hosts_line=excluded.known_hosts_line,
             status='trusted',trusted_by=excluded.trusted_by,trusted_at=excluded.trusted_at,
             last_error=NULL,version=remote_setup_hosts.version+1,updated_at=excluded.updated_at""",
        (machine["id"], target["host"], target["port"], username, selected["key_type"],
         selected["fingerprint"], selected["known_hosts_line"], actor, now, now),
    )
    conn.commit()
    _write_known_hosts(conn)
    return host_profile(conn, target["machine_key"])


def forget_host(conn: sqlite3.Connection, machine_key: str, actor: str) -> dict:
    machine = _machine_row(conn, machine_key)
    row = conn.execute(
        "SELECT id FROM remote_setup_hosts WHERE machine_id=?", (machine["id"],)
    ).fetchone()
    if not row:
        raise ValueError(f"No trusted SSH host exists for '{machine_key}'")
    now = _now()
    conn.execute(
        """UPDATE remote_setup_hosts SET status='revoked',last_error=?,version=version+1,
           updated_at=? WHERE id=?""",
        (f"Trust revoked by {actor}", now, row["id"]),
    )
    conn.commit()
    _write_known_hosts(conn)
    return host_profile(conn, machine_key)


def host_profile(conn: sqlite3.Connection, machine_key: str) -> Optional[dict]:
    row = conn.execute(
        """SELECT rsh.*,m.machine_key,m.name machine_name FROM remote_setup_hosts rsh
           JOIN machines m ON m.id=rsh.machine_id WHERE m.machine_key=?""",
        (machine_key,),
    ).fetchone()
    return dict(row) if row else None


def _trusted_target(conn: sqlite3.Connection, cfg_path: Path, payload: dict) -> dict:
    target = _target(cfg_path, payload)
    profile = host_profile(conn, target["machine_key"])
    if not profile or profile["status"] != "trusted":
        raise ValueError("Approve the machine's SSH host-key fingerprint before live execution")
    if profile["host"] != target["host"] or int(profile["port"]) != target["port"]:
        raise ValueError("The requested endpoint differs from the trusted SSH endpoint; scan it again")
    requested_user = payload.get("username")
    if requested_user and _validate_username(requested_user) != profile["username"]:
        raise ValueError("The requested username differs from the trusted SSH profile")
    target["username"] = _validate_username(profile["username"])
    identity = identity_status()
    if identity["status"] != "ready":
        raise ValueError("Generate or configure the HIVE SSH deployment identity first")
    _write_known_hosts(conn)
    return target


def _ssh_options(target: dict) -> list[str]:
    null_config = "NUL" if os.name == "nt" else "/dev/null"
    return [
        "-F", null_config,
        "-o", "BatchMode=yes",
        "-o", "PasswordAuthentication=no",
        "-o", "KbdInteractiveAuthentication=no",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={_known_hosts_path()}",
        "-o", "IdentitiesOnly=yes",
        "-o", "ConnectTimeout=10",
        "-i", str(_identity_path()),
        "-p", str(target["port"]),
    ]


def _powershell(script: str) -> list[str]:
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return [
        "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded,
    ]


def _ssh(target: dict, script: str, timeout_s: float = 60) -> subprocess.CompletedProcess:
    tool = shutil.which("ssh")
    if not tool:
        raise ValueError("OpenSSH ssh is not installed")
    return _run_process([
        tool, *_ssh_options(target), f"{target['username']}@{target['host']}",
        *_powershell(script),
    ], timeout_s=timeout_s)


def _scp(target: dict, source: Path, remote_path: str, timeout_s: float = 120) -> subprocess.CompletedProcess:
    tool = shutil.which("scp")
    if not tool:
        raise ValueError("OpenSSH scp is not installed")
    options = _ssh_options(target)
    port_index = options.index("-p")
    options[port_index] = "-P"
    return _run_process([
        tool, *options, str(source),
        f"{target['username']}@{target['host']}:{remote_path}",
    ], timeout_s=timeout_s)


def _tail(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return value[-OUTPUT_LIMIT:]


def _start_run(conn: sqlite3.Connection, target: dict, action: str,
               mode: str, actor: str, summary: str) -> int:
    machine = _machine_row(conn, target["machine_key"])
    cursor = conn.execute(
        """INSERT INTO remote_setup_runs
           (machine_id,action,mode,status,host,port,username,command_summary,actor,started_at)
           VALUES (?,?,?,'running',?,?,?,?,?,?)""",
        (machine["id"], action, mode, target["host"], target["port"],
         target.get("username"), summary, actor, _now()),
    )
    conn.commit()
    return cursor.lastrowid


def _finish_run(conn: sqlite3.Connection, run_id: int, target: dict,
                result: subprocess.CompletedProcess) -> None:
    success = result.returncode == 0
    now = _now()
    conn.execute(
        """UPDATE remote_setup_runs SET status=?,exit_code=?,stdout_tail=?,stderr_tail=?,
           completed_at=? WHERE id=?""",
        ("succeeded" if success else "failed", result.returncode, _tail(result.stdout),
         _tail(result.stderr), now, run_id),
    )
    conn.execute(
        """UPDATE remote_setup_hosts SET last_connected_at=CASE WHEN ? THEN ? ELSE last_connected_at END,
           last_error=?,updated_at=? WHERE machine_id=(SELECT id FROM machines WHERE machine_key=?)""",
        (success, now, None if success else _tail(result.stderr or result.stdout), now,
         target["machine_key"]),
    )
    conn.commit()


def _failed_result(error: Exception) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 1, "", str(error))


def _parse_json_output(result: subprocess.CompletedProcess) -> dict:
    if result.returncode:
        detail = (result.stderr or result.stdout or "Remote command failed").strip()
        raise ValueError(detail[-2000:])
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            parsed = json.loads(line)
            return parsed if isinstance(parsed, dict) else {"items": parsed}
        except json.JSONDecodeError:
            continue
    raise ValueError("The remote command completed without a valid result")


def _run_json(conn: sqlite3.Connection, target: dict, action: str, actor: str,
              summary: str, script: str, timeout_s: float = 60) -> dict:
    run_id = _start_run(conn, target, action, "live", actor, summary)
    try:
        result = _ssh(target, script, timeout_s=timeout_s)
    except Exception as error:
        result = _failed_result(error)
    data = None
    if result.returncode == 0:
        try:
            data = _parse_json_output(result)
        except ValueError as error:
            result = subprocess.CompletedProcess(result.args, 1, result.stdout, str(error))
    _finish_run(conn, run_id, target, result)
    if data is None:
        data = _parse_json_output(result)
    return {"run_id": run_id, "mode": "live", **data}


def plan(conn: sqlite3.Connection, cfg_path: Path, machine_key: str) -> dict:
    machine = _machine(cfg_path, machine_key)
    profile = host_profile(conn, machine_key)
    identity = identity_status()
    agent_payload = mqtt_security.agent_payload_status()
    latest = conn.execute(
        """SELECT rcr.id FROM remote_commissioning_runs rcr
           JOIN machines m ON m.id=rcr.machine_id WHERE m.machine_key=?
           ORDER BY rcr.id DESC LIMIT 1""", (machine_key,),
    ).fetchone()
    return {
        "generated_at": _now(), "machine_key": machine_key,
        "label": machine.get("label") or machine_key, "host": machine.get("host"),
        "transport": "ssh", "ssh_port": profile["port"] if profile else 22,
        "mode": "commissioning", "credentials_stored": False,
        "identity": identity, "host_trust": profile, "agent_payload": agent_payload,
        "latest_commissioning": _commissioning_result(conn, latest["id"]) if latest else None,
        "ready_for_live_execution": bool(
            identity["status"] == "ready" and profile and profile["status"] == "trusted"
            and agent_payload["ready"]
        ),
        "steps": [
            "Generate the central HIVE deployment key",
            "Run enable-hive-ssh.ps1 once as Administrator on the machine PC",
            "Scan and physically compare the machine SSH fingerprint",
            "Approve the matching fingerprint and test key authentication",
            "Discover the Maestro folders over SSH",
            "Issue a machine certificate, copy the package, and install the agent",
            "Verify the scheduled task, MQTT heartbeat, and commissioning log",
        ],
    }


def snapshot(conn: sqlite3.Connection, cfg_path: Path) -> dict:
    cfg = yaml.safe_load(cfg_path.read_text()) or {}
    hosts = [dict(row) for row in conn.execute(
        """SELECT rsh.*,m.machine_key,m.name machine_name FROM remote_setup_hosts rsh
           JOIN machines m ON m.id=rsh.machine_id ORDER BY m.name"""
    ).fetchall()]
    runs = [dict(row) for row in conn.execute(
        """SELECT rsr.*,m.machine_key,m.name machine_name FROM remote_setup_runs rsr
           JOIN machines m ON m.id=rsr.machine_id ORDER BY rsr.id DESC LIMIT 100"""
    ).fetchall()]
    machine_keys = {item.get("machine_key") for item in cfg.get("maestro_agents", [])}
    trusted = sum(item["status"] == "trusted" for item in hosts if item["machine_key"] in machine_keys)
    installed = sum(
        any(run["machine_key"] == key and run["action"] == "install" and run["status"] == "succeeded"
            for run in runs) for key in machine_keys
    )
    latest_by_machine = {}
    for run in runs:
        latest_by_machine.setdefault(run["machine_key"], run)
    commissioning_ids = [row["id"] for row in conn.execute(
        "SELECT id FROM remote_commissioning_runs ORDER BY id DESC LIMIT 100"
    )]
    commissioning_runs = [_commissioning_result(conn, run_id) for run_id in commissioning_ids]
    latest_commissioning = {}
    for run in commissioning_runs:
        latest_commissioning.setdefault(run["machine_key"], run)
    return {
        "generated_at": _now(), "identity": identity_status(),
        "agent_payload": mqtt_security.agent_payload_status(), "hosts": hosts, "runs": runs,
        "commissioning_runs": commissioning_runs,
        "summary": {"configured_machines": len(machine_keys), "trusted_hosts": trusted,
                    "installed_hosts": installed,
                    "failed_runs": sum(run["status"] == "failed" for run in latest_by_machine.values()),
                    "commissioned_hosts": sum(
                        run["status"] == "succeeded" for run in latest_commissioning.values()
                    ),
                    "commissioning_attention": sum(
                        run["status"] in {"failed", "needs_input", "awaiting_signal"}
                        for run in latest_commissioning.values()
                    )},
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
        return {"checked_at": _now(), "host": host, "port": port,
                "status": "reachable", "reachable": True,
                "detail": "SSH TCP port accepted a connection; authentication was not attempted"}
    except OSError as error:
        return {"checked_at": _now(), "host": host, "port": port,
                "status": "unreachable", "reachable": False, "detail": str(error)}


def authenticate(conn: sqlite3.Connection, cfg_path: Path, payload: dict, actor: str) -> dict:
    target = _trusted_target(conn, cfg_path, payload)
    task = f"HIVE Agent - {target['machine_key']}"
    script = rf"""
$isAdmin = (New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$task = Get-ScheduledTask -TaskName '{task}' -ErrorAction SilentlyContinue
$agentConfig='C:\HIVE-Agent\config\machines.yaml'
$configuredLog=$null
if(Test-Path -LiteralPath $agentConfig){{$match=Select-String -LiteralPath $agentConfig -Pattern '^\s*log_folder:\s*["'']?(.*?)["'']?\s*$' | Select-Object -First 1;if($match){{$configuredLog=$match.Matches[0].Groups[1].Value.Trim()}}}}
[ordered]@{{status=if($isAdmin){{'ready'}}else{{'insufficient_privileges'}};computer_name=$env:COMPUTERNAME;username=$env:USERNAME;is_admin=$isAdmin;powershell=$PSVersionTable.PSVersion.ToString();agent_installed=[bool](Test-Path -LiteralPath 'C:\HIVE-Agent');task_state=if($task){{$task.State.ToString()}}else{{$null}};configured_log_folder=$configuredLog}} | ConvertTo-Json -Compress
"""
    return _run_json(conn, target, "authenticate", actor, "Verify SSH key and administrator context", script)


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def detect_folders(conn: sqlite3.Connection, cfg_path: Path, payload: dict, actor: str) -> dict:
    target = _target(cfg_path, payload)
    log_candidates = list(dict.fromkeys(
        [item for item in [target.get("log_folder"), *COMMON_MAESTRO_FOLDERS] if item]
    ))
    cnc_candidates = list(dict.fromkeys(
        [item for item in [target.get("cnc_folder"), *COMMON_CNC_FOLDERS] if item]
    ))
    if not payload.get("execute"):
        return {
            "generated_at": _now(), "machine_key": target["machine_key"],
            "status": "preview_ready", "mode": "dry_run", "will_execute": False,
            "log_candidates": [{"path": path, "exists": None} for path in log_candidates],
            "cnc_candidates": [{"path": path, "exists": None} for path in cnc_candidates],
        }
    target = _trusted_target(conn, cfg_path, payload)
    logs = ",".join(_ps_quote(path) for path in log_candidates)
    cnc = ",".join(_ps_quote(path) for path in cnc_candidates)
    script = f"""
$logs=@({logs}); $cnc=@({cnc})
$logResult=@($logs | ForEach-Object {{ [ordered]@{{path=$_;exists=[bool](Test-Path -LiteralPath $_);latest_log=(Get-ChildItem -LiteralPath $_ -Filter '*.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName)}} }})
$cncResult=@($cnc | ForEach-Object {{ [ordered]@{{path=$_;exists=[bool](Test-Path -LiteralPath $_);program_count=@(Get-ChildItem -LiteralPath $_ -File -ErrorAction SilentlyContinue | Where-Object {{$_.Extension -in '.xcs','.ard'}}).Count}} }})
[ordered]@{{status='completed';log_candidates=$logResult;cnc_candidates=$cncResult}} | ConvertTo-Json -Depth 5 -Compress
"""
    return _run_json(conn, target, "detect_folders", actor, "Inspect Maestro log and CNC folders", script)


def install_agent(conn: sqlite3.Connection, cfg_path: Path, payload: dict, actor: str) -> dict:
    target = _target(cfg_path, payload)
    preview = {
        "generated_at": _now(), "machine_key": target["machine_key"], "host": target["host"],
        "status": "preview_ready", "mode": "dry_run", "will_execute": False,
        "install_dir": r"C:\HIVE-Agent", "log_folder": target.get("log_folder"),
        "scheduled_task": f"HIVE Agent - {target['machine_key']}",
        "agent_payload": mqtt_security.agent_payload_status(),
        "security": "Live execution uses a verified offline runtime and issues a fresh MQTT certificate.",
    }
    if not payload.get("execute"):
        return preview
    target = _trusted_target(conn, cfg_path, payload)
    log_folder = str(target.get("log_folder") or "").strip()
    if not log_folder:
        raise ValueError("Select a verified Maestro log folder before installation")
    agent_payload = mqtt_security.agent_payload_status()
    if not agent_payload["ready"]:
        raise ValueError(
            "Verified offline machine-agent payload is not ready on the central PC: "
            + agent_payload["detail"]
        )
    run_id = _start_run(conn, target, "install", "live", actor,
                        "Copy enrollment package and install HIVE machine agent")
    enrollment_id = None
    result = None
    remote_name = f"hive-enrollment-{target['machine_key']}-{uuid.uuid4().hex}.zip"
    remote_zip = f"C:/Windows/Temp/{remote_name}"
    try:
        bundle, manifest = mqtt_security.issue_bundle(conn, target["machine_key"], actor)
        enrollment_id = manifest["enrollment_id"]
        with tempfile.TemporaryDirectory(prefix="hive-remote-") as directory:
            local_zip = Path(directory) / remote_name
            local_zip.write_bytes(bundle)
            copied = _scp(target, local_zip, remote_zip)
            if copied.returncode:
                result = copied
            else:
                stage = rf"C:\Windows\Temp\hive-deploy-{uuid.uuid4().hex}"
                script = rf"""
$ErrorActionPreference='Stop'; $zip={_ps_quote(remote_zip.replace('/', '\\'))}; $stage={_ps_quote(stage)}
try {{
  New-Item -ItemType Directory -Force -Path $stage | Out-Null
  Expand-Archive -LiteralPath $zip -DestinationPath $stage -Force
  & (Join-Path $stage 'install-machine-agent.ps1') -MachineKey {_ps_quote(target['machine_key'])} -LogFolder {_ps_quote(log_folder)}
  if ($LASTEXITCODE -ne 0) {{ throw "Agent installer exited with code $LASTEXITCODE" }}
  $task=Get-ScheduledTask -TaskName {_ps_quote(f"HIVE Agent - {target['machine_key']}")} -ErrorAction Stop
  [ordered]@{{status='installed';agent_installed=[bool](Test-Path -LiteralPath 'C:\HIVE-Agent');task_state=$task.State.ToString();log_folder={_ps_quote(log_folder)}}} | ConvertTo-Json -Compress
}} finally {{ Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue; Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue }}
"""
                result = _ssh(target, script, timeout_s=900)
    except Exception as error:
        result = _failed_result(error)
    assert result is not None
    data = None
    if result.returncode == 0:
        try:
            data = _parse_json_output(result)
        except ValueError as error:
            result = subprocess.CompletedProcess(result.args, 1, result.stdout, str(error))
    _finish_run(conn, run_id, target, result)
    if result.returncode and enrollment_id is not None:
        try:
            mqtt_security.revoke(conn, enrollment_id, actor, "Remote installation failed")
        except Exception:
            pass
    if data is None:
        data = _parse_json_output(result)
    return {"run_id": run_id, "mode": "live", "enrollment_id": enrollment_id, **data}


def restart_agent(conn: sqlite3.Connection, cfg_path: Path, payload: dict, actor: str) -> dict:
    target = _target(cfg_path, payload)
    task = f"HIVE Agent - {target['machine_key']}"
    if not payload.get("execute"):
        return {"generated_at": _now(), "machine_key": target["machine_key"],
                "status": "preview_ready", "mode": "dry_run", "will_execute": False,
                "scheduled_task": task}
    target = _trusted_target(conn, cfg_path, payload)
    script = f"""
$task={_ps_quote(task)}; Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue; Start-Sleep -Seconds 1; Start-ScheduledTask -TaskName $task -ErrorAction Stop; Start-Sleep -Seconds 1
$state=(Get-ScheduledTask -TaskName $task -ErrorAction Stop).State.ToString(); [ordered]@{{status='restarted';task_state=$state}} | ConvertTo-Json -Compress
"""
    return _run_json(conn, target, "restart", actor, "Restart HIVE machine-agent scheduled task", script)


def fetch_log(conn: sqlite3.Connection, cfg_path: Path, payload: dict, actor: str) -> dict:
    target = _target(cfg_path, payload)
    if not payload.get("execute"):
        return {"generated_at": _now(), "machine_key": target["machine_key"],
                "status": "preview_ready", "mode": "dry_run", "will_execute": False,
                "remote_path": r"C:\HIVE-Agent\logs\agent.log", "tail_lines": 200}
    target = _trusted_target(conn, cfg_path, payload)
    script = r"""
$path='C:\HIVE-Agent\logs\agent.log'; $exists=Test-Path -LiteralPath $path; $lines=if($exists){@(Get-Content -LiteralPath $path -Tail 200 -ErrorAction Stop)}else{@()}; [ordered]@{status=if($exists){'completed'}else{'not_found'};remote_path=$path;lines=$lines} | ConvertTo-Json -Depth 3 -Compress
"""
    return _run_json(conn, target, "fetch_log", actor, "Fetch the last 200 HIVE agent log lines", script)


def _json_text(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    if len(encoded) <= OUTPUT_LIMIT:
        return encoded
    return json.dumps({
        "truncated": True,
        "original_length": len(encoded),
        "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    }, sort_keys=True, separators=(",", ":"))


def _commissioning_step(conn: sqlite3.Connection, run_id: int, step_key: str,
                        status: str, detail: Optional[dict] = None) -> None:
    now = _now()
    completed_at = None if status == "running" else now
    conn.execute(
        """INSERT INTO remote_commissioning_steps
           (commissioning_run_id,step_key,status,detail_json,started_at,completed_at)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(commissioning_run_id,step_key) DO UPDATE SET
             status=excluded.status,detail_json=excluded.detail_json,
             completed_at=excluded.completed_at""",
        (run_id, step_key, status, _json_text(detail or {}), now, completed_at),
    )
    conn.execute(
        "UPDATE remote_commissioning_runs SET stage=?,updated_at=? WHERE id=?",
        (step_key, now, run_id),
    )
    conn.commit()


def _commissioning_result(conn: sqlite3.Connection, run_id: int) -> dict:
    row = conn.execute(
        """SELECT rcr.*,m.machine_key,m.name machine_name
           FROM remote_commissioning_runs rcr JOIN machines m ON m.id=rcr.machine_id
           WHERE rcr.id=?""", (run_id,),
    ).fetchone()
    if not row:
        raise KeyError(f"Unknown commissioning run '{run_id}'")
    result = dict(row)
    result["force_reinstall"] = bool(result["force_reinstall"])
    try:
        result["result"] = json.loads(result.pop("result_json"))
    except json.JSONDecodeError:
        result["result"] = {}
        result.pop("result_json", None)
    result["steps"] = []
    for step in conn.execute(
        """SELECT step_key,status,detail_json,started_at,completed_at
           FROM remote_commissioning_steps WHERE commissioning_run_id=? ORDER BY id""",
        (run_id,),
    ):
        item = dict(step)
        try:
            item["detail"] = json.loads(item.pop("detail_json"))
        except json.JSONDecodeError:
            item["detail"] = {}
            item.pop("detail_json", None)
        result["steps"].append(item)
    return result


def _finish_commissioning(conn: sqlite3.Connection, run_id: int, status: str,
                          result: Optional[dict] = None, error: Optional[str] = None) -> dict:
    now = _now()
    completed = now if status in {"succeeded", "failed"} else None
    conn.execute(
        """UPDATE remote_commissioning_runs SET status=?,updated_at=?,completed_at=?,
           last_error=?,result_json=? WHERE id=?""",
        (status, now, completed, _tail(error), _json_text(result or {}), run_id),
    )
    conn.commit()
    return _commissioning_result(conn, run_id)


def _passport_gate(conn: sqlite3.Connection, machine_key: str) -> dict:
    row = conn.execute(
        """SELECT mp.status,mp.telemetry_strategy,mp.controller_host
           FROM machine_passports mp JOIN machines m ON m.id=mp.machine_id
           WHERE m.machine_key=?""", (machine_key,),
    ).fetchone()
    if not row or row["status"] != "confirmed":
        raise ValueError("Confirm the machine passport before live agent commissioning")
    if row["telemetry_strategy"] != "maestro_agent":
        raise ValueError("The confirmed passport must select the Maestro agent telemetry strategy")
    return dict(row)


def _select_discovered(candidates: list[dict], selected: Optional[str], label: str,
                       required: bool) -> tuple[Optional[str], list[str]]:
    existing = [str(item.get("path")) for item in candidates if item.get("exists")]
    by_key = {path.casefold(): path for path in existing}
    if selected:
        match = by_key.get(str(selected).strip().casefold())
        if not match:
            raise ValueError(f"The selected {label} folder was not found on the machine PC")
        return match, existing
    if len(existing) == 1:
        return existing[0], existing
    if required:
        return None, existing
    return None, existing


def _save_machine_config(cfg_path: Path, target: dict, log_folder: str,
                         cnc_folder: Optional[str]) -> dict:
    site = config_editor.load(cfg_path)
    agents = site.get("maestro_agents", [])
    found = False
    updated = []
    for agent in agents:
        if agent.get("machine_key") != target["machine_key"]:
            updated.append(agent)
            continue
        found = True
        item = {**agent, "host": target["host"], "log_folder": log_folder}
        if cnc_folder:
            item["cnc_folder"] = cnc_folder
        updated.append(item)
    if not found:
        raise ValueError(f"Unknown Maestro machine '{target['machine_key']}'")
    saved = config_editor.save(cfg_path, {"maestro_agents": updated})
    return {"backup_path": saved["backup_path"], "saved_at": saved["saved_at"],
            "host": target["host"], "log_folder": log_folder,
            "cnc_folder": cnc_folder}


def _remote_agent_verification(conn: sqlite3.Connection, cfg_path: Path,
                               payload: dict, actor: str) -> dict:
    target = _trusted_target(conn, cfg_path, payload)
    task = f"HIVE Agent - {target['machine_key']}"
    script = rf"""
$task=Get-ScheduledTask -TaskName {_ps_quote(task)} -ErrorAction SilentlyContinue
$config='C:\HIVE-Agent\config\machines.yaml'; $log='C:\HIVE-Agent\logs\agent.log'
$configuredLog=$null
if(Test-Path -LiteralPath $config){{$match=Select-String -LiteralPath $config -Pattern '^\s*log_folder:\s*["'']?(.*?)["'']?\s*$' | Select-Object -First 1;if($match){{$configuredLog=$match.Matches[0].Groups[1].Value.Trim()}}}}
[ordered]@{{status='completed';agent_installed=[bool](Test-Path -LiteralPath 'C:\HIVE-Agent');task_exists=[bool]$task;task_state=if($task){{$task.State.ToString()}}else{{$null}};config_exists=[bool](Test-Path -LiteralPath $config);configured_log_folder=$configuredLog;log_exists=[bool](Test-Path -LiteralPath $log);log_lines=if(Test-Path -LiteralPath $log){{@(Get-Content -LiteralPath $log -Tail 20 -ErrorAction SilentlyContinue).Count}}else{{0}}}} | ConvertTo-Json -Compress
"""
    return _run_json(conn, target, "verify_agent", actor,
                     "Verify installed agent, scheduled task, configuration, and log", script)


def _central_signal(conn: sqlite3.Connection, machine_key: str) -> dict:
    row = conn.execute(
        """SELECT a.source,a.last_heartbeat_at,a.last_event_at,a.last_received_at
           FROM agent_status a JOIN machines m ON m.id=a.machine_id
           WHERE m.machine_key=?""", (machine_key,),
    ).fetchone()
    if not row:
        return {"status": "awaiting_signal", "fresh": False,
                "detail": "No heartbeat or event has reached the central PC yet"}
    data = dict(row)
    latest = data.get("last_heartbeat_at") or data.get("last_event_at") or data.get("last_received_at")
    try:
        parsed = datetime.fromisoformat(str(latest).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age_s = max(0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        age_s = None
    fresh = age_s is not None and age_s <= HEARTBEAT_FRESH_SECONDS
    return {**data, "status": "received" if fresh else "awaiting_signal",
            "fresh": fresh, "latest_signal_at": latest, "age_s": age_s}


def _start_commissioning(conn: sqlite3.Connection, target: dict, payload: dict,
                         actor: str) -> int:
    resume_id = payload.get("resume_run_id")
    if resume_id:
        row = conn.execute(
            """SELECT rcr.*,m.machine_key FROM remote_commissioning_runs rcr
               JOIN machines m ON m.id=rcr.machine_id WHERE rcr.id=?""", (resume_id,),
        ).fetchone()
        if not row or row["machine_key"] != target["machine_key"]:
            raise ValueError("The commissioning run does not belong to this machine")
        if row["status"] not in {"needs_input", "awaiting_signal"}:
            raise ValueError("Only a paused commissioning run can be resumed")
        if row["host"] != target["host"] or int(row["port"]) != target["port"]:
            raise ValueError("The commissioning endpoint changed; start a new trusted run")
        conn.execute(
            """UPDATE remote_commissioning_runs SET status='running',updated_at=?,
               completed_at=NULL,last_error=NULL WHERE id=?""", (_now(), resume_id),
        )
        conn.commit()
        return int(resume_id)
    machine = _machine_row(conn, target["machine_key"])
    now = _now()
    cursor = conn.execute(
        """INSERT INTO remote_commissioning_runs
           (machine_id,mode,status,stage,host,port,username,force_reinstall,actor,
            started_at,updated_at)
           VALUES (?,'live','running','authenticate',?,?,?,?,?,?,?)""",
        (machine["id"], target["host"], target["port"], target["username"],
         int(bool(payload.get("force_reinstall"))), actor, now, now),
    )
    conn.commit()
    return cursor.lastrowid


def _paths_equal(left: Optional[str], right: Optional[str]) -> bool:
    return bool(left and right and left.strip().casefold() == right.strip().casefold())


def _verification_outcome(conn: sqlite3.Connection, cfg_path: Path, run_id: int,
                          target: dict, actor: str) -> dict:
    _commissioning_step(conn, run_id, "verify_remote", "running")
    remote = _remote_agent_verification(conn, cfg_path, target, actor)
    expected = conn.execute(
        "SELECT selected_log_folder FROM remote_commissioning_runs WHERE id=?", (run_id,),
    ).fetchone()["selected_log_folder"]
    remote_ok = bool(
        remote.get("agent_installed") and remote.get("task_exists")
        and remote.get("task_state") in {"Running", "Ready"}
        and remote.get("config_exists") and _paths_equal(remote.get("configured_log_folder"), expected)
    )
    _commissioning_step(conn, run_id, "verify_remote",
                        "succeeded" if remote_ok else "failed", remote)
    if not remote_ok:
        return _finish_commissioning(
            conn, run_id, "failed", {"remote": remote},
            "Remote agent verification did not match the selected machine configuration",
        )
    signal = _central_signal(conn, target["machine_key"])
    signal_status = "succeeded" if signal["fresh"] else "awaiting_signal"
    _commissioning_step(conn, run_id, "verify_signal", signal_status, signal)
    return _finish_commissioning(
        conn, run_id, "succeeded" if signal["fresh"] else "awaiting_signal",
        {"remote": remote, "signal": signal},
    )


def commission_agent(conn: sqlite3.Connection, cfg_path: Path, payload: dict,
                     actor: str) -> dict:
    target = _target(cfg_path, payload)
    if not payload.get("execute"):
        return {
            "generated_at": _now(), "machine_key": target["machine_key"],
            "mode": "dry_run", "status": "preview_ready", "will_execute": False,
            "steps": ["authenticate", "discover_folders", "save_config", "install_agent",
                      "verify_remote", "verify_signal"],
            "guardrail": "Live commissioning requires a confirmed Maestro passport, trusted SSH fingerprint, and administrator access.",
        }
    target = _trusted_target(conn, cfg_path, payload)
    passport = _passport_gate(conn, target["machine_key"])
    passport_host = str(passport.get("controller_host") or "").strip()
    if passport_host and passport_host.casefold() != target["host"].casefold():
        raise ValueError("The trusted SSH endpoint does not match the confirmed passport host")
    run_id = _start_commissioning(conn, target, payload, actor)
    try:
        if payload.get("resume_run_id") and _commissioning_result(conn, run_id)["status"] == "running":
            previous = conn.execute(
                "SELECT status FROM remote_commissioning_steps WHERE commissioning_run_id=? AND step_key='verify_remote'",
                (run_id,),
            ).fetchone()
            if previous and previous["status"] == "succeeded" and not payload.get("selected_log_folder"):
                return _verification_outcome(conn, cfg_path, run_id, target, actor)

        _commissioning_step(conn, run_id, "authenticate", "running")
        authentication = authenticate(conn, cfg_path, {**target, "execute": True}, actor)
        if not authentication.get("is_admin"):
            raise ValueError("The trusted SSH account does not have Windows administrator access")
        _commissioning_step(conn, run_id, "authenticate", "succeeded", authentication)

        _commissioning_step(conn, run_id, "discover_folders", "running")
        discovery_payload = {**target, "execute": True}
        if payload.get("selected_log_folder"):
            discovery_payload["log_folder"] = payload["selected_log_folder"]
        if payload.get("selected_cnc_folder"):
            discovery_payload["cnc_folder"] = payload["selected_cnc_folder"]
        discovery = detect_folders(conn, cfg_path, discovery_payload, actor)
        selected_log, available_logs = _select_discovered(
            discovery.get("log_candidates", []), payload.get("selected_log_folder"),
            "Maestro log", True,
        )
        selected_cnc, available_cnc = _select_discovered(
            discovery.get("cnc_candidates", []), payload.get("selected_cnc_folder"),
            "CNC", False,
        )
        detail = {"log_candidates": discovery.get("log_candidates", []),
                  "cnc_candidates": discovery.get("cnc_candidates", [])}
        if not selected_log:
            detail["input_required"] = "selected_log_folder"
            detail["available_log_folders"] = available_logs
            detail["available_cnc_folders"] = available_cnc
            _commissioning_step(conn, run_id, "discover_folders", "needs_input", detail)
            return _finish_commissioning(conn, run_id, "needs_input", detail)
        detail.update({"selected_log_folder": selected_log,
                       "selected_cnc_folder": selected_cnc})
        _commissioning_step(conn, run_id, "discover_folders", "succeeded", detail)
        conn.execute(
            """UPDATE remote_commissioning_runs SET selected_log_folder=?,selected_cnc_folder=?,
               updated_at=? WHERE id=?""", (selected_log, selected_cnc, _now(), run_id),
        )
        conn.commit()

        _commissioning_step(conn, run_id, "save_config", "running")
        saved = _save_machine_config(cfg_path, target, selected_log, selected_cnc)
        _commissioning_step(conn, run_id, "save_config", "succeeded", saved)

        installed_and_current = bool(
            authentication.get("agent_installed")
            and authentication.get("task_state") in {"Running", "Ready"}
            and _paths_equal(authentication.get("configured_log_folder"), selected_log)
            and not payload.get("force_reinstall")
        )
        if installed_and_current:
            _commissioning_step(conn, run_id, "install_agent", "skipped", {
                "reason": "A healthy agent already uses the selected log folder",
                "task_state": authentication.get("task_state"),
            })
        else:
            _commissioning_step(conn, run_id, "install_agent", "running")
            installed = install_agent(conn, cfg_path, {
                **target, "log_folder": selected_log, "cnc_folder": selected_cnc,
                "execute": True,
            }, actor)
            _commissioning_step(conn, run_id, "install_agent", "succeeded", installed)
        return _verification_outcome(conn, cfg_path, run_id, target, actor)
    except ValueError as error:
        stage = conn.execute(
            "SELECT stage FROM remote_commissioning_runs WHERE id=?", (run_id,),
        ).fetchone()["stage"]
        _commissioning_step(conn, run_id, stage, "failed", {"detail": str(error)})
        return _finish_commissioning(conn, run_id, "failed", error=str(error))


def verify_commissioning(conn: sqlite3.Connection, cfg_path: Path, run_id: int,
                         actor: str) -> dict:
    run = _commissioning_result(conn, run_id)
    if run["mode"] != "live" or run["status"] != "awaiting_signal":
        raise ValueError("Only a live commissioning run awaiting a central signal can be verified")
    target = _trusted_target(conn, cfg_path, {
        "machine_key": run["machine_key"], "host": run["host"],
        "port": run["port"], "username": run["username"],
    })
    return _verification_outcome(conn, cfg_path, run_id, target, actor)
