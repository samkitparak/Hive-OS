"""Production-order lifecycle and planned/observed route reconciliation."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import routing

ORDER_STATUSES = ("draft", "ready", "released", "in_progress", "hold", "completed", "cancelled")
TRANSITIONS = {
    "draft": {"ready", "cancelled"},
    "ready": {"draft", "released", "hold", "cancelled"},
    "released": {"in_progress", "hold", "cancelled"},
    "in_progress": {"hold", "completed", "cancelled"},
    "hold": {"draft", "ready", "released", "in_progress", "cancelled"},
    "completed": set(),
    "cancelled": {"draft"},
}


class VersionConflict(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_datetime(value: str | None, field: str) -> str | None:
    if value is None or value == "":
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO-8601 date-time") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone offset")
    return parsed.astimezone(timezone.utc).isoformat()


def sync_orders(conn: sqlite3.Connection) -> dict:
    now = _now()
    jobs = conn.execute(
        """SELECT j.id FROM jobs j LEFT JOIN production_orders po ON po.job_id=j.id
           WHERE po.id IS NULL ORDER BY j.id"""
    ).fetchall()
    for job in jobs:
        cursor = conn.execute(
            """INSERT INTO production_orders
               (job_id, status, priority, source, created_at, updated_at)
               VALUES (?,'draft',50,'cv_import',?,?)""", (job["id"], now, now)
        )
        conn.execute(
            """INSERT INTO production_order_events
               (production_order_id, event_type, to_status, actor, payload_json, ts)
               VALUES (?,'created','draft','system',?,?)""",
            (cursor.lastrowid, json.dumps({"source": "cv_import"}), now),
        )
    conn.commit()
    return {"created": len(jobs)}


def sync_routes(conn: sqlite3.Connection, job_id: int | None = None) -> dict:
    where = "AND p.job_id=?" if job_id is not None else ""
    params = (job_id,) if job_id is not None else ()
    parts = conn.execute(
        f"""SELECT p.* FROM parts p
            WHERE NOT EXISTS (SELECT 1 FROM part_route_steps prs WHERE prs.part_id=p.id)
            {where} ORDER BY p.id""", params
    ).fetchall()
    machine_ids = {row["machine_key"]: row["id"] for row in conn.execute(
        "SELECT id, machine_key FROM machines"
    ).fetchall()}
    now = _now()
    step_count = 0
    for row in parts:
        part = dict(row)
        route_info = routing.part_route(conn, part)
        source = "observed" if route_info["source"] == "part_history" else "cv_feature"
        confidence = route_info["confidence"]
        for index, machine_key in enumerate(route_info["machines"], start=1):
            machine_id = machine_ids.get(machine_key)
            if machine_id is None:
                continue
            conn.execute(
                """INSERT INTO part_route_steps
                   (part_id, step_index, machine_id, source, confidence,
                    required_qty, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (part["id"], index, machine_id, source, confidence,
                 max(int(part.get("qty") or 1), 1), now, now),
            )
            step_count += 1
    conn.commit()
    return {"parts_created": len(parts), "steps_created": step_count}


def sync_all(conn: sqlite3.Connection) -> dict:
    return {"orders": sync_orders(conn), "routes": sync_routes(conn)}


