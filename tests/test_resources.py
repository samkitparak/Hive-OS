import json
from datetime import datetime, timedelta, timezone

import pytest

from db import init_db
import digital_twin
import production_control
import resources


def _factory(job_name="RESOURCE-1", material="A", qty=2):
    conn = init_db(":memory:")
    conn.execute("INSERT INTO jobs (job_name, total_parts) VALUES (?,?)", (job_name, qty))
    job_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    conn.execute(
        """INSERT INTO parts
           (job_id, part_name, material, length_mm, width_mm, qty)
           VALUES (?, 'Panel', ?, 1000, 500, ?)""",
        (job_id, material, qty),
    )
    conn.commit()
    production_control.sync_all(conn)
    resources.sync_defaults(conn)
    return conn


def _verify_saw_resources(conn, job_names, stock=20, workers=1, tooling=1):
    for material in resources.snapshot(conn, job_names)["materials"]:
        resources.set_material_stock(conn, material["material_key"], {
            "on_hand_sheets": stock, "verified": True, "actor": "test",
        })
    resources.update_labor_role(conn, "cutting_operator", {
        "headcount": workers, "verified": True, "actor": "test",
    })
    resources.update_tool_pool(conn, "cutting_tooling", {
        "total_qty": tooling, "available_qty": tooling, "verified": True, "actor": "test",
    })
    for machine_key in ("gabbiani_pt80", "nova_si400"):
        resources.update_machine_profile(conn, machine_key, {
            "role_key": "cutting_operator", "labor_qty": 1,
            "pool_key": "cutting_tooling", "tool_qty": 1,
            "machine_capacity": 1, "verified": True, "actor": "test",
        })
    resources.update_factory_calendar(conn, {
        "weekdays": list(range(7)), "start_time": "00:00", "end_time": "23:59",
        "timezone": "UTC", "verified": True, "actor": "test",
    })


