"""Deployment and connection diagnostics for HIVE OS."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

import inventory
import improvement
import procurement
import root_cause
import alerting
import access_control

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
    industrial_counts = conn.execute(
        """SELECT COUNT(*) total,
                  SUM(CASE WHEN verified=1 THEN 1 ELSE 0 END) verified,
                  SUM(CASE WHEN enabled=1 THEN 1 ELSE 0 END) enabled,
                  SUM(CASE WHEN enabled=1 AND last_error IS NOT NULL THEN 1 ELSE 0 END) failing
           FROM industrial_profiles"""
    ).fetchone()
    industrial_total = int(industrial_counts["total"] or 0)
    industrial_verified = int(industrial_counts["verified"] or 0)
    industrial_enabled = int(industrial_counts["enabled"] or 0)
    industrial_failing = int(industrial_counts["failing"] or 0)
    warehouse = inventory.snapshot(conn, sync=False)
    warehouse_summary = warehouse["summary"]
    warehouse_ready = (
        warehouse_summary["component_items"] > 0
        and warehouse["component_ready"]
        and warehouse_summary["component_shortages"] == 0
        and warehouse_summary["open_sync_issues"] == 0
    )
    purchasing = procurement.snapshot(conn)
    purchasing_summary = purchasing["summary"]
    procurement_ready = bool(
        purchasing_summary["suppliers"] > 0 and purchasing["commissioned"]
    )
    improvements = improvement.snapshot(conn)
    improvement_summary = improvements["summary"]
    promoted_patterns = sum(
        1 for item in improvements["learned_patterns"] if item["promoted"]
    )
    root_causes = root_cause.snapshot(conn)
    root_cause_summary = root_causes["summary"]
    learned_incident_types = sum(
        item["empirical_prior_active"] for item in root_causes["learning"].values()
    )
    alerts = alerting.snapshot(conn)
    alert_summary = alerts["summary"]
    alert_settings = alerts["settings"]
    verified_alert_destinations = sum(
        bool(item["verified_at"]) for item in alerts["destinations"]
    )
    enabled_alert_destinations = sum(
        bool(item["enabled"]) for item in alerts["destinations"]
    )
    access = access_control.snapshot(conn)
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
            "verified_industrial_profiles": industrial_verified,
            "enabled_industrial_profiles": industrial_enabled,
            "component_stock_items": warehouse_summary["component_items"],
            "component_shortages": warehouse_summary["component_shortages"],
            "verified_available_remnants": warehouse_summary["available_remnants"],
            "inventory_sync_issues": warehouse_summary["open_sync_issues"],
            "verified_suppliers": purchasing_summary["verified_suppliers"],
            "mapped_procurement_shortages": purchasing_summary["mapped_shortages"],
            "procurement_supply_risks": purchasing_summary["supply_risks"],
            "open_purchase_orders": purchasing_summary["open_purchase_orders"],
            "procurement_outbox_pending": purchasing_summary["pending_outbox"],
            "improvement_actions_active": improvement_summary["active"],
            "improvement_actions_evaluable": improvement_summary["evaluable"],
            "validated_improvements": improvement_summary["validated"],
            "promoted_improvement_patterns": promoted_patterns,
            "open_diagnostic_cases": root_cause_summary["open"],
            "confirmed_root_causes": root_cause_summary["confirmed"],
            "diagnostic_models_learning": learned_incident_types,
            "active_alerts": alert_summary["active"],
            "critical_unacknowledged_alerts": alert_summary["critical_unacknowledged"],
            "failed_alert_deliveries": alert_summary["failed_deliveries"],
            "verified_alert_destinations": verified_alert_destinations,
            "active_access_users": access["active_users"],
            "active_access_sessions": access["active_sessions"],
            "failed_logins_24h": access["failed_logins_24h"],
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
            {"key": "industrial_io", "name": "Industrial telemetry gateway",
             "status": "ready" if industrial_enabled and not industrial_failing else (
                 "offline" if industrial_failing else "needs_site_value"
             ),
             "detail": (
                 f"{industrial_verified}/{industrial_total} device contracts approved; "
                 f"{industrial_enabled} polling; {industrial_failing} failing"
             )},
            {"key": "warehouse", "name": "Warehouse intelligence",
             "status": "ready" if warehouse_ready else "needs_site_value",
             "detail": (
                 f"{warehouse_summary['component_items']} component items; "
                 f"{warehouse_summary['component_shortages']} shortages; "
                 f"{warehouse_summary['available_remnants']} verified remnants; "
                 f"{warehouse_summary['open_sync_issues']} source issues"
             )},
            {"key": "procurement", "name": "Procurement and ERP exchange",
             "status": "offline" if any(
                 item["status"] == "failed" for item in purchasing["outbox"]
             ) else ("ready" if procurement_ready else "needs_site_value"),
             "detail": (
                 f"{purchasing_summary['mapped_shortages']}/"
                 f"{purchasing_summary['uncovered_shortages']} shortages mapped; "
                 f"{purchasing_summary['supply_risks']} supply risks; "
                 f"{purchasing_summary['open_purchase_orders']} open POs; "
                 f"{purchasing_summary['pending_outbox']} exchange documents pending"
             )},
            {"key": "improvement_learning", "name": "Improvement outcome learning",
             "status": "ready" if improvement_summary["validated"] else (
                 "learning" if improvement_summary["active"] else "needs_site_value"
             ),
             "detail": (
                 f"{improvement_summary['active']} active; "
                 f"{improvement_summary['evaluable']} ready to evaluate; "
                 f"{improvement_summary['validated']} validated; "
                 f"{promoted_patterns} reusable patterns"
             )},
            {"key": "root_cause_diagnostics", "name": "Root-cause diagnostics",
             "status": "ready" if root_cause_summary["confirmed"] else (
                 "review" if root_cause_summary["open"] else "needs_site_value"
             ),
             "detail": (
                 f"{root_cause_summary['open']} open cases; "
                 f"{root_cause_summary['confirmed']} operator-confirmed; "
                 f"{learned_incident_types}/3 incident models learning from local priors"
             )},
            {"key": "alert_management", "name": "Alarm and escalation management",
             "status": "offline" if alert_summary["failed_deliveries"] else (
                 "ready" if alert_settings["auto_sync"] and (
                     not alert_settings["auto_dispatch"] or enabled_alert_destinations
                 ) else "needs_site_value"
             ),
             "detail": (
                 f"{alert_summary['active']} active; {alert_summary['critical_unacknowledged']} critical unacknowledged; "
                 f"{verified_alert_destinations} destinations verified; auto sync "
                 f"{'on' if alert_settings['auto_sync'] else 'off'}; dispatch "
                 f"{'on' if alert_settings['auto_dispatch'] else 'off'}"
             )},
            {"key": "access_control", "name": "Identity and access control",
             "status": "needs_site_value" if access["setup_required"] else (
                 "ready" if access["auth_required"] and access["active_users"] else "offline"
             ),
             "detail": (
                 f"{access['active_users']} active users; {access['active_sessions']} sessions; "
                 f"{access['active_api_keys']} service keys; {access['failed_logins_24h']} failed logins in 24h"
             )},
        ],
        "machines": machines,
    }
