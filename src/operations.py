"""Phase 1 operations workflows: downtime, maintenance, quality, rework, barcode."""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _machine_id(conn: sqlite3.Connection, machine_key: Optional[str]) -> Optional[int]:
    if not machine_key:
        return None
    row = conn.execute(
        "SELECT id FROM machines WHERE machine_key=?", (machine_key,)
    ).fetchone()
    return row["id"] if row else None


def _job_id(conn: sqlite3.Connection, job_name: Optional[str]) -> Optional[int]:
    if not job_name:
        return None
    row = conn.execute("SELECT id FROM jobs WHERE job_name=?", (job_name,)).fetchone()
    return row["id"] if row else None


def _part_id(conn: sqlite3.Connection, part_id: Optional[int],
             job_name: Optional[str], part_name: Optional[str]) -> Optional[int]:
    if part_id:
        return part_id
    if not job_name or not part_name:
        return None
    row = conn.execute(
        """SELECT p.id FROM parts p JOIN jobs j ON j.id=p.job_id
           WHERE j.job_name=? AND p.part_name=? LIMIT 1""",
        (job_name, part_name),
    ).fetchone()
    return row["id"] if row else None


def _reason_id(conn: sqlite3.Connection, reason_code: Optional[str]) -> Optional[int]:
    if not reason_code:
        return None
    row = conn.execute(
        "SELECT id FROM downtime_reasons WHERE code=?", (reason_code,)
    ).fetchone()
    return row["id"] if row else None


def _defect_type_id(conn: sqlite3.Connection, defect_code: Optional[str]) -> Optional[int]:
    if not defect_code:
        return None
    row = conn.execute(
        "SELECT id FROM defect_types WHERE code=?", (defect_code,)
    ).fetchone()
    return row["id"] if row else None


def summary(conn: sqlite3.Connection) -> dict:
    today = datetime.now(timezone.utc).date().isoformat()
    row = conn.execute(
        "SELECT COUNT(*) FROM downtime_events WHERE status='open'"
    ).fetchone()
    open_downtime = row[0] if row else 0
    row = conn.execute(
        "SELECT COUNT(*) FROM maintenance_work_orders WHERE status IN ('open','in_progress')"
    ).fetchone()
    open_work_orders = row[0] if row else 0
    row = conn.execute(
        "SELECT COUNT(*) FROM rework_tasks WHERE status IN ('open','in_progress')"
    ).fetchone()
    open_rework = row[0] if row else 0
    row = conn.execute(
        "SELECT COUNT(*) FROM quality_checks WHERE result='fail' AND ts >= ?",
        (today,),
    ).fetchone()
    defects_today = row[0] if row else 0
    row = conn.execute(
        "SELECT COUNT(*) FROM barcode_events WHERE ts >= ?", (today,)
    ).fetchone()
    scans_today = row[0] if row else 0
    return {
        "open_downtime": open_downtime,
        "open_work_orders": open_work_orders,
        "open_rework": open_rework,
        "defects_today": defects_today,
        "scans_today": scans_today,
    }


