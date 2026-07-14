"""Deployment and connection diagnostics for HIVE OS."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

PLACEHOLDER_HOSTS = {
    *(f"192.168.1.{number}" for number in range(51, 55)),
    *(f"192.168.1.{number}" for number in range(101, 111)),
}


def _age_seconds(ts: Optional[str]) -> Optional[int]:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - parsed).total_seconds()))
    except ValueError:
        return None


def _status(age_s: Optional[int], configured: bool) -> str:
    if age_s is not None:
        if age_s <= 180:
            return "online"
        if age_s <= 900:
            return "stale"
        return "offline"
    return "waiting" if configured else "not_configured"


def _latest_ts(*values: Optional[str]) -> Optional[str]:
    present = [value for value in values if value]
    if not present:
        return None
    def key(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return max(present, key=key)


def _configured(source: str, cfg: dict) -> bool:
    host = cfg.get("host") or cfg.get("modbus_host")
    if host in PLACEHOLDER_HOSTS:
        return False
    if source == "maestro":
        return bool(host and cfg.get("log_folder"))
    if source == "energy_meter":
        return bool(host)
    return False


def build(conn: sqlite3.Connection, cfg_path: Path,
          mqtt_connected: bool, cv_watcher_running: bool) -> dict:
    cfg = yaml.safe_load(cfg_path.read_text())
    maestro = {item["machine_key"]: item for item in cfg.get("maestro_agents", [])}
    energy = {item["machine_key"]: item for item in cfg.get("energy_meters", [])}
    latest_rows = conn.execute(
        """SELECT m.machine_key,
                  MAX(me.ts) latest_event_ts,
                  a.last_heartbeat_at,
                  a.last_received_at
           FROM machines m
           LEFT JOIN machine_events me ON me.machine_id=m.id
           LEFT JOIN agent_status a ON a.machine_id=m.id
           GROUP BY m.id"""
    ).fetchall()
    latest = {
        row["machine_key"]: _latest_ts(
            row["last_heartbeat_at"], row["last_received_at"], row["latest_event_ts"]
        )
        for row in latest_rows
    }
    machines = []

    for row in conn.execute(
        "SELECT machine_key,name,type FROM machines WHERE active=1 ORDER BY id"
    ).fetchall():
        key = row["machine_key"]
        source = "maestro" if key in maestro else ("energy_meter" if key in energy else "controller")
        source_cfg = maestro.get(key) or energy.get(key) or {}
        configured = _configured(source, source_cfg)
        age_s = _age_seconds(latest.get(key))
        machines.append({
            "machine_key": key,
            "name": row["name"],
            "type": row["type"],
            "source": source,
            "configured": configured,
            "status": _status(age_s, configured),
            "last_seen": latest.get(key),
            "age_seconds": age_s,
            "host": source_cfg.get("host") or source_cfg.get("modbus_host"),
            "path": source_cfg.get("log_folder"),
        })

    cv_folder = cfg.get("cv_watch_folder")
    cv_configured = bool(
        cv_folder
        and cv_folder != r"C:\CabinetVision\Export"
        and "TODO" not in str(cv_folder)
    )
    maintenance = conn.execute(
        """SELECT COUNT(*) plans,
                  COUNT(DISTINCT CASE WHEN verified=1 AND active=1 THEN machine_id END) verified
           FROM maintenance_plans"""
    ).fetchone()
    spare_shortages = conn.execute(
        """SELECT COUNT(*) count FROM maintenance_spare_reservations
           WHERE required=1 AND status='shortage'"""
    ).fetchone()["count"]
    verified_plans = int(maintenance["verified"] or 0)
    maintenance_ready = verified_plans >= len(machines) and not spare_shortages
    connector_counts = conn.execute(
        """SELECT COUNT(*) total,
                  SUM(CASE WHEN verified=1 THEN 1 ELSE 0 END) verified,
                  SUM(CASE WHEN enabled=1 THEN 1 ELSE 0 END) enabled
           FROM connector_profiles"""
    ).fetchone()
    connector_total = int(connector_counts["total"] or 0)
    connector_verified = int(connector_counts["verified"] or 0)
    connector_enabled = int(connector_counts["enabled"] or 0)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_machines": len(machines),
            "configured_machines": sum(1 for machine in machines if machine["configured"]),
            "online_machines": sum(1 for machine in machines if machine["status"] == "online"),
            "attention_needed": sum(
                1 for machine in machines if machine["status"] in ("stale", "offline")
            ),
            "verified_maintenance_plans": verified_plans,
            "maintenance_spare_shortages": int(spare_shortages),
            "verified_connectors": connector_verified,
            "enabled_connectors": connector_enabled,
        },
        "services": [
            {"key": "database", "name": "Database", "status": "online",
             "detail": "SQLite connection active"},
            {"key": "mqtt", "name": "MQTT broker bridge",
             "status": "online" if mqtt_connected else "offline",
             "detail": f"{cfg['mqtt']['broker_host']}:{cfg['mqtt']['broker_port']}"},
            {"key": "cabinet_vision", "name": "Cabinet Vision watcher",
             "status": "online" if cv_watcher_running else (
                 "offline" if cv_configured else "not_configured"
             ),
             "detail": cv_folder or "No export folder configured"},
            {"key": "maintenance", "name": "Preventive maintenance",
             "status": "ready" if maintenance_ready else "needs_site_value",
             "detail": (
                 f"{verified_plans}/{len(machines)} machines covered; "
                 f"{spare_shortages} required spare shortages"
             )},
            {"key": "connectors", "name": "Factory data connectors",
             "status": "ready" if connector_total and connector_verified == connector_total else "needs_site_value",
             "detail": (
                 f"{connector_verified}/{connector_total} approved; "
                 f"{connector_enabled} enabled"
             )},
        ],
        "machines": machines,
    }
