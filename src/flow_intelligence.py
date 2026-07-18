"""Evidence-gated WIP, queue-time, and shift-flow intelligence.

Execution state is sampled because WIP is a stock, not an event count. Completed
shift records are revisioned: late evidence creates a new close instead of
silently rewriting the conclusion an operator previously saw.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import production_loss


METHOD_VERSION = "flow-intelligence-v1"
SAMPLE_INTERVAL_S = 300
SAMPLE_RETENTION_DAYS = 180
PHYSICAL_SOURCES = {"machine_event", "barcode"}
READY_STATES = {"available", "dispatched", "acknowledged", "running"}


def _dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _weighted_percentile(values: list[tuple[float, int]], percentile: float) -> Optional[float]:
    weighted = sorted((float(value), max(1, int(weight))) for value, weight in values)
    total = sum(weight for _, weight in weighted)
    if not total:
        return None
    target = max(1, math.ceil(total * percentile))
    running = 0
    for value, weight in weighted:
        running += weight
        if running >= target:
            return round(value, 1)
    return round(weighted[-1][0], 1)


def _mean(values: list[float]) -> Optional[float]:
    return round(statistics.fmean(values), 3) if values else None


def _cv(values: list[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    mean = statistics.fmean(values)
    return round(statistics.stdev(values) / mean, 4) if mean > 0 else None


def _seconds(start: Optional[str], end: Optional[str]) -> Optional[float]:
    if not start or not end:
        return None
    return max(0.0, round((_dt(end) - _dt(start)).total_seconds(), 1))


def _production_machines(conn: sqlite3.Connection) -> list[dict]:
    placeholders = ",".join("?" for _ in production_loss.PRODUCTION_MACHINE_KEYS)
    return [dict(row) for row in conn.execute(
        f"""SELECT m.id,m.machine_key,m.name,
                   wb.capacity_qty buffer_capacity_qty,wb.current_qty buffer_qty,
                   wb.source buffer_source,wb.verified buffer_verified,wb.updated_at buffer_updated_at
            FROM machines m LEFT JOIN wip_buffers wb ON wb.machine_id=m.id
            WHERE m.active=1 AND m.machine_key IN ({placeholders}) ORDER BY m.id""",
        production_loss.PRODUCTION_MACHINE_KEYS,
    ).fetchall()]


def _execution_rows(conn: sqlite3.Connection) -> list[dict]:
    return [dict(row) for row in conn.execute(
        """SELECT ej.id,ej.machine_id,ej.state,ej.required_qty,ej.in_process_qty,
                  ej.completed_qty,ej.scrap_qty,ej.started_at,ej.completed_at,
                  ej.created_at,ej.updated_at,ej.held_reason,
                  prs.part_id,prs.step_index,po.released_at,po.due_at,po.status order_status,
                  m.machine_key,m.name machine_name,j.job_name,p.part_name,
                  (SELECT prev.completed_at FROM part_route_steps prev_step
                   JOIN execution_jobs prev ON prev.route_step_id=prev_step.id
                   WHERE prev_step.part_id=prs.part_id AND prev_step.required=1
                     AND prev_step.step_index<prs.step_index
                   ORDER BY prev_step.step_index DESC LIMIT 1) predecessor_completed_at,
                  (SELECT pe.source FROM part_route_steps prev_step
                   JOIN execution_jobs prev ON prev.route_step_id=prev_step.id
                   JOIN execution_job_events pe ON pe.execution_job_id=prev.id
                   WHERE prev_step.part_id=prs.part_id AND prev_step.required=1
                     AND prev_step.step_index<prs.step_index
                     AND pe.event_type IN ('completed','quantity_completed')
                   ORDER BY prev_step.step_index DESC,julianday(pe.ts) DESC,pe.id DESC LIMIT 1)
                   predecessor_completion_source,
                  (SELECT se.source FROM execution_job_events se
                   WHERE se.execution_job_id=ej.id
                     AND se.event_type IN ('started','implicit_start')
                   ORDER BY julianday(se.ts) DESC,se.id DESC LIMIT 1) start_source,
                  (SELECT ae.ts FROM execution_job_events ae
                   WHERE ae.execution_job_id=ej.id AND ae.to_state='available'
                   ORDER BY julianday(ae.ts),ae.id LIMIT 1) available_at
           FROM execution_jobs ej
           JOIN part_route_steps prs ON prs.id=ej.route_step_id
           JOIN production_orders po ON po.id=ej.production_order_id
           JOIN jobs j ON j.id=po.job_id JOIN parts p ON p.id=prs.part_id
           JOIN machines m ON m.id=ej.machine_id
           WHERE ej.state NOT IN ('completed','cancelled')
           ORDER BY ej.dispatch_sequence,ej.id"""
    ).fetchall()]


def _ready_at(row: dict) -> Optional[str]:
    if int(row["step_index"]) > 1:
        return row["predecessor_completed_at"] or row["available_at"]
    return row["released_at"] or row["available_at"]


def _blocked_reason(row: dict) -> str:
    if row["order_status"] not in ("released", "in_progress"):
        return "order_not_released"
    if int(row["step_index"]) > 1 and not row["predecessor_completed_at"]:
        return "upstream_incomplete"
    return "execution_control_gap"


def _window_activity(conn: sqlite3.Connection, start: datetime, end: datetime) -> dict[int, dict]:
    result: dict[int, dict] = defaultdict(lambda: {
        "arrivals_qty": 0, "completed_qty": 0, "scrap_qty": 0,
        "physical_completed_qty": 0,
    })
    completion_rows = conn.execute(
        """SELECT ej.machine_id,eje.good_qty,eje.scrap_qty,eje.source
           FROM execution_job_events eje JOIN execution_jobs ej ON ej.id=eje.execution_job_id
           WHERE eje.event_type IN ('completed','quantity_completed')
             AND julianday(eje.ts)>=julianday(?) AND julianday(eje.ts)<julianday(?)""",
        (_iso(start), _iso(end)),
    ).fetchall()
    for row in completion_rows:
        good = max(0, int(row["good_qty"] or 0))
        item = result[int(row["machine_id"])]
        item["completed_qty"] += good
        item["scrap_qty"] += max(0, int(row["scrap_qty"] or 0))
        if row["source"] in PHYSICAL_SOURCES:
            item["physical_completed_qty"] += good
    arrival_rows = conn.execute(
        """SELECT ej.machine_id,ej.required_qty
           FROM execution_jobs ej
           JOIN (SELECT execution_job_id,MIN(julianday(ts)) first_ready
                 FROM execution_job_events WHERE to_state='available'
                 GROUP BY execution_job_id) ready ON ready.execution_job_id=ej.id
           WHERE ready.first_ready>=julianday(?) AND ready.first_ready<julianday(?)""",
        (_iso(start), _iso(end)),
    ).fetchall()
    for row in arrival_rows:
        result[int(row["machine_id"])]["arrivals_qty"] += max(0, int(row["required_qty"]))
    return result


def current_snapshot(conn: sqlite3.Connection, now: Optional[datetime] = None) -> dict:
    now = _dt(now or datetime.now(timezone.utc))
    shift = production_loss.resolve_window(conn, now)
    start = _dt(shift["window_start"])
    activity = _window_activity(conn, start, now)
    rows = _execution_rows(conn)
    grouped: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[int(row["machine_id"])].append(row)
    open_constraints = {
        int(row["machine_id"]) for row in conn.execute(
            "SELECT machine_id FROM constraint_episodes WHERE status='open'"
        ).fetchall()
    }

    machines = []
    fingerprint_rows = []
    total_controlled_wip = total_physical = timestamped_qty = 0
    for machine in _production_machines(conn):
        released_queue = ready_wip = in_process = held_wip = blocked = physical = 0
        ready_ages: list[tuple[float, int]] = []
        blocked_reasons: dict[str, int] = defaultdict(int)
        machine_fingerprint = []
        for row in grouped.get(int(machine["id"]), []):
            remaining = max(0, int(row["required_qty"]) - int(row["completed_qty"]))
            process_qty = min(remaining, max(0, int(row["in_process_qty"])))
            waiting_qty = max(0, remaining - process_qty)
            ready_at = _ready_at(row)
            own_physical = row["start_source"] in PHYSICAL_SOURCES
            upstream_physical = row["predecessor_completion_source"] in PHYSICAL_SOURCES
            if row["state"] == "queued":
                blocked += waiting_qty
                blocked_reasons[_blocked_reason(row)] += waiting_qty
            elif row["state"] == "held":
                held_wip += remaining
                if own_physical or upstream_physical:
                    physical += remaining
            else:
                in_process += process_qty
                if own_physical:
                    physical += process_qty
                if waiting_qty:
                    if int(row["step_index"]) > 1:
                        ready_wip += waiting_qty
                        if upstream_physical:
                            physical += waiting_qty
                    else:
                        released_queue += waiting_qty
                    if ready_at:
                        ready_ages.append((max(0, (now - _dt(ready_at)).total_seconds()), waiting_qty))
                        timestamped_qty += waiting_qty
            machine_fingerprint.append([
                row["id"], row["state"], row["required_qty"], row["completed_qty"],
                row["in_process_qty"], row["updated_at"], ready_at, row["start_source"],
                row["predecessor_completion_source"],
            ])

        controlled_wip = ready_wip + in_process + held_wip
        total_controlled_wip += controlled_wip
        total_physical += physical
        buffer_qty = machine["buffer_qty"]
        buffer_diff = int(buffer_qty) - ready_wip if buffer_qty is not None else None
        buffer_reconciled = bool(
            machine["buffer_verified"] and machine["buffer_source"] == "execution"
            and buffer_diff == 0
        )
        age_p50 = _weighted_percentile(ready_ages, 0.50)
        age_p90 = _weighted_percentile(ready_ages, 0.90)
        age_max = max((value for value, _ in ready_ages), default=None)
        capacity = int(machine["buffer_capacity_qty"] or 0)
        queue_pressure = (released_queue + ready_wip) / capacity if capacity else 0.0
        elapsed = max(SAMPLE_INTERVAL_S, (now - start).total_seconds())
        age_pressure = (age_p90 or 0) / elapsed
        active_wip = max(1, controlled_wip)
        hold_pressure = held_wip / active_wip
        flow = activity[int(machine["id"])]
        imbalance = max(0, flow["arrivals_qty"] - flow["completed_qty"]) / max(1, flow["arrivals_qty"])
        pressure = min(100.0, (
            35 * min(1, queue_pressure)
            + 25 * min(1, age_pressure)
            + 15 * min(1, imbalance)
            + 15 * min(1, hold_pressure)
            + (10 if int(machine["id"]) in open_constraints else 0)
        ))
        confidence = "high" if buffer_reconciled and controlled_wip and physical / controlled_wip >= 0.9 else (
            "medium" if controlled_wip and physical / controlled_wip >= 0.5 else "low"
        )
        if not (released_queue or controlled_wip or blocked or flow["completed_qty"]):
            state = "no_released_flow"
        elif blocked and not (released_queue or controlled_wip):
            state = "upstream_blocked"
        elif pressure >= 60 and confidence == "high":
            state = "constraint_candidate"
        elif pressure > 0:
            state = "flow_pressure"
        else:
            state = "balanced"
        item = {
            "machine_id": machine["id"], "machine_key": machine["machine_key"],
            "machine_name": machine["name"], "state": state, "confidence": confidence,
            "released_queue_qty": released_queue, "ready_wip_qty": ready_wip,
            "in_process_qty": in_process, "held_wip_qty": held_wip,
            "blocked_demand_qty": blocked, "physically_observed_qty": physical,
            "ready_age_p50_s": age_p50, "ready_age_p90_s": age_p90,
            "ready_age_max_s": round(age_max, 1) if age_max is not None else None,
            "arrivals_qty": flow["arrivals_qty"], "completed_qty": flow["completed_qty"],
            "scrap_qty": flow["scrap_qty"],
            "physical_completion_ratio": round(
                flow["physical_completed_qty"] / flow["completed_qty"], 4
            ) if flow["completed_qty"] else None,
            "buffer": {
                "quantity": buffer_qty, "capacity": machine["buffer_capacity_qty"],
                "source": machine["buffer_source"], "verified": bool(machine["buffer_verified"]),
                "difference_qty": buffer_diff, "reconciled": buffer_reconciled,
            },
            "blocked_reasons": dict(sorted(blocked_reasons.items())),
            "pressure_score": round(pressure, 1),
            "corroborated_constraint_episode": int(machine["id"]) in open_constraints,
        }
        machines.append(item)
        fingerprint_rows.append([machine["id"], machine_fingerprint, item["buffer"], flow])

    active_machines = [item for item in machines if (
        item["released_queue_qty"] + item["ready_wip_qty"] + item["in_process_qty"]
        + item["held_wip_qty"] + item["blocked_demand_qty"]
    ) > 0]
    top = max(active_machines, key=lambda item: (item["pressure_score"], item["ready_wip_qty"]),
              default=None)
    eligible_buffers = [item for item in active_machines if item["buffer"]["verified"]]
    reconciled_buffers = sum(1 for item in eligible_buffers if item["buffer"]["reconciled"])
    physical_ratio = total_physical / total_controlled_wip if total_controlled_wip else None
    queue_qty = sum(item["released_queue_qty"] + item["ready_wip_qty"] for item in machines)
    timestamp_coverage = timestamped_qty / queue_qty if queue_qty else None
    decision_ready = bool(
        rows and total_controlled_wip and physical_ratio is not None and physical_ratio >= 0.9
        and eligible_buffers and reconciled_buffers == len(eligible_buffers)
        and (timestamp_coverage is None or timestamp_coverage >= 0.9)
    )
    gaps = []
    if not rows:
        gaps.append("No approved schedule has generated execution jobs.")
    if total_controlled_wip and (physical_ratio or 0) < 0.9:
        gaps.append("Less than 90% of controlled WIP is backed by barcode or machine evidence.")
    if not eligible_buffers:
        gaps.append("No active station has a verified WIP buffer for reconciliation.")
    elif reconciled_buffers < len(eligible_buffers):
        gaps.append("One or more verified WIP buffers disagree with execution state.")
    if timestamp_coverage is not None and timestamp_coverage < 0.9:
        gaps.append("Queue-ready timestamps cover less than 90% of queued quantity.")
    return {
        "generated_at": _iso(now), "method_version": METHOD_VERSION, "shift": shift,
        "status": "decision_ready" if decision_ready else ("learning" if rows else "waiting_for_schedule"),
        "summary": {
            "execution_jobs": len(rows), "active_machines": len(active_machines),
            "released_queue_qty": sum(item["released_queue_qty"] for item in machines),
            "ready_wip_qty": sum(item["ready_wip_qty"] for item in machines),
            "in_process_qty": sum(item["in_process_qty"] for item in machines),
            "held_wip_qty": sum(item["held_wip_qty"] for item in machines),
            "blocked_demand_qty": sum(item["blocked_demand_qty"] for item in machines),
            "physically_observed_qty": total_physical,
            "physical_evidence_ratio": round(physical_ratio, 4) if physical_ratio is not None else None,
            "queue_timestamp_coverage": round(timestamp_coverage, 4) if timestamp_coverage is not None else None,
            "verified_buffers": len(eligible_buffers), "reconciled_buffers": reconciled_buffers,
            "completed_qty_this_shift": sum(item["completed_qty"] for item in machines),
            "decision_ready": decision_ready,
        },
        "top_flow_pressure": ({
            "machine_key": top["machine_key"], "machine_name": top["machine_name"],
            "pressure_score": top["pressure_score"], "state": top["state"],
            "confidence": top["confidence"], "ready_wip_qty": top["ready_wip_qty"],
            "ready_age_p90_s": top["ready_age_p90_s"],
            "guardrail": "Flow pressure is corroborating evidence, not proof of the system constraint.",
        } if top else None),
        "evidence_gaps": gaps, "machines": machines,
        "evidence_sha256": _hash(fingerprint_rows),
        "guardrail": (
            "Released queue is production intent; downstream ready, running, and held quantities are WIP. "
            "Physical evidence means a barcode or machine event, not merely a schedule transition."
        ),
    }


def _bucket(now: datetime) -> datetime:
    epoch = int(now.timestamp())
    return datetime.fromtimestamp(epoch - epoch % SAMPLE_INTERVAL_S, timezone.utc)


def capture_sample(conn: sqlite3.Connection, now: Optional[datetime] = None,
                   *, commit: bool = True) -> dict:
    now = _dt(now or datetime.now(timezone.utc))
    bucket = _iso(_bucket(now))
    existing = conn.execute(
        "SELECT id,summary_json,sampled_at,evidence_sha256 FROM flow_samples WHERE sample_bucket=?",
        (bucket,),
    ).fetchone()
    if existing:
        return {"sample_id": existing["id"], "sampled_at": existing["sampled_at"],
                "evidence_sha256": existing["evidence_sha256"], "created": False,
                "summary": json.loads(existing["summary_json"])}
    snapshot = current_snapshot(conn, now)
    cursor = conn.execute(
        """INSERT OR IGNORE INTO flow_samples
           (sample_bucket,sampled_at,method_version,evidence_sha256,summary_json,created_at)
           VALUES (?,?,?,?,?,?)""",
        (bucket, _iso(now), METHOD_VERSION, snapshot["evidence_sha256"],
         json.dumps(snapshot["summary"], sort_keys=True), _iso(now)),
    )
    if cursor.rowcount == 0:
        existing = conn.execute(
            "SELECT id,summary_json,sampled_at,evidence_sha256 FROM flow_samples WHERE sample_bucket=?",
            (bucket,),
        ).fetchone()
        return {"sample_id": existing["id"], "sampled_at": existing["sampled_at"],
                "evidence_sha256": existing["evidence_sha256"], "created": False,
                "summary": json.loads(existing["summary_json"])}
    sample_id = int(cursor.lastrowid)
    for item in snapshot["machines"]:
        conn.execute(
            """INSERT INTO flow_machine_samples
               (sample_id,machine_id,released_queue_qty,ready_wip_qty,in_process_qty,
                held_wip_qty,blocked_demand_qty,physically_observed_qty,ready_age_p50_s,
                ready_age_p90_s,ready_age_max_s,buffer_qty,buffer_capacity_qty,
                buffer_verified,pressure_score,evidence_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sample_id, item["machine_id"], item["released_queue_qty"], item["ready_wip_qty"],
             item["in_process_qty"], item["held_wip_qty"], item["blocked_demand_qty"],
             item["physically_observed_qty"], item["ready_age_p50_s"], item["ready_age_p90_s"],
             item["ready_age_max_s"], item["buffer"]["quantity"], item["buffer"]["capacity"],
             int(item["buffer"]["verified"]), item["pressure_score"],
             json.dumps({"confidence": item["confidence"], "state": item["state"],
                         "buffer_reconciled": item["buffer"]["reconciled"]}, sort_keys=True)),
        )
    cutoff = _iso(now - timedelta(days=SAMPLE_RETENTION_DAYS))
    conn.execute("DELETE FROM flow_samples WHERE sampled_at<?", (cutoff,))
    if commit:
        conn.commit()
    return {"sample_id": sample_id, "sampled_at": _iso(now),
            "evidence_sha256": snapshot["evidence_sha256"], "created": True,
            "summary": snapshot["summary"]}


