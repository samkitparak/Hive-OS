"""Preventive maintenance, condition triggers, checklists, and spare control."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone


PLAN_STRATEGIES = ("calendar", "usage", "hybrid", "condition")
CRITICALITIES = ("low", "medium", "high", "critical")
WORK_ORDER_TRANSITIONS = {
    "open": {"in_progress", "cancelled"},
    "in_progress": {"open", "cancelled"},
    "done": set(),
    "cancelled": set(),
}
ACTIVE_STATES = {"power_on", "state_on", "state_idle", "idle", "cycle_start", "cycle_end"}
OFF_STATES = {"power_off", "state_off"}
MAX_USAGE_GAP_S = 16 * 60 * 60


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _machine(conn: sqlite3.Connection, machine_key: str) -> dict:
    row = conn.execute(
        "SELECT id, machine_key, name, type FROM machines WHERE machine_key=?",
        (machine_key,),
    ).fetchone()
    if not row:
        raise KeyError(f"Machine '{machine_key}' not found")
    return dict(row)


def _plan(conn: sqlite3.Connection, plan_id: int) -> dict:
    row = conn.execute(
        """SELECT mp.*, m.machine_key, m.name machine_name, m.type machine_type
           FROM maintenance_plans mp JOIN machines m ON m.id=mp.machine_id
           WHERE mp.id=?""", (plan_id,),
    ).fetchone()
    if not row:
        raise KeyError(f"Maintenance plan {plan_id} not found")
    return dict(row)


def _task_templates(machine_type: str | None) -> list[dict]:
    base = [
        {
            "task_key": "guards_interlocks", "title": "Inspect guards and safety interlocks",
            "instructions": "Follow the OEM and site test procedure; never bypass an interlock.",
            "response_type": "pass_fail",
        },
        {
            "task_key": "leaks_damage", "title": "Inspect for leaks, loose parts, heat, and damage",
            "instructions": "Record any abnormal electrical, pneumatic, hydraulic, or mechanical condition.",
            "response_type": "pass_fail",
        },
        {
            "task_key": "clean_extraction", "title": "Clean safely and inspect dust extraction",
            "instructions": "Use the approved isolation and cleaning method; do not use unsafe compressed-air cleaning.",
            "response_type": "check",
        },
    ]
    kind = (machine_type or "").lower()
    if "saw" in kind:
        extra = [
            ("cutting_tool", "Inspect blade condition, mounting, and guards"),
            ("alignment", "Check fence, stops, and dimensional calibration"),
        ]
    elif any(value in kind for value in ("cnc", "router", "driller")):
        extra = [
            ("spindle_tooling", "Inspect spindle, collets, holders, and tool retention"),
            ("vacuum_lubrication", "Check vacuum, pneumatics, and lubrication delivery"),
        ]
    elif "edge" in kind:
        extra = [
            ("glue_system", "Inspect glue system temperature, contamination, and feed"),
            ("feed_trim", "Inspect feed track, pressure rollers, and trimming tools"),
        ]
    elif "sander" in kind:
        extra = [
            ("abrasive_tracking", "Inspect abrasive belt, tracking, and contact elements"),
            ("conveyor_extraction", "Inspect conveyor tracking and extraction airflow"),
        ]
    elif any(value in kind for value in ("press", "glue")):
        extra = [
            ("pressure_system", "Inspect pressure system, hoses, seals, and platen condition"),
            ("process_controls", "Check pressure, temperature, and timing controls"),
        ]
    elif "paint" in kind:
        extra = [
            ("filters_nozzles", "Inspect filters, nozzles, pumps, and fluid paths"),
            ("booth_extraction", "Inspect booth airflow, extraction, and fire-safety condition"),
        ]
    elif "compressor" in kind:
        extra = [
            ("oil_condensate", "Check oil level, condensate drains, and leaks"),
            ("filters_temperature", "Inspect filters, cooling path, and operating temperature"),
        ]
    elif "dust collector" in kind:
        extra = [
            ("filters_pressure", "Inspect filters and differential-pressure indication"),
            ("ducts_fire", "Inspect ducts, bins, spark/fire protection, and grounding"),
        ]
    else:
        extra = [
            ("drive_wear", "Inspect drives, bearings, belts, chains, and wear items"),
            ("functional_test", "Run the approved post-maintenance functional test"),
        ]
    return base + [
        {"task_key": key, "title": title, "response_type": "pass_fail"}
        for key, title in extra
    ]


def usage_counters(conn: sqlite3.Connection, machine_id: int | None = None) -> list[dict]:
    params: tuple = ()
    where = ""
    if machine_id is not None:
        where = "WHERE m.id=?"
        params = (machine_id,)
    machines = conn.execute(
        f"SELECT m.id, m.machine_key, m.name FROM machines m {where} ORDER BY m.id", params
    ).fetchall()
    result = []
    for machine in machines:
        events = conn.execute(
            """SELECT event_type, ts FROM machine_events WHERE machine_id=?
               ORDER BY ts, id""", (machine["id"],),
        ).fetchall()
        powered_s = 0.0
        capped_intervals = 0
        active = False
        previous = None
        for event in events:
            current = _parse_ts(event["ts"])
            if active and previous is not None:
                gap = max(0.0, (current - previous).total_seconds())
                if gap > MAX_USAGE_GAP_S:
                    capped_intervals += 1
                powered_s += min(gap, MAX_USAGE_GAP_S)
            if event["event_type"] in OFF_STATES:
                active = False
            elif event["event_type"] in ACTIVE_STATES:
                active = True
            previous = current
        cycle_count = sum(1 for event in events if event["event_type"] == "cycle_end")
        cycle_row = conn.execute(
            """SELECT COALESCE(SUM(duration_s),0) runtime_s, COUNT(*) count
               FROM cycle_observations WHERE machine_id=? AND validity='valid'""",
            (machine["id"],),
        ).fetchone()
        result.append({
            "machine_id": machine["id"], "machine_key": machine["machine_key"],
            "machine_name": machine["name"], "powered_runtime_h": round(powered_s / 3600, 4),
            "cycle_runtime_h": round(float(cycle_row["runtime_s"] or 0) / 3600, 4),
            "cycles": cycle_count, "event_count": len(events),
            "valid_cycle_observations": int(cycle_row["count"] or 0),
            "usage_evidence": ("missing" if not events else
                               "estimated" if capped_intervals else "measured"),
            "capped_intervals": capped_intervals,
        })
    return result


def sync_defaults(conn: sqlite3.Connection, commit: bool = True) -> dict:
    now = _now()
    usage = {item["machine_id"]: item for item in usage_counters(conn)}
    created = tasks_created = 0
    for machine in conn.execute(
        "SELECT id, machine_key, name, type FROM machines WHERE active=1 ORDER BY id"
    ).fetchall():
        plan_key = f"{machine['machine_key']}_baseline_inspection"
        current = usage[machine["id"]]
        cursor = conn.execute(
            """INSERT OR IGNORE INTO maintenance_plans
               (plan_key, machine_id, title, description, strategy, interval_days,
                warning_days, estimated_duration_min, criticality, requires_shutdown,
                loto_required, source, verified, anchor_at, last_completed_runtime_h,
                last_completed_cycles, created_at, updated_at)
               VALUES (?,?,?,?,'calendar',30,7,45,'high',1,1,
                       'engineering_assumption',0,?,?,?,?,?)""",
            (plan_key, machine["id"], "Baseline safety and condition inspection",
             "Unverified 30-day commissioning assumption; replace with the OEM schedule.",
             now, current["powered_runtime_h"], current["cycles"], now, now),
        )
        if cursor.rowcount:
            created += 1
        plan_id = conn.execute(
            "SELECT id FROM maintenance_plans WHERE plan_key=?", (plan_key,)
        ).fetchone()["id"]
        for sequence, task in enumerate(_task_templates(machine["type"]), start=1):
            cursor = conn.execute(
                """INSERT OR IGNORE INTO maintenance_plan_tasks
                   (maintenance_plan_id, task_key, sequence, title, instructions,
                    response_type, required, active, updated_at)
                   VALUES (?,?,?,?,?,?,1,1,?)""",
                (plan_id, task["task_key"], sequence, task["title"],
                 task.get("instructions"), task["response_type"], now),
            )
            tasks_created += cursor.rowcount
    if commit:
        conn.commit()
    return {"plans_created": created, "tasks_created": tasks_created}


def _condition_matches(value: float, threshold: float, operator: str) -> bool:
    return {
        "gt": value > threshold, "gte": value >= threshold,
        "lt": value < threshold, "lte": value <= threshold,
    }[operator]


def _evaluate(plan: dict, usage: dict, now: datetime,
              open_condition_count: int = 0) -> dict:
    dimensions = []
    anchor = _parse_ts(plan["last_completed_at"] or plan["anchor_at"])
    if plan["interval_days"] is not None:
        elapsed = max(0.0, (now - anchor).total_seconds() / 86400)
        remaining = float(plan["interval_days"]) - elapsed
        dimensions.append({
            "kind": "calendar", "remaining": round(remaining, 3), "unit": "days",
            "warning": float(plan["warning_days"]), "evidence": "clock",
        })
    runtime_value = usage[f"{plan['runtime_basis']}_runtime_h"]
    if plan["interval_runtime_h"] is not None:
        consumed = max(0.0, runtime_value - float(plan["last_completed_runtime_h"] or 0))
        remaining = float(plan["interval_runtime_h"]) - consumed
        dimensions.append({
            "kind": "runtime", "remaining": round(remaining, 3), "unit": "hours",
            "warning": float(plan["warning_runtime_h"]),
            "evidence": usage["usage_evidence"], "basis": plan["runtime_basis"],
        })
    if plan["interval_cycles"] is not None:
        consumed = max(0, int(usage["cycles"]) - int(plan["last_completed_cycles"] or 0))
        remaining = int(plan["interval_cycles"]) - consumed
        dimensions.append({
            "kind": "cycles", "remaining": remaining, "unit": "cycles",
            "warning": int(plan["warning_cycles"]),
            "evidence": "missing" if not usage["event_count"] else "measured",
        })
    if plan["condition_metric"]:
        dimensions.append({
            "kind": "condition", "remaining": 0 if open_condition_count else None,
            "unit": plan["condition_metric"], "warning": 0,
            "evidence": "triggered" if open_condition_count else "no_active_signal",
        })
    overdue = [item for item in dimensions if item["remaining"] is not None and item["remaining"] <= 0]
    warning = [item for item in dimensions if item["remaining"] is not None
               and 0 < item["remaining"] <= item["warning"]]
    evidence_dimensions = [item for item in dimensions if item["kind"] in ("runtime", "cycles")]
    evidence_missing = bool(evidence_dimensions) and all(
        item["evidence"] == "missing" for item in evidence_dimensions
    ) and not any(item["kind"] == "calendar" for item in dimensions)
    if not plan["active"]:
        status = "inactive"
    elif not plan["verified"]:
        status = "unverified"
    elif overdue:
        status = "overdue"
    elif warning:
        status = "due_soon"
    elif evidence_missing:
        status = "awaiting_evidence"
    else:
        status = "healthy"
    next_due_at = None
    if plan["interval_days"] is not None:
        next_due_at = (anchor + timedelta(days=float(plan["interval_days"]))).isoformat()
    trigger = "condition" if open_condition_count else (
        overdue[0]["kind"] if overdue else "warning" if warning else None
    )
    return {
        **plan, "status": status, "dimensions": dimensions,
        "next_due_at": next_due_at, "trigger_type": trigger,
        "open_condition_signals": open_condition_count,
        "usage": usage,
    }


def list_plans(conn: sqlite3.Connection, active_only: bool = False) -> list[dict]:
    usage = {item["machine_id"]: item for item in usage_counters(conn)}
    clause = "WHERE mp.active=1" if active_only else ""
    rows = conn.execute(
        f"""SELECT mp.*, m.machine_key, m.name machine_name, m.type machine_type
            FROM maintenance_plans mp JOIN machines m ON m.id=mp.machine_id
            {clause} ORDER BY m.name, mp.title"""
    ).fetchall()
    now = datetime.now(timezone.utc)
    result = []
    for row in rows:
        item = dict(row)
        condition_count = conn.execute(
            """SELECT COUNT(*) count FROM maintenance_condition_signals
               WHERE maintenance_plan_id=? AND status IN ('open','acknowledged') AND triggered=1""",
            (item["id"],),
        ).fetchone()["count"]
        evaluated = _evaluate(item, usage[item["machine_id"]], now, condition_count)
        evaluated["tasks"] = [dict(task) for task in conn.execute(
            """SELECT id, task_key, sequence, title, instructions, response_type,
                      unit, required, active FROM maintenance_plan_tasks
               WHERE maintenance_plan_id=? AND active=1 ORDER BY sequence, id""", (item["id"],),
        ).fetchall()]
        evaluated["spares"] = [dict(spare) for spare in conn.execute(
            """SELECT mps.id, sp.part_key, sp.name, sp.unit, mps.quantity, mps.required
               FROM maintenance_plan_spares mps JOIN spare_parts sp ON sp.id=mps.spare_part_id
               WHERE mps.maintenance_plan_id=? AND mps.active=1 ORDER BY sp.name""",
            (item["id"],),
        ).fetchall()]
        result.append(evaluated)
    order = {"overdue": 0, "due_soon": 1, "awaiting_evidence": 2,
             "unverified": 3, "healthy": 4, "inactive": 5}
    return sorted(result, key=lambda item: (order[item["status"]], item["machine_name"], item["title"]))


def _validate_plan(payload: dict) -> None:
    strategy = payload["strategy"]
    if strategy not in PLAN_STRATEGIES:
        raise ValueError(f"Unknown maintenance strategy '{strategy}'")
    if payload["criticality"] not in CRITICALITIES:
        raise ValueError(f"Unknown maintenance criticality '{payload['criticality']}'")
    has_calendar = payload.get("interval_days") is not None
    has_usage = payload.get("interval_runtime_h") is not None or payload.get("interval_cycles") is not None
    has_condition = bool(payload.get("condition_metric"))
    if strategy == "calendar" and not has_calendar:
        raise ValueError("Calendar maintenance requires interval_days")
    if strategy == "usage" and not has_usage:
        raise ValueError("Usage maintenance requires runtime hours or cycles")
    if strategy == "hybrid" and sum((has_calendar, has_usage, has_condition)) < 2:
        raise ValueError("Hybrid maintenance requires at least two trigger families")
    if strategy == "condition" and not has_condition:
        raise ValueError("Condition maintenance requires a condition metric")
    if has_condition and (payload.get("condition_operator") not in ("gt", "gte", "lt", "lte")
                          or payload.get("condition_threshold") is None):
        raise ValueError("Condition maintenance requires an operator and threshold")


def _replace_tasks(conn: sqlite3.Connection, plan_id: int, tasks: list[dict], now: str) -> None:
    keys = [task["task_key"] for task in tasks]
    if len(keys) != len(set(keys)):
        raise ValueError("Maintenance task keys must be unique within a plan")
    conn.execute(
        "UPDATE maintenance_plan_tasks SET active=0, updated_at=? WHERE maintenance_plan_id=?",
        (now, plan_id),
    )
    for sequence, task in enumerate(tasks, start=1):
        conn.execute(
            """INSERT INTO maintenance_plan_tasks
               (maintenance_plan_id, task_key, sequence, title, instructions,
                response_type, unit, required, active, updated_at)
               VALUES (?,?,?,?,?,?,?,?,1,?)
               ON CONFLICT(maintenance_plan_id,task_key) DO UPDATE SET
                 sequence=excluded.sequence, title=excluded.title,
                 instructions=excluded.instructions, response_type=excluded.response_type,
                 unit=excluded.unit, required=excluded.required, active=1,
                 updated_at=excluded.updated_at""",
            (plan_id, task["task_key"], sequence, task["title"], task.get("instructions"),
             task.get("response_type", "check"), task.get("unit"),
             int(bool(task.get("required", True))), now),
        )


def _replace_plan_spares(conn: sqlite3.Connection, plan_id: int,
                         spares: list[dict], now: str) -> None:
    conn.execute(
        "UPDATE maintenance_plan_spares SET active=0, updated_at=? WHERE maintenance_plan_id=?",
        (now, plan_id),
    )
    for item in spares:
        spare = conn.execute(
            "SELECT id FROM spare_parts WHERE part_key=? AND active=1", (item["part_key"],)
        ).fetchone()
        if not spare:
            raise KeyError(f"Spare part '{item['part_key']}' not found")
        conn.execute(
            """INSERT INTO maintenance_plan_spares
               (maintenance_plan_id, spare_part_id, quantity, required, active, updated_at)
               VALUES (?,?,?,?,1,?)
               ON CONFLICT(maintenance_plan_id,spare_part_id) DO UPDATE SET
                 quantity=excluded.quantity, required=excluded.required, active=1,
                 updated_at=excluded.updated_at""",
            (plan_id, spare["id"], float(item["quantity"]),
             int(bool(item.get("required", True))), now),
        )


def create_plan(conn: sqlite3.Connection, payload: dict) -> dict:
    machine = _machine(conn, payload["machine_key"])
    key = payload["plan_key"].strip().lower()
    if not re.fullmatch(r"[a-z0-9_]+", key):
        raise ValueError("plan_key may contain lowercase letters, numbers, and underscores")
    data = {
        "strategy": payload.get("strategy", "calendar"),
        "runtime_basis": payload.get("runtime_basis", "powered"),
        "interval_days": payload.get("interval_days"),
        "interval_runtime_h": payload.get("interval_runtime_h"),
        "interval_cycles": payload.get("interval_cycles"),
        "warning_days": payload.get("warning_days", 7),
        "warning_runtime_h": payload.get("warning_runtime_h", 25),
        "warning_cycles": payload.get("warning_cycles", 100),
        "estimated_duration_min": payload.get("estimated_duration_min", 60),
        "criticality": payload.get("criticality", "medium"),
        "requires_shutdown": int(bool(payload.get("requires_shutdown", True))),
        "loto_required": int(bool(payload.get("loto_required", True))),
        "condition_metric": payload.get("condition_metric"),
        "condition_operator": payload.get("condition_operator"),
        "condition_threshold": payload.get("condition_threshold"),
    }
    _validate_plan(data)
    current = usage_counters(conn, machine["id"])[0]
    now = payload.get("anchor_at") or _now()
    try:
        _parse_ts(now)
    except ValueError as error:
        raise ValueError("anchor_at must be an ISO timestamp") from error
    try:
        cursor = conn.execute(
            """INSERT INTO maintenance_plans
               (plan_key,machine_id,title,description,strategy,runtime_basis,
                interval_days,interval_runtime_h,interval_cycles,warning_days,
                warning_runtime_h,warning_cycles,estimated_duration_min,criticality,
                requires_shutdown,loto_required,condition_metric,condition_operator,
                condition_threshold,active,source,verified,anchor_at,
                last_completed_runtime_h,last_completed_cycles,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (key, machine["id"], payload["title"], payload.get("description"),
             data["strategy"], data["runtime_basis"], data["interval_days"],
             data["interval_runtime_h"], data["interval_cycles"], data["warning_days"],
             data["warning_runtime_h"], data["warning_cycles"], data["estimated_duration_min"],
             data["criticality"], data["requires_shutdown"], data["loto_required"],
             data["condition_metric"], data["condition_operator"], data["condition_threshold"],
             int(bool(payload.get("active", True))), payload.get("source", "manual"),
             int(bool(payload.get("verified", False))), now,
             current["powered_runtime_h"] if data["runtime_basis"] == "powered"
             else current["cycle_runtime_h"], current["cycles"], _now(), _now()),
        )
        _replace_tasks(conn, cursor.lastrowid, payload.get("tasks", []), _now())
        _replace_plan_spares(conn, cursor.lastrowid, payload.get("spares", []), _now())
        conn.commit()
    except sqlite3.IntegrityError as error:
        conn.rollback()
        raise ValueError(f"Maintenance plan '{key}' already exists") from error
    except Exception:
        conn.rollback()
        raise
    if payload.get("verified"):
        sync(conn)
    return next(item for item in list_plans(conn) if item["id"] == cursor.lastrowid)


