"""Deployment package readiness and install guidance for HIVE OS."""

from datetime import datetime, timezone
from pathlib import Path

import yaml

from diagnostics import PLACEHOLDER_HOSTS


ROOT = Path(__file__).parent.parent
WINDOWS_DIR = ROOT / "deploy" / "windows"


INSTALL_ASSETS = [
    {
        "key": "central_installer",
        "label": "Central / Cabinet Vision PC installer",
        "path": "deploy/windows/install-central.ps1",
        "target": "CV or central HIVE PC",
        "command": (
            "Set-ExecutionPolicy -Scope Process Bypass; "
            ".\\deploy\\windows\\install-central.ps1"
        ),
    },
    {
        "key": "offline_builder",
        "label": "Verified offline release builder",
        "path": "deploy/windows/build-offline-bundle.ps1",
        "target": "Internet-connected Windows build PC before travel",
        "command": ".\\deploy\\windows\\build-offline-bundle.ps1 -Version 0.28.0 <installer paths>",
    },
    {
        "key": "offline_installer",
        "label": "No-network central installer",
        "path": "deploy/windows/install-central-offline.ps1",
        "target": "CV or central HIVE PC",
        "command": ".\\Install-HIVE-OS.cmd",
    },
    {
        "key": "offline_verifier",
        "label": "Cross-platform offline release verifier",
        "path": "src/offline_release.py",
        "target": "Build PC or travel laptop before copying the release to USB",
        "command": "PYTHONPATH=src python src/offline_release.py <release.zip> --version 0.28.0",
    },
    {
        "key": "backup",
        "label": "Verified system backup",
        "path": "deploy/windows/backup-hive.ps1",
        "target": "Central HIVE PC",
        "command": ".\\deploy\\windows\\backup-hive.ps1",
    },
    {
        "key": "restore",
        "label": "Controlled system restore",
        "path": "deploy/windows/restore-hive.ps1",
        "target": "Central HIVE PC recovery console",
        "command": ".\\deploy\\windows\\restore-hive.ps1 -BackupPath <backup.zip>",
    },
    {
        "key": "upgrade",
        "label": "Offline upgrade and rollback",
        "path": "deploy/windows/upgrade-hive.ps1",
        "target": "Central HIVE PC",
        "command": ".\\deploy\\windows\\upgrade-hive.ps1 -BundlePath <release.zip>",
    },
    {
        "key": "ssh_bootstrap",
        "label": "Machine-PC SSH bootstrap",
        "path": "deploy/windows/enable-hive-ssh.ps1",
        "target": "Each Maestro machine PC once",
        "command": ".\\enable-hive-ssh.ps1 -PublicKeyPath .\\hive-deploy.pub",
    },
    {
        "key": "machine_agent_installer",
        "label": "Maestro machine-agent installer",
        "path": "deploy/windows/install-machine-agent.ps1",
        "target": "Each Maestro machine PC",
        "command": (
            "Set-ExecutionPolicy -Scope Process Bypass; "
            ".\\deploy\\windows\\install-machine-agent.ps1"
        ),
    },
    {
        "key": "mqtt_restart",
        "label": "Secure MQTT restart helper",
        "path": "deploy/windows/restart-hive-mqtt.ps1",
        "target": "Central HIVE PC after certificate revocation",
        "command": ".\\deploy\\windows\\restart-hive-mqtt.ps1",
    },
    {
        "key": "install_tester",
        "label": "Post-install health checker",
        "path": "deploy/windows/test-hive-install.ps1",
        "target": "Any HIVE PC after install",
        "command": ".\\deploy\\windows\\test-hive-install.ps1",
    },
    {
        "key": "maestro_capture",
        "label": "Maestro evidence capture",
        "path": "deploy/windows/capture-maestro-logs.ps1",
        "target": "Machine PC with Maestro logs",
        "command": ".\\deploy\\windows\\capture-maestro-logs.ps1",
    },
    {
        "key": "industrial_preflight",
        "label": "Industrial network preflight",
        "path": "deploy/windows/test-industrial-network.ps1",
        "target": "Central HIVE PC on the factory OT network",
        "command": ".\\deploy\\windows\\test-industrial-network.ps1",
    },
    {
        "key": "uninstaller",
        "label": "Startup/firewall uninstaller",
        "path": "deploy/windows/uninstall-hive.ps1",
        "target": "Central or machine PC",
        "command": ".\\deploy\\windows\\uninstall-hive.ps1",
    },
]


