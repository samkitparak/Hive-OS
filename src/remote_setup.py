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
    return {
        "generated_at": _now(), "machine_key": machine_key,
        "label": machine.get("label") or machine_key, "host": machine.get("host"),
        "transport": "ssh", "ssh_port": profile["port"] if profile else 22,
        "mode": "commissioning", "credentials_stored": False,
        "identity": identity, "host_trust": profile,
        "ready_for_live_execution": bool(identity["status"] == "ready" and profile and profile["status"] == "trusted"),
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
    return {
        "generated_at": _now(), "identity": identity_status(), "hosts": hosts, "runs": runs,
        "summary": {"configured_machines": len(machine_keys), "trusted_hosts": trusted,
                    "installed_hosts": installed,
                    "failed_runs": sum(run["status"] == "failed" for run in latest_by_machine.values())},
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
[ordered]@{{status=if($isAdmin){{'ready'}}else{{'insufficient_privileges'}};computer_name=$env:COMPUTERNAME;username=$env:USERNAME;is_admin=$isAdmin;powershell=$PSVersionTable.PSVersion.ToString();agent_installed=[bool](Test-Path -LiteralPath 'C:\HIVE-Agent');task_state=if($task){{$task.State.ToString()}}else{{$null}}}} | ConvertTo-Json -Compress
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
        "security": "A fresh MQTT client certificate is issued only for live execution.",
    }
    if not payload.get("execute"):
        return preview
    target = _trusted_target(conn, cfg_path, payload)
    log_folder = str(target.get("log_folder") or "").strip()
    if not log_folder:
        raise ValueError("Select a verified Maestro log folder before installation")
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
