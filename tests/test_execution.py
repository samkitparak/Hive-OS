import json
from datetime import datetime, timedelta, timezone

import pytest

from db import init_db
import execution
import operations
import planning
import production_control
import resources


def _model(conn, machine_key, signature):
    machine_id = conn.execute(
        "SELECT id FROM machines WHERE machine_key=?", (machine_key,)
    ).fetchone()["id"]
    conn.execute(
        """INSERT INTO cycle_models
           (machine_id, version, training_signature, sample_count, train_count,
            validation_count, inlier_count, coefficients_json,
            identified_features_json, confidence, status, trained_at)
           VALUES (?,1,?,30,24,6,24,?,'["base_s"]','high','active',?)""",
        (machine_id, signature, json.dumps({"base_s": 30}),
         datetime.now(timezone.utc).isoformat()),
    )


def _approved_factory(qty=2, part_count=1):
    conn = init_db(":memory:")
    conn.execute("INSERT INTO jobs (job_name, total_parts) VALUES ('EXEC-1',?)", (qty * part_count,))
    job_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    for index in range(part_count):
        conn.execute(
            """INSERT INTO parts
               (job_id, part_name, material, length_mm, width_mm, qty)
               VALUES (?, ?, 'A', 1000, 500, ?)""", (job_id, f"Panel {index + 1}", qty),
        )
    conn.commit()
    production_control.sync_all(conn)
    resources.sync_defaults(conn)
    for part in conn.execute("SELECT id FROM parts ORDER BY id").fetchall():
        production_control.replace_part_route(
            conn, part["id"], ["gabbiani_pt80", "morbidelli_cx100"], "planner"
        )
    for machine_key, role_key, pool_key in (
        ("gabbiani_pt80", "cutting_operator", "cutting_tooling"),
        ("morbidelli_cx100", "cnc_operator", "cnc_tooling"),
    ):
        resources.update_labor_role(conn, role_key, {
            "headcount": 1, "verified": True, "actor": "test",
        })
        resources.update_tool_pool(conn, pool_key, {
            "total_qty": 1, "available_qty": 1, "verified": True, "actor": "test",
        })
        resources.update_machine_profile(conn, machine_key, {
            "role_key": role_key, "labor_qty": 1, "pool_key": pool_key,
            "tool_qty": 1, "machine_capacity": 1, "verified": True, "actor": "test",
        })
        _model(conn, machine_key, f"execution-{machine_key}")
    resources.update_wip_buffer(conn, "morbidelli_cx100", {
        "capacity_qty": 10, "current_qty": 0, "verified": True, "actor": "test",
    })
    resources.update_factory_calendar(conn, {
        "weekdays": list(range(7)), "start_time": "00:00", "end_time": "23:59",
        "timezone": "UTC", "verified": True, "actor": "test",
    })
    material = resources.snapshot(conn, ["EXEC-1"])["materials"][0]
    resources.set_material_stock(conn, material["material_key"], {
        "on_hand_sheets": 10, "verified": True, "actor": "test",
    })
    order = production_control.list_orders(conn)[0]
    due = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    production_control.update_order(conn, order["id"], {
        "status": "ready", "due_at": due, "actor": "planner",
        "expected_version": order["version"],
    })
    scenario = planning.create_scenario(conn, {
        "created_by": "planner", "job_names": ["EXEC-1"], "policies": ["fifo"],
    })
    planning.decide(conn, scenario["id"], "approve", "supervisor", "fifo")
    return conn


def _release(conn):
    order = production_control.list_orders(conn)[0]
    production_control.update_order(conn, order["id"], {
        "status": "released", "actor": "supervisor", "expected_version": order["version"],
    })
    execution.sync(conn)


def test_approved_schedule_generates_blocked_station_jobs_until_release():
    conn = _approved_factory()
    result = execution.sync(conn)
    jobs = execution.list_jobs(conn)
    assert result["scenario_id"] is not None
    assert len(jobs) == 2
    assert [job["state"] for job in jobs] == ["queued", "queued"]
    assert jobs[0]["blocked_reason"] == "production order not released"
    _release(conn)
    jobs = execution.list_jobs(conn)
    assert [job["state"] for job in jobs] == ["available", "queued"]
    assert jobs[1]["blocked_reason"] == "previous operation incomplete"