def _completed_flow(conn: sqlite3.Connection, start: datetime, end: datetime) -> dict:
    activity = _window_activity(conn, start, end)
    rows = [dict(row) for row in conn.execute(
        """SELECT ej.id,ej.machine_id,ej.required_qty,ej.completed_qty,ej.started_at,
                  ej.completed_at,po.released_at,prs.step_index,m.machine_key,m.name machine_name,
                  (SELECT prev.completed_at FROM part_route_steps prev_step
                   JOIN execution_jobs prev ON prev.route_step_id=prev_step.id
                   WHERE prev_step.part_id=prs.part_id AND prev_step.required=1
                     AND prev_step.step_index<prs.step_index
                   ORDER BY prev_step.step_index DESC LIMIT 1) predecessor_completed_at,
                  (SELECT ce.source FROM execution_job_events ce
                   WHERE ce.execution_job_id=ej.id AND ce.event_type='completed'
                   ORDER BY julianday(ce.ts) DESC,ce.id DESC LIMIT 1) completion_source
           FROM execution_jobs ej JOIN part_route_steps prs ON prs.id=ej.route_step_id
           JOIN production_orders po ON po.id=ej.production_order_id
           JOIN machines m ON m.id=ej.machine_id
           WHERE julianday(ej.completed_at)>=julianday(?)
             AND julianday(ej.completed_at)<julianday(?)
           ORDER BY julianday(ej.completed_at),ej.id""",
        (_iso(start), _iso(end)),
    ).fetchall()]
    queue_values: list[tuple[float, int]] = []
    process_values: list[tuple[float, int]] = []
    flow_values: list[tuple[float, int]] = []
    release_values: list[tuple[float, int]] = []
    timestamped = 0
    machine_values: dict[int, dict] = defaultdict(lambda: {
        "queue": [], "process": [], "flow": [], "release": [], "completed_operations": 0,
    })
    fingerprint = []
    for row in rows:
        qty = max(1, int(row["completed_qty"] or row["required_qty"] or 1))
        ready_at = row["predecessor_completed_at"] if int(row["step_index"]) > 1 else row["released_at"]
        queue_s = _seconds(ready_at, row["started_at"])
        process_s = _seconds(row["started_at"], row["completed_at"])
        flow_s = _seconds(ready_at, row["completed_at"])
        release_s = _seconds(row["released_at"], row["completed_at"])
        if queue_s is not None and process_s is not None and flow_s is not None:
            timestamped += qty
        for value, target, key in (
            (queue_s, queue_values, "queue"), (process_s, process_values, "process"),
            (flow_s, flow_values, "flow"), (release_s, release_values, "release"),
        ):
            if value is not None:
                target.append((value, qty))
                machine_values[int(row["machine_id"])][key].append((value, qty))
        machine_values[int(row["machine_id"])]["completed_operations"] += 1
        fingerprint.append([row["id"], row["completed_at"], row["started_at"], ready_at,
                            row["completed_qty"], row["completion_source"]])
    completed_qty = sum(item["completed_qty"] for item in activity.values())
    physical_qty = sum(item["physical_completed_qty"] for item in activity.values())
    scrap_qty = sum(item["scrap_qty"] for item in activity.values())
    return {
        "completed_qty": completed_qty, "scrap_qty": scrap_qty,
        "physical_completion_ratio": round(physical_qty / completed_qty, 4) if completed_qty else None,
        "timestamp_coverage": round(timestamped / completed_qty, 4) if completed_qty else None,
        "queue_time_p50_s": _weighted_percentile(queue_values, 0.50),
        "queue_time_p90_s": _weighted_percentile(queue_values, 0.90),
        "process_time_p50_s": _weighted_percentile(process_values, 0.50),
        "operation_flow_p50_s": _weighted_percentile(flow_values, 0.50),
        "operation_flow_p90_s": _weighted_percentile(flow_values, 0.90),
        "release_to_operation_p90_s": _weighted_percentile(release_values, 0.90),
        "activity": activity, "machine_values": machine_values,
        "fingerprint": fingerprint,
    }