def update_plan(conn: sqlite3.Connection, plan_id: int, payload: dict) -> dict:
    current = _plan(conn, plan_id)
    expected = payload.get("expected_version")
    if expected is not None and expected != current["version"]:
        raise ValueError(
            f"Maintenance plan changed from version {expected} to {current['version']}"
        )
    fields = (
        "title", "description", "strategy", "runtime_basis", "interval_days",
        "interval_runtime_h", "interval_cycles", "warning_days", "warning_runtime_h",
        "warning_cycles", "estimated_duration_min", "criticality", "condition_metric",
        "condition_operator", "condition_threshold", "anchor_at",
    )
    updates = {field: payload[field] for field in fields if field in payload}
    for field in ("requires_shutdown", "loto_required", "active", "verified"):
        if field in payload:
            updates[field] = int(bool(payload[field]))
    final = {**current, **updates}
    _validate_plan(final)
    now = _now()
    becoming_verified = not current["verified"] and bool(final["verified"])
    if becoming_verified and "anchor_at" not in payload:
        usage = usage_counters(conn, current["machine_id"])[0]
        updates["anchor_at"] = now
        updates["last_completed_runtime_h"] = usage[f"{final['runtime_basis']}_runtime_h"]
        updates["last_completed_cycles"] = usage["cycles"]
    updates.update({"source": "manual", "version": current["version"] + 1, "updated_at": now})
    try:
        if updates:
            columns = ",".join(f"{key}=?" for key in updates)
            conn.execute(
                f"UPDATE maintenance_plans SET {columns} WHERE id=?",
                (*updates.values(), plan_id),
            )
        if "tasks" in payload:
            _replace_tasks(conn, plan_id, payload["tasks"], now)
        if "spares" in payload:
            _replace_plan_spares(conn, plan_id, payload["spares"], now)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    if final["verified"] and final["active"]:
        sync(conn)
    return next(item for item in list_plans(conn) if item["id"] == plan_id)