def route_summary(conn: sqlite3.Connection, job_id: int) -> dict:
    row = conn.execute(
        """SELECT COUNT(DISTINCT p.id) total_parts,
                  COUNT(DISTINCT CASE WHEN prs.id IS NOT NULL THEN p.id END) planned_parts,
                  COUNT(prs.id) total_steps,
                  SUM(CASE WHEN prs.status='confirmed' THEN 1 ELSE 0 END) confirmed_steps,
                  SUM(CASE WHEN prs.source='observed' THEN 1 ELSE 0 END) observed_steps,
                  SUM(CASE WHEN prs.source='manual' THEN 1 ELSE 0 END) manual_steps
           FROM parts p LEFT JOIN part_route_steps prs ON prs.part_id=p.id
           WHERE p.job_id=?""", (job_id,)
    ).fetchone()
    exceptions = conn.execute(
        """SELECT COUNT(*) count FROM route_exceptions re
           JOIN parts p ON p.id=re.part_id WHERE p.job_id=? AND re.status='open'""",
        (job_id,),
    ).fetchone()["count"]
    total_parts = row["total_parts"] or 0
    planned_parts = row["planned_parts"] or 0
    total_steps = row["total_steps"] or 0
    confirmed_steps = row["confirmed_steps"] or 0
    return {
        "total_parts": total_parts,
        "planned_parts": planned_parts,
        "coverage": round(planned_parts / total_parts, 4) if total_parts else 0,
        "total_steps": total_steps,
        "confirmed_steps": confirmed_steps,
        "confirmation": round(confirmed_steps / total_steps, 4) if total_steps else 0,
        "observed_steps": row["observed_steps"] or 0,
        "manual_steps": row["manual_steps"] or 0,
        "open_exceptions": exceptions,
    }


def _order_row(conn: sqlite3.Connection, order_id: int) -> dict:
    row = conn.execute(
        """SELECT po.*, j.job_name, j.job_date source_job_date, j.total_parts,
                  c.name client_name
           FROM production_orders po JOIN jobs j ON j.id=po.job_id
           LEFT JOIN clients c ON c.id=j.client_id WHERE po.id=?""", (order_id,)
    ).fetchone()
    if not row:
        raise KeyError(f"Production order {order_id} not found")
    result = dict(row)
    result["route"] = route_summary(conn, result["job_id"])
    return result