def _sample_aggregate(conn: sqlite3.Connection, start: datetime, end: datetime) -> dict:
    sample_rows = [dict(row) for row in conn.execute(
        "SELECT id,sampled_at,evidence_sha256 FROM flow_samples WHERE sampled_at>=? AND sampled_at<? ORDER BY sampled_at",
        (_iso(start), _iso(end)),
    ).fetchall()]
    machine_rows = [dict(row) for row in conn.execute(
        """SELECT fms.*,m.machine_key,m.name machine_name
           FROM flow_machine_samples fms JOIN flow_samples fs ON fs.id=fms.sample_id
           JOIN machines m ON m.id=fms.machine_id
           WHERE fs.sampled_at>=? AND fs.sampled_at<? ORDER BY fs.sampled_at,fms.machine_id""",
        (_iso(start), _iso(end)),
    ).fetchall()]
    grouped: dict[int, list[dict]] = defaultdict(list)
    for row in machine_rows:
        grouped[int(row["machine_id"])].append(row)
    return {"samples": sample_rows, "machines": grouped}


def archive_shift(conn: sqlite3.Connection, local_date: str,
                  now: Optional[datetime] = None, actor: str = "hive-flow-worker") -> dict:
    now = _dt(now or datetime.now(timezone.utc))
    shift = production_loss.resolve_window(conn, now, local_date)
    scheduled_end = _dt(shift["scheduled_end"])
    if shift["active"] or scheduled_end > now:
        raise ValueError("Only a completed factory shift can be closed")
    start, end = _dt(shift["window_start"]), _dt(shift["scheduled_end"])
    sample_data = _sample_aggregate(conn, start, end)
    duration_s = sum(
        max(0, (_dt(interval["end"]) - _dt(interval["start"])).total_seconds())
        for interval in shift["intervals"]
    )
    expected_samples = max(1, math.ceil(duration_s / SAMPLE_INTERVAL_S))
    sample_count = len(sample_data["samples"])
    sample_coverage = min(1.0, sample_count / expected_samples)
    completed = _completed_flow(conn, start, end)
    loss = production_loss.build(conn, now=now, local_date=local_date)
    hours = max(1 / 3600, duration_s / 3600)

    machine_results = []
    for machine in _production_machines(conn):
        samples = sample_data["machines"].get(int(machine["id"]), [])
        activity = completed["activity"].get(int(machine["id"]), {
            "arrivals_qty": 0, "completed_qty": 0, "scrap_qty": 0,
            "physical_completed_qty": 0,
        })
        values = completed["machine_values"].get(int(machine["id"]), {
            "queue": [], "process": [], "flow": [], "release": [], "completed_operations": 0,
        })
        avg_ready = _mean([float(row["ready_wip_qty"]) for row in samples])
        avg_process = _mean([float(row["in_process_qty"]) for row in samples])
        avg_held = _mean([float(row["held_wip_qty"]) for row in samples])
        avg_wip = round((avg_ready or 0) + (avg_process or 0) + (avg_held or 0), 3)
        machine_results.append({
            "machine_id": machine["id"], "machine_key": machine["machine_key"],
            "machine_name": machine["name"], "sample_count": len(samples),
            "average_ready_wip": avg_ready, "average_in_process_wip": avg_process,
            "average_held_wip": avg_held, "average_wip": avg_wip,
            "peak_wip": max((int(row["ready_wip_qty"]) + int(row["in_process_qty"])
                             + int(row["held_wip_qty"]) for row in samples), default=None),
            "arrivals_qty": activity["arrivals_qty"], "completed_qty": activity["completed_qty"],
            "scrap_qty": activity["scrap_qty"],
            "throughput_per_hour": round(activity["completed_qty"] / hours, 3),
            "physical_completion_ratio": round(
                activity["physical_completed_qty"] / activity["completed_qty"], 4
            ) if activity["completed_qty"] else None,
            "queue_time_p50_s": _weighted_percentile(values["queue"], 0.50),
            "queue_time_p90_s": _weighted_percentile(values["queue"], 0.90),
            "operation_flow_p90_s": _weighted_percentile(values["flow"], 0.90),
            "average_pressure_score": _mean([float(row["pressure_score"]) for row in samples]),
            "verified_buffer_sample_ratio": round(
                sum(1 for row in samples if row["buffer_verified"]) / len(samples), 4
            ) if samples else None,
        })
    top_pressure = max(
        (item for item in machine_results if item["average_pressure_score"] is not None),
        key=lambda item: (item["average_pressure_score"], item["average_wip"]), default=None,
    )
    average_wip = round(sum(item["average_wip"] for item in machine_results), 3)
    physical_ratio = completed["physical_completion_ratio"]
    timestamp_coverage = completed["timestamp_coverage"]
    decision_ready = bool(
        shift["verified"] and sample_coverage >= 0.9 and completed["completed_qty"] > 0
        and physical_ratio is not None and physical_ratio >= 0.9
        and timestamp_coverage is not None and timestamp_coverage >= 0.9
    )
    summary = {
        "average_wip": average_wip,
        "peak_wip": sum(item["peak_wip"] or 0 for item in machine_results),
        "completed_qty": completed["completed_qty"], "scrap_qty": completed["scrap_qty"],
        "throughput_per_hour": round(completed["completed_qty"] / hours, 3),
        "queue_time_p50_s": completed["queue_time_p50_s"],
        "queue_time_p90_s": completed["queue_time_p90_s"],
        "operation_flow_p50_s": completed["operation_flow_p50_s"],
        "operation_flow_p90_s": completed["operation_flow_p90_s"],
        "release_to_operation_p90_s": completed["release_to_operation_p90_s"],
        "sample_count": sample_count, "expected_samples": expected_samples,
        "sample_coverage": round(sample_coverage, 4),
        "physical_completion_ratio": physical_ratio,
        "timestamp_coverage": timestamp_coverage, "decision_ready": decision_ready,
    }
    fingerprint = {
        "shift": {key: shift[key] for key in (
            "shift_key", "window_start", "scheduled_end", "verified", "source"
        )},
        "samples": [[row["id"], row["sampled_at"], row["evidence_sha256"]]
                    for row in sample_data["samples"]],
        "completed": completed["fingerprint"],
        "production_loss": [[item["machine_key"], item["evidence_sha256"]]
                            for item in loss["machines"]],
    }
    evidence_sha = _hash(fingerprint)
    result = {
        "method_version": METHOD_VERSION, "shift": shift, "summary": summary,
        "top_flow_pressure": ({
            "machine_key": top_pressure["machine_key"],
            "machine_name": top_pressure["machine_name"],
            "average_pressure_score": top_pressure["average_pressure_score"],
            "average_wip": top_pressure["average_wip"],
            "guardrail": "Highest sampled flow pressure is not automatically the system constraint.",
        } if top_pressure else None),
        "production_loss": {
            "classified_coverage": loss["summary"]["classified_coverage"],
            "decision_ready_oee": loss["summary"]["decision_ready_oee"],
            "decision_ready_machines": loss["summary"]["decision_ready_machines"],
            "top_loss": loss["recommendation"],
        },
        "machines": machine_results, "evidence_sha256": evidence_sha,
        "guardrail": (
            "A shift is flow-decision-ready only with a verified calendar, at least 90% five-minute "
            "sample coverage, at least 90% physical completion evidence, and complete flow timestamps."
        ),
    }

    existing = conn.execute(
        "SELECT id,revision,result_json FROM flow_shift_snapshots WHERE shift_key=? AND evidence_sha256=?",
        (shift["shift_key"], evidence_sha),
    ).fetchone()
    if existing:
        stored = json.loads(existing["result_json"])
        stored.update({"snapshot_id": existing["id"], "revision": existing["revision"],
                       "created": False})
        return stored
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT id,revision,result_json FROM flow_shift_snapshots WHERE shift_key=? AND evidence_sha256=?",
            (shift["shift_key"], evidence_sha),
        ).fetchone()
        if existing:
            conn.commit()
            stored = json.loads(existing["result_json"])
            stored.update({"snapshot_id": existing["id"], "revision": existing["revision"],
                           "created": False})
            return stored
        previous = conn.execute(
            "SELECT id,revision FROM flow_shift_snapshots WHERE shift_key=? AND is_current=1",
            (shift["shift_key"],),
        ).fetchone()
        revision = int(previous["revision"]) + 1 if previous else 1
        if previous:
            conn.execute("UPDATE flow_shift_snapshots SET is_current=0 WHERE id=?", (previous["id"],))
        cursor = conn.execute(
            """INSERT INTO flow_shift_snapshots
               (shift_key,revision,is_current,local_date,window_start,window_end,timezone,
                calendar_verified,method_version,evidence_sha256,result_json,sample_count,
                sample_coverage,decision_ready,supersedes_id,finalized_at,actor)
               VALUES (?,?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (shift["shift_key"], revision, shift["local_date"], shift["window_start"],
             shift["scheduled_end"], shift["timezone"], int(shift["verified"]), METHOD_VERSION,
             evidence_sha, json.dumps(result, sort_keys=True), sample_count,
             round(sample_coverage, 4), int(decision_ready),
             previous["id"] if previous else None, _iso(now), actor),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    result.update({"snapshot_id": cursor.lastrowid, "revision": revision, "created": True})
    return result


def _has_shift_evidence(conn: sqlite3.Connection, start: datetime, end: datetime) -> bool:
    for table, column in (
        ("machine_events", "ts"), ("execution_job_events", "ts"), ("flow_samples", "sampled_at")
    ):
        if conn.execute(
            f"""SELECT 1 FROM {table}
                WHERE julianday({column})>=julianday(?) AND julianday({column})<julianday(?)
                LIMIT 1""",
            (_iso(start), _iso(end)),
        ).fetchone():
            return True
    return False


def archive_due_shifts(conn: sqlite3.Connection, now: Optional[datetime] = None,
                       actor: str = "hive-flow-worker", max_shifts: int = 2) -> list[dict]:
    now = _dt(now or datetime.now(timezone.utc))
    rows = production_loss._calendar_rows(conn)
    timezone_name = rows[0]["timezone"] if rows else "UTC"
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        zone = timezone.utc
    local_today = now.astimezone(zone).date()
    archived = []
    for offset in range(15):
        local_day = local_today - timedelta(days=offset)
        try:
            shift = production_loss.resolve_window(conn, now, local_day.isoformat())
        except ValueError:
            continue
        start, end = _dt(shift["window_start"]), _dt(shift["scheduled_end"])
        if shift["active"] or end > now or not _has_shift_evidence(conn, start, end):
            continue
        result = archive_shift(conn, local_day.isoformat(), now, actor)
        if result["created"]:
            archived.append({"snapshot_id": result["snapshot_id"], "shift_key": shift["shift_key"],
                             "revision": result["revision"]})
        if len(archived) >= max_shifts:
            break
    return archived


def _history(conn: sqlite3.Connection, days: int, now: datetime) -> list[dict]:
    cutoff = (now.date() - timedelta(days=days)).isoformat()
    rows = conn.execute(
        """SELECT id,revision,local_date,finalized_at,actor,result_json
           FROM flow_shift_snapshots WHERE is_current=1 AND local_date>=?
           ORDER BY local_date DESC,id DESC""", (cutoff,),
    ).fetchall()
    result = []
    for row in rows:
        payload = json.loads(row["result_json"])
        result.append({
            "snapshot_id": row["id"], "revision": row["revision"],
            "local_date": row["local_date"], "finalized_at": row["finalized_at"],
            "actor": row["actor"], "shift": payload["shift"], "summary": payload["summary"],
            "top_flow_pressure": payload.get("top_flow_pressure"),
            "production_loss": payload.get("production_loss"), "machines": payload["machines"],
        })
    return result


def _historical_intelligence(shifts: list[dict]) -> dict:
    ready = [item for item in shifts if item["summary"]["decision_ready"]]
    top_counts: dict[str, dict] = {}
    for shift in ready:
        top = shift.get("top_flow_pressure")
        if not top:
            continue
        item = top_counts.setdefault(top["machine_key"], {
            "machine_key": top["machine_key"], "machine_name": top["machine_name"],
            "shift_count": 0, "scores": [],
        })
        item["shift_count"] += 1
        item["scores"].append(float(top["average_pressure_score"] or 0))
    recurring = [{
        "machine_key": item["machine_key"], "machine_name": item["machine_name"],
        "shift_count": item["shift_count"],
        "share_of_ready_shifts": round(item["shift_count"] / len(ready), 4) if ready else 0,
        "average_pressure_score": _mean(item["scores"]),
        "recurring": item["shift_count"] >= 3 and item["shift_count"] / len(ready) >= 0.3,
    } for item in top_counts.values()]
    recurring.sort(key=lambda item: (-item["shift_count"], -item["average_pressure_score"]))

    metrics = {
        "average_wip": [float(item["summary"]["average_wip"]) for item in ready],
        "queue_time_p90_s": [float(item["summary"]["queue_time_p90_s"])
                             for item in ready if item["summary"]["queue_time_p90_s"] is not None],
        "operation_flow_p90_s": [float(item["summary"]["operation_flow_p90_s"])
                                 for item in ready if item["summary"]["operation_flow_p90_s"] is not None],
        "throughput_per_hour": [float(item["summary"]["throughput_per_hour"]) for item in ready
                                if item["summary"]["throughput_per_hour"] > 0],
    }
    baselines = {}
    for key, values in metrics.items():
        enough = len(values) >= 20
        mean = statistics.fmean(values) if values else None
        deviation = statistics.stdev(values) if len(values) >= 2 else None
        baselines[key] = {
            "sample_count": len(values), "mean": round(mean, 3) if mean is not None else None,
            "cv": _cv(values),
            "exploratory_upper_3s": round(mean + 3 * deviation, 3)
            if enough and deviation is not None else None,
            "status": "exploratory" if enough else "insufficient_history",
            "control_limit_ready": False,
        }

    laws = []
    by_machine: dict[str, dict] = defaultdict(lambda: {"name": "", "wip": [], "throughput": []})
    for shift in ready:
        for machine in shift["machines"]:
            if machine["throughput_per_hour"] <= 0:
                continue
            group = by_machine[machine["machine_key"]]
            group["name"] = machine["machine_name"]
            group["wip"].append(float(machine["average_wip"]))
            group["throughput"].append(float(machine["throughput_per_hour"]))
    for key, values in by_machine.items():
        wip_cv, throughput_cv = _cv(values["wip"]), _cv(values["throughput"])
        stable_basis = bool(
            len(values["wip"]) >= 30 and wip_cv is not None and throughput_cv is not None
            and wip_cv <= 0.25 and throughput_cv <= 0.25
        )
        mean_wip = statistics.fmean(values["wip"])
        mean_throughput = statistics.fmean(values["throughput"])
        laws.append({
            "machine_key": key, "machine_name": values["name"],
            "shift_count": len(values["wip"]), "average_wip": round(mean_wip, 3),
            "average_throughput_per_hour": round(mean_throughput, 3),
            "wip_cv": wip_cv, "throughput_cv": throughput_cv,
            "estimated_flow_time_h": round(mean_wip / mean_throughput, 3) if stable_basis else None,
            "status": "decision_support" if stable_basis else "learning",
        })
    laws.sort(key=lambda item: (item["estimated_flow_time_h"] is None,
                                -(item["estimated_flow_time_h"] or 0), item["machine_name"]))
    return {
        "archived_shifts": len(shifts), "decision_ready_shifts": len(ready),
        "recurring_constraints": recurring, "baselines": baselines,
        "little_law": laws,
        "stability_guardrail": (
            "Three-sigma values are exploratory after 20 decision-ready shifts and never labeled control "
            "limits automatically. Little's Law is shown only after 30 low-variation shifts; stationarity "
            "still requires engineering review."
        ),
    }


def build(conn: sqlite3.Connection, days: int = 30,
          now: Optional[datetime] = None) -> dict:
    if not 1 <= int(days) <= 365:
        raise ValueError("days must be between 1 and 365")
    now = _dt(now or datetime.now(timezone.utc))
    current = current_snapshot(conn, now)
    shifts = _history(conn, int(days), now)
    latest_sample = conn.execute(
        "SELECT id,sampled_at FROM flow_samples ORDER BY sampled_at DESC,id DESC LIMIT 1"
    ).fetchone()
    sample_age_s = max(0, (now - _dt(latest_sample["sampled_at"])).total_seconds()) \
        if latest_sample else None
    return {
        "generated_at": _iso(now), "method_version": METHOD_VERSION,
        "current": current, "history": _historical_intelligence(shifts),
        "shifts": shifts,
        "sampling": {
            "interval_seconds": SAMPLE_INTERVAL_S,
            "latest_sample_id": latest_sample["id"] if latest_sample else None,
            "latest_sample_at": latest_sample["sampled_at"] if latest_sample else None,
            "sample_age_s": round(sample_age_s, 1) if sample_age_s is not None else None,
            "status": "healthy" if sample_age_s is not None and sample_age_s <= SAMPLE_INTERVAL_S * 2
            else ("stale" if latest_sample else "starting"),
        },
    }


def automatic_refresh(conn: sqlite3.Connection, now: Optional[datetime] = None) -> dict:
    now = _dt(now or datetime.now(timezone.utc))
    sample = capture_sample(conn, now)
    archived = archive_due_shifts(conn, now) if sample["created"] else []
    return {"sample": sample, "archived": archived, "generated_at": _iso(now)}