def test_two_station_execution_moves_wip_and_completes_order():
    conn = _approved_factory()
    _release(conn)
    first, second = execution.list_jobs(conn)
    first = execution.apply_action(conn, first["id"], {
        "action": "dispatch", "actor": "lead", "expected_version": first["version"],
    })
    first = execution.apply_action(conn, first["id"], {
        "action": "acknowledge", "actor": "cutter", "expected_version": first["version"],
    })
    first = execution.apply_action(conn, first["id"], {
        "action": "start", "quantity": 2, "actor": "cutter",
        "expected_version": first["version"],
    })
    first = execution.apply_action(conn, first["id"], {
        "action": "complete", "good_qty": 2, "actor": "cutter",
        "expected_version": first["version"],
    })
    assert first["state"] == "completed"
    buffer_qty = conn.execute(
        """SELECT wb.current_qty FROM wip_buffers wb JOIN machines m ON m.id=wb.machine_id
           WHERE m.machine_key='morbidelli_cx100'"""
    ).fetchone()["current_qty"]
    assert buffer_qty == 2
    second = execution._job_row(conn, second["id"])
    assert second["state"] == "available"
    for action, extra in (
        ("dispatch", {}), ("acknowledge", {}), ("start", {"quantity": 2}),
        ("complete", {"good_qty": 2}),
    ):
        second = execution.apply_action(conn, second["id"], {
            "action": action, "actor": "cnc", "expected_version": second["version"], **extra,
        })
    assert second["state"] == "completed"
    assert production_control.list_orders(conn)[0]["status"] == "completed"
    assert conn.execute(
        """SELECT wb.current_qty FROM wip_buffers wb JOIN machines m ON m.id=wb.machine_id
           WHERE m.machine_key='morbidelli_cx100'"""
    ).fetchone()["current_qty"] == 0
    assert conn.execute(
        "SELECT status FROM material_reservations"
    ).fetchone()["status"] == "consumed"
    trace = execution.list_traceability(conn, part_id=first["part_id"])
    assert [event["event_type"] for event in trace].count("operation_started") == 2
    assert [event["event_type"] for event in trace].count("operation_completed") == 2


def test_execution_actions_are_versioned_idempotent_and_holdable():
    conn = _approved_factory()
    _release(conn)
    job = execution.list_jobs(conn)[0]
    dispatched = execution.apply_action(conn, job["id"], {
        "action": "dispatch", "actor": "lead", "expected_version": job["version"],
        "idempotency_key": "dispatch-1",
    })
    retried = execution.apply_action(conn, job["id"], {
        "action": "dispatch", "actor": "lead", "expected_version": job["version"],
        "idempotency_key": "dispatch-1",
    })
    assert retried["version"] == dispatched["version"]
    other = next(item for item in execution.list_jobs(conn) if item["id"] != job["id"])
    with pytest.raises(ValueError, match="another execution job"):
        execution.apply_action(conn, other["id"], {
            "action": "dispatch", "actor": "lead",
            "idempotency_key": "dispatch-1",
        })
    assert not conn.in_transaction
    with pytest.raises(execution.VersionConflict):
        execution.apply_action(conn, job["id"], {
            "action": "acknowledge", "actor": "cutter", "expected_version": job["version"],
        })
    assert not conn.in_transaction
    held = execution.apply_action(conn, job["id"], {
        "action": "hold", "actor": "lead", "notes": "check blade",
        "expected_version": dispatched["version"],
    })
    assert held["state"] == "held"
    assert held["held_reason"] == "check blade"
    resumed = execution.apply_action(conn, job["id"], {
        "action": "resume", "actor": "lead", "expected_version": held["version"],
    })
    assert resumed["state"] == "dispatched"


def test_barcode_actuals_advance_execution_and_preserve_unplanned_exception():
    conn = _approved_factory()
    _release(conn)
    first = execution.list_jobs(conn)[0]
    for index in range(2):
        result = operations.create_barcode_event(conn, {
            "barcode": f"EXEC-1|Panel|{index + 1}",
            "job_name": "EXEC-1", "part_id": first["part_id"],
            "station": "gabbiani_pt80", "event_type": "operation_complete",
            "operator": "scanner-1", "source": "barcode",
        })
        assert result["execution"]["completed_qty"] == index + 1
    first = execution._job_row(conn, first["id"])
    assert first["state"] == "completed"
    assert first["route_confirmed_qty"] == 2
    exceptions = execution.list_exceptions(conn)
    assert {item["exception_type"] for item in exceptions} == {"unplanned_execution"}


def test_live_machine_capacity_prevents_parallel_start_beyond_profile():
    conn = _approved_factory(qty=1, part_count=2)
    _release(conn)
    saw_jobs = [job for job in execution.list_jobs(conn) if job["machine_key"] == "gabbiani_pt80"]
    assert len(saw_jobs) == 2
    started = saw_jobs[0]
    for action in ("dispatch", "acknowledge"):
        started = execution.apply_action(conn, started["id"], {
            "action": action, "actor": "cutter", "expected_version": started["version"],
        })
    started = execution.apply_action(conn, started["id"], {
        "action": "start", "quantity": 1, "actor": "cutter",
        "expected_version": started["version"],
    })
    waiting = execution._job_row(conn, saw_jobs[1]["id"])
    for action in ("dispatch", "acknowledge"):
        waiting = execution.apply_action(conn, waiting["id"], {
            "action": action, "actor": "cutter", "expected_version": waiting["version"],
        })
    with pytest.raises(ValueError, match="machine execution capacity"):
        execution.apply_action(conn, waiting["id"], {
            "action": "start", "quantity": 1, "actor": "cutter",
            "expected_version": waiting["version"],
        })
