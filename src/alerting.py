"""Rationalized operator alarms, escalation, and commissioned delivery."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import sqlite3
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

import forecasting


SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}
ACTIVE_STATUSES = ("open", "acknowledged", "snoozed")
RULES = {
    "machine_alarm": {
        "label": "Machine alarm", "domain": "maintenance", "owner_role": "maintenance_lead",
        "response_minutes": 15,
        "rationale": "A controller alarm indicates an equipment condition requiring inspection.",
    },
    "downtime_open": {
        "label": "Extended downtime", "domain": "production", "owner_role": "shift_supervisor",
        "response_minutes": 15,
        "rationale": "Unresolved downtime blocks production and needs an owner and reason.",
    },
    "maintenance_attention": {
        "label": "Urgent maintenance work", "domain": "maintenance", "owner_role": "maintenance_lead",
        "response_minutes": 240,
        "rationale": "Urgent or overdue high-priority maintenance requires a response plan.",
    },
    "condition_triggered": {
        "label": "Condition threshold", "domain": "maintenance", "owner_role": "maintenance_lead",
        "response_minutes": 30,
        "rationale": "A commissioned condition threshold requires inspection or acknowledgement.",
    },
    "maintenance_spare_shortage": {
        "label": "Required maintenance spare shortage", "domain": "maintenance", "owner_role": "maintenance_lead",
        "response_minutes": 240,
        "rationale": "A required spare shortage can prevent planned or corrective maintenance.",
    },
    "execution_exception": {
        "label": "Execution exception", "domain": "production", "owner_role": "shift_supervisor",
        "response_minutes": 30,
        "rationale": "A station execution deviation requires reconciliation before quantity can be trusted.",
    },
    "route_exception": {
        "label": "Route exception", "domain": "production", "owner_role": "production_planner",
        "response_minutes": 60,
        "rationale": "Unexpected or out-of-sequence routing evidence needs a disposition.",
    },
    "quality_recurrence": {
        "label": "Recurring quality defect", "domain": "quality", "owner_role": "quality_lead",
        "response_minutes": 120,
        "rationale": "Three similar failures or rework records in eight hours require containment.",
    },
    "procurement_delivery_failed": {
        "label": "ERP delivery failed", "domain": "procurement", "owner_role": "procurement_lead",
        "response_minutes": 240,
        "rationale": "A failed exchange means the external system has not accepted the document.",
    },
    "industrial_profile_failed": {
        "label": "Industrial telemetry failure", "domain": "integration", "owner_role": "site_engineer",
        "response_minutes": 30,
        "rationale": "An enabled commissioned profile with a read error cannot provide trusted telemetry.",
    },
    "diagnostic_review": {
        "label": "High-confidence diagnostic review", "domain": "reliability", "owner_role": "reliability_lead",
        "response_minutes": 240,
        "rationale": "A high-confidence open hypothesis needs operator confirmation or dismissal.",
    },
    "dispatch_unacknowledged": {
        "label": "Dispatch not acknowledged", "domain": "production", "owner_role": "shift_supervisor",
        "response_minutes": 15,
        "rationale": "A dispatched station job without acknowledgement has no confirmed owner.",
    },
    "forecast_delivery_risk": {
        "label": "Forecast delivery risk", "domain": "production", "owner_role": "production_planner",
        "response_minutes": 240,
        "rationale": "A decision-ready stochastic forecast indicates a material probability of missing a committed due time.",
    },
}


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _load(value, fallback):
    if value is None:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _named_actor(value: Optional[str]) -> str:
    actor = (value or "").strip()
    if not actor or actor.lower() in {"operator", "system", "alert-engine", "alert-console"}:
        raise ValueError("A named operator is required")
    return actor


def _severity(value: str) -> str:
    return value if value in SEVERITY_ORDER else "warning"


def _candidate(rule_key: str, alert_key: str, source_type: str, source_id,
               title: str, detail: str, action: str, consequence: str,
               evidence_token, occurred_at: str, evidence: dict,
               machine_id: Optional[int] = None, severity: str = "warning",
               reopen_while_active: bool = True) -> dict:
    rule = RULES[rule_key]
    return {
        "alert_key": alert_key, "rule_key": rule_key,
        "source_type": source_type, "source_id": str(source_id),
        "machine_id": machine_id, "domain": rule["domain"],
        "severity": _severity(severity), "title": title, "detail": detail,
        "required_action": action, "consequence": consequence,
        "owner_role": rule["owner_role"], "evidence_token": str(evidence_token),
        "occurred_at": occurred_at, "evidence": evidence,
        "response_minutes": rule["response_minutes"],
        "reopen_while_active": reopen_while_active,
    }


def _machine_alarms(conn: sqlite3.Connection, now: datetime) -> list[dict]:
    rows = conn.execute(
        """SELECT me.id,me.machine_id,me.ts,me.raw_payload,m.machine_key,m.name machine_name
           FROM machine_events me JOIN machines m ON m.id=me.machine_id
           WHERE me.event_type='alarm' AND me.ts>=? ORDER BY me.ts,me.id""",
        (_iso(now - timedelta(hours=24)),),
    ).fetchall()
    groups: dict[tuple, list] = defaultdict(list)
    for row in rows:
        raw = _load(row["raw_payload"], {})
        if not isinstance(raw, dict):
            raw = {"raw": str(raw)}
        code = str(raw.get("alarm_code") or raw.get("code") or "unclassified")[:120]
        groups[(row["machine_id"], code)].append((row, raw))
    result = []
    for (machine_id, code), values in groups.items():
        row, raw = values[-1]
        critical = str(raw.get("severity", "")).lower() in {"critical", "emergency"}
        message = raw.get("message") or raw.get("msg") or "No controller message captured"
        result.append(_candidate(
            "machine_alarm", f"machine_alarm:{machine_id}:{code}", "machine_event", row["id"],
            f"{row['machine_name']} alarm {code}",
            f"{len(values)} occurrence(s) in 24 hours; latest message: {message}",
            "Inspect the machine and controller alarm history; record the disposition.",
            "Production may remain interrupted or equipment damage may worsen if the condition persists.",
            row["id"], row["ts"], {"alarm_code": code, "occurrences_24h": len(values), "latest_event_id": row["id"]},
            machine_id, "critical" if critical else "warning", False,
        ))
    return result


def _downtime(conn: sqlite3.Connection, now: datetime) -> list[dict]:
    result = []
    for row in conn.execute(
        """SELECT de.id,de.machine_id,de.started_at,de.notes,m.name machine_name,
                  COALESCE(dr.label,'Unclassified downtime') reason
           FROM downtime_events de LEFT JOIN machines m ON m.id=de.machine_id
           LEFT JOIN downtime_reasons dr ON dr.id=de.reason_id WHERE de.status='open'"""
    ).fetchall():
        elapsed = max(0, (now - _parse(row["started_at"])).total_seconds() / 60)
        if elapsed < 15:
            continue
        name = row["machine_name"] or "Factory"
        result.append(_candidate(
            "downtime_open", f"downtime_open:{row['id']}", "downtime_event", row["id"],
            f"{name} downtime exceeds {round(elapsed)} minutes",
            f"Recorded reason: {row['reason']}. {row['notes'] or 'No operator note.'}",
            "Assign an owner, verify the reason, and restore or formally hold the machine.",
            "The active production queue may miss its planned completion time.",
            row["id"], row["started_at"], {"minutes_open": round(elapsed, 1), "reason": row["reason"]},
            row["machine_id"], "critical" if elapsed >= 60 else "warning",
        ))
    return result


def _maintenance_attention(conn: sqlite3.Connection, now: datetime) -> list[dict]:
    result = []
    rows = conn.execute(
        """SELECT wo.*,m.name machine_name FROM maintenance_work_orders wo
           LEFT JOIN machines m ON m.id=wo.machine_id
           WHERE wo.status IN ('open','in_progress') AND
                 (wo.priority='urgent' OR (wo.priority='high' AND wo.due_date IS NOT NULL AND wo.due_date<=?))""",
        (_iso(now),),
    ).fetchall()
    for row in rows:
        result.append(_candidate(
            "maintenance_attention", f"maintenance_attention:{row['id']}", "maintenance_work_order", row["id"],
            f"Maintenance attention: {row['title']}",
            f"{row['machine_name'] or 'Factory'} · {row['priority']} priority · {row['status']}",
            "Review the work order, schedule safe access, and confirm required people and spares.",
            "The equipment may remain unavailable or the known condition may deteriorate.",
            f"{row['id']}:{row['status']}", row["created_at"], {"priority": row["priority"], "due_date": row["due_date"]},
            row["machine_id"], "critical" if row["priority"] == "urgent" else "warning",
        ))
    return result


def _condition_signals(conn: sqlite3.Connection) -> list[dict]:
    latest = {}
    rows = conn.execute(
        """SELECT cs.*,m.name machine_name FROM maintenance_condition_signals cs
           JOIN machines m ON m.id=cs.machine_id
           WHERE cs.triggered=1 AND cs.status IN ('open','acknowledged')
           ORDER BY cs.observed_at,cs.id"""
    ).fetchall()
    for row in rows:
        latest[(row["machine_id"], row["maintenance_plan_id"], row["metric_key"])] = row
    result = []
    for row in latest.values():
        threshold = "" if row["threshold"] is None else f" against {row['comparison'] or ''} {row['threshold']:g}"
        result.append(_candidate(
            "condition_triggered", f"condition_triggered:{row['machine_id']}:{row['maintenance_plan_id']}:{row['metric_key']}",
            "maintenance_condition", row["id"], f"{row['machine_name']} condition: {row['metric_key']}",
            f"Observed {row['value']:g} {row['unit'] or ''}{threshold}",
            "Inspect the commissioned condition source and acknowledge or clear it in maintenance.",
            "Continued operation may worsen the monitored equipment condition.",
            row["id"], row["observed_at"], {"metric": row["metric_key"], "value": row["value"], "unit": row["unit"], "threshold": row["threshold"]},
            row["machine_id"], row["severity"],
        ))
    return result


def _spare_shortages(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT wo.id work_order_id,wo.machine_id,wo.title,wo.priority,m.name machine_name,
                  COUNT(*) shortage_count,MAX(msr.id) latest_id,MIN(msr.created_at) occurred_at
           FROM maintenance_spare_reservations msr
           JOIN maintenance_work_orders wo ON wo.id=msr.work_order_id
           LEFT JOIN machines m ON m.id=wo.machine_id
           WHERE msr.required=1 AND msr.status='shortage' AND wo.status IN ('open','in_progress')
           GROUP BY wo.id"""
    ).fetchall()
    return [_candidate(
        "maintenance_spare_shortage", f"maintenance_spare_shortage:{row['work_order_id']}",
        "maintenance_work_order", row["work_order_id"], f"Required spares missing for {row['title']}",
        f"{row['shortage_count']} required line(s) unavailable for {row['machine_name'] or 'factory maintenance'}.",
        "Source or substitute the approved spare before the maintenance window.",
        "The work order cannot be completed as planned.", row["latest_id"], row["occurred_at"],
        {"shortage_lines": row["shortage_count"]}, row["machine_id"],
        "critical" if row["priority"] == "urgent" else "warning",
    ) for row in rows]