def _model(conn, machine_key, seconds=100):
    machine_id = conn.execute(
        "SELECT id FROM machines WHERE machine_key=?", (machine_key,)
    ).fetchone()["id"]
    conn.execute(
        """INSERT INTO cycle_models
           (machine_id, version, training_signature, sample_count, train_count,
            validation_count, inlier_count, coefficients_json,
            identified_features_json, confidence, status, trained_at)
           VALUES (?,1,?,30,24,6,24,?,'["base_s"]','high','active',?)""",
        (machine_id, f"resource-{machine_key}", json.dumps({"base_s": seconds}),
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def test_defaults_estimate_sheet_demand_but_remain_unverified():
    conn = _factory()
    status = resources.snapshot(conn, ["RESOURCE-1"])
    material = status["materials"][0]
    assert material["required_area_m2"] == 1.0
    assert material["required_sheets"] == 1
    assert material["shortage_sheets"] == 1
    assert status["resource_ready"] is False
    assert all(item["source"] == "engineering_assumption" for item in status["machine_profiles"])


def test_draft_orders_expose_open_material_demand_without_passing_resource_gates():
    conn = _factory()
    status = resources.snapshot(conn)
    material = status["materials"][0]
    assert status["applicable"] is False
    assert material["required_sheets"] == 0
    assert material["open_required_sheets"] == 1
    assert material["open_shortage_sheets"] == 1
    assert next(check for check in status["checks"] if check["key"] == "wip")["passed"] is False


def test_order_cannot_be_readied_until_resources_are_verified():
    conn = _factory()
    order = production_control.list_orders(conn)[0]
    due = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match="Verify production resources"):
        production_control.update_order(conn, order["id"], {
            "status": "ready", "due_at": due, "actor": "planner",
        })
    _verify_saw_resources(conn, ["RESOURCE-1"])
    status = resources.snapshot(conn, ["RESOURCE-1"])
    assert status["resource_ready"] is True
    ready = production_control.update_order(conn, order["id"], {
        "status": "ready", "due_at": due, "actor": "planner",
    })
    assert ready["status"] == "ready"


def test_rejected_stock_reduction_does_not_mutate_material_definition():
    conn = _factory()
    material = resources.snapshot(conn, ["RESOURCE-1"])["materials"][0]
    resources.set_material_stock(conn, material["material_key"], {
        "on_hand_sheets": 10, "sheet_length_mm": 2440,
        "verified": True, "actor": "test",
    })
    conn.execute("UPDATE material_lots SET reserved_sheets=5")
    conn.commit()
    with pytest.raises(ValueError, match="committed reservations"):
        resources.set_material_stock(conn, material["material_key"], {
            "on_hand_sheets": 4, "sheet_length_mm": 2000,
            "verified": True, "actor": "test",
        })
    definition = conn.execute(
        "SELECT sheet_length_mm FROM material_definitions WHERE material_key=?",
        (material["material_key"],),
    ).fetchone()
    assert definition["sheet_length_mm"] == 2440


def test_calendar_and_planned_unavailability_delay_operations():
    conn = _factory()
    resources.update_factory_calendar(conn, {
        "weekdays": list(range(7)), "start_time": "09:00", "end_time": "17:00",
        "timezone": "Asia/Kolkata", "verified": True, "actor": "test",
    })
    simulated_at = datetime(2026, 7, 13, 2, 0, tzinfo=timezone.utc)
    context = resources.simulation_context(conn, [], simulated_at)
    assert resources.next_available_delay(
        context, "gabbiani_pt80", "cutting_operator", "cutting_tooling", 0, 60
    ) == 5400
    resources.create_unavailability(conn, {
        "resource_type": "machine", "resource_key": "gabbiani_pt80",
        "starts_at": "2026-07-13T03:30:00+00:00",
        "ends_at": "2026-07-13T04:30:00+00:00",
        "reason": "blade service", "actor": "maintenance",
    })
    context = resources.simulation_context(conn, [], simulated_at)
    assert resources.next_available_delay(
        context, "gabbiani_pt80", "cutting_operator", "cutting_tooling", 0, 60
    ) == 9000


def test_shared_labor_capacity_changes_finite_capacity_schedule():
    conn = init_db(":memory:")
    for index, machine_key in enumerate(("gabbiani_pt80", "nova_si400"), start=1):
        job_name = f"PARALLEL-{index}"
        conn.execute("INSERT INTO jobs (job_name, total_parts) VALUES (?,1)", (job_name,))
        job_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
        conn.execute(
            """INSERT INTO parts (job_id, part_name, material, length_mm, width_mm)
               VALUES (?, 'Panel', 'A', 1000, 500)""", (job_id,)
        )
        conn.commit()
    production_control.sync_all(conn)
    _verify_saw_resources(conn, ["PARALLEL-1", "PARALLEL-2"], workers=1, tooling=2)
    due = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    for order, machine_key in zip(production_control.list_orders(conn),
                                  ("gabbiani_pt80", "nova_si400")):
        part_id = conn.execute("SELECT id FROM parts WHERE job_id=?", (order["job_id"],)).fetchone()["id"]
        production_control.replace_part_route(conn, part_id, [machine_key], "planner")
        production_control.update_order(conn, order["id"], {
            "status": "ready", "due_at": due, "actor": "planner",
        })
    _model(conn, "gabbiani_pt80")
    _model(conn, "nova_si400")
    one_worker = digital_twin.compare(conn, policies=["fifo"])["scenarios"][0]
    resources.update_labor_role(conn, "cutting_operator", {
        "headcount": 2, "verified": True, "actor": "test",
    })
    two_workers = digital_twin.compare(conn, policies=["fifo"])["scenarios"][0]
    assert one_worker["feasible"] is True
    assert two_workers["makespan_s"] < one_worker["makespan_s"]
    assert one_worker["capacity_wait_s"] > two_workers["capacity_wait_s"]