def list_orders(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    where = "WHERE po.status=?" if status else ""
    params = (status,) if status else ()
    rows = conn.execute(
        f"""SELECT po.id FROM production_orders po {where}
            ORDER BY CASE po.status
                WHEN 'in_progress' THEN 1 WHEN 'released' THEN 2 WHEN 'ready' THEN 3
                WHEN 'hold' THEN 4 WHEN 'draft' THEN 5 ELSE 6 END,
                po.release_sequence, po.due_at, po.priority DESC, po.id""", params
    ).fetchall()
    return [_order_row(conn, row["id"]) for row in rows]


def update_order(conn: sqlite3.Connection, order_id: int, payload: dict) -> dict:
    current = _order_row(conn, order_id)
    expected_version = payload.get("expected_version")
    if expected_version is not None and expected_version != current["version"]:
        raise VersionConflict(
            f"Production order changed from version {expected_version} to {current['version']}"
        )
    updates = {}
    for field in ("external_order_id", "notes", "source"):
        if field in payload:
            updates[field] = payload[field]
    for field in ("due_at", "planned_start_at"):
        if field in payload:
            updates[field] = _validate_datetime(payload[field], field)
    if "priority" in payload:
        priority = int(payload["priority"])
        if priority < 1 or priority > 100:
            raise ValueError("priority must be between 1 and 100")
        updates["priority"] = priority

    target = payload.get("status")
    if target is not None:
        if target not in ORDER_STATUSES:
            raise ValueError(f"Unknown production-order status '{target}'")
        if target != current["status"] and target not in TRANSITIONS[current["status"]]:
            raise ValueError(f"Cannot move production order from {current['status']} to {target}")
        effective_due = updates.get("due_at", current["due_at"])
        if target in ("ready", "released"):
            if not effective_due:
                raise ValueError("Set a timezone-aware due date before readying or releasing an order")
            sync_routes(conn, current["job_id"])
            route = route_summary(conn, current["job_id"])
            if route["coverage"] < 1:
                raise ValueError("Every part needs a planned route before release")
        updates["status"] = target
        if target == "released" and current["status"] != "released":
            updates["released_at"] = _now()
            updates["released_by"] = payload.get("actor", "operator")
            if current["release_sequence"] is None:
                updates["release_sequence"] = conn.execute(
                    "SELECT COALESCE(MAX(release_sequence), 0) + 1 value FROM production_orders"
                ).fetchone()["value"]

    if not updates:
        return current
    updates["version"] = current["version"] + 1
    updates["updated_at"] = _now()
    columns = ", ".join(f"{key}=?" for key in updates)
    values = list(updates.values()) + [order_id, current["version"]]
    cursor = conn.execute(
        f"UPDATE production_orders SET {columns} WHERE id=? AND version=?", values
    )
    if cursor.rowcount != 1:
        conn.rollback()
        raise VersionConflict("Production order was changed by another operator")
    actor = payload.get("actor", "operator")
    target_status = updates.get("status", current["status"])
    event_type = "status_changed" if target_status != current["status"] else "updated"
    conn.execute(
        """INSERT INTO production_order_events
           (production_order_id, event_type, from_status, to_status, actor,
            notes, payload_json, ts) VALUES (?,?,?,?,?,?,?,?)""",
        (order_id, event_type, current["status"], target_status, actor,
         payload.get("notes"), json.dumps(updates, sort_keys=True), updates["updated_at"]),
    )
    conn.commit()
    return _order_row(conn, order_id)


def get_job_routes(conn: sqlite3.Connection, job_name: str) -> dict:
    job = conn.execute("SELECT id FROM jobs WHERE job_name=?", (job_name,)).fetchone()
    if not job:
        raise KeyError(f"Job '{job_name}' not found")
    sync_routes(conn, job["id"])
    rows = conn.execute(
        """SELECT prs.id, prs.part_id, p.part_name, p.qty, prs.step_index,
                  m.machine_key, m.name machine_name, prs.source, prs.confidence,
                  prs.required, prs.required_qty, prs.confirmed_qty, prs.status,
                  prs.confirmed_at, prs.confirmed_by
           FROM part_route_steps prs JOIN parts p ON p.id=prs.part_id
           JOIN machines m ON m.id=prs.machine_id WHERE p.job_id=?
           ORDER BY p.id, prs.step_index""", (job["id"],)
    ).fetchall()
    return {"job_name": job_name, "summary": route_summary(conn, job["id"]),
            "steps": [dict(row) for row in rows]}


def replace_part_route(conn: sqlite3.Connection, part_id: int, machine_keys: list[str],
                       actor: str, notes: str | None = None) -> dict:
    if not machine_keys or len(machine_keys) != len(set(machine_keys)):
        raise ValueError("A route needs one or more unique machine keys")
    part = conn.execute(
        """SELECT p.*, po.status order_status FROM parts p
           LEFT JOIN production_orders po ON po.job_id=p.job_id WHERE p.id=?""", (part_id,)
    ).fetchone()
    if not part:
        raise KeyError(f"Part {part_id} not found")
    if part["order_status"] in ("released", "in_progress", "completed"):
        raise ValueError("Hold the production order before changing its route")
    evidence = conn.execute(
        """SELECT COUNT(*) count FROM route_step_events rse
           JOIN part_route_steps prs ON prs.id=rse.route_step_id
           WHERE prs.part_id=? AND rse.event_type!='route_defined'""", (part_id,)
    ).fetchone()["count"]
    if evidence:
        raise ValueError("A route with execution evidence cannot be replaced; resolve it as an exception")
    placeholders = ",".join("?" for _ in machine_keys)
    machines = conn.execute(
        f"SELECT id, machine_key FROM machines WHERE machine_key IN ({placeholders})",
        machine_keys,
    ).fetchall()
    machine_ids = {row["machine_key"]: row["id"] for row in machines}
    missing = [key for key in machine_keys if key not in machine_ids]
    if missing:
        raise ValueError(f"Unknown machines: {', '.join(missing)}")
    now = _now()
    conn.execute("DELETE FROM part_route_steps WHERE part_id=?", (part_id,))
    for index, key in enumerate(machine_keys, start=1):
        cursor = conn.execute(
            """INSERT INTO part_route_steps
               (part_id, step_index, machine_id, source, confidence, required_qty,
                confirmed_by, created_at, updated_at)
               VALUES (?,?,?,'manual','confirmed',?,?,?,?)""",
            (part_id, index, machine_ids[key], max(int(part["qty"] or 1), 1), actor, now, now),
        )
        conn.execute(
            """INSERT INTO route_step_events
               (route_step_id, event_type, to_status, source, actor, notes, ts)
               VALUES (?,'route_defined','planned','manual',?,?,?)""",
            (cursor.lastrowid, actor, notes, now),
        )
    conn.commit()
    return {"part_id": part_id, "machines": machine_keys, "source": "manual"}


def _add_exception(conn: sqlite3.Connection, part_id: int, expected_step_id: int | None,
                   machine_id: int | None, source: str, evidence_id: int,
                   exception_type: str, ts: str, details: str) -> None:
    machine_event_id = evidence_id if source == "machine_event" else None
    barcode_event_id = evidence_id if source == "barcode" else None
    exists = conn.execute(
        """SELECT 1 FROM route_exceptions
           WHERE part_id=? AND exception_type=?
             AND COALESCE(machine_event_id, -1)=COALESCE(?, -1)
             AND COALESCE(barcode_event_id, -1)=COALESCE(?, -1)""",
        (part_id, exception_type, machine_event_id, barcode_event_id),
    ).fetchone()
    if not exists:
        conn.execute(
            """INSERT INTO route_exceptions
               (part_id, expected_step_id, observed_machine_id, machine_event_id,
                barcode_event_id, exception_type, details, ts)
               VALUES (?,?,?,?,?,?,?,?)""",
            (part_id, expected_step_id, machine_id, machine_event_id,
             barcode_event_id, exception_type, details, ts),
        )


def confirm_route_step(conn: sqlite3.Connection, part_id: int, machine_key: str,
                       event_type: str, source: str, evidence_id: int,
                       ts: str, actor: str | None = None) -> dict:
    machine = conn.execute(
        "SELECT id FROM machines WHERE machine_key=?", (machine_key,)
    ).fetchone()
    if not machine:
        raise ValueError(f"Unknown route station '{machine_key}'")
    part = conn.execute("SELECT job_id FROM parts WHERE id=?", (part_id,)).fetchone()
    if not part:
        raise ValueError(f"Unknown part {part_id}")
    sync_routes(conn, part["job_id"])
    steps = conn.execute(
        "SELECT * FROM part_route_steps WHERE part_id=? ORDER BY step_index", (part_id,)
    ).fetchall()
    expected = next((row for row in steps if row["required"] and row["status"] not in ("confirmed", "skipped")), None)
    matched = next((row for row in steps if row["machine_id"] == machine["id"]), None)
    if not matched:
        _add_exception(conn, part_id, expected["id"] if expected else None, machine["id"],
                       source, evidence_id, "unexpected_machine", ts,
                       f"Observed {machine_key}, which is not in the planned route")
        conn.commit()
        return {"matched": False, "exception": "unexpected_machine"}
    if expected and matched["id"] != expected["id"]:
        _add_exception(conn, part_id, expected["id"], machine["id"], source,
                       evidence_id, "out_of_sequence", ts,
                       f"Observed step {matched['step_index']} before step {expected['step_index']}")

    completion = event_type in ("cycle_end", "operation_complete", "part_complete")
    target_status = "confirmed" if completion else "started"
    before = conn.total_changes
    conn.execute(
        """INSERT OR IGNORE INTO route_step_events
           (route_step_id, event_type, from_status, to_status, source,
            evidence_id, actor, ts) VALUES (?,?,?,?,?,?,?,?)""",
        (matched["id"], event_type, matched["status"], target_status,
         source, evidence_id, actor, ts),
    )
    inserted = conn.total_changes > before
    if inserted:
        confirmed_qty = matched["confirmed_qty"] + (1 if completion else 0)
        status = ("confirmed" if confirmed_qty >= matched["required_qty"]
                  else ("started" if matched["status"] == "planned" else matched["status"]))
        conn.execute(
            """UPDATE part_route_steps SET status=?, confirmed_qty=?, confidence=?,
                  confirmed_event_id=COALESCE(?, confirmed_event_id),
                  confirmed_barcode_id=COALESCE(?, confirmed_barcode_id),
                  confirmed_at=?, confirmed_by=?, updated_at=? WHERE id=?""",
            (status, confirmed_qty, "confirmed" if completion else matched["confidence"],
             evidence_id if source == "machine_event" else None,
             evidence_id if source == "barcode" else None,
             ts if completion else matched["confirmed_at"], actor, _now(), matched["id"]),
        )
    conn.commit()
    _refresh_order_state(conn, part["job_id"], actor or "system")
    return {"matched": True, "step_id": matched["id"], "event_recorded": inserted,
            "exception": "out_of_sequence" if expected and matched["id"] != expected["id"] else None}


def _refresh_order_state(conn: sqlite3.Connection, job_id: int, actor: str) -> None:
    order = conn.execute("SELECT * FROM production_orders WHERE job_id=?", (job_id,)).fetchone()
    if not order or order["status"] not in ("released", "in_progress"):
        return
    counts = conn.execute(
        """SELECT COUNT(*) total,
                  SUM(CASE WHEN prs.status='confirmed' THEN 1 ELSE 0 END) confirmed,
                  SUM(CASE WHEN prs.status IN ('started','confirmed') THEN 1 ELSE 0 END) touched
           FROM part_route_steps prs JOIN parts p ON p.id=prs.part_id
           WHERE p.job_id=? AND prs.required=1""", (job_id,)
    ).fetchone()
    target = None
    if counts["total"] and counts["confirmed"] == counts["total"]:
        target = "completed"
    elif counts["touched"] and order["status"] == "released":
        target = "in_progress"
    if target:
        update_order(conn, order["id"], {
            "status": target, "actor": actor, "expected_version": order["version"],
            "notes": "Route evidence advanced the production order automatically",
        })


def reconcile_machine_events(conn: sqlite3.Connection) -> dict:
    events = conn.execute(
        """SELECT me.id, me.part_id, me.event_type, me.ts, m.machine_key
           FROM machine_events me JOIN machines m ON m.id=me.machine_id
           WHERE me.part_id IS NOT NULL AND me.event_type IN ('cycle_start','cycle_end')
             AND NOT EXISTS (
               SELECT 1 FROM route_step_events rse
               WHERE rse.source='machine_event' AND rse.evidence_id=me.id)
           ORDER BY me.ts, me.id"""
    ).fetchall()
    matched = exceptions = 0
    for event in events:
        result = confirm_route_step(
            conn, event["part_id"], event["machine_key"], event["event_type"],
            "machine_event", event["id"], event["ts"], "machine-agent",
        )
        matched += result["matched"]
        exceptions += bool(result["exception"])
    return {"processed": len(events), "matched": matched, "exceptions": exceptions}


def list_exceptions(conn: sqlite3.Connection, status: str = "open") -> list[dict]:
    rows = conn.execute(
        """SELECT re.id, re.exception_type, re.status, re.details, re.ts,
                  j.job_name, p.part_name, em.machine_key expected_machine,
                  om.machine_key observed_machine
           FROM route_exceptions re JOIN parts p ON p.id=re.part_id
           JOIN jobs j ON j.id=p.job_id
           LEFT JOIN part_route_steps prs ON prs.id=re.expected_step_id
           LEFT JOIN machines em ON em.id=prs.machine_id
           LEFT JOIN machines om ON om.id=re.observed_machine_id
           WHERE re.status=? ORDER BY re.ts DESC""", (status,)
    ).fetchall()
    return [dict(row) for row in rows]


def readiness(conn: sqlite3.Connection) -> dict:
    orders = conn.execute(
        """SELECT COUNT(*) total,
                  SUM(CASE WHEN status IN ('ready','released','in_progress') THEN 1 ELSE 0 END) active,
                  SUM(CASE WHEN status IN ('released','in_progress') THEN 1 ELSE 0 END) released,
                  SUM(CASE WHEN status IN ('ready','released','in_progress') AND due_at IS NULL THEN 1 ELSE 0 END) missing_due
           FROM production_orders"""
    ).fetchone()
    route = conn.execute(
        """SELECT COUNT(*) steps,
                  COUNT(DISTINCT prs.part_id) planned_parts,
                  COUNT(DISTINCT p.id) required_parts,
                  COUNT(DISTINCT prs.machine_id) route_machines
           FROM production_orders po JOIN parts p ON p.job_id=po.job_id
           LEFT JOIN part_route_steps prs ON prs.part_id=p.id AND prs.required=1
           WHERE po.status IN ('ready','released','in_progress')"""
    ).fetchone()
    modeled = conn.execute(
        """SELECT COUNT(DISTINCT prs.machine_id) count
           FROM production_orders po JOIN parts p ON p.job_id=po.job_id
           JOIN part_route_steps prs ON prs.part_id=p.id AND prs.required=1
           JOIN cycle_models cm ON cm.machine_id=prs.machine_id AND cm.status='active'
           WHERE po.status IN ('ready','released','in_progress')"""
    ).fetchone()["count"]
    exceptions = conn.execute(
        "SELECT COUNT(*) count FROM route_exceptions WHERE status='open'"
    ).fetchone()["count"]
    schedule = conn.execute(
        "SELECT id FROM planning_scenarios WHERE status='approved' LIMIT 1"
    ).fetchone()
    active = orders["active"] or 0
    required_parts = route["required_parts"] or 0
    planned_parts = route["planned_parts"] or 0
    route_machines = route["route_machines"] or 0
    checks = [
        {"key": "work", "label": "Work selected", "passed": active > 0,
         "detail": f"{active} ready or released orders"},
        {"key": "due_dates", "label": "Due dates", "passed": active > 0 and not (orders["missing_due"] or 0),
         "detail": f"{orders['missing_due'] or 0} active orders missing due time"},
        {"key": "routes", "label": "Planned routes", "passed": required_parts > 0 and planned_parts == required_parts,
         "detail": f"{planned_parts} of {required_parts} active parts planned"},
        {"key": "models", "label": "Cycle models", "passed": route_machines > 0 and modeled == route_machines,
         "detail": f"{modeled} of {route_machines} route machines modeled"},
        {"key": "exceptions", "label": "Route exceptions", "passed": exceptions == 0,
         "detail": f"{exceptions} unresolved deviations"},
        {"key": "schedule", "label": "Approved schedule", "passed": schedule is not None,
         "detail": "approved" if schedule else "not approved"},
    ]
    control_ready = all(check["passed"] for check in checks if check["key"] in (
        "work", "due_dates", "routes", "exceptions"
    ))
    optimization_ready = control_ready and all(check["passed"] for check in checks)
    return {
        "status": "optimization_ready" if optimization_ready else "control_ready" if control_ready else "commissioning",
        "control_ready": control_ready,
        "optimization_ready": optimization_ready,
        "checks": checks,
        "summary": {"total_orders": orders["total"] or 0, "active_orders": active,
                    "released_orders": orders["released"] or 0,
                    "open_exceptions": exceptions},
    }


def resolve_exception(conn: sqlite3.Connection, exception_id: int, status: str,
                      actor: str, notes: str | None = None) -> dict:
    if status not in ("accepted", "ignored", "corrected"):
        raise ValueError("Invalid route-exception resolution")
    row = conn.execute("SELECT * FROM route_exceptions WHERE id=?", (exception_id,)).fetchone()
    if not row:
        raise KeyError(f"Route exception {exception_id} not found")
    if row["status"] != "open":
        raise ValueError(f"Route exception is already {row['status']}")
    resolved_at = _now()
    conn.execute(
        """UPDATE route_exceptions SET status=?, details=CASE WHEN ? IS NULL THEN details
              ELSE details || ' | Resolution: ' || ? END, resolved_at=?, resolved_by=?
           WHERE id=?""",
        (status, notes, notes, resolved_at, actor, exception_id),
    )
    conn.commit()
    return {"id": exception_id, "status": status, "resolved_at": resolved_at,
            "resolved_by": actor}