def create_spare_part(conn: sqlite3.Connection, payload: dict) -> dict:
    key = payload["part_key"].strip().lower()
    if not re.fullmatch(r"[a-z0-9_.-]+", key):
        raise ValueError("part_key contains unsupported characters")
    criticality = payload.get("criticality", "medium")
    if criticality not in CRITICALITIES:
        raise ValueError(f"criticality must be one of {', '.join(CRITICALITIES)}")
    now = _now()
    try:
        conn.execute(
            """INSERT INTO spare_parts
               (part_key,name,manufacturer,manufacturer_part_number,unit,criticality,
                reorder_point,reorder_qty,lead_time_days,preferred_supplier,source,
                verified,active,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
            (key, payload["name"], payload.get("manufacturer"),
             payload.get("manufacturer_part_number"), payload.get("unit", "each"),
             criticality, float(payload.get("reorder_point", 0)),
             float(payload.get("reorder_qty", 0)), payload.get("lead_time_days"),
             payload.get("preferred_supplier"), payload.get("source", "manual"),
             int(bool(payload.get("verified", False))), now, now),
        )
        conn.commit()
    except sqlite3.IntegrityError as error:
        conn.rollback()
        raise ValueError(f"Spare part '{key}' already exists") from error
    return next(item for item in list_spares(conn) if item["part_key"] == key)


def list_spares(conn: sqlite3.Connection) -> list[dict]:
    parts = [dict(row) for row in conn.execute(
        "SELECT * FROM spare_parts WHERE active=1 ORDER BY criticality DESC, name"
    ).fetchall()]
    for part in parts:
        locations = [dict(row) for row in conn.execute(
            """SELECT id, location, on_hand_qty, reserved_qty, unit_cost, source,
                      verified, updated_at FROM spare_stock WHERE spare_part_id=?
               ORDER BY location""", (part["id"],),
        ).fetchall()]
        on_hand = sum(float(item["on_hand_qty"]) for item in locations)
        reserved = sum(float(item["reserved_qty"]) for item in locations)
        available = on_hand - reserved
        part.update({
            "locations": locations, "on_hand_qty": round(on_hand, 3),
            "reserved_qty": round(reserved, 3), "available_qty": round(available, 3),
            "below_reorder": available <= float(part["reorder_point"]),
            "stock_verified": bool(locations) and all(bool(item["verified"]) for item in locations),
        })
    return parts


def set_spare_stock(conn: sqlite3.Connection, part_key: str, payload: dict) -> dict:
    part_key = part_key.strip().lower()
    part = conn.execute(
        "SELECT id FROM spare_parts WHERE part_key=? AND active=1", (part_key,)
    ).fetchone()
    if not part:
        raise KeyError(f"Spare part '{part_key}' not found")
    location = payload.get("location", "maintenance_store").strip()
    if not location:
        raise ValueError("Stock location is required")
    current = conn.execute(
        "SELECT * FROM spare_stock WHERE spare_part_id=? AND location=?",
        (part["id"], location),
    ).fetchone()
    reserved = float(current["reserved_qty"] if current else 0)
    on_hand = float(payload["on_hand_qty"])
    if on_hand < reserved:
        raise ValueError("On-hand stock cannot be lower than committed spare reservations")
    now = _now()
    before = float(current["on_hand_qty"] if current else 0)
    conn.execute(
        """INSERT INTO spare_stock
           (spare_part_id,location,on_hand_qty,reserved_qty,unit_cost,source,verified,updated_at)
           VALUES (?,?,?,?,?,'manual',?,?)
           ON CONFLICT(spare_part_id,location) DO UPDATE SET
             on_hand_qty=excluded.on_hand_qty,
             unit_cost=COALESCE(excluded.unit_cost,spare_stock.unit_cost),
             source='manual', verified=excluded.verified, updated_at=excluded.updated_at""",
        (part["id"], location, on_hand, reserved, payload.get("unit_cost"),
         int(bool(payload.get("verified", False))), now),
    )
    stock_id = conn.execute(
        "SELECT id FROM spare_stock WHERE spare_part_id=? AND location=?",
        (part["id"], location),
    ).fetchone()["id"]
    conn.execute(
        """INSERT INTO spare_stock_movements
           (spare_stock_id,movement_type,on_hand_delta,reserved_delta,actor,notes,ts)
           VALUES (?,'adjustment',?,0,?,?,?)""",
        (stock_id, on_hand - before, payload.get("actor", "operator"),
         payload.get("notes"), now),
    )
    conn.commit()
    return next(item for item in list_spares(conn) if item["part_key"] == part_key)


def _reserve_plan_spares(conn: sqlite3.Connection, work_order_id: int,
                         plan_id: int, actor: str, now: str) -> dict:
    requirements = conn.execute(
        """SELECT mps.*, sp.part_key FROM maintenance_plan_spares mps
           JOIN spare_parts sp ON sp.id=mps.spare_part_id
           WHERE mps.maintenance_plan_id=? AND mps.active=1 AND sp.active=1""", (plan_id,),
    ).fetchall()
    shortage = reserved = 0
    for requirement in requirements:
        remaining = float(requirement["quantity"])
        stocks = conn.execute(
            """SELECT * FROM spare_stock WHERE spare_part_id=?
               ORDER BY verified DESC, updated_at, id""", (requirement["spare_part_id"],),
        ).fetchall()
        for stock in stocks:
            available = float(stock["on_hand_qty"]) - float(stock["reserved_qty"])
            quantity = min(remaining, max(0.0, available))
            if quantity <= 0:
                continue
            conn.execute(
                """INSERT INTO maintenance_spare_reservations
                   (work_order_id,spare_part_id,spare_stock_id,quantity_required,
                    quantity_reserved,required,status,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,'reserved',?,?)""",
                (work_order_id, requirement["spare_part_id"], stock["id"], quantity,
                 quantity, requirement["required"], now, now),
            )
            conn.execute(
                "UPDATE spare_stock SET reserved_qty=reserved_qty+?, updated_at=? WHERE id=?",
                (quantity, now, stock["id"]),
            )
            conn.execute(
                """INSERT INTO spare_stock_movements
                   (spare_stock_id,work_order_id,movement_type,on_hand_delta,
                    reserved_delta,actor,ts) VALUES (?,?,'reservation',0,?,?,?)""",
                (stock["id"], work_order_id, quantity, actor, now),
            )
            remaining -= quantity
            reserved += 1
            if remaining <= 1e-9:
                break
        if remaining > 1e-9:
            conn.execute(
                """INSERT INTO maintenance_spare_reservations
                   (work_order_id,spare_part_id,quantity_required,quantity_reserved,
                    required,status,created_at,updated_at)
                   VALUES (?,?,?,0,?,'shortage',?,?)""",
                (work_order_id, requirement["spare_part_id"], remaining,
                 requirement["required"], now, now),
            )
            shortage += 1
    return {"reserved_lines": reserved, "shortage_lines": shortage}


def sync(conn: sqlite3.Connection, commit: bool = True) -> dict:
    plans = list_plans(conn, active_only=True)
    created = shortages = 0
    generated = []
    now = _now()
    priority = {"low": "low", "medium": "medium", "high": "high", "critical": "urgent"}
    for plan in plans:
        if not plan["verified"] or plan["status"] not in ("overdue", "due_soon"):
            continue
        active = conn.execute(
            """SELECT wo.id FROM maintenance_work_order_links mwol
               JOIN maintenance_work_orders wo ON wo.id=mwol.work_order_id
               WHERE mwol.maintenance_plan_id=? AND wo.status IN ('open','in_progress')
               LIMIT 1""", (plan["id"],),
        ).fetchone()
        if active:
            continue
        cursor = conn.execute(
            """INSERT INTO maintenance_work_orders
               (machine_id,title,description,priority,status,source,due_date,created_at)
               VALUES (?,?,?,?,'open',?,?,?)""",
            (plan["machine_id"], plan["title"], plan["description"],
             priority[plan["criticality"]],
             "condition_monitoring" if plan["trigger_type"] == "condition"
             else "preventive_schedule", plan["next_due_at"], now),
        )
        work_order_id = cursor.lastrowid
        details = json.dumps({"dimensions": plan["dimensions"]}, sort_keys=True)
        conn.execute(
            """INSERT INTO maintenance_work_order_links
               (work_order_id,maintenance_plan_id,trigger_type,trigger_details_json,generated_at)
               VALUES (?,?,?,?,?)""",
            (work_order_id, plan["id"], plan["trigger_type"] or "scheduled", details, now),
        )
        conn.execute(
            """INSERT INTO maintenance_work_order_events
               (work_order_id,event_type,to_status,actor,details_json,ts)
               VALUES (?,'generated','open','hive-maintenance',?,?)""",
            (work_order_id, details, now),
        )
        reservation = _reserve_plan_spares(
            conn, work_order_id, plan["id"], "hive-maintenance", now
        )
        shortages += reservation["shortage_lines"]
        created += 1
        generated.append(work_order_id)
    if commit:
        conn.commit()
    return {"plans_evaluated": len(plans), "work_orders_created": created,
            "spare_shortages": shortages, "work_order_ids": generated}


def record_condition_signal(conn: sqlite3.Connection, payload: dict) -> dict:
    machine = _machine(conn, payload["machine_key"])
    params: list = [machine["id"], payload["metric_key"]]
    plan_filter = ""
    if payload.get("plan_id"):
        plan_filter = "AND id=?"
        params.append(payload["plan_id"])
    plans = conn.execute(
        f"""SELECT * FROM maintenance_plans WHERE machine_id=? AND condition_metric=?
            AND active=1 {plan_filter}""", params,
    ).fetchall()
    if payload.get("plan_id") and not plans:
        raise KeyError(f"Condition plan {payload['plan_id']} not found for this machine and metric")
    now = _now()
    observed_at = payload.get("observed_at") or now
    _parse_ts(observed_at)
    records = []
    targets = plans or [None]
    for plan in targets:
        threshold = (float(plan["condition_threshold"]) if plan else
                     payload.get("threshold"))
        comparison = (plan["condition_operator"] if plan else payload.get("comparison"))
        triggered = bool(threshold is not None and comparison and _condition_matches(
            float(payload["value"]), float(threshold), comparison
        ))
        plan_id = plan["id"] if plan else None
        if plan_id and not triggered:
            conn.execute(
                """UPDATE maintenance_condition_signals SET status='cleared'
                   WHERE maintenance_plan_id=? AND metric_key=?
                     AND status IN ('open','acknowledged')""",
                (plan_id, payload["metric_key"]),
            )
        cursor = conn.execute(
            """INSERT INTO maintenance_condition_signals
               (machine_id,maintenance_plan_id,metric_key,value,unit,threshold,
                comparison,triggered,severity,status,source,evidence_type,evidence_id,
                observed_at,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (machine["id"], plan_id, payload["metric_key"], float(payload["value"]),
             payload.get("unit"), threshold, comparison, int(triggered),
             payload.get("severity", "warning"), "open" if triggered else "observed",
             payload.get("source", "manual"), payload.get("evidence_type"),
             payload.get("evidence_id"), observed_at, now),
        )
        records.append(cursor.lastrowid)
    conn.commit()
    sync_result = sync(conn)
    return {"signal_ids": records, "matched_plans": len(plans), "sync": sync_result}


def _release_reservations(conn: sqlite3.Connection, work_order_id: int,
                          actor: str, now: str) -> None:
    rows = conn.execute(
        """SELECT * FROM maintenance_spare_reservations
           WHERE work_order_id=? AND status IN ('reserved','shortage')""", (work_order_id,),
    ).fetchall()
    for row in rows:
        if row["status"] == "reserved" and row["spare_stock_id"]:
            conn.execute(
                """UPDATE spare_stock SET reserved_qty=MAX(0,reserved_qty-?), updated_at=?
                   WHERE id=?""", (row["quantity_reserved"], now, row["spare_stock_id"]),
            )
            conn.execute(
                """INSERT INTO spare_stock_movements
                   (spare_stock_id,work_order_id,movement_type,on_hand_delta,
                    reserved_delta,actor,ts) VALUES (?,?,'release',0,?,?,?)""",
                (row["spare_stock_id"], work_order_id, -float(row["quantity_reserved"]),
                 actor, now),
            )
        conn.execute(
            "UPDATE maintenance_spare_reservations SET status='released',updated_at=? WHERE id=?",
            (now, row["id"]),
        )


def list_work_orders(conn: sqlite3.Connection, status: str | None = None,
                     work_order_id: int | None = None) -> list[dict]:
    filters = []
    params: list[object] = []
    if status:
        filters.append("wo.status=?")
        params.append(status)
    if work_order_id is not None:
        filters.append("wo.id=?")
        params.append(work_order_id)
    clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    limit = "" if work_order_id is not None else "LIMIT 200"
    rows = conn.execute(
        f"""SELECT wo.id, m.machine_key, m.name machine_name, wo.title, wo.description,
                   wo.priority, wo.status, wo.source, wo.due_date, wo.created_at,
                   wo.closed_at, mwol.maintenance_plan_id, mwol.trigger_type,
                   mp.plan_key, mp.loto_required, mp.requires_shutdown,
                   (SELECT ru.starts_at FROM resource_unavailability ru
                    WHERE ru.work_order_id=wo.id ORDER BY ru.starts_at DESC LIMIT 1) scheduled_start_at,
                   (SELECT ru.ends_at FROM resource_unavailability ru
                    WHERE ru.work_order_id=wo.id ORDER BY ru.starts_at DESC LIMIT 1) scheduled_end_at,
                   (SELECT COUNT(*) FROM maintenance_spare_reservations msr
                    WHERE msr.work_order_id=wo.id AND msr.status='shortage'
                      AND msr.required=1) required_spare_shortages
            FROM maintenance_work_orders wo
            LEFT JOIN machines m ON m.id=wo.machine_id
            LEFT JOIN maintenance_work_order_links mwol ON mwol.work_order_id=wo.id
            LEFT JOIN maintenance_plans mp ON mp.id=mwol.maintenance_plan_id
            {clause}
            ORDER BY CASE wo.priority WHEN 'urgent' THEN 1 WHEN 'high' THEN 2
                     WHEN 'medium' THEN 3 ELSE 4 END,
                     CASE wo.status WHEN 'in_progress' THEN 1 WHEN 'open' THEN 2 ELSE 3 END,
                     wo.due_date, wo.created_at DESC {limit}""", params,
    ).fetchall()
    return [dict(row) for row in rows]


def get_work_order(conn: sqlite3.Connection, work_order_id: int) -> dict:
    rows = list_work_orders(conn, work_order_id=work_order_id)
    row = rows[0] if rows else None
    if not row:
        raise KeyError(f"Maintenance work order {work_order_id} not found")
    row["tasks"] = [dict(task) for task in conn.execute(
        """SELECT mpt.id, mpt.task_key, mpt.sequence, mpt.title, mpt.instructions,
                  mpt.response_type, mpt.unit, mpt.required
           FROM maintenance_plan_tasks mpt JOIN maintenance_work_order_links mwol
             ON mwol.maintenance_plan_id=mpt.maintenance_plan_id
           WHERE mwol.work_order_id=? AND mpt.active=1 ORDER BY mpt.sequence""",
        (work_order_id,),
    ).fetchall()]
    row["spares"] = [dict(item) for item in conn.execute(
        """SELECT msr.id, sp.part_key, sp.name, sp.unit, ss.location,
                  msr.quantity_required, msr.quantity_reserved, msr.required, msr.status
           FROM maintenance_spare_reservations msr
           JOIN spare_parts sp ON sp.id=msr.spare_part_id
           LEFT JOIN spare_stock ss ON ss.id=msr.spare_stock_id
           WHERE msr.work_order_id=? ORDER BY sp.name, ss.location""", (work_order_id,),
    ).fetchall()]
    row["events"] = [dict(event) for event in conn.execute(
        """SELECT event_type,from_status,to_status,actor,details_json,ts
           FROM maintenance_work_order_events WHERE work_order_id=? ORDER BY ts,id""",
        (work_order_id,),
    ).fetchall()]
    execution = conn.execute(
        "SELECT * FROM maintenance_executions WHERE work_order_id=?", (work_order_id,)
    ).fetchone()
    row["execution"] = dict(execution) if execution else None
    return row


def update_work_order(conn: sqlite3.Connection, work_order_id: int, payload: dict) -> dict:
    current = get_work_order(conn, work_order_id)
    now = _now()
    target = payload.get("status")
    if target == "done":
        raise ValueError("Use the maintenance completion endpoint to close work with evidence")
    if target and target != current["status"]:
        if target not in WORK_ORDER_TRANSITIONS[current["status"]]:
            raise ValueError(f"Cannot move work order from {current['status']} to {target}")
        conn.execute(
            "UPDATE maintenance_work_orders SET status=? WHERE id=?", (target, work_order_id),
        )
        conn.execute(
            """INSERT INTO maintenance_work_order_events
               (work_order_id,event_type,from_status,to_status,actor,details_json,ts)
               VALUES (?,?,?,?,?,?,?)""",
            (work_order_id, "status_changed", current["status"], target,
             payload.get("actor", "operator"), json.dumps(payload, sort_keys=True), now),
        )
        if target == "cancelled":
            _release_reservations(conn, work_order_id, payload.get("actor", "operator"), now)
            conn.execute("DELETE FROM resource_unavailability WHERE work_order_id=?", (work_order_id,))
    starts = payload.get("scheduled_start_at")
    ends = payload.get("scheduled_end_at")
    if bool(starts) != bool(ends):
        raise ValueError("A maintenance window requires both start and end timestamps")
    if starts and ends:
        start = _parse_ts(starts)
        end = _parse_ts(ends)
        if end <= start:
            raise ValueError("Maintenance window must end after it starts")
        if not current["machine_key"]:
            raise ValueError("Only machine work orders can reserve a maintenance window")
        conn.execute("DELETE FROM resource_unavailability WHERE work_order_id=?", (work_order_id,))
        conn.execute(
            """INSERT INTO resource_unavailability
               (resource_type,resource_key,starts_at,ends_at,reason,source,
                work_order_id,created_by,created_at)
               VALUES ('machine',?,?,?,?,?,?,?,?)""",
            (current["machine_key"], start.isoformat(), end.isoformat(),
             f"Maintenance work order {work_order_id}: {current['title']}",
             "maintenance_plan", work_order_id, payload.get("actor", "operator"), now),
        )
        conn.execute(
            """INSERT INTO maintenance_work_order_events
               (work_order_id,event_type,from_status,to_status,actor,details_json,ts)
               VALUES (?,'scheduled',?,?,?,?,?)""",
            (work_order_id, current["status"], target or current["status"],
             payload.get("actor", "operator"),
             json.dumps({"starts_at": start.isoformat(), "ends_at": end.isoformat()}), now),
        )
    conn.commit()
    return get_work_order(conn, work_order_id)


def complete_work_order(conn: sqlite3.Connection, work_order_id: int, payload: dict) -> dict:
    work_order = get_work_order(conn, work_order_id)
    if work_order["status"] not in ("open", "in_progress"):
        raise ValueError(f"Work order is already {work_order['status']}")
    if work_order.get("loto_required") and (
        not payload.get("loto_verified") or not payload.get("loto_verified_by")
    ):
        raise ValueError("A named authorized person must verify hazardous-energy isolation")
    supplied = {item["task_id"]: item for item in payload.get("task_results", [])}
    valid_task_ids = {task["id"] for task in work_order["tasks"]}
    unknown_task_ids = set(supplied) - valid_task_ids
    if unknown_task_ids:
        raise ValueError(
            f"Task {min(unknown_task_ids)} does not belong to this work order"
        )
    failed = []
    for task in work_order["tasks"]:
        result = supplied.get(task["id"])
        if task["required"] and not result:
            raise ValueError(f"Required maintenance task '{task['title']}' has no result")
        if not result:
            continue
        response = result["result"]
        if task["response_type"] == "check" and response != "checked":
            raise ValueError(f"Task '{task['title']}' must be checked")
        if task["response_type"] == "pass_fail" and response not in ("pass", "fail"):
            raise ValueError(f"Task '{task['title']}' requires pass or fail")
        if task["response_type"] == "number" and result.get("value_number") is None:
            raise ValueError(f"Task '{task['title']}' requires a numeric value")
        if task["response_type"] == "text" and not result.get("value_text"):
            raise ValueError(f"Task '{task['title']}' requires text")
        if response == "fail":
            failed.append(task["title"])
    required_shortage = any(
        item["required"] and item["status"] == "shortage" for item in work_order["spares"]
    )
    if required_shortage:
        raise ValueError("Required spare parts are still short for this work order")
    now = payload.get("completed_at") or _now()
    _parse_ts(now)
    usage = (usage_counters(conn, _machine(conn, work_order["machine_key"])["id"])[0]
             if work_order["machine_key"] else None)
    outcome = "follow_up_required" if failed else "completed"
    cursor = conn.execute(
        """INSERT INTO maintenance_executions
           (work_order_id,maintenance_plan_id,machine_id,outcome,completed_by,notes,
            loto_verified,loto_verified_by,loto_verified_at,completed_at,
            runtime_h_at_completion,cycles_at_completion)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (work_order_id, work_order["maintenance_plan_id"],
         usage["machine_id"] if usage else None, outcome, payload["completed_by"],
         payload.get("notes"), int(bool(payload.get("loto_verified"))),
         payload.get("loto_verified_by"), now if payload.get("loto_verified") else None,
         now, usage["powered_runtime_h"] if usage else None,
         usage["cycles"] if usage else None),
    )
    for task_id, result in supplied.items():
        conn.execute(
            """INSERT INTO maintenance_task_results
               (maintenance_execution_id,maintenance_task_id,result,value_text,
                value_number,notes) VALUES (?,?,?,?,?,?)""",
            (cursor.lastrowid, task_id, result["result"], result.get("value_text"),
             result.get("value_number"), result.get("notes")),
        )
    reservations = conn.execute(
        """SELECT * FROM maintenance_spare_reservations
           WHERE work_order_id=? AND status='reserved'""", (work_order_id,),
    ).fetchall()
    for reservation in reservations:
        stock = conn.execute(
            "SELECT * FROM spare_stock WHERE id=?", (reservation["spare_stock_id"],)
        ).fetchone()
        quantity = float(reservation["quantity_reserved"])
        if not stock or float(stock["on_hand_qty"]) < quantity or float(stock["reserved_qty"]) < quantity:
            conn.rollback()
            raise ValueError("Reserved spare stock changed; reconcile inventory before completion")
        conn.execute(
            """UPDATE spare_stock SET on_hand_qty=on_hand_qty-?,
                 reserved_qty=reserved_qty-?,updated_at=? WHERE id=?""",
            (quantity, quantity, now, stock["id"]),
        )
        conn.execute(
            """INSERT INTO spare_stock_movements
               (spare_stock_id,work_order_id,movement_type,on_hand_delta,
                reserved_delta,actor,notes,ts) VALUES (?,?,'issue',?,?,?,?,?)""",
            (stock["id"], work_order_id, -quantity, -quantity, payload["completed_by"],
             payload.get("notes"), now),
        )
        conn.execute(
            "UPDATE maintenance_spare_reservations SET status='issued',updated_at=? WHERE id=?",
            (now, reservation["id"]),
        )
    conn.execute(
        """UPDATE maintenance_spare_reservations SET status='released',updated_at=?
           WHERE work_order_id=? AND status='shortage'""", (now, work_order_id),
    )
    conn.execute(
        "UPDATE maintenance_work_orders SET status='done',closed_at=? WHERE id=?",
        (now, work_order_id),
    )
    if work_order["maintenance_plan_id"] and usage:
        plan = _plan(conn, work_order["maintenance_plan_id"])
        conn.execute(
            """UPDATE maintenance_plans SET last_completed_at=?,anchor_at=?,
                 last_completed_runtime_h=?,last_completed_cycles=?,version=version+1,
                 updated_at=? WHERE id=?""",
            (now, now, usage[f"{plan['runtime_basis']}_runtime_h"], usage["cycles"],
             now, plan["id"]),
        )
        conn.execute(
            """UPDATE maintenance_condition_signals SET status='cleared'
               WHERE maintenance_plan_id=? AND status IN ('open','acknowledged')""",
            (plan["id"],),
        )
    conn.execute(
        """INSERT INTO maintenance_work_order_events
           (work_order_id,event_type,from_status,to_status,actor,details_json,ts)
           VALUES (?,'completed',?,'done',?,?,?)""",
        (work_order_id, work_order["status"], payload["completed_by"],
         json.dumps({"outcome": outcome, "failed_tasks": failed}), now),
    )
    follow_up_id = None
    if failed:
        follow_up = conn.execute(
            """INSERT INTO maintenance_work_orders
               (machine_id,title,description,priority,status,source,created_at)
               VALUES (?,?,?,'high','open','inspection_followup',?)""",
            (usage["machine_id"] if usage else None,
             f"Follow up failed inspection: {work_order['title']}",
             "Failed checks: " + "; ".join(failed), now),
        )
        follow_up_id = follow_up.lastrowid
        conn.execute(
            """INSERT INTO maintenance_work_order_events
               (work_order_id,event_type,to_status,actor,details_json,ts)
               VALUES (?,'generated','open',?,?,?)""",
            (follow_up_id, payload["completed_by"],
             json.dumps({"source_work_order_id": work_order_id}), now),
        )
    conn.execute(
        "DELETE FROM resource_unavailability WHERE work_order_id=? AND starts_at>?",
        (work_order_id, now),
    )
    conn.commit()
    result = get_work_order(conn, work_order_id)
    result["follow_up_work_order_id"] = follow_up_id
    return result


def reliability_metrics(conn: sqlite3.Connection) -> list[dict]:
    usage = {item["machine_id"]: item for item in usage_counters(conn)}
    result = []
    for machine in conn.execute(
        "SELECT id,machine_key,name FROM machines WHERE active=1 ORDER BY name"
    ).fetchall():
        failures = conn.execute(
            """SELECT COUNT(*) count FROM downtime_events de
               JOIN downtime_reasons dr ON dr.id=de.reason_id
               WHERE de.machine_id=? AND dr.code='breakdown'""", (machine["id"],),
        ).fetchone()["count"]
        repair = conn.execute(
            """SELECT AVG((julianday(de.ended_at)-julianday(de.started_at))*24) value
               FROM downtime_events de JOIN downtime_reasons dr ON dr.id=de.reason_id
               WHERE de.machine_id=? AND dr.code='breakdown' AND de.status='closed'
                 AND de.ended_at IS NOT NULL""", (machine["id"],),
        ).fetchone()["value"]
        pm = conn.execute(
            """SELECT COUNT(*) total,
                      SUM(CASE WHEN wo.closed_at<=wo.due_date OR wo.due_date IS NULL THEN 1 ELSE 0 END) on_time
               FROM maintenance_work_orders wo JOIN maintenance_work_order_links mwol
                 ON mwol.work_order_id=wo.id
               WHERE wo.machine_id=? AND wo.status='done'""", (machine["id"],),
        ).fetchone()
        powered = usage[machine["id"]]["powered_runtime_h"]
        result.append({
            "machine_key": machine["machine_key"], "machine_name": machine["name"],
            "failure_count": failures,
            "mtbf_powered_h": round(powered / failures, 2) if failures and powered else None,
            "mttr_h": round(float(repair), 2) if repair is not None else None,
            "preventive_completed": int(pm["total"] or 0),
            "preventive_on_time_rate": (round(int(pm["on_time"] or 0) / int(pm["total"]), 4)
                                         if pm["total"] else None),
            "evidence": usage[machine["id"]]["usage_evidence"],
        })
    return result


def snapshot(conn: sqlite3.Connection, ensure_defaults: bool = True) -> dict:
    defaults = sync_defaults(conn) if ensure_defaults else {"plans_created": 0, "tasks_created": 0}
    plans = list_plans(conn)
    work_orders = list_work_orders(conn)
    spares = list_spares(conn)
    summary = {
        "plans": len(plans),
        "verified_plans": sum(1 for item in plans if item["verified"] and item["active"]),
        "unverified_plans": sum(1 for item in plans if not item["verified"] and item["active"]),
        "overdue_plans": sum(1 for item in plans if item["status"] == "overdue"),
        "due_soon_plans": sum(1 for item in plans if item["status"] == "due_soon"),
        "open_work_orders": sum(1 for item in work_orders if item["status"] in ("open", "in_progress")),
        "spare_shortages": sum(1 for item in work_orders if item["required_spare_shortages"]),
        "below_reorder": sum(1 for item in spares if item["below_reorder"]),
    }
    covered = {item["machine_id"] for item in plans if item["verified"] and item["active"]}
    machine_count = conn.execute("SELECT COUNT(*) count FROM machines WHERE active=1").fetchone()["count"]
    summary["machines_without_verified_plan"] = int(machine_count) - len(covered)
    if summary["overdue_plans"] or summary["spare_shortages"]:
        status = "attention_required"
    elif summary["unverified_plans"] or summary["machines_without_verified_plan"]:
        status = "commissioning_required"
    else:
        status = "ready"
    signals = [dict(row) for row in conn.execute(
        """SELECT mcs.*,m.machine_key,m.name machine_name,mp.plan_key
           FROM maintenance_condition_signals mcs JOIN machines m ON m.id=mcs.machine_id
           LEFT JOIN maintenance_plans mp ON mp.id=mcs.maintenance_plan_id
           ORDER BY mcs.observed_at DESC,mcs.id DESC LIMIT 50"""
    ).fetchall()]
    return {
        "status": status, "summary": summary, "plans": plans,
        "work_orders": work_orders, "spares": spares,
        "usage": usage_counters(conn), "reliability": reliability_metrics(conn),
        "condition_signals": signals, "defaults": defaults,
        "safety_policy": {
            "loto": "HIVE records named human verification; it never isolates or energizes equipment",
            "automatic_commands": False,
        },
    }