def _exists(asset: dict) -> bool:
    return (ROOT / asset["path"]).exists()


def _configured_cv_folder(cfg: dict) -> bool:
    folder = str(cfg.get("cv_watch_folder") or "")
    return bool(folder and folder != r"C:\CabinetVision\Export" and "TODO" not in folder)


def _configured_maestro_count(cfg: dict) -> int:
    count = 0
    for item in cfg.get("maestro_agents", []):
        host = item.get("host")
        folder = item.get("log_folder")
        if host and folder and host not in PLACEHOLDER_HOSTS:
            count += 1
    return count


def _configured_energy_count(cfg: dict) -> int:
    count = 0
    for item in cfg.get("energy_meters", []):
        host = item.get("modbus_host")
        if host and host not in PLACEHOLDER_HOSTS:
            count += 1
    return count


def build(cfg_path: Path) -> dict:
    cfg = yaml.safe_load(cfg_path.read_text())
    assets = [{**asset, "exists": _exists(asset)} for asset in INSTALL_ASSETS]
    central_ready = all(
        asset["exists"]
        for asset in assets
        if asset["key"] in {
            "central_installer", "offline_builder", "offline_installer", "backup",
            "offline_verifier", "restore", "upgrade", "mqtt_restart", "install_tester", "uninstaller",
        }
    )
    agent_ready = all(
        asset["exists"]
        for asset in assets
        if asset["key"] in {"ssh_bootstrap", "machine_agent_installer", "install_tester", "maestro_capture"}
    )
    cv_configured = _configured_cv_folder(cfg)
    maestro_count = _configured_maestro_count(cfg)
    energy_count = _configured_energy_count(cfg)

    checklist = [
        {
            "key": "central_package",
            "label": "Central install package present",
            "status": "ready" if central_ready else "missing",
            "detail": "PowerShell central installer, uninstaller, and health checker exist",
        },
        {
            "key": "recovery_package",
            "label": "Offline recovery package present",
            "status": "ready" if all(
                asset["exists"] for asset in assets
                if asset["key"] in {"offline_builder", "offline_installer", "offline_verifier", "backup", "restore", "upgrade"}
            ) else "missing",
            "detail": "Offline build, static verification, install, backup, restore, upgrade, and rollback scripts exist",
        },
        {
            "key": "agent_package",
            "label": "Machine-agent package present",
            "status": "ready" if agent_ready else "missing",
            "detail": "SSH bootstrap, machine installer, Maestro capture, and health checker exist",
        },
        {
            "key": "cv_folder",
            "label": "Cabinet Vision export folder configured",
            "status": "ready" if cv_configured else "needs_site_value",
            "detail": cfg.get("cv_watch_folder") or "No folder configured",
        },
        {
            "key": "maestro_agents",
            "label": "Maestro machines configured",
            "status": "ready" if maestro_count else "needs_site_value",
            "detail": f"{maestro_count} configured in config/machines.yaml",
        },
        {
            "key": "energy_meters",
            "label": "Energy meter IPs configured",
            "status": "ready" if energy_count else "optional",
            "detail": f"{energy_count} configured in config/machines.yaml",
        },
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "install_dir": r"C:\HIVE-OS",
        "agent_dir": r"C:\HIVE-Agent",
        "central_url": "http://localhost:8000",
        "api_url": "http://localhost:8000",
        "mqtt_port": 8883,
        "assets": assets,
        "checklist": checklist,
        "copy_steps": [
            "Copy or unzip the hive-os folder onto the target Windows PC.",
            "Open PowerShell as Administrator.",
            "Run the central installer on the CV or HIVE PC.",
            "Create a verified backup before commissioning or upgrading production state.",
            "Run the public-key-only HIVE SSH bootstrap once on each Maestro PC.",
            "Compare and approve each machine host fingerprint in Setup.",
            "Detect its folders and install the machine agent centrally over SSH.",
            "Open Diagnostics in the dashboard and confirm services and agents report online.",
        ],
    }
