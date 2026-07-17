"""Rolling-horizon recovery trigger, stability, residual, and approval tests."""

import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from db import init_db
import alerting
import digital_twin
import execution
import optimization
import planning
import production_control
import recovery
import resources


def _recovery_factory():
    conn = init_db(":memory:", check_same_thread=False)
    machine_id = conn.execute(
        "SELECT id FROM machines WHERE machine_key='gabbiani_pt80'"
    ).fetchone()["id"]
    conn.execute(
        """INSERT INTO cycle_models
           (machine_id,version,training_signature,sample_count,train_count,
            validation_count,inlier_count,coefficients_json,identified_features_json,
            mae_s,mape,r2,residual_cv,confidence,status,trained_at)
           VALUES (?,1,'recovery-model',60,48,12,48,?,'["base_s"]',10,.05,.9,.1,
                   'high','active',?)""",
        (machine_id, json.dumps({"base_s": 1000}), datetime.now(timezone.utc).isoformat()),
    )
    for index in range(4):
        conn.execute(
            "INSERT INTO jobs (job_name,total_parts) VALUES (?,1)",
            (f"REC-{index + 1}",),
        )
        job_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
        conn.execute(
            """INSERT INTO parts
               (job_id,part_name,qty,material,length_mm,width_mm)
               VALUES (?,'Panel',1,'A',1000,500)""", (job_id,),
        )
    conn.commit()
    production_control.sync_all(conn)
    resources.sync_defaults(conn)
    material = resources.snapshot(conn)["materials"][0]
    resources.set_material_stock(conn, material["material_key"], {
        "on_hand_sheets": 20, "verified": True, "actor": "test",
    })
    resources.update_labor_role(conn, "cutting_operator", {
        "headcount": 1, "verified": True, "actor": "test",
    })
    resources.update_tool_pool(conn, "cutting_tooling", {
        "total_qty": 1, "available_qty": 1, "verified": True, "actor": "test",
    })
    resources.update_machine_profile(conn, "gabbiani_pt80", {
        "role_key": "cutting_operator", "labor_qty": 1,
        "pool_key": "cutting_tooling", "tool_qty": 1,
        "machine_capacity": 1, "verified": True, "actor": "test",
    })
    resources.update_factory_calendar(conn, {
        "weekdays": list(range(7)), "start_time": "00:00", "end_time": "23:59",
        "timezone": "UTC", "verified": True, "actor": "test",
    })
    now = datetime.now(timezone.utc)
    for order in production_control.list_orders(conn):
        part_id = conn.execute(
            "SELECT id FROM parts WHERE job_id=?", (order["job_id"],)
        ).fetchone()["id"]
        production_control.replace_part_route(
            conn, part_id, ["gabbiani_pt80"], "planner", "Confirmed recovery route"
        )
        due = now + (timedelta(seconds=2500)
                     if order["job_name"] == "REC-4" else timedelta(days=1))
        production_control.update_order(conn, order["id"], {
            "due_at": due.isoformat(), "status": "ready", "actor": "planner",
            "expected_version": order["version"],
        })
    scenario = planning.create_scenario(conn, {
        "created_by": "planner", "policies": ["fifo"],
    })
    planning.decide(conn, scenario["id"], "approve", "supervisor", "fifo")
    first = production_control.list_orders(conn)[0]
    production_control.update_order(conn, first["id"], {
        "status": "released", "actor": "supervisor",
        "expected_version": first["version"],
    })
    execution.sync(conn)
    first_job = execution.list_jobs(conn)[0]
    first_job = execution.apply_action(conn, first_job["id"], {
        "action": "dispatch", "actor": "lead", "expected_version": first_job["version"],
    })
    execution.apply_action(conn, first_job["id"], {
        "action": "hold", "actor": "lead", "notes": "Awaiting blade check",
        "expected_version": first_job["version"],
    })
    return conn, scenario["id"]


def test_residual_twin_removes_completed_quantities():
    conn, _ = _recovery_factory()
    held = execution.list_jobs(conn)[0]
    held = execution.apply_action(conn, held["id"], {
        "action": "resume", "actor": "lead", "expected_version": held["version"],
    })
    held = execution.apply_action(conn, held["id"], {
        "action": "acknowledge", "actor": "cutter", "expected_version": held["version"],
    })
    held = execution.apply_action(conn, held["id"], {
        "action": "start", "quantity": 1, "actor": "cutter",
        "expected_version": held["version"],
    })
    execution.apply_action(conn, held["id"], {
        "action": "complete", "good_qty": 1, "actor": "cutter",
        "expected_version": held["version"],
    })
    remaining_names = ["REC-2", "REC-3", "REC-4"]
    result = digital_twin.compare_orders(
        conn, {"current": remaining_names}, job_names=remaining_names,
    )
    assert result["mode"] == "deterministic_residual"
    assert result["readiness"]["part_count"] == 3
    assert result["scenarios"][0]["completed_parts"] == 3
    assert "REC-1" not in result["scenarios"][0]["job_order"]


def test_recovery_preserves_frozen_positions_and_approves_better_sequence():
    conn, original_scenario_id = _recovery_factory()
    detected = recovery.detect(conn)
    assert detected["status"] == "triggered"
    assert any(item["type"] == "held_execution" for item in detected["triggers"])

    state = recovery.analyze(conn, actor="planner", force=True)
    latest = state["latest"]
    assert state["action_required"] is True
    assert latest["status"] == "review"
    assert latest["result"]["recommendation"]["policy"] == "edd"
    chosen = next(item for item in latest["result"]["scenarios"]
                  if item["policy"] == "edd")
    assert chosen["job_order"] == ["REC-1", "REC-2", "REC-4", "REC-3"]
    assert chosen["stability"]["frozen_positions_preserved"] is True
    assert chosen["recovery"]["tardiness_reduction_s"] >= 900
    recommendation = next(item for item in optimization.build(conn)["recommendations"]
                          if item["category"] == "schedule_recovery")
    assert recommendation["target_key"] == str(latest["planning_scenario_id"])
    alert = next(item for item in alerting.collect_candidates(conn)
                 if item["rule_key"] == "schedule_recovery_review")
    assert alert["evidence"]["assessment_id"] == latest["id"]

    try:
        recovery.decide(conn, latest["id"], "approve", "supervisor", "current")
        assert False, "current policy must not pass recovery thresholds"
    except ValueError as error:
        assert "benefit and stability thresholds" in str(error)

    approved = recovery.decide(
        conn, latest["id"], "approve", "supervisor", "edd", "Recover urgent order"
    )
    assert approved["latest"]["decision"] == "approve"
    active = planning.active_schedule(conn)
    assert active["id"] != original_scenario_id
    assert [item["job_name"] for item in active["items"]] == [
        "REC-1", "REC-2", "REC-4", "REC-3",
    ]
    held = next(item for item in execution.list_jobs(conn) if item["job_name"] == "REC-1")
    assert held["state"] == "held"


def test_recovery_api_waits_for_schedule_and_validates_decisions():
    conn = init_db(":memory:", check_same_thread=False)
    import main
    main.set_conn(conn)
    with TestClient(main.app) as client:
        main.set_conn(conn)
        state = client.get("/recovery")
        assert state.status_code == 200
        assert state.json()["status"] == "waiting_for_schedule"
        missing = client.post("/recovery/999/decision", json={
            "decision": "approve", "actor": "planner",
        })
        assert missing.status_code == 404