def list_downtime(conn: sqlite3.Connection, status: Optional[str] = None) -> list[dict]:
    where = "WHERE de.status=?" if status else ""
    params = (status,) if status else ()
    rows = conn.execute(
        f"""SELECT de.id, m.machine_key, m.name machine_name, dr.code reason_code,
                  dr.label reason_label, de.status, de.notes, de.started_at, de.ended_at
            FROM downtime_events de
            LEFT JOIN machines m ON m.id=de.machine_id
            LEFT JOIN downtime_reasons dr ON dr.id=de.reason_id
            {where}
            ORDER BY de.started_at DESC LIMIT 100""",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def create_downtime(conn: sqlite3.Connection, payload: dict) -> dict:
    machine_id = _machine_id(conn, payload.get("machine_key"))
    reason_id = _reason_id(conn, payload.get("reason_code"))
    if payload.get("machine_key") and machine_id is None:
        raise ValueError(f"Unknown machine '{payload['machine_key']}'")
    if payload.get("reason_code") and reason_id is None:
        raise ValueError(f"Unknown downtime reason '{payload['reason_code']}'")
    started_at = payload.get("started_at") or _now()
    cur = conn.execute(
        """INSERT INTO downtime_events
           (machine_id, reason_id, status, notes, started_at)
           VALUES (?,?,?,?,?)""",
        (machine_id, reason_id, payload.get("status", "open"),
         payload.get("notes"), started_at),
    )
    conn.commit()
    return {"id": cur.lastrowid, **payload, "started_at": started_at}


def close_downtime(conn: sqlite3.Connection, downtime_id: int,
                   payload: Optional[dict] = None) -> dict:
    payload = payload or {}
    ended_at = payload.get("ended_at") or _now()
    cur = conn.execute(
        """UPDATE downtime_events
           SET status='closed', ended_at=?, notes=COALESCE(?, notes)
           WHERE id=?""",
        (ended_at, payload.get("notes"), downtime_id),
    )
    if cur.rowcount == 0:
        raise KeyError(f"Downtime event {downtime_id} not found")
    conn.commit()
    return {"id": downtime_id, "status": "closed", "ended_at": ended_at}


def list_work_orders(conn: sqlite3.Connection, status: Optional[str] = None) -> list[dict]:
    where = "WHERE wo.status=?" if status else ""
    params = (status,) if status else ()
    rows = conn.execute(
        f"""SELECT wo.id, m.machine_key, m.name machine_name, wo.title, wo.description,
                  wo.priority, wo.status, wo.source, wo.due_date, wo.created_at, wo.closed_at
            FROM maintenance_work_orders wo
            LEFT JOIN machines m ON m.id=wo.machine_id
            {where}
            ORDER BY wo.created_at DESC LIMIT 100""",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def create_work_order(conn: sqlite3.Connection, payload: dict) -> dict:
    machine_id = _machine_id(conn, payload.get("machine_key"))
    if payload.get("machine_key") and machine_id is None:
        raise ValueError(f"Unknown machine '{payload['machine_key']}'")
    cur = conn.execute(
        """INSERT INTO maintenance_work_orders
           (machine_id, title, description, priority, status, source, due_date)
           VALUES (?,?,?,?,?,?,?)""",
        (machine_id, payload["title"], payload.get("description"),
         payload.get("priority", "medium"), payload.get("status", "open"),
         payload.get("source", "manual"), payload.get("due_date")),
    )
    conn.commit()
    return {"id": cur.lastrowid, **payload}


def list_quality_checks(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    rows = conn.execute(
        """SELECT qc.id, j.job_name, p.part_name, m.machine_key, dt.code defect_code,
                  dt.label defect_label, qc.result, qc.inspector, qc.notes, qc.source, qc.ts
           FROM quality_checks qc
           LEFT JOIN jobs j ON j.id=qc.job_id
           LEFT JOIN parts p ON p.id=qc.part_id
           LEFT JOIN machines m ON m.id=qc.machine_id
           LEFT JOIN defect_types dt ON dt.id=qc.defect_type_id
           ORDER BY qc.ts DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def create_quality_check(conn: sqlite3.Connection, payload: dict,
                         commit: bool = True) -> dict:
    job_id = _job_id(conn, payload.get("job_name"))
    part_id = _part_id(conn, payload.get("part_id"), payload.get("job_name"), payload.get("part_name"))
    machine_id = _machine_id(conn, payload.get("machine_key"))
    defect_type_id = _defect_type_id(conn, payload.get("defect_code"))
    if payload.get("machine_key") and machine_id is None:
        raise ValueError(f"Unknown machine '{payload['machine_key']}'")
    if payload.get("defect_code") and defect_type_id is None:
        raise ValueError(f"Unknown defect type '{payload['defect_code']}'")
    ts = payload.get("ts") or _now()
    cur = conn.execute(
        """INSERT INTO quality_checks
           (job_id, part_id, machine_id, defect_type_id, result, inspector,
            notes, photo_path, source, ts)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (job_id, part_id, machine_id, defect_type_id, payload["result"],
         payload.get("inspector"), payload.get("notes"), payload.get("photo_path"),
         payload.get("source", "manual"), ts),
    )
    check_id = cur.lastrowid
    if payload["result"] in ("fail", "rework"):
        conn.execute(
            """INSERT INTO rework_tasks
               (quality_check_id, job_id, part_id, assigned_area, notes)
               VALUES (?,?,?,?,?)""",
            (check_id, job_id, part_id, payload.get("assigned_area"), payload.get("notes")),
        )
    if commit:
        conn.commit()
    return {"id": check_id, **payload, "ts": ts}


def list_rework(conn: sqlite3.Connection, status: Optional[str] = None) -> list[dict]:
    where = "WHERE rt.status=?" if status else ""
    params = (status,) if status else ()
    rows = conn.execute(
        f"""SELECT rt.id, j.job_name, p.part_name, rt.assigned_area, rt.status,
                  rt.notes, rt.created_at, rt.closed_at, qc.result
            FROM rework_tasks rt
            LEFT JOIN jobs j ON j.id=rt.job_id
            LEFT JOIN parts p ON p.id=rt.part_id
            LEFT JOIN quality_checks qc ON qc.id=rt.quality_check_id
            {where}
            ORDER BY rt.created_at DESC LIMIT 100""",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def close_rework(conn: sqlite3.Connection, rework_id: int,
                 payload: Optional[dict] = None) -> dict:
    payload = payload or {}
    closed_at = payload.get("closed_at") or _now()
    cur = conn.execute(
        """UPDATE rework_tasks
           SET status='done', closed_at=?, notes=COALESCE(?, notes)
           WHERE id=?""",
        (closed_at, payload.get("notes"), rework_id),
    )
    if cur.rowcount == 0:
        raise KeyError(f"Rework task {rework_id} not found")
    conn.commit()
    return {"id": rework_id, "status": "done", "closed_at": closed_at}


def list_barcode_events(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    rows = conn.execute(
        """SELECT be.id, be.barcode, j.job_name, p.part_name, be.station,
                  be.event_type, be.operator, be.source, be.ts
           FROM barcode_events be
           LEFT JOIN jobs j ON j.id=be.job_id
           LEFT JOIN parts p ON p.id=be.part_id
           ORDER BY be.ts DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def create_barcode_event(conn: sqlite3.Connection, payload: dict) -> dict:
    job_id = _job_id(conn, payload.get("job_name"))
    part_id = _part_id(conn, payload.get("part_id"), payload.get("job_name"), payload.get("part_name"))
    ts = payload.get("ts") or _now()
    raw_payload = payload.get("raw_payload")
    if raw_payload is not None and not isinstance(raw_payload, str):
        raw_payload = json.dumps(raw_payload)
    try:
        cur = conn.execute(
            """INSERT INTO barcode_events
               (barcode, job_id, part_id, station, event_type, operator, source, raw_payload, ts)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (payload["barcode"], job_id, part_id, payload.get("station"),
             payload.get("event_type", "unknown"), payload.get("operator"),
             payload.get("source", "manual"), raw_payload, ts),
        )
        if payload.get("event_type") in ("qc_pass", "qc_fail"):
            create_quality_check(conn, {
                "job_name": payload.get("job_name"),
                "part_id": part_id,
                "result": "pass" if payload["event_type"] == "qc_pass" else "fail",
                "inspector": payload.get("operator"),
                "source": payload.get("source", "barcode"),
                "notes": payload.get("notes"),
                "ts": ts,
            }, commit=False)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    result = {"id": cur.lastrowid, **payload, "job_id": job_id, "part_id": part_id, "ts": ts}
    route_events = {
        "route_arrival": "operation_start",
        "operation_start": "operation_start",
        "operation_complete": "operation_complete",
        "part_complete": "operation_complete",
    }
    if part_id and payload.get("station") and payload.get("event_type") in route_events:
        machine = conn.execute(
            "SELECT machine_key FROM machines WHERE machine_key=?", (payload["station"],)
        ).fetchone()
        if machine:
            import production_control
            result["route_confirmation"] = production_control.confirm_route_step(
                conn, part_id, machine["machine_key"], route_events[payload["event_type"]],
                "barcode", cur.lastrowid, ts, payload.get("operator"),
            )
    return result
