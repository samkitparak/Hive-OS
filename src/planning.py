"""Persisted digital-twin scenarios and human approval decisions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

import digital_twin
import production_control


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def factory_signature(conn: sqlite3.Connection, job_names: list[str] | None = None) -> str:
    params: list = []
    where = ""
    if job_names:
        where = f"WHERE j.job_name IN ({','.join('?' for _ in job_names)})"
        params = job_names
    orders = [dict(row) for row in conn.execute(
        f"""SELECT po.id, po.version, po.status, po.due_at, po.priority,
                   po.release_sequence, j.job_name
            FROM production_orders po JOIN jobs j ON j.id=po.job_id {where}
            ORDER BY po.id""", params
    ).fetchall()]
    models = [dict(row) for row in conn.execute(
        """SELECT id, machine_id, version, status, training_signature
           FROM cycle_models WHERE status IN ('active','candidate') ORDER BY id"""
    ).fetchall()]
    routes = conn.execute(
        """SELECT COUNT(*) count, COALESCE(MAX(updated_at), '') updated
           FROM part_route_steps"""
    ).fetchone()
    evidence = conn.execute(
        """SELECT COALESCE(MAX(id), 0) machine_event_id,
                  (SELECT COALESCE(MAX(id), 0) FROM barcode_events) barcode_event_id
           FROM machine_events"""
    ).fetchone()
    payload = {
        "orders": orders, "models": models,
        "routes": dict(routes), "evidence": dict(evidence),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def create_scenario(conn: sqlite3.Connection, payload: dict) -> dict:
    production_control.sync_all(conn)
    job_names = payload.get("job_names")
    request = {
        "job_names": job_names,
        "policies": payload.get("policies"),
        "stochastic": bool(payload.get("stochastic", False)),
        "seed": int(payload.get("seed", 1)),
    }
    result = digital_twin.compare(conn, **request)
    signature = factory_signature(conn, job_names)
    now = _now()
    cursor = conn.execute(
        """INSERT INTO planning_scenarios
           (name, created_by, request_json, result_json, readiness_json,
            input_signature, status, created_at)
           VALUES (?,?,?,?,?,?,'draft',?)""",
        (payload.get("name"), payload.get("created_by", "operator"),
         json.dumps(request, sort_keys=True), json.dumps(result, sort_keys=True),
         json.dumps(result["readiness"], sort_keys=True), signature, now),
    )
    conn.commit()
    return get_scenario(conn, cursor.lastrowid)


def get_scenario(conn: sqlite3.Connection, scenario_id: int) -> dict:
    row = conn.execute("SELECT * FROM planning_scenarios WHERE id=?", (scenario_id,)).fetchone()
    if not row:
        raise KeyError(f"Planning scenario {scenario_id} not found")
    result = dict(row)
    result["request"] = json.loads(result.pop("request_json"))
    result["result"] = json.loads(result.pop("result_json"))
    result["readiness"] = json.loads(result.pop("readiness_json"))
    result["decisions"] = [dict(item) for item in conn.execute(
        "SELECT * FROM planning_decisions WHERE scenario_id=? ORDER BY ts", (scenario_id,)
    ).fetchall()]
    return result


def list_scenarios(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        """SELECT id, name, created_by, status, selected_policy, approved_by,
                  approved_at, rejection_reason, created_at, readiness_json
           FROM planning_scenarios ORDER BY created_at DESC LIMIT ?""", (limit,)
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        readiness = json.loads(item.pop("readiness_json"))
        item["operational_recommendation"] = readiness.get("operational_recommendation", False)
        item["model_coverage"] = readiness.get("model_coverage", 0)
        item["route_coverage"] = readiness.get("observed_route_coverage", 0)
        result.append(item)
    return result


def decide(conn: sqlite3.Connection, scenario_id: int, decision: str,
           actor: str, selected_policy: str | None = None,
           notes: str | None = None) -> dict:
    scenario = get_scenario(conn, scenario_id)
    if scenario["status"] != "draft":
        raise ValueError(f"Scenario is already {scenario['status']}")
    if decision not in ("approve", "reject"):
        raise ValueError("decision must be approve or reject")
    now = _now()
    if decision == "reject":
        conn.execute(
            "UPDATE planning_scenarios SET status='rejected', rejection_reason=? WHERE id=?",
            (notes, scenario_id),
        )
        conn.execute(
            """INSERT INTO planning_decisions
               (scenario_id, decision, actor, notes, ts) VALUES (?,'reject',?,?,?)""",
            (scenario_id, actor, notes, now),
        )
        conn.commit()
        return get_scenario(conn, scenario_id)

    if not scenario["readiness"].get("operational_recommendation"):
        raise ValueError("This scenario is commissioning-only and cannot be approved for production")
    policies = {item["policy"]: item for item in scenario["result"].get("scenarios", [])}
    if not selected_policy or selected_policy not in policies:
        raise ValueError("Select one of the evaluated policies before approval")
    current_signature = factory_signature(conn, scenario["request"].get("job_names"))
    if current_signature != scenario["input_signature"]:
        conn.execute("UPDATE planning_scenarios SET status='expired' WHERE id=?", (scenario_id,))
        conn.execute(
            """INSERT INTO planning_decisions
               (scenario_id, decision, actor, selected_policy, notes, ts)
               VALUES (?,'expire',?,?,?,?)""",
            (scenario_id, actor, selected_policy,
             "Factory inputs changed after this scenario was generated", now),
        )
        conn.commit()
        raise ValueError("Factory inputs changed; generate a fresh scenario")

    chosen = policies[selected_policy]
    conn.execute("UPDATE planning_scenarios SET status='expired' WHERE status='approved'")
    conn.execute(
        """UPDATE planning_scenarios SET status='approved', selected_policy=?,
              approved_by=?, approved_at=? WHERE id=?""",
        (selected_policy, actor, now, scenario_id),
    )
    conn.execute(
        """INSERT INTO planning_decisions
           (scenario_id, decision, actor, selected_policy, notes, ts)
           VALUES (?,'approve',?,?,?,?)""",
        (scenario_id, actor, selected_policy, notes, now),
    )
    conn.execute("DELETE FROM production_schedule_items WHERE scenario_id=?", (scenario_id,))
    for position, job_name in enumerate(chosen["job_order"], start=1):
        order = conn.execute(
            """SELECT po.id, po.version FROM production_orders po
               JOIN jobs j ON j.id=po.job_id WHERE j.job_name=?""", (job_name,)
        ).fetchone()
        if not order:
            conn.rollback()
            raise ValueError(f"No production order exists for '{job_name}'")
        conn.execute(
            """INSERT INTO production_schedule_items
               (scenario_id, production_order_id, position, planned_end_s)
               VALUES (?,?,?,?)""",
            (scenario_id, order["id"], position, chosen["job_completion_s"].get(job_name)),
        )
        conn.execute(
            """UPDATE production_orders SET release_sequence=?, version=version+1,
                  updated_at=? WHERE id=?""", (position, now, order["id"]),
        )
        conn.execute(
            """INSERT INTO production_order_events
               (production_order_id, event_type, actor, payload_json, ts)
               VALUES (?,'scheduled',?,?,?)""",
            (order["id"], actor, json.dumps({"scenario_id": scenario_id,
                                             "policy": selected_policy,
                                             "position": position}), now),
        )
    conn.commit()
    return get_scenario(conn, scenario_id)


def active_schedule(conn: sqlite3.Connection) -> dict | None:
    scenario = conn.execute(
        "SELECT id FROM planning_scenarios WHERE status='approved' ORDER BY approved_at DESC LIMIT 1"
    ).fetchone()
    if not scenario:
        return None
    result = get_scenario(conn, scenario["id"])
    result["items"] = [dict(row) for row in conn.execute(
        """SELECT psi.position, psi.planned_start_s, psi.planned_end_s,
                  po.id production_order_id, po.status, po.due_at, j.job_name
           FROM production_schedule_items psi
           JOIN production_orders po ON po.id=psi.production_order_id
           JOIN jobs j ON j.id=po.job_id WHERE psi.scenario_id=?
           ORDER BY psi.position""", (scenario["id"],)
    ).fetchall()]
    return result