def _execution_exceptions(conn: sqlite3.Connection) -> list[dict]:
    critical = {"wip_overflow", "capacity_bypass", "unplanned_execution", "machine_evidence_rejected"}
    rows = conn.execute(
        """SELECT ee.*,m.name machine_name,j.job_name,p.part_name
           FROM execution_exceptions ee LEFT JOIN machines m ON m.id=ee.machine_id
           LEFT JOIN production_orders po ON po.id=ee.production_order_id
           LEFT JOIN jobs j ON j.id=po.job_id LEFT JOIN parts p ON p.id=ee.part_id
           WHERE ee.status='open'"""
    ).fetchall()
    return [_candidate(
        "execution_exception", f"execution_exception:{row['id']}", "execution_exception", row["id"],
        f"Execution exception: {row['exception_type'].replace('_', ' ')}",
        f"{row['job_name'] or 'Unknown job'} · {row['part_name'] or 'unknown part'} · {row['machine_name'] or 'unknown station'}: {row['details']}",
        "Reconcile the physical quantity and resolve or reject the exception.",
        "WIP, completion, or traceability quantities may be unreliable until reviewed.",
        row["id"], row["occurred_at"], {"exception_type": row["exception_type"], "source": row["source"]},
        row["machine_id"], "critical" if row["exception_type"] in critical else "warning",
    ) for row in rows]


