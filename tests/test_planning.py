import json
from datetime import datetime, timedelta, timezone

import pytest

from db import init_db
import planning
import production_control
import resources


def _active_saw_model(conn):
    machine_id = conn.execute(
        "SELECT id FROM machines WHERE machine_key='gabbiani_pt80'"
    ).fetchone()["id"]
    conn.execute(
        """INSERT INTO cycle_models
           (machine_id, version, training_signature, sample_count, train_count,
            validation_count, inlier_count, coefficients_json,
            identified_features_json, mae_s, mape, r2, residual_cv,
            confidence, status, trained_at)
           VALUES (?,1,'planning-model',30,24,6,24,?,'["base_s"]',1,0.05,0.9,0.05,
                   'high','active',?)""",
        (machine_id, json.dumps({"base_s": 20}), datetime.now(timezone.utc).isoformat()),
    )


def _ready_factory():
    conn = init_db(":memory:")
    for index in range(2):
        conn.execute(
            "INSERT INTO jobs (job_name, total_parts) VALUES (?,1)", (f"PLAN-{index + 1}",)
        )
        job_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
        conn.execute(
            """INSERT INTO parts
               (job_id, part_name, qty, material, length_mm, width_mm)
               VALUES (?, 'Panel', 1, ?, 1000, 500)""",
            (job_id, "A" if index == 0 else "B"),
        )
    conn.commit()
    _active_saw_model(conn)
    production_control.sync_all(conn)
    resources.sync_defaults(conn)
    for material in resources.snapshot(conn, ["PLAN-1", "PLAN-2"])["materials"]:
        resources.set_material_stock(conn, material["material_key"], {
            "on_hand_sheets": 10, "verified": True, "actor": "test",
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
    due = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    for order in production_control.list_orders(conn):
        route = production_control.get_job_routes(conn, order["job_name"])
        part_id = route["steps"][0]["part_id"]
        production_control.replace_part_route(
            conn, part_id, ["gabbiani_pt80"], "planner", "Confirmed from process plan"
        )
        production_control.update_order(conn, order["id"], {
            "due_at": due, "status": "ready", "actor": "planner",
            "expected_version": order["version"],
        })
    return conn


def test_ready_scenario_can_be_approved_and_becomes_active_schedule():
    conn = _ready_factory()
    scenario = planning.create_scenario(conn, {
        "name": "Shift A", "created_by": "planner",
        "policies": ["fifo", "material_batch"], "seed": 7,
    })
    assert scenario["readiness"]["operational_recommendation"] is True
    assert len(scenario["result"]["scenarios"]) == 2
    approved = planning.decide(
        conn, scenario["id"], "approve", "supervisor", "fifo", "Run approved sequence"
    )
    assert approved["status"] == "approved"
    active = planning.active_schedule(conn)
    assert active["id"] == scenario["id"]
    assert [item["position"] for item in active["items"]] == [1, 2]
    reservations = conn.execute(
        "SELECT status, quantity_sheets FROM material_reservations ORDER BY id"
    ).fetchall()
    assert len(reservations) == 2
    assert all(row["status"] == "committed" for row in reservations)
    assert sum(row["quantity_sheets"] for row in reservations) == 2


def test_changed_order_expires_scenario_before_approval():
    conn = _ready_factory()
    scenario = planning.create_scenario(conn, {
        "created_by": "planner", "policies": ["fifo"]
    })
    order = production_control.list_orders(conn)[0]
    production_control.update_order(conn, order["id"], {
        "priority": 90, "actor": "other-planner", "expected_version": order["version"],
    })
    with pytest.raises(ValueError, match="fresh scenario"):
        planning.decide(conn, scenario["id"], "approve", "supervisor", "fifo")
    assert planning.get_scenario(conn, scenario["id"])["status"] == "expired"


def test_commissioning_scenario_cannot_be_approved():
    conn = init_db(":memory:")
    conn.execute("INSERT INTO jobs (job_name, total_parts) VALUES ('NO-MODEL',1)")
    job_id = conn.execute("SELECT id FROM jobs").fetchone()["id"]
    conn.execute("INSERT INTO parts (job_id, part_name) VALUES (?, 'Panel')", (job_id,))
    conn.commit()
    production_control.sync_all(conn)
    scenario = planning.create_scenario(conn, {
        "created_by": "planner", "job_names": ["NO-MODEL"], "policies": ["fifo"]
    })
    with pytest.raises(ValueError, match="commissioning-only"):
        planning.decide(conn, scenario["id"], "approve", "supervisor", "fifo")


def test_resource_change_expires_scenario_before_approval():
    conn = _ready_factory()
    scenario = planning.create_scenario(conn, {
        "created_by": "planner", "policies": ["fifo"],
    })
    resources.update_labor_role(conn, "cutting_operator", {
        "headcount": 2, "verified": True, "actor": "shift-manager",
    })
    with pytest.raises(ValueError, match="fresh scenario"):
        planning.decide(conn, scenario["id"], "approve", "supervisor", "fifo")
    assert planning.get_scenario(conn, scenario["id"])["status"] == "expired"


def test_order_completion_consumes_stock_and_cancellation_releases_it():
    conn = _ready_factory()
    scenario = planning.create_scenario(conn, {
        "created_by": "planner", "policies": ["fifo"],
    })
    planning.decide(conn, scenario["id"], "approve", "supervisor", "fifo")
    first, second = production_control.list_orders(conn)
    first = production_control.update_order(conn, first["id"], {
        "status": "released", "actor": "supervisor", "expected_version": first["version"],
    })
    first = production_control.update_order(conn, first["id"], {
        "status": "in_progress", "actor": "machine", "expected_version": first["version"],
    })
    production_control.update_order(conn, first["id"], {
        "status": "completed", "actor": "machine", "expected_version": first["version"],
    })
    second = production_control.list_orders(conn)[0]
    production_control.update_order(conn, second["id"], {
        "status": "cancelled", "actor": "planner", "expected_version": second["version"],
    })
    statuses = [row["status"] for row in conn.execute(
        "SELECT status FROM material_reservations ORDER BY production_order_id"
    ).fetchall()]
    assert statuses == ["consumed", "released"]
    totals = conn.execute(
        "SELECT SUM(on_hand_sheets) on_hand, SUM(reserved_sheets) reserved FROM material_lots"
    ).fetchone()
    assert totals["on_hand"] == 19
    assert totals["reserved"] == 0
