"""Station dispatch, execution actuals, and physical-flow traceability."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import identity
import production_control
import resources as factory_resources

ACTIVE_STATES = ("queued", "available", "dispatched", "acknowledged", "running", "held")
TERMINAL_STATES = ("completed", "cancelled")


class VersionConflict(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_event(conn: sqlite3.Connection, execution_job_id: int, event_type: str,
                  from_state: str | None, to_state: str | None, payload: dict,
                  *, quantity: int | None = None, good_qty: int | None = None,
                  scrap_qty: int | None = None,
                  idempotency_key: str | None = None) -> int:
    cursor = conn.execute(
        """INSERT INTO execution_job_events
           (execution_job_id, event_type, from_state, to_state, quantity,
            good_qty, scrap_qty, source, evidence_type, evidence_id, actor,
            notes, idempotency_key, ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (execution_job_id, event_type, from_state, to_state, quantity,
         good_qty, scrap_qty, payload.get("source", "manual"),
         payload.get("evidence_type"), payload.get("evidence_id"),
         payload.get("actor", "operator"), payload.get("notes"),
         idempotency_key, payload.get("ts") or _now()),
    )
    return cursor.lastrowid


def _trace(conn: sqlite3.Connection, row: dict, event_type: str,
           disposition: str, quantity: float, payload: dict, event_id: int,
           *, action: str = "observe", uom: str = "each",
           object_key: str | None = None) -> None:
    source = payload.get("source", "manual")
    evidence_type = payload.get("evidence_type") or "execution_event"
    evidence_id = payload.get("evidence_id") or event_id
    key = payload.get("idempotency_key")
    trace_key = f"{key}:trace:{event_type}" if key else None
    conn.execute(
        """INSERT OR IGNORE INTO traceability_events
           (object_type, object_key, production_order_id, part_id,
            execution_job_id, event_type, action, quantity, uom, read_point,
            business_location, disposition, source, evidence_type, evidence_id,
            actor, idempotency_key, event_time, recorded_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (payload.get("object_type", "part"),
         object_key or f"part:{row['part_id']}", row["production_order_id"],
         row["part_id"], row["id"], event_type, action, quantity, uom,
         row["machine_key"], payload.get("business_location") or row["machine_key"],
         disposition, source, evidence_type, evidence_id,
         payload.get("actor", "operator"), trace_key,
         payload.get("ts") or _now(), _now()),
    )


def _exception(conn: sqlite3.Connection, row: dict | None, exception_type: str,
               details: str, payload: dict) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO execution_exceptions
           (execution_job_id, production_order_id, part_id, machine_id,
            exception_type, details, source, evidence_type, evidence_id,
            occurred_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (row["id"] if row else None,
         row["production_order_id"] if row else payload.get("production_order_id"),
         row["part_id"] if row else payload.get("part_id"),
         row["machine_id"] if row else payload.get("machine_id"),
         exception_type, details, payload.get("source", "manual"),
         payload.get("evidence_type"), payload.get("evidence_id"),
         payload.get("ts") or _now()),
    )


def _job_row(conn: sqlite3.Connection, execution_job_id: int) -> dict:
    row = conn.execute(
        """SELECT ej.*, ps.status scenario_status, psi.position schedule_position,
                  po.status order_status, po.version order_version, po.due_at,
                  j.job_name, p.id part_id, p.part_name, p.qty part_qty,
                  prs.step_index, prs.status route_status,
                  prs.confirmed_qty route_confirmed_qty,
                  m.machine_key, m.name machine_name,
                  (SELECT prev.state FROM part_route_steps prev_step
                   JOIN execution_jobs prev ON prev.route_step_id=prev_step.id
                   WHERE prev_step.part_id=prs.part_id AND prev_step.required=1
                     AND prev_step.step_index < prs.step_index
                   ORDER BY prev_step.step_index DESC LIMIT 1) predecessor_state,
                  (SELECT COUNT(*) FROM route_exceptions re
                   WHERE re.part_id=p.id AND re.status='open') open_route_exceptions,
                  (SELECT COUNT(*) FROM downtime_events de
                   WHERE de.machine_id=ej.machine_id AND de.status='open') open_downtime
           FROM execution_jobs ej
           JOIN planning_scenarios ps ON ps.id=ej.scenario_id
           JOIN production_schedule_items psi ON psi.id=ej.schedule_item_id
           JOIN production_orders po ON po.id=ej.production_order_id
           JOIN jobs j ON j.id=po.job_id
           JOIN part_route_steps prs ON prs.id=ej.route_step_id
           JOIN parts p ON p.id=prs.part_id
           JOIN machines m ON m.id=ej.machine_id
           WHERE ej.id=?""", (execution_job_id,),
    ).fetchone()
    if not row:
        raise KeyError(f"Execution job {execution_job_id} not found")
    return dict(row)


def _blocked_reason(row: dict) -> str | None:
    if row["order_status"] not in ("released", "in_progress"):
        return "production order not released"
    if row["step_index"] > 1 and row["predecessor_state"] != "completed":
        return "previous operation incomplete"
    if row["open_route_exceptions"]:
        return "unresolved route exception"
    if row["open_downtime"]:
        return "machine downtime open"
    return None


def _refresh_availability(conn: sqlite3.Connection) -> int:
    candidates = conn.execute(
        "SELECT id FROM execution_jobs WHERE state IN ('queued','available') ORDER BY id"
    ).fetchall()
    changed = 0
    for candidate in candidates:
        row = _job_row(conn, candidate["id"])
        target = "available" if _blocked_reason(row) is None else "queued"
        if target == row["state"]:
            continue
        now = _now()
        conn.execute(
            """UPDATE execution_jobs SET state=?, version=version+1, updated_at=?
               WHERE id=?""", (target, now, row["id"]),
        )
        _record_event(conn, row["id"], "availability_changed", row["state"],
                      target, {"source": "system", "actor": "system", "ts": now})
        changed += 1
    return changed


def sync(conn: sqlite3.Connection, commit: bool = True) -> dict:
    production_control.sync_orders(conn, commit=False)
    production_control.sync_routes(conn, commit=False)
    scenario = conn.execute(
        """SELECT id FROM planning_scenarios
           WHERE status='approved' ORDER BY approved_at DESC LIMIT 1"""
    ).fetchone()
    if not scenario:
        if commit:
            conn.commit()
        return {"scenario_id": None, "created": 0, "relinked": 0, "available_changed": 0}
    rows = conn.execute(
        """SELECT psi.id schedule_item_id, psi.position, po.id production_order_id,
                  prs.id route_step_id, prs.required_qty, prs.step_index,
                  prs.machine_id, p.id part_id
           FROM production_schedule_items psi
           JOIN production_orders po ON po.id=psi.production_order_id
           JOIN parts p ON p.job_id=po.job_id
           JOIN part_route_steps prs ON prs.part_id=p.id AND prs.required=1
           WHERE psi.scenario_id=?
           ORDER BY psi.position, prs.step_index, p.id""", (scenario["id"],),
    ).fetchall()
    created = relinked = 0
    now = _now()
    for row in rows:
        sequence = int(row["position"]) * 1_000_000 + int(row["step_index"]) * 10_000 + int(row["part_id"])
        existing = conn.execute(
            "SELECT id, state, scenario_id FROM execution_jobs WHERE route_step_id=?",
            (row["route_step_id"],),
        ).fetchone()
        if existing:
            if existing["scenario_id"] != scenario["id"] and existing["state"] in ("queued", "available", "cancelled"):
                target_state = "queued" if existing["state"] == "cancelled" else existing["state"]
                conn.execute(
                    """UPDATE execution_jobs SET scenario_id=?, schedule_item_id=?,
                          dispatch_sequence=?, state=?, version=version+1, updated_at=? WHERE id=?""",
                    (scenario["id"], row["schedule_item_id"], sequence, target_state,
                     now, existing["id"]),
                )
                _record_event(conn, existing["id"], "schedule_relinked", existing["state"],
                              target_state, {"source": "system", "actor": "system", "ts": now})
                relinked += 1
            continue
        cursor = conn.execute(
            """INSERT INTO execution_jobs
               (scenario_id, schedule_item_id, production_order_id, route_step_id,
                machine_id, dispatch_sequence, required_qty, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (scenario["id"], row["schedule_item_id"], row["production_order_id"],
             row["route_step_id"], row["machine_id"], sequence,
             max(1, int(row["required_qty"])), now, now),
        )
        _record_event(conn, cursor.lastrowid, "generated", None, "queued",
                      {"source": "schedule", "actor": "system", "ts": now})
        created += 1
    current_route_steps = {int(row["route_step_id"]) for row in rows}
    stale = conn.execute(
        """SELECT id, state FROM execution_jobs
           WHERE scenario_id!=? AND state IN ('queued','available')""", (scenario["id"],)
    ).fetchall()
    for item in stale:
        route_step_id = conn.execute(
            "SELECT route_step_id FROM execution_jobs WHERE id=?", (item["id"],)
        ).fetchone()["route_step_id"]
        if route_step_id in current_route_steps:
            continue
        conn.execute(
            """UPDATE execution_jobs SET state='cancelled', version=version+1,
                  updated_at=? WHERE id=?""", (now, item["id"]),
        )
        _record_event(conn, item["id"], "schedule_removed", item["state"], "cancelled",
                      {"source": "schedule", "actor": "system", "ts": now})
    changed = _refresh_availability(conn)
    if commit:
        conn.commit()
    return {"scenario_id": scenario["id"], "created": created,
            "relinked": relinked, "available_changed": changed}


def list_jobs(conn: sqlite3.Connection, machine_key: str | None = None,
              include_terminal: bool = False, limit: int = 500) -> list[dict]:
    sync(conn)
    clauses = []
    params: list = []
    if machine_key:
        clauses.append("m.machine_key=?")
        params.append(machine_key)
    if not include_terminal:
        clauses.append("ej.state NOT IN ('completed','cancelled')")
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    rows = conn.execute(
        f"""SELECT ej.id FROM execution_jobs ej
            JOIN machines m ON m.id=ej.machine_id {where}
            ORDER BY ej.dispatch_sequence, ej.id LIMIT ?""", (*params, limit),
    ).fetchall()
    result = []
    for item in rows:
        row = _job_row(conn, item["id"])
        row["blocked_reason"] = _blocked_reason(row) if row["state"] in ("queued", "available") else None
        row["remaining_qty"] = max(0, row["required_qty"] - row["completed_qty"])
        result.append(row)
    return result


def list_events(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    return [dict(row) for row in conn.execute(
        """SELECT eje.*, j.job_name, p.part_name, m.machine_key
           FROM execution_job_events eje
           JOIN execution_jobs ej ON ej.id=eje.execution_job_id
           JOIN production_orders po ON po.id=ej.production_order_id
           JOIN jobs j ON j.id=po.job_id
           JOIN part_route_steps prs ON prs.id=ej.route_step_id
           JOIN parts p ON p.id=prs.part_id
           JOIN machines m ON m.id=ej.machine_id
           ORDER BY eje.ts DESC, eje.id DESC LIMIT ?""", (limit,)
    ).fetchall()]


def list_exceptions(conn: sqlite3.Connection, status: str = "open",
                    limit: int = 100) -> list[dict]:
    return [dict(row) for row in conn.execute(
        """SELECT ee.*, j.job_name, p.part_name, m.machine_key
           FROM execution_exceptions ee
           LEFT JOIN production_orders po ON po.id=ee.production_order_id
           LEFT JOIN jobs j ON j.id=po.job_id
           LEFT JOIN parts p ON p.id=ee.part_id
           LEFT JOIN machines m ON m.id=ee.machine_id
           WHERE ee.status=? ORDER BY ee.occurred_at DESC, ee.id DESC LIMIT ?""",
        (status, limit),
    ).fetchall()]


def list_traceability(conn: sqlite3.Connection, object_key: str | None = None,
                      part_id: int | None = None, limit: int = 100) -> list[dict]:
    clauses = []
    params: list = []
    if object_key:
        clauses.append("te.object_key=?")
        params.append(object_key)
    if part_id:
        clauses.append("te.part_id=?")
        params.append(part_id)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    return [dict(row) for row in conn.execute(
        f"""SELECT te.*, j.job_name, p.part_name
            FROM traceability_events te
            LEFT JOIN production_orders po ON po.id=te.production_order_id
            LEFT JOIN jobs j ON j.id=po.job_id
            LEFT JOIN parts p ON p.id=te.part_id {where}
            ORDER BY te.event_time DESC, te.id DESC LIMIT ?""", (*params, limit),
    ).fetchall()]


def snapshot(conn: sqlite3.Connection) -> dict:
    sync_result = sync(conn)
    jobs = list_jobs(conn)
    counts = {state: 0 for state in (*ACTIVE_STATES, *TERMINAL_STATES)}
    for row in conn.execute("SELECT state, COUNT(*) count FROM execution_jobs GROUP BY state").fetchall():
        counts[row["state"]] = row["count"]
    stations = [dict(row) for row in conn.execute(
        """SELECT m.machine_key, m.name machine_name,
                  SUM(CASE WHEN ej.state='available' THEN 1 ELSE 0 END) available,
                  SUM(CASE WHEN ej.state='running' THEN 1 ELSE 0 END) running,
                  SUM(CASE WHEN ej.state='held' THEN 1 ELSE 0 END) held,
                  COUNT(*) total
           FROM execution_jobs ej JOIN machines m ON m.id=ej.machine_id
           WHERE ej.state NOT IN ('completed','cancelled')
           GROUP BY m.id ORDER BY MIN(ej.dispatch_sequence)"""
    ).fetchall()]
    exceptions = list_exceptions(conn, "open", 100)
    return {
        "scenario_id": sync_result["scenario_id"],
        "status": "active" if sync_result["scenario_id"] else "waiting_for_approved_schedule",
        "summary": {**counts, "open_exceptions": len(exceptions)},
        "stations": stations,
        "jobs": jobs,
        "exceptions": exceptions,
        "recent_events": list_events(conn, 30),
        "recent_traceability": list_traceability(conn, limit=30),
    }


def _update_buffer(conn: sqlite3.Connection, machine_id: int, delta: int,
                   row: dict, payload: dict) -> None:
    buffer_row = conn.execute(
        "SELECT capacity_qty, current_qty FROM wip_buffers WHERE machine_id=?", (machine_id,)
    ).fetchone()
    if not buffer_row:
        return
    current = int(buffer_row["current_qty"])
    target = max(0, current + delta)
    conn.execute(
        """UPDATE wip_buffers SET current_qty=?, source='execution', updated_at=?
           WHERE machine_id=?""", (target, _now(), machine_id),
    )
    if delta < 0 and current < abs(delta):
        _exception(conn, row, "wip_underflow",
                   f"Started {abs(delta)} units but only {current} were recorded in the input buffer", payload)
    if delta > 0 and target > int(buffer_row["capacity_qty"]):
        _exception(conn, row, "wip_overflow",
                   f"Input buffer reached {target} against configured capacity {buffer_row['capacity_qty']}", payload)


def _route_evidence(payload: dict, execution_event_id: int) -> tuple[str, int]:
    source = payload.get("source", "manual")
    if source in ("machine_event", "barcode") and payload.get("evidence_id"):
        return source, int(payload["evidence_id"])
    return "execution", execution_event_id


def _start_blocker(conn: sqlite3.Connection, row: dict) -> str | None:
    reason = _blocked_reason(row)
    if reason:
        return reason
    if row["state"] == "running":
        return None
    profile = conn.execute(
        """SELECT mrp.machine_capacity, mrp.labor_qty, mrp.tool_qty,
                  lr.role_key, lr.headcount, tp.pool_key, tp.available_qty
           FROM machine_resource_profiles mrp
           LEFT JOIN labor_roles lr ON lr.id=mrp.labor_role_id
           LEFT JOIN tool_pools tp ON tp.id=mrp.tool_pool_id
           WHERE mrp.machine_id=?""", (row["machine_id"],),
    ).fetchone()
    if not profile:
        return "machine resource profile missing"
    machine_running = conn.execute(
        """SELECT COUNT(*) count FROM execution_jobs
           WHERE machine_id=? AND state='running' AND id!=?""",
        (row["machine_id"], row["id"]),
    ).fetchone()["count"]
    if machine_running >= profile["machine_capacity"]:
        return "machine execution capacity is in use"
    if profile["role_key"]:
        labor_used = conn.execute(
            """SELECT COALESCE(SUM(mrp.labor_qty),0) used
               FROM execution_jobs ej
               JOIN machine_resource_profiles mrp ON mrp.machine_id=ej.machine_id
               WHERE ej.state='running' AND ej.id!=? AND mrp.labor_role_id=(
                 SELECT labor_role_id FROM machine_resource_profiles WHERE machine_id=?)""",
            (row["id"], row["machine_id"]),
        ).fetchone()["used"]
        if labor_used + profile["labor_qty"] > profile["headcount"]:
            return f"{profile['role_key']} capacity is in use"
    if profile["pool_key"]:
        tools_used = conn.execute(
            """SELECT COALESCE(SUM(mrp.tool_qty),0) used
               FROM execution_jobs ej
               JOIN machine_resource_profiles mrp ON mrp.machine_id=ej.machine_id
               WHERE ej.state='running' AND ej.id!=? AND mrp.tool_pool_id=(
                 SELECT tool_pool_id FROM machine_resource_profiles WHERE machine_id=?)""",
            (row["id"], row["machine_id"]),
        ).fetchone()["used"]
        if tools_used + profile["tool_qty"] > profile["available_qty"]:
            return f"{profile['pool_key']} capacity is in use"
    simulated_at = datetime.now(timezone.utc)
    context = factory_resources.simulation_context(
        conn, [{"job_name": row["job_name"]}], simulated_at, sync=False
    )
    delay = factory_resources.next_available_delay(
        context, row["machine_key"], profile["role_key"], profile["pool_key"], 0, 1
    )
    if delay is None or delay > 0:
        return "outside the verified work calendar or planned availability"
    return None


def _start(conn: sqlite3.Connection, row: dict, quantity: int, payload: dict,
           *, implicit: bool = False) -> dict:
    if quantity < 1:
        raise ValueError("Start quantity must be positive")
    capacity = row["required_qty"] - row["completed_qty"] - row["in_process_qty"]
    if quantity > capacity:
        raise ValueError(f"Only {capacity} units remain available to start")
    manual = payload.get("source", "manual") == "manual"
    blocker = _start_blocker(conn, row)
    if blocker and manual:
        raise ValueError(blocker)
    if blocker and not manual:
        _exception(conn, row, "capacity_bypass", blocker, payload)
    if manual and row["state"] not in ("dispatched", "acknowledged", "running"):
        raise ValueError("Dispatch the station job before starting it")
    if not manual and row["state"] not in ("dispatched", "acknowledged", "running"):
        _exception(conn, row, "unplanned_execution",
                   f"Actual start arrived while station job was {row['state']}", payload)
    now = payload.get("ts") or _now()
    event_type = "implicit_start" if implicit else "started"
    key = payload.get("idempotency_key")
    event_key = f"{key}:start" if implicit and key else key
    event_id = _record_event(conn, row["id"], event_type, row["state"], "running",
                             payload, quantity=quantity, idempotency_key=event_key)
    conn.execute(
        """UPDATE execution_jobs SET state='running', in_process_qty=in_process_qty+?,
              assigned_operator=COALESCE(?, assigned_operator), started_at=COALESCE(started_at,?),
              held_reason=NULL, version=version+1, updated_at=? WHERE id=?""",
        (quantity, payload.get("actor"), now, now, row["id"]),
    )
    if row["step_index"] > 1:
        _update_buffer(conn, row["machine_id"], -quantity, row, payload)
    route_source, evidence_id = _route_evidence(payload, event_id)
    production_control.confirm_route_step(
        conn, row["part_id"], row["machine_key"], "operation_start",
        route_source, evidence_id, now, payload.get("actor"), quantity=quantity,
        commit=False,
    )
    _trace(conn, row, "operation_started", "in_progress", quantity, payload, event_id,
           object_key=payload.get("object_key"))
    return _job_row(conn, row["id"])


def _complete(conn: sqlite3.Connection, row: dict, good_qty: int,
              scrap_qty: int, payload: dict) -> dict:
    if good_qty < 0 or scrap_qty < 0 or good_qty + scrap_qty < 1:
        raise ValueError("Completion needs a positive good or scrap quantity")
    total = good_qty + scrap_qty
    if total > row["in_process_qty"]:
        if payload.get("source", "manual") == "manual":
            raise ValueError(f"Only {row['in_process_qty']} units are currently in process")
        row = _start(conn, row, total - row["in_process_qty"], payload, implicit=True)
    if row["completed_qty"] + good_qty > row["required_qty"]:
        raise ValueError("Good quantity would exceed the station job requirement")
    completed = row["completed_qty"] + good_qty
    target = "completed" if completed >= row["required_qty"] else (
        "running" if row["in_process_qty"] - total > 0 else
        ("acknowledged" if row["assigned_operator"] else "dispatched")
    )
    now = payload.get("ts") or _now()
    event_id = _record_event(
        conn, row["id"], "completed" if target == "completed" else "quantity_completed",
        row["state"], target, payload, good_qty=good_qty, scrap_qty=scrap_qty,
        idempotency_key=payload.get("idempotency_key"),
    )
    conn.execute(
        """UPDATE execution_jobs SET state=?, in_process_qty=in_process_qty-?,
              completed_qty=completed_qty+?, scrap_qty=scrap_qty+?,
              completed_at=CASE WHEN ?='completed' THEN ? ELSE completed_at END,
              version=version+1, updated_at=? WHERE id=?""",
        (target, total, good_qty, scrap_qty, target, now, now, row["id"]),
    )
    route_source, evidence_id = _route_evidence(payload, event_id)
    if good_qty:
        production_control.confirm_route_step(
            conn, row["part_id"], row["machine_key"], "operation_complete",
            route_source, evidence_id, now, payload.get("actor"), quantity=good_qty,
            commit=False,
        )
        successor = conn.execute(
            """SELECT next.machine_id FROM part_route_steps current
               JOIN part_route_steps next ON next.part_id=current.part_id
                 AND next.required=1 AND next.step_index=(
                   SELECT MIN(step_index) FROM part_route_steps
                   WHERE part_id=current.part_id AND required=1
                     AND step_index>current.step_index)
               WHERE current.id=?""", (row["route_step_id"],)
        ).fetchone()
        if successor:
            _update_buffer(conn, successor["machine_id"], good_qty, row, payload)
        _trace(conn, row, "operation_completed", "work_in_progress" if successor else "completed",
               good_qty, payload, event_id, object_key=payload.get("object_key"))
    if scrap_qty:
        _trace(conn, row, "scrap_recorded", "non_conforming", scrap_qty,
               payload, event_id, action="delete", object_key=payload.get("object_key"))
        _exception(conn, row, "scrap_recorded",
                   f"{scrap_qty} unit(s) scrapped at {row['machine_key']}", payload)
    return _job_row(conn, row["id"])


def _apply_action(conn: sqlite3.Connection, execution_job_id: int,
                  payload: dict) -> dict:
    sync(conn, commit=False)
    key = payload.get("idempotency_key")
    if key:
        existing = conn.execute(
            "SELECT execution_job_id FROM execution_job_events WHERE idempotency_key=?", (key,)
        ).fetchone()
        if existing:
            if existing["execution_job_id"] != execution_job_id:
                raise ValueError("Idempotency key belongs to another execution job")
            return _job_row(conn, existing["execution_job_id"])
    row = _job_row(conn, execution_job_id)
    expected = payload.get("expected_version")
    if expected is not None and int(expected) != row["version"]:
        raise VersionConflict(
            f"Execution job changed from version {expected} to {row['version']}"
        )
    action = payload["action"]
    actor = payload.get("actor", "operator")
    now = payload.get("ts") or _now()
    payload = {**payload, "actor": actor, "ts": now}
    if action == "dispatch":
        if row["state"] != "available":
            reason = _blocked_reason(row)
            raise ValueError(reason or f"Cannot dispatch a {row['state']} station job")
        resource_status = factory_resources.snapshot(conn, [row["job_name"]], sync=False)
        if not resource_status["resource_ready"]:
            raise ValueError("Factory resources changed; hold the order and generate a fresh schedule")
        event_id = _record_event(conn, row["id"], "dispatched", row["state"], "dispatched",
                                 payload, idempotency_key=key)
        conn.execute(
            """UPDATE execution_jobs SET state='dispatched', dispatched_at=?,
                  assigned_operator=COALESCE(?,assigned_operator), version=version+1,
                  updated_at=? WHERE id=?""", (now, payload.get("assigned_operator"), now, row["id"]),
        )
    elif action == "acknowledge":
        if row["state"] != "dispatched":
            raise ValueError("Only a dispatched station job can be acknowledged")
        _record_event(conn, row["id"], "acknowledged", row["state"], "acknowledged",
                      payload, idempotency_key=key)
        conn.execute(
            """UPDATE execution_jobs SET state='acknowledged', acknowledged_at=?,
                  assigned_operator=?, version=version+1, updated_at=? WHERE id=?""",
            (now, payload.get("assigned_operator") or actor, now, row["id"]),
        )
    elif action == "start":
        quantity = int(payload.get("quantity") or (row["required_qty"] - row["completed_qty"] - row["in_process_qty"]))
        _start(conn, row, quantity, payload)
    elif action == "complete":
        good_qty = int(payload.get("good_qty") if payload.get("good_qty") is not None else row["in_process_qty"])
        scrap_qty = int(payload.get("scrap_qty") or 0)
        _complete(conn, row, good_qty, scrap_qty, payload)
    elif action == "hold":
        if row["state"] not in ("available", "dispatched", "acknowledged", "running"):
            raise ValueError(f"Cannot hold a {row['state']} station job")
        reason = payload.get("notes") or "Operator hold"
        event_id = _record_event(conn, row["id"], "held", row["state"], "held",
                                 payload, idempotency_key=key)
        conn.execute(
            """UPDATE execution_jobs SET state='held', resume_state=?, held_reason=?,
                  version=version+1, updated_at=? WHERE id=?""",
            (row["state"], reason, now, row["id"]),
        )
        _trace(conn, row, "work_held", "on_hold", row["in_process_qty"], payload, event_id)
    elif action == "resume":
        if row["state"] != "held":
            raise ValueError("Only a held station job can be resumed")
        target = row["resume_state"] or ("running" if row["in_process_qty"] else "available")
        if target in ("queued", "available"):
            target = "available" if _blocked_reason(row) is None else "queued"
        _record_event(conn, row["id"], "resumed", "held", target, payload,
                      idempotency_key=key)
        conn.execute(
            """UPDATE execution_jobs SET state=?, resume_state=NULL, held_reason=NULL,
                  version=version+1, updated_at=? WHERE id=?""", (target, now, row["id"]),
        )
    elif action == "cancel":
        if row["state"] in TERMINAL_STATES or row["in_process_qty"]:
            raise ValueError("Only inactive, non-terminal station jobs can be cancelled")
        _record_event(conn, row["id"], "cancelled", row["state"], "cancelled",
                      payload, idempotency_key=key)
        conn.execute(
            """UPDATE execution_jobs SET state='cancelled', version=version+1,
                  updated_at=? WHERE id=?""", (now, row["id"]),
        )
    else:
        raise ValueError(f"Unknown execution action '{action}'")
    _refresh_availability(conn)
    return _job_row(conn, execution_job_id)


def apply_action(conn: sqlite3.Connection, execution_job_id: int,
                 payload: dict, commit: bool = True) -> dict:
    conn.execute("SAVEPOINT execution_action")
    try:
        result = _apply_action(conn, execution_job_id, payload)
        conn.execute("RELEASE SAVEPOINT execution_action")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT execution_action")
        conn.execute("RELEASE SAVEPOINT execution_action")
        raise
    if commit:
        conn.commit()
    return result


def resolve_exception(conn: sqlite3.Connection, exception_id: int, status: str,
                      actor: str, notes: str | None = None) -> dict:
    if status not in ("accepted", "corrected", "ignored"):
        raise ValueError("Invalid execution-exception resolution")
    row = conn.execute("SELECT * FROM execution_exceptions WHERE id=?", (exception_id,)).fetchone()
    if not row:
        raise KeyError(f"Execution exception {exception_id} not found")
    if row["status"] != "open":
        raise ValueError(f"Execution exception is already {row['status']}")
    now = _now()
    conn.execute(
        """UPDATE execution_exceptions SET status=?, resolved_at=?, resolved_by=?,
              resolution_notes=? WHERE id=?""", (status, now, actor, notes, exception_id),
    )
    conn.commit()
    return {"id": exception_id, "status": status, "resolved_at": now, "resolved_by": actor}


def reconcile_barcode_event(conn: sqlite3.Connection, barcode_event_id: int) -> dict | None:
    event = conn.execute(
        "SELECT * FROM barcode_events WHERE id=?", (barcode_event_id,)
    ).fetchone()
    if not event:
        raise KeyError(f"Barcode event {barcode_event_id} not found")
    resolution = identity.get_barcode_resolution(conn, barcode_event_id)
    unit_id = resolution["unit_id"] if resolution else None
    object_key = resolution["unit_key"] if unit_id else event["barcode"]
    payload = {
        "source": "barcode", "evidence_type": "barcode", "evidence_id": event["id"],
        "actor": event["operator"] or "scanner", "ts": event["ts"],
        "idempotency_key": f"barcode:{event['id']}:{event['event_type']}",
        "object_key": object_key, "object_type": "unit" if unit_id else "barcode",
        "unit_id": unit_id,
    }
    if event["part_id"] and event["station"] and event["event_type"] in (
        "route_arrival", "operation_start", "operation_complete", "part_complete"
    ):
        job = conn.execute(
            """SELECT ej.id, ej.route_step_id, ej.machine_id FROM execution_jobs ej
               JOIN part_route_steps prs ON prs.id=ej.route_step_id
               JOIN machines m ON m.id=ej.machine_id
               WHERE prs.part_id=? AND m.machine_key=?
               ORDER BY ej.id DESC LIMIT 1""", (event["part_id"], event["station"]),
        ).fetchone()
        if job:
            action = "start" if event["event_type"] in ("route_arrival", "operation_start") else "complete"
            route_event = "operation_start" if action == "start" else "operation_complete"
            if unit_id and identity.route_scan_is_duplicate(
                conn, unit_id, job["route_step_id"], route_event
            ):
                identity.mark_barcode_resolution(
                    conn, event["id"], "duplicate",
                    f"{route_event} was already recorded for this unit and station",
                )
                conn.commit()
                return {"accepted": True, "duplicate": True,
                        "execution_job_id": job["id"], "unit_key": object_key}
            action_payload = {**payload, "action": action}
            if action == "start":
                action_payload["quantity"] = 1
            else:
                action_payload["good_qty"] = 1
            try:
                result = apply_action(conn, job["id"], action_payload, commit=not unit_id)
                if unit_id:
                    result["unit"] = identity.record_route_scan(
                        conn, unit_id, job["route_step_id"], event["id"], route_event,
                        event["ts"], job["machine_id"],
                    )
                    conn.commit()
                return result
            except ValueError as error:
                row = _job_row(conn, job["id"])
                _exception(conn, row, "barcode_evidence_rejected", str(error), action_payload)
                if resolution:
                    identity.mark_barcode_resolution(conn, event["id"], "conflict", str(error))
                conn.commit()
                return {"accepted": False, "reason": str(error), "execution_job_id": job["id"]}
        if not conn.execute(
            "SELECT 1 FROM machines WHERE machine_key=?", (event["station"],)
        ).fetchone():
            return None
        route_event = "operation_start" if event["event_type"] in ("route_arrival", "operation_start") else "operation_complete"
        step = conn.execute(
            """SELECT prs.id, prs.machine_id FROM part_route_steps prs
               JOIN machines m ON m.id=prs.machine_id
               WHERE prs.part_id=? AND m.machine_key=?""",
            (event["part_id"], event["station"]),
        ).fetchone()
        if unit_id and step and identity.route_scan_is_duplicate(
            conn, unit_id, step["id"], route_event
        ):
            identity.mark_barcode_resolution(
                conn, event["id"], "duplicate",
                f"{route_event} was already recorded for this unit and station",
            )
            conn.commit()
            return {"matched": True, "duplicate": True, "step_id": step["id"],
                    "unit_key": object_key}
        result = production_control.confirm_route_step(
            conn, event["part_id"], event["station"], route_event,
            "barcode", event["id"], event["ts"], event["operator"], commit=not unit_id,
        )
        if unit_id and step and result.get("matched"):
            identity.record_route_scan(
                conn, unit_id, step["id"], event["id"], route_event,
                event["ts"], step["machine_id"],
            )
            conn.commit()
        return result
    disposition = {
        "qc_pass": "conforming", "qc_fail": "non_conforming",
        "packed": "packed", "dispatched": "dispatched",
    }.get(event["event_type"])
    if disposition:
        order = conn.execute(
            "SELECT id FROM production_orders WHERE job_id=?", (event["job_id"],)
        ).fetchone() if event["job_id"] else None
        conn.execute(
            """INSERT OR IGNORE INTO traceability_events
               (object_type, object_key, production_order_id, part_id, event_type,
                action, quantity, uom, read_point, business_location, disposition,
                source, evidence_type, evidence_id, actor, idempotency_key,
                event_time, recorded_at) VALUES (?,?,?,?,?,
                'observe',1,'each',?,?,?,?,?,?,?,?,?,?)""",
            ("unit" if unit_id else "barcode", object_key,
             order["id"] if order else None, event["part_id"],
             event["event_type"], event["station"], event["station"], disposition,
             event["source"], "barcode", event["id"], event["operator"],
             payload["idempotency_key"], event["ts"], _now()),
        )
        unit = identity.record_disposition_scan(conn, event["id"], event["event_type"])
        conn.commit()
        return {"traceability_recorded": True, "disposition": disposition, "unit": unit}
    return None


def reconcile_machine_events(conn: sqlite3.Connection) -> dict:
    sync(conn)
    events = conn.execute(
        """SELECT me.id, me.part_id, me.event_type, me.ts, m.id machine_id,
                  m.machine_key
           FROM machine_events me JOIN machines m ON m.id=me.machine_id
           WHERE me.part_id IS NOT NULL AND me.event_type IN ('cycle_start','cycle_end')
             AND NOT EXISTS (SELECT 1 FROM execution_job_events eje
                             WHERE eje.evidence_type='machine_event' AND eje.evidence_id=me.id)
             AND NOT EXISTS (SELECT 1 FROM execution_exceptions ee
                             WHERE ee.evidence_type='machine_event' AND ee.evidence_id=me.id)
             AND NOT EXISTS (SELECT 1 FROM route_step_events rse
                             WHERE rse.source='machine_event' AND rse.evidence_id=me.id)
           ORDER BY me.ts, me.id"""
    ).fetchall()
    applied = fallback = exceptions = 0
    for event in events:
        job_id = conn.execute(
            """SELECT ej.id FROM execution_jobs ej
               JOIN part_route_steps prs ON prs.id=ej.route_step_id
               WHERE prs.part_id=? AND ej.machine_id=?
               ORDER BY ej.id DESC LIMIT 1""", (event["part_id"], event["machine_id"]),
        ).fetchone()
        payload = {
            "action": "start" if event["event_type"] == "cycle_start" else "complete",
            "quantity": 1, "good_qty": 1,
            "source": "machine_event", "evidence_type": "machine_event",
            "evidence_id": event["id"], "actor": "machine-agent", "ts": event["ts"],
            "idempotency_key": f"machine:{event['id']}:{event['event_type']}",
            "part_id": event["part_id"], "machine_id": event["machine_id"],
        }
        try:
            if job_id:
                apply_action(conn, job_id["id"], payload)
                applied += 1
            else:
                production_control.confirm_route_step(
                    conn, event["part_id"], event["machine_key"], event["event_type"],
                    "machine_event", event["id"], event["ts"], "machine-agent",
                )
                fallback += 1
        except ValueError as error:
            row = _job_row(conn, job_id["id"]) if job_id else None
            _exception(conn, row, "machine_evidence_rejected", str(error), payload)
            conn.commit()
            exceptions += 1
    return {"processed": len(events), "applied": applied,
            "route_fallback": fallback, "exceptions": exceptions}