def _route_exceptions(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT re.*,j.job_name,p.part_name,om.name observed_machine
           FROM route_exceptions re JOIN parts p ON p.id=re.part_id JOIN jobs j ON j.id=p.job_id
           LEFT JOIN machines om ON om.id=re.observed_machine_id WHERE re.status='open'"""
    ).fetchall()
    return [_candidate(
        "route_exception", f"route_exception:{row['id']}", "route_exception", row["id"],
        f"Route exception for {row['part_name']}",
        f"{row['job_name']} · {row['exception_type'].replace('_', ' ')} at {row['observed_machine'] or 'unknown station'}: {row['details']}",
        "Confirm the intended route or reject the unexpected evidence.",
        "The part route and schedule state may diverge from the physical floor.",
        row["id"], row["ts"], {"exception_type": row["exception_type"], "job_name": row["job_name"]},
        row["observed_machine_id"], "warning",
    ) for row in rows]


def _quality_recurrence(conn: sqlite3.Connection, now: datetime) -> list[dict]:
    rows = conn.execute(
        """SELECT qc.machine_id,qc.defect_type_id,COALESCE(dt.code,'unclassified') defect_code,
                  COALESCE(dt.label,'Unclassified defect') defect_label,m.name machine_name,
                  COUNT(*) count,MAX(qc.id) latest_id,MAX(qc.ts) latest_at
           FROM quality_checks qc LEFT JOIN defect_types dt ON dt.id=qc.defect_type_id
           LEFT JOIN machines m ON m.id=qc.machine_id
           WHERE qc.result IN ('fail','rework') AND qc.ts>=?
           GROUP BY qc.machine_id,qc.defect_type_id HAVING COUNT(*)>=3""",
        (_iso(now - timedelta(hours=8)),),
    ).fetchall()
    return [_candidate(
        "quality_recurrence", f"quality_recurrence:{row['machine_id'] or 'factory'}:{row['defect_code']}",
        "quality_pattern", row["latest_id"], f"Recurring {row['defect_label'].lower()} defects",
        f"{row['count']} failures or rework records in eight hours at {row['machine_name'] or 'unassigned inspection'}.",
        "Contain affected work and review material, tooling, program, and setup evidence.",
        "Additional parts may require rework or become scrap.", row["latest_id"], row["latest_at"],
        {"defect_code": row["defect_code"], "count_8h": row["count"]}, row["machine_id"], "warning", False,
    ) for row in rows]


def _procurement_failures(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM procurement_outbox WHERE status='failed'").fetchall()
    return [_candidate(
        "procurement_delivery_failed", f"procurement_delivery_failed:{row['id']}", "procurement_outbox", row["id"],
        f"{row['document_type']} delivery failed",
        f"{row['object_type']} {row['object_key']} was not acknowledged: {row['last_error'] or 'unknown error'}",
        "Correct the adapter or destination and retry the same idempotent document.",
        "The ERP or supplier system may not contain the approved purchasing document.",
        f"{row['id']}:{row['attempts']}:{row['last_error']}", row["updated_at"],
        {"attempts": row["attempts"], "object_type": row["object_type"], "object_key": row["object_key"]},
        None, "warning",
    ) for row in rows]


def _industrial_failures(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT ip.*,m.name machine_name FROM industrial_profiles ip
           LEFT JOIN machines m ON m.id=ip.machine_id
           WHERE ip.enabled=1 AND ip.verified=1 AND ip.last_error IS NOT NULL"""
    ).fetchall()
    return [_candidate(
        "industrial_profile_failed", f"industrial_profile_failed:{row['profile_key']}",
        "industrial_profile", row["profile_key"], f"Telemetry unavailable: {row['name']}",
        f"{row['protocol']} profile for {row['machine_name'] or row['profile_key']} failed: {row['last_error']}",
        "Check the endpoint, device availability, credentials, and commissioned signal contract.",
        "Machine state, condition, or utility calculations may be stale.",
        f"{row['last_poll_at']}:{row['last_error']}", row["last_poll_at"] or row["updated_at"],
        {"profile_key": row["profile_key"], "protocol": row["protocol"], "last_error": row["last_error"]},
        row["machine_id"], "critical",
    ) for row in rows]


def _diagnostic_reviews(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT dc.*,m.name machine_name FROM diagnostic_cases dc
           LEFT JOIN machines m ON m.id=dc.machine_id
           WHERE dc.status='open' AND dc.confidence='high'"""
    ).fetchall()
    return [_candidate(
        "diagnostic_review", f"diagnostic_review:{row['id']}", "diagnostic_case", row["id"],
        f"Review likely cause for {row['symptom_label']}",
        f"{row['machine_name'] or 'Factory'} · top hypothesis {row['top_hypothesis_code']} · analysis v{row['analysis_version']}",
        "Review alternatives and evidence, then confirm or dismiss the diagnostic case.",
        "The improvement loop will retain a generic cause until a named review is recorded.",
        f"{row['analysis_version']}:{row['top_hypothesis_code']}", row["occurred_at"],
        {"case_id": row["id"], "top_hypothesis": row["top_hypothesis_code"], "analysis_version": row["analysis_version"]},
        row["machine_id"], "warning",
    ) for row in rows]


def _unacknowledged_dispatch(conn: sqlite3.Connection, now: datetime) -> list[dict]:
    rows = conn.execute(
        """SELECT ej.id,ej.machine_id,ej.dispatched_at,m.name machine_name,j.job_name,p.part_name
           FROM execution_jobs ej JOIN machines m ON m.id=ej.machine_id
           JOIN production_orders po ON po.id=ej.production_order_id JOIN jobs j ON j.id=po.job_id
           JOIN part_route_steps prs ON prs.id=ej.route_step_id JOIN parts p ON p.id=prs.part_id
           WHERE ej.state='dispatched' AND ej.dispatched_at<=?""",
        (_iso(now - timedelta(minutes=15)),),
    ).fetchall()
    return [_candidate(
        "dispatch_unacknowledged", f"dispatch_unacknowledged:{row['id']}", "execution_job", row["id"],
        f"Dispatch awaiting acknowledgement at {row['machine_name']}",
        f"{row['job_name']} · {row['part_name']} has been dispatched for more than 15 minutes.",
        "Confirm an operator accepts the station job or place it on hold.",
        "The schedule assumes work ownership that has not been confirmed.",
        row["id"], row["dispatched_at"], {"execution_job_id": row["id"], "job_name": row["job_name"]},
        row["machine_id"], "warning",
    ) for row in rows]


def _forecast_delivery_risks(conn: sqlite3.Connection) -> list[dict]:
    state = forecasting.snapshot(conn)
    latest = state.get("latest")
    if not latest or not state.get("decision_ready"):
        return []
    result = latest["result"]
    risks = [item for item in result.get("jobs", [])
             if item.get("production_order_id") and item.get("late_probability") is not None
             and item["late_probability"] >= 0.5]
    return [_candidate(
        "forecast_delivery_risk",
        f"forecast_delivery_risk:{item['production_order_id']}",
        "production_forecast", latest["id"],
        f"{item['job_name']} is forecast late",
        (f"{round(item['late_probability'] * 100)}% late probability under the "
         f"{result['policy']} policy; P80 completion {item['completion_at']['p80']}."),
        "Review the order sequence, forecast constraint, materials, and due-time recovery options.",
        "The committed production due time may be missed without a reviewed recovery action.",
        f"{latest['id']}:{round(item['late_probability'], 3)}",
        latest["generated_at"], {
            "forecast_id": latest["id"], "job_name": item["job_name"],
            "late_probability": item["late_probability"],
            "p80_completion_at": item["completion_at"]["p80"],
            "calibration_status": state["calibration"]["status"],
        }, severity="critical" if item["late_probability"] >= 0.8 else "warning",
    ) for item in risks]


def collect_candidates(conn: sqlite3.Connection, now: Optional[datetime] = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    candidates = [
        *_machine_alarms(conn, now), *_downtime(conn, now), *_maintenance_attention(conn, now),
        *_condition_signals(conn), *_spare_shortages(conn), *_execution_exceptions(conn),
        *_route_exceptions(conn), *_quality_recurrence(conn, now), *_procurement_failures(conn),
        *_industrial_failures(conn), *_diagnostic_reviews(conn), *_unacknowledged_dispatch(conn, now),
        *_forecast_delivery_risks(conn),
    ]
    return sorted(candidates, key=lambda item: (-SEVERITY_ORDER[item["severity"]], item["occurred_at"], item["alert_key"]))


def _alert_row(conn: sqlite3.Connection, alert_id: int) -> dict:
    row = conn.execute(
        """SELECT ai.*,m.machine_key,m.name machine_name FROM alert_instances ai
           LEFT JOIN machines m ON m.id=ai.machine_id WHERE ai.id=?""", (alert_id,)
    ).fetchone()
    if not row:
        raise KeyError(f"Alert {alert_id} not found")
    result = dict(row)
    result["evidence"] = _load(result.pop("evidence_json"), {})
    return result


def _event_envelope(alert: dict, event: dict, delivery_key: str, now: datetime) -> dict:
    return {
        "specversion": "1.0", "id": delivery_key, "source": "/hive-os/alerts",
        "type": f"com.hiveos.alert.{event['event_type']}", "subject": alert["alert_key"],
        "time": _iso(now), "datacontenttype": "application/json",
        "data": {
            "alert_id": alert["id"], "alert_key": alert["alert_key"],
            "rule_key": alert["rule_key"], "severity": alert["severity"],
            "status": alert["status"], "title": alert["title"], "detail": alert["detail"],
            "required_action": alert["required_action"], "owner_role": alert["owner_role"],
            "owner": alert["owner"], "occurred_at": alert["occurred_at"],
            "event_type": event["event_type"], "actor": event.get("actor"),
            "notes": event.get("notes"), "api_path": f"/api/alerts/{alert['id']}",
        },
    }


def _destination_accepts(destination: dict, severity: str) -> bool:
    return SEVERITY_ORDER[severity] >= SEVERITY_ORDER.get(destination["min_severity"], 1)


def _queue_one(conn: sqlite3.Connection, alert: dict, destination: dict,
               event: dict, delivery_key: str, now: datetime,
               alert_event_id: Optional[int] = None) -> None:
    envelope = _event_envelope(alert, event, delivery_key, now)
    body = json.dumps(envelope, sort_keys=True, separators=(",", ":"))
    if len(body.encode("utf-8")) > 65_536:
        raise ValueError("Alert delivery exceeds the 64 KiB interoperability limit")
    conn.execute(
        """INSERT OR IGNORE INTO alert_deliveries
           (alert_id,alert_event_id,destination_id,delivery_key,event_type,payload_json,status,
            attempts,next_attempt_at,created_at,updated_at)
           VALUES (?,?,?,?,?,?,'pending',0,?,?,?)""",
        (alert["id"], alert_event_id, destination["id"], delivery_key,
         event["event_type"], body, _iso(now), _iso(now), _iso(now)),
    )


def _queue_event(conn: sqlite3.Connection, alert_id: int, event_id: int, now: datetime) -> None:
    alert = _alert_row(conn, alert_id)
    event = dict(conn.execute("SELECT * FROM alert_events WHERE id=?", (event_id,)).fetchone())
    destinations = conn.execute(
        "SELECT * FROM alert_destinations WHERE enabled=1 AND verified_at IS NOT NULL"
    ).fetchall()
    for row in destinations:
        destination = dict(row)
        if _destination_accepts(destination, alert["severity"]):
            _queue_one(conn, alert, destination, event,
                       f"{destination['destination_key']}:event:{event_id}", now, event_id)


def _record_event(conn: sqlite3.Connection, alert_id: int, event_type: str,
                  from_status: Optional[str], to_status: str, actor: str,
                  notes: Optional[str], payload: dict, now: datetime) -> int:
    cursor = conn.execute(
        """INSERT INTO alert_events
           (alert_id,event_type,from_status,to_status,actor,notes,payload_json,ts)
           VALUES (?,?,?,?,?,?,?,?)""",
        (alert_id, event_type, from_status, to_status, actor, notes,
         json.dumps(payload, sort_keys=True), _iso(now)),
    )
    _queue_event(conn, alert_id, cursor.lastrowid, now)
    return cursor.lastrowid


def _desired_escalation(row: dict, now: datetime, response_minutes: int) -> int:
    if row["status"] != "open":
        return 0
    due = _parse(row["response_due_at"])
    if now >= due + timedelta(minutes=response_minutes):
        return 2
    return 1 if now >= due else 0


def sync(conn: sqlite3.Connection, actor: str = "hive-alert-worker",
         now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    actor = _named_actor(actor)
    candidates = {item["alert_key"]: item for item in collect_candidates(conn, now)}
    created = refreshed = recurred = resolved = escalated = 0
    for key, candidate in candidates.items():
        row = conn.execute("SELECT * FROM alert_instances WHERE alert_key=?", (key,)).fetchone()
        if not row:
            due = now + timedelta(minutes=candidate["response_minutes"])
            cursor = conn.execute(
                """INSERT INTO alert_instances
                   (alert_key,rule_key,source_type,source_id,machine_id,domain,severity,status,title,
                    detail,required_action,consequence,owner_role,evidence_token,evidence_json,
                    occurred_at,first_seen_at,last_seen_at,response_due_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,'open',?,?,?,?,?,?,?,?,?,?,?,?)""",
                (key, candidate["rule_key"], candidate["source_type"], candidate["source_id"],
                 candidate["machine_id"], candidate["domain"], candidate["severity"],
                 candidate["title"], candidate["detail"], candidate["required_action"],
                 candidate["consequence"], candidate["owner_role"], candidate["evidence_token"],
                 json.dumps(candidate["evidence"], sort_keys=True), candidate["occurred_at"],
                 _iso(now), _iso(now), _iso(due), _iso(now)),
            )
            _record_event(conn, cursor.lastrowid, "opened", None, "open", actor, None,
                          {"rule_key": candidate["rule_key"], "severity": candidate["severity"]}, now)
            created += 1
            continue

        current = dict(row)
        refreshed += 1
        new_evidence = current["evidence_token"] != candidate["evidence_token"]
        severity_increased = SEVERITY_ORDER[candidate["severity"]] > SEVERITY_ORDER[current["severity"]]
        snooze_expired = current["status"] == "snoozed" and current["snoozed_until"] and now >= _parse(current["snoozed_until"])
        target_status = current["status"]
        event_specs = []
        reset_response = False
        if current["status"] == "resolved" and (candidate["reopen_while_active"] or new_evidence):
            target_status = "open"; reset_response = True
            event_specs.append(("reopened", current["status"], "open", "Source condition remains active"))
        elif new_evidence and current["status"] in ("acknowledged", "snoozed"):
            target_status = "open"; reset_response = True; recurred += 1
            event_specs.append(("recurred", current["status"], "open", "New source evidence arrived"))
        elif snooze_expired:
            target_status = "open"; reset_response = True
            event_specs.append(("snooze_expired", current["status"], "open", None))
        elif new_evidence:
            recurred += 1
            event_specs.append(("recurred", current["status"], current["status"], "New source evidence arrived"))
        if severity_increased:
            if target_status != "open":
                event_specs.append(("severity_escalated", target_status, "open", None))
                target_status = "open"; reset_response = True
            else:
                event_specs.append(("severity_escalated", target_status, target_status, None))

        response_due = (_iso(now + timedelta(minutes=candidate["response_minutes"]))
                        if reset_response else current["response_due_at"])
        version_delta = int(bool(event_specs or new_evidence or severity_increased))
        occurrence_delta = int(new_evidence or (current["status"] == "resolved" and target_status == "open"))
        conn.execute(
            """UPDATE alert_instances SET source_id=?,machine_id=?,domain=?,severity=?,status=?,title=?,
               detail=?,required_action=?,consequence=?,owner_role=?,evidence_token=?,evidence_json=?,
               occurred_at=?,last_seen_at=?,response_due_at=?,occurrence_count=occurrence_count+?,
               escalation_level=CASE WHEN ? THEN 0 ELSE escalation_level END,
               escalated_at=CASE WHEN ? THEN NULL ELSE escalated_at END,
               acknowledged_at=CASE WHEN ?='open' THEN NULL ELSE acknowledged_at END,
               acknowledged_by=CASE WHEN ?='open' THEN NULL ELSE acknowledged_by END,
               snoozed_until=CASE WHEN ?='open' THEN NULL ELSE snoozed_until END,
               resolved_at=CASE WHEN ?='open' THEN NULL ELSE resolved_at END,
               resolved_by=CASE WHEN ?='open' THEN NULL ELSE resolved_by END,
               resolution_notes=CASE WHEN ?='open' THEN NULL ELSE resolution_notes END,
               version=version+?,updated_at=? WHERE id=?""",
            (candidate["source_id"], candidate["machine_id"], candidate["domain"], candidate["severity"],
             target_status, candidate["title"], candidate["detail"], candidate["required_action"],
             candidate["consequence"], candidate["owner_role"], candidate["evidence_token"],
             json.dumps(candidate["evidence"], sort_keys=True), candidate["occurred_at"], _iso(now),
             response_due, occurrence_delta, int(reset_response), int(reset_response), target_status,
             target_status, target_status, target_status, target_status, target_status,
             version_delta, _iso(now), current["id"]),
        )
        for event_type, from_status, to_status, notes in event_specs:
            _record_event(conn, current["id"], event_type, from_status, to_status, actor, notes,
                          {"severity": candidate["severity"], "evidence_token": candidate["evidence_token"]}, now)

        updated = dict(conn.execute("SELECT * FROM alert_instances WHERE id=?", (current["id"],)).fetchone())
        desired = _desired_escalation(updated, now, candidate["response_minutes"])
        if desired > int(updated["escalation_level"]):
            conn.execute(
                """UPDATE alert_instances SET escalation_level=?,escalated_at=?,version=version+1,
                   updated_at=? WHERE id=?""", (desired, _iso(now), _iso(now), current["id"])
            )
            _record_event(conn, current["id"], "response_overdue", target_status, target_status, actor,
                          None, {"escalation_level": desired, "response_due_at": response_due}, now)
            escalated += 1

    managed = tuple(RULES)
    placeholders = ",".join("?" for _ in managed)
    active_rows = conn.execute(
        f"SELECT id,alert_key,status FROM alert_instances WHERE rule_key IN ({placeholders}) AND status!='resolved'",
        managed,
    ).fetchall()
    for row in active_rows:
        if row["alert_key"] in candidates:
            continue
        conn.execute(
            """UPDATE alert_instances SET status='resolved',resolved_at=?,resolved_by='source-sync',
               resolution_notes='Source condition cleared',snoozed_until=NULL,version=version+1,updated_at=?
               WHERE id=?""", (_iso(now), _iso(now), row["id"])
        )
        _record_event(conn, row["id"], "source_cleared", row["status"], "resolved", actor,
                      "Source condition cleared", {}, now)
        resolved += 1
    conn.commit()
    result = snapshot(conn, now=now)
    result["sync"] = {"candidates": len(candidates), "created": created, "refreshed": refreshed,
                      "recurred": recurred, "resolved": resolved, "escalated": escalated}
    return result


def alert_detail(conn: sqlite3.Connection, alert_id: int) -> dict:
    result = _alert_row(conn, alert_id)
    result["events"] = []
    for row in conn.execute(
        "SELECT * FROM alert_events WHERE alert_id=? ORDER BY id DESC LIMIT 50", (alert_id,)
    ).fetchall():
        event = dict(row)
        event["payload"] = _load(event.pop("payload_json"), {})
        result["events"].append(event)
    return result


def act(conn: sqlite3.Connection, alert_id: int, payload: dict,
        now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    actor = _named_actor(payload.get("actor"))
    row = conn.execute("SELECT * FROM alert_instances WHERE id=?", (alert_id,)).fetchone()
    if not row:
        raise KeyError(f"Alert {alert_id} not found")
    current = dict(row)
    if payload.get("expected_version") is not None and int(payload["expected_version"]) != int(current["version"]):
        raise ValueError("Alert changed; refresh before acting")
    action = payload["action"]
    notes = (payload.get("notes") or "").strip() or None
    owner = (payload.get("owner") or actor).strip()
    if action == "acknowledge":
        if current["status"] != "open":
            raise ValueError("Only an open alert can be acknowledged")
        target = "acknowledged"
        values = {"acknowledged_at": _iso(now), "acknowledged_by": actor,
                  "snoozed_until": None, "resolved_at": None, "resolved_by": None,
                  "resolution_notes": None, "response_due_at": current["response_due_at"]}
    elif action == "snooze":
        if current["status"] not in ("open", "acknowledged"):
            raise ValueError("Only an open or acknowledged alert can be snoozed")
        minutes = int(payload.get("snooze_minutes") or 0)
        if minutes < 5 or minutes > 1440 or not notes:
            raise ValueError("Snoozing requires a reason and 5 to 1440 minutes")
        target = "snoozed"
        values = {"acknowledged_at": current["acknowledged_at"] or _iso(now),
                  "acknowledged_by": current["acknowledged_by"] or actor,
                  "snoozed_until": _iso(now + timedelta(minutes=minutes)),
                  "resolved_at": None, "resolved_by": None, "resolution_notes": None,
                  "response_due_at": current["response_due_at"]}
    elif action == "resolve":
        if current["status"] not in ACTIVE_STATUSES or not notes:
            raise ValueError("Resolving an active alert requires a disposition note")
        target = "resolved"
        values = {"acknowledged_at": current["acknowledged_at"],
                  "acknowledged_by": current["acknowledged_by"], "snoozed_until": None,
                  "resolved_at": _iso(now), "resolved_by": actor, "resolution_notes": notes,
                  "response_due_at": current["response_due_at"]}
    elif action == "reopen":
        if current["status"] != "resolved":
            raise ValueError("Only a resolved alert can be reopened")
        target = "open"
        minutes = RULES.get(current["rule_key"], {}).get("response_minutes", 60)
        values = {"acknowledged_at": None, "acknowledged_by": None, "snoozed_until": None,
                  "resolved_at": None, "resolved_by": None, "resolution_notes": None,
                  "response_due_at": _iso(now + timedelta(minutes=minutes))}
    else:
        raise ValueError(f"Unsupported alert action '{action}'")
    conn.execute(
        """UPDATE alert_instances SET status=?,owner=?,acknowledged_at=?,acknowledged_by=?,
           snoozed_until=?,resolved_at=?,resolved_by=?,resolution_notes=?,response_due_at=?,
           escalation_level=0,escalated_at=NULL,version=version+1,updated_at=? WHERE id=?""",
        (target, owner, values["acknowledged_at"], values["acknowledged_by"], values["snoozed_until"],
         values["resolved_at"], values["resolved_by"], values["resolution_notes"],
         values["response_due_at"], _iso(now), alert_id),
    )
    _record_event(conn, alert_id, action, current["status"], target, actor, notes,
                  {"owner": owner, "snooze_minutes": payload.get("snooze_minutes")}, now)
    conn.commit()
    return alert_detail(conn, alert_id)


def _validate_endpoint(endpoint: str) -> None:
    parsed = urlparse(endpoint)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Webhook endpoint must be an absolute HTTP or HTTPS URL")
    if parsed.username or parsed.password:
        raise ValueError("Webhook credentials must not be embedded in the URL")
    if parsed.scheme == "http" and parsed.hostname != "localhost":
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError as error:
            raise ValueError("Public and DNS-named webhook endpoints must use HTTPS") from error
        if not (address.is_private or address.is_loopback):
            raise ValueError("Public webhook endpoints must use HTTPS")


def _admin_event(conn: sqlite3.Connection, target_type: str, target_key: str,
                 event_type: str, actor: str, payload: dict, now: datetime) -> None:
    conn.execute(
        """INSERT INTO alert_admin_events
           (target_type,target_key,event_type,actor,payload_json,ts) VALUES (?,?,?,?,?,?)""",
        (target_type, target_key, event_type, actor, json.dumps(payload, sort_keys=True), _iso(now)),
    )


def upsert_destination(conn: sqlite3.Connection, destination_key: str, payload: dict,
                       now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    actor = _named_actor(payload.get("actor"))
    if not re.fullmatch(r"[a-z0-9_.-]+", destination_key):
        raise ValueError("Destination key may contain lowercase letters, numbers, dots, dashes, and underscores")
    if payload.get("channel", "webhook") != "webhook":
        raise ValueError("Only the commissioned webhook channel is currently supported")
    if payload.get("min_severity", "warning") not in SEVERITY_ORDER:
        raise ValueError("Minimum severity must be info, warning, or critical")
    _validate_endpoint(payload["endpoint"])
    current_row = conn.execute(
        "SELECT * FROM alert_destinations WHERE destination_key=?", (destination_key,)
    ).fetchone()
    current = dict(current_row) if current_row else None
    if current and payload.get("expected_version") is not None and int(payload["expected_version"]) != int(current["version"]):
        raise ValueError("Alert destination changed; refresh before saving")
    changed_contract = not current or current["endpoint"] != payload["endpoint"] or current["secret_env"] != payload.get("secret_env")
    verified_at = None if changed_contract else current["verified_at"]
    enabled = bool(payload.get("enabled", False))
    if enabled and not verified_at:
        raise ValueError("Run a successful live destination test before enabling delivery")
    if current:
        conn.execute(
            """UPDATE alert_destinations SET name=?,channel='webhook',endpoint=?,secret_env=?,
               min_severity=?,enabled=?,verified_at=?,last_error=CASE WHEN ? THEN NULL ELSE last_error END,
               version=version+1,updated_at=? WHERE id=?""",
            (payload["name"], payload["endpoint"], payload.get("secret_env"), payload.get("min_severity", "warning"),
             int(enabled), verified_at, int(changed_contract), _iso(now), current["id"]),
        )
        event_type = "destination_contract_changed" if changed_contract else "destination_updated"
    else:
        conn.execute(
            """INSERT INTO alert_destinations
               (destination_key,name,channel,endpoint,secret_env,min_severity,enabled,created_at,updated_at)
               VALUES (?,?,'webhook',?,?,?,?,?,?)""",
            (destination_key, payload["name"], payload["endpoint"], payload.get("secret_env"),
             payload.get("min_severity", "warning"), int(enabled), _iso(now), _iso(now)),
        )
        event_type = "destination_created"
    _admin_event(conn, "destination", destination_key, event_type, actor,
                 {"endpoint": payload["endpoint"], "min_severity": payload.get("min_severity", "warning"), "enabled": enabled}, now)
    conn.commit()
    return destination_detail(conn, destination_key)


def destination_detail(conn: sqlite3.Connection, destination_key: str) -> dict:
    row = conn.execute(
        "SELECT * FROM alert_destinations WHERE destination_key=?", (destination_key,)
    ).fetchone()
    if not row:
        raise KeyError(f"Alert destination '{destination_key}' not found")
    return dict(row)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _delivery_headers(destination: dict, body: bytes, delivery_key: str) -> dict:
    headers = {
        "Content-Type": "application/cloudevents+json; charset=utf-8",
        "User-Agent": "HIVE-OS-Alerting/1.0", "X-HIVE-Delivery": delivery_key,
    }
    if destination.get("secret_env"):
        secret = os.getenv(destination["secret_env"])
        if not secret:
            raise ValueError(f"Credential environment variable '{destination['secret_env']}' is not set")
        signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers["X-HIVE-Signature"] = f"sha256={signature}"
    return headers


def _post_webhook(destination: dict, payload_json: str, delivery_key: str,
                  timeout: float = 5.0) -> tuple[int, str]:
    _validate_endpoint(destination["endpoint"])
    body = payload_json.encode("utf-8")
    if len(body) > 65_536:
        raise ValueError("Webhook payload exceeds 64 KiB")
    request = urllib.request.Request(
        destination["endpoint"], data=body,
        headers=_delivery_headers(destination, body, delivery_key), method="POST",
    )
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            status = int(response.status)
            response_body = response.read(501).decode("utf-8", errors="replace")[:500]
    except urllib.error.HTTPError as error:
        response_body = error.read(501).decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Webhook returned HTTP {error.code}: {response_body}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Webhook connection failed: {error.reason}") from error
    if status < 200 or status >= 300:
        raise RuntimeError(f"Webhook returned HTTP {status}")
    return status, response_body


def _test_envelope(destination_key: str, now: datetime) -> dict:
    return {
        "specversion": "1.0", "id": f"test:{destination_key}:{int(now.timestamp())}",
        "source": "/hive-os/alerts", "type": "com.hiveos.alert.destination.test",
        "subject": destination_key, "time": _iso(now), "datacontenttype": "application/json",
        "data": {"message": "HIVE OS alert destination test", "destination_key": destination_key},
    }


def test_destination(conn: sqlite3.Connection, destination_key: str, payload: dict,
                     now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    actor = _named_actor(payload.get("actor"))
    destination = destination_detail(conn, destination_key)
    envelope = _test_envelope(destination_key, now)
    body = json.dumps(envelope, sort_keys=True, separators=(",", ":"))
    delivery_key = envelope["id"]
    if not payload.get("live", False):
        headers = {"Content-Type": "application/cloudevents+json; charset=utf-8",
                   "X-HIVE-Delivery": delivery_key,
                   "X-HIVE-Signature": "configured" if destination.get("secret_env") else None}
        return {"mode": "simulation", "would_send": True, "endpoint": destination["endpoint"],
                "headers": {key: value for key, value in headers.items() if value}, "payload": envelope,
                "verified": bool(destination["verified_at"])}
    try:
        status, response_body = _post_webhook(destination, body, delivery_key)
    except (ValueError, RuntimeError) as error:
        conn.execute(
            """UPDATE alert_destinations SET last_tested_at=?,last_error=?,verified_at=NULL,
               enabled=0,version=version+1,updated_at=? WHERE id=?""",
            (_iso(now), str(error)[:1000], _iso(now), destination["id"]),
        )
        _admin_event(conn, "destination", destination_key, "live_test_failed", actor,
                     {"error": str(error)}, now)
        conn.commit()
        raise ValueError(str(error)) from error
    conn.execute(
        """UPDATE alert_destinations SET verified_at=?,last_tested_at=?,last_error=NULL,
           version=version+1,updated_at=? WHERE id=?""",
        (_iso(now), _iso(now), _iso(now), destination["id"]),
    )
    destination = destination_detail(conn, destination_key)
    for row in conn.execute(
        "SELECT id FROM alert_instances WHERE status!='resolved' ORDER BY id"
    ).fetchall():
        alert = _alert_row(conn, row["id"])
        if _destination_accepts(destination, alert["severity"]):
            event = {"event_type": "current_state", "actor": actor, "notes": "Destination commissioned"}
            _queue_one(conn, alert, destination, event,
                       f"{destination_key}:current:{alert['id']}:v{alert['version']}", now)
    _admin_event(conn, "destination", destination_key, "live_test_passed", actor,
                 {"response_code": status}, now)
    conn.commit()
    return {"mode": "live", "verified": True, "response_code": status,
            "response_body": response_body, "destination": destination_detail(conn, destination_key)}


def dispatch(conn: sqlite3.Connection, limit: int = 50, actor: str = "hive-alert-worker",
             now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    actor = _named_actor(actor)
    rows = conn.execute(
        """SELECT ad.*,d.endpoint,d.secret_env,d.destination_key,d.channel
           FROM alert_deliveries ad JOIN alert_destinations d ON d.id=ad.destination_id
           WHERE ad.status IN ('pending','failed') AND ad.attempts<5
             AND (ad.next_attempt_at IS NULL OR ad.next_attempt_at<=?)
             AND d.enabled=1 AND d.verified_at IS NOT NULL
           ORDER BY ad.id LIMIT ?""", (_iso(now), limit)
    ).fetchall()
    delivered = failed = 0
    for source in rows:
        row = dict(source)
        try:
            code, body = _post_webhook(row, row["payload_json"], row["delivery_key"])
            conn.execute(
                """UPDATE alert_deliveries SET status='delivered',attempts=attempts+1,
                   response_code=?,response_body=?,last_error=NULL,delivered_at=?,updated_at=? WHERE id=?""",
                (code, body, _iso(now), _iso(now), row["id"]),
            )
            delivered += 1
        except (ValueError, RuntimeError) as error:
            attempts = int(row["attempts"]) + 1
            delay = min(3600, (2 ** attempts) * 30)
            conn.execute(
                """UPDATE alert_deliveries SET status='failed',attempts=?,last_error=?,
                   next_attempt_at=?,updated_at=? WHERE id=?""",
                (attempts, str(error)[:1000], _iso(now + timedelta(seconds=delay)), _iso(now), row["id"]),
            )
            failed += 1
    _admin_event(conn, "runtime", "delivery", "dispatch_run", actor,
                 {"selected": len(rows), "delivered": delivered, "failed": failed}, now)
    conn.commit()
    return {"selected": len(rows), "delivered": delivered, "failed": failed,
            "remaining": conn.execute("SELECT COUNT(*) FROM alert_deliveries WHERE status IN ('pending','failed') AND attempts<5").fetchone()[0]}


def runtime_settings(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT * FROM alert_runtime_settings WHERE id=1").fetchone()
    if not row:
        raise RuntimeError("Alert runtime settings are not initialized")
    return dict(row)


def update_settings(conn: sqlite3.Connection, payload: dict,
                    now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    actor = _named_actor(payload.get("actor"))
    current = runtime_settings(conn)
    if payload.get("expected_version") is not None and int(payload["expected_version"]) != int(current["version"]):
        raise ValueError("Alert settings changed; refresh before saving")
    auto_dispatch = bool(payload.get("auto_dispatch", current["auto_dispatch"]))
    if auto_dispatch:
        ready = conn.execute(
            "SELECT COUNT(*) FROM alert_destinations WHERE enabled=1 AND verified_at IS NOT NULL"
        ).fetchone()[0]
        if not ready:
            raise ValueError("Automatic dispatch requires an enabled, live-verified destination")
    auto_sync = bool(payload.get("auto_sync", current["auto_sync"]))
    interval = int(payload.get("interval_seconds", current["interval_seconds"]))
    if interval < 15 or interval > 3600:
        raise ValueError("Alert interval must be between 15 and 3600 seconds")
    conn.execute(
        """UPDATE alert_runtime_settings SET auto_sync=?,auto_dispatch=?,interval_seconds=?,
           version=version+1,updated_by=?,updated_at=? WHERE id=1""",
        (int(auto_sync), int(auto_dispatch), interval, actor, _iso(now)),
    )
    _admin_event(conn, "runtime", "settings", "settings_updated", actor,
                 {"auto_sync": auto_sync, "auto_dispatch": auto_dispatch, "interval_seconds": interval}, now)
    conn.commit()
    return runtime_settings(conn)


def snapshot(conn: sqlite3.Connection, status: Optional[str] = None,
             now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    where = "WHERE status=?" if status else ""
    params = (status,) if status else ()
    ids = [row["id"] for row in conn.execute(
        f"SELECT id FROM alert_instances {where} ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, first_seen_at DESC LIMIT 200",
        params,
    ).fetchall()]
    alerts = [alert_detail(conn, alert_id) for alert_id in ids]
    destinations = [dict(row) for row in conn.execute(
        "SELECT * FROM alert_destinations ORDER BY enabled DESC,name"
    ).fetchall()]
    deliveries = []
    for row in conn.execute(
        """SELECT ad.*,d.destination_key,d.name destination_name,ai.alert_key,ai.title
           FROM alert_deliveries ad JOIN alert_destinations d ON d.id=ad.destination_id
           LEFT JOIN alert_instances ai ON ai.id=ad.alert_id ORDER BY ad.id DESC LIMIT 100"""
    ).fetchall():
        item = dict(row)
        item.pop("payload_json", None)
        deliveries.append(item)
    active = [item for item in alerts if item["status"] in ACTIVE_STATUSES]
    settings = runtime_settings(conn)
    return {
        "generated_at": _iso(now),
        "summary": {
            "total": len(alerts), "open": sum(item["status"] == "open" for item in alerts),
            "acknowledged": sum(item["status"] == "acknowledged" for item in alerts),
            "snoozed": sum(item["status"] == "snoozed" for item in alerts),
            "resolved": sum(item["status"] == "resolved" for item in alerts),
            "critical_unacknowledged": sum(item["status"] == "open" and item["severity"] == "critical" for item in alerts),
            "response_overdue": sum(item["status"] == "open" and now >= _parse(item["response_due_at"]) for item in alerts),
            "pending_deliveries": sum(item["status"] == "pending" for item in deliveries),
            "failed_deliveries": sum(item["status"] == "failed" for item in deliveries),
            "active": len(active),
        },
        "alerts": alerts, "destinations": destinations, "deliveries": deliveries,
        "settings": settings,
        "rule_catalog": [{"key": key, **value} for key, value in RULES.items()],
        "guardrail": "Only rationalized conditions requiring operator action become alerts. Automatic sync and external delivery remain disabled until a named operator commissions them.",
    }
