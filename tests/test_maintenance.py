from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from db import init_db
import event_pipeline
import maintenance


@pytest.fixture
def conn():
    connection = init_db(":memory:", check_same_thread=False)
    maintenance.sync_defaults(connection)
    yield connection
    connection.close()


def _plan(conn, machine_key="gabbiani_pt80"):
    return next(item for item in maintenance.list_plans(conn)
                if item["machine_key"] == machine_key)


def _past(days=40):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _task_results(work_order, fail_task_id=None):
    results = []
    for task in work_order["tasks"]:
        result = "checked" if task["response_type"] == "check" else "pass"
        if task["id"] == fail_task_id:
            result = "fail"
        results.append({"task_id": task["id"], "result": result})
    return results


def _due_plan(conn, machine_key="gabbiani_pt80", **extras):
    plan = _plan(conn, machine_key)
    payload = {
        "expected_version": plan["version"], "verified": True,
        "interval_days": 30, "anchor_at": _past(), "actor": "planner",
        **extras,
    }
    maintenance.update_plan(conn, plan["id"], payload)
    return next(item for item in maintenance.list_plans(conn) if item["id"] == plan["id"])


def test_defaults_are_machine_specific_but_cannot_trigger_until_verified(conn):
    snapshot = maintenance.snapshot(conn)
    assert snapshot["status"] == "commissioning_required"
    assert snapshot["summary"]["plans"] == 15
    assert snapshot["summary"]["verified_plans"] == 0
    assert snapshot["summary"]["machines_without_verified_plan"] == 15
    assert all(plan["status"] == "unverified" for plan in snapshot["plans"])
    assert all(len(plan["tasks"]) == 5 for plan in snapshot["plans"])
    assert maintenance.sync(conn)["work_orders_created"] == 0


def test_calendar_trigger_generates_one_idempotent_work_order(conn):
    plan = _due_plan(conn)
    assert plan["status"] == "overdue"
    work_orders = maintenance.list_work_orders(conn, "open")
    assert len(work_orders) == 1
    assert work_orders[0]["maintenance_plan_id"] == plan["id"]
    assert work_orders[0]["source"] == "preventive_schedule"
    assert maintenance.sync(conn)["work_orders_created"] == 0


def test_usage_plan_starts_from_verification_baseline_and_triggers_on_cycles(conn):
    plan = _plan(conn, "morbidelli_cx100")
    verified = maintenance.update_plan(conn, plan["id"], {
        "expected_version": plan["version"], "verified": True,
        "strategy": "usage", "interval_days": None, "interval_cycles": 2,
        "warning_cycles": 0, "actor": "planner",
    })
    assert verified["status"] == "awaiting_evidence"
    base = datetime.now(timezone.utc)
    for index in range(2):
        event_pipeline.ingest_event(conn, {
            "machine_key": "morbidelli_cx100", "event_type": "cycle_end",
            "ts": (base + timedelta(seconds=index)).isoformat(), "source": "test",
        })
    result = maintenance.sync(conn)
    assert result["work_orders_created"] == 1
    assert next(item for item in maintenance.list_plans(conn)
                if item["id"] == plan["id"])["status"] == "overdue"


def test_condition_signal_uses_verified_threshold_and_clears_on_normal_reading(conn):
    plan = _plan(conn, "elgi_1")
    plan = maintenance.update_plan(conn, plan["id"], {
        "expected_version": plan["version"], "verified": True,
        "strategy": "condition", "interval_days": None,
        "condition_metric": "discharge_temperature_c", "condition_operator": "gte",
        "condition_threshold": 95, "actor": "planner",
    })
    normal = maintenance.record_condition_signal(conn, {
        "machine_key": "elgi_1", "metric_key": "discharge_temperature_c",
        "value": 80, "unit": "C", "source": "test",
    })
    assert normal["sync"]["work_orders_created"] == 0
    hot = maintenance.record_condition_signal(conn, {
        "machine_key": "elgi_1", "metric_key": "discharge_temperature_c",
        "value": 100, "unit": "C", "source": "test",
    })
    assert hot["sync"]["work_orders_created"] == 1
    assert maintenance.list_work_orders(conn, "open")[0]["source"] == "condition_monitoring"
    maintenance.record_condition_signal(conn, {
        "machine_key": "elgi_1", "metric_key": "discharge_temperature_c",
        "value": 75, "unit": "C", "source": "test",
    })
    assert conn.execute(
        """SELECT COUNT(*) count FROM maintenance_condition_signals
           WHERE maintenance_plan_id=? AND status='open'""", (plan["id"],),
    ).fetchone()["count"] == 0


def test_scheduling_blocks_machine_capacity_and_completion_requires_safety_evidence(conn):
    _due_plan(conn)
    work_order = maintenance.get_work_order(conn, maintenance.list_work_orders(conn)[0]["id"])
    start = datetime.now(timezone.utc) + timedelta(days=1)
    scheduled = maintenance.update_work_order(conn, work_order["id"], {
        "scheduled_start_at": start.isoformat(),
        "scheduled_end_at": (start + timedelta(hours=1)).isoformat(),
        "status": "in_progress", "actor": "maintainer",
    })
    assert scheduled["scheduled_start_at"] == start.isoformat()
    outage = conn.execute(
        "SELECT resource_key,work_order_id FROM resource_unavailability"
    ).fetchone()
    assert dict(outage) == {
        "resource_key": "gabbiani_pt80", "work_order_id": work_order["id"],
    }
    with pytest.raises(ValueError, match="authorized person"):
        maintenance.complete_work_order(conn, work_order["id"], {
            "completed_by": "maintainer", "task_results": _task_results(work_order),
        })
    completed = maintenance.complete_work_order(conn, work_order["id"], {
        "completed_by": "maintainer", "loto_verified": True,
        "loto_verified_by": "authorized-maintainer",
        "task_results": _task_results(work_order),
    })
    assert completed["status"] == "done"
    assert completed["execution"]["outcome"] == "completed"
    assert completed["execution"]["loto_verified_by"] == "authorized-maintainer"
    assert next(item for item in maintenance.list_plans(conn)
                if item["id"] == work_order["maintenance_plan_id"])["status"] == "healthy"


def test_failed_inspection_creates_corrective_follow_up(conn):
    _due_plan(conn, machine_key="stefani_kd")
    work_order = maintenance.get_work_order(conn, maintenance.list_work_orders(conn)[0]["id"])
    failed_task = next(task for task in work_order["tasks"]
                       if task["response_type"] == "pass_fail")
    completed = maintenance.complete_work_order(conn, work_order["id"], {
        "completed_by": "maintainer", "loto_verified": True,
        "loto_verified_by": "authorized-maintainer",
        "task_results": _task_results(work_order, failed_task["id"]),
    })
    assert completed["execution"]["outcome"] == "follow_up_required"
    follow_up = maintenance.get_work_order(conn, completed["follow_up_work_order_id"])
    assert follow_up["source"] == "inspection_followup"
    assert follow_up["status"] == "open"


def test_spares_reserve_issue_and_audit_stock(conn):
    maintenance.create_spare_part(conn, {
        "part_key": "saw_blade_400", "name": "400 mm saw blade",
        "criticality": "high", "reorder_point": 1, "reorder_qty": 2,
        "verified": True,
    })
    maintenance.set_spare_stock(conn, "saw_blade_400", {
        "on_hand_qty": 2, "verified": True, "actor": "storekeeper",
    })
    plan = _plan(conn)
    maintenance.update_plan(conn, plan["id"], {
        "expected_version": plan["version"], "verified": True,
        "interval_days": 30, "anchor_at": _past(), "actor": "planner",
        "spares": [{"part_key": "saw_blade_400", "quantity": 1, "required": True}],
    })
    work_order = maintenance.get_work_order(conn, maintenance.list_work_orders(conn)[0]["id"])
    spare = maintenance.list_spares(conn)[0]
    assert spare["on_hand_qty"] == 2
    assert spare["reserved_qty"] == 1
    with pytest.raises(ValueError, match="committed spare reservations"):
        maintenance.set_spare_stock(conn, "saw_blade_400", {
            "on_hand_qty": 0, "verified": True, "actor": "storekeeper",
        })
    completed = maintenance.complete_work_order(conn, work_order["id"], {
        "completed_by": "maintainer", "loto_verified": True,
        "loto_verified_by": "authorized-maintainer",
        "task_results": _task_results(work_order),
    })
    assert completed["status"] == "done"
    spare = maintenance.list_spares(conn)[0]
    assert spare["on_hand_qty"] == 1
    assert spare["reserved_qty"] == 0
    movement_types = {row["movement_type"] for row in conn.execute(
        "SELECT movement_type FROM spare_stock_movements"
    ).fetchall()}
    assert movement_types == {"adjustment", "reservation", "issue"}


def test_required_spare_shortage_blocks_completion(conn):
    maintenance.create_spare_part(conn, {
        "part_key": "filter_x", "name": "Filter X", "verified": True,
    })
    plan = _plan(conn, "elgi_2")
    maintenance.update_plan(conn, plan["id"], {
        "expected_version": plan["version"], "verified": True,
        "interval_days": 30, "anchor_at": _past(), "actor": "planner",
        "spares": [{"part_key": "filter_x", "quantity": 1, "required": True}],
    })
    work_order = maintenance.get_work_order(conn, maintenance.list_work_orders(conn)[0]["id"])
    assert work_order["required_spare_shortages"] == 1
    with pytest.raises(ValueError, match="still short"):
        maintenance.complete_work_order(conn, work_order["id"], {
            "completed_by": "maintainer", "loto_verified": True,
            "loto_verified_by": "authorized-maintainer",
            "task_results": _task_results(work_order),
        })


def test_maintenance_api_commissions_plan_and_exposes_detail(conn):
    import main

    main.set_conn(conn)
    with TestClient(main.app) as client:
        main.set_conn(conn)
        snapshot = client.get("/api/maintenance/snapshot")
        assert snapshot.status_code == 200
        assert snapshot.json()["summary"]["unverified_plans"] == 15

        plan = snapshot.json()["plans"][0]
        updated = client.put(f"/api/maintenance/plans/{plan['id']}", json={
            "expected_version": plan["version"],
            "interval_days": 45,
            "verified": True,
            "actor": "planner",
        })
        assert updated.status_code == 200
        assert updated.json()["verified"] == 1
        assert updated.json()["interval_days"] == 45
        assert client.post("/api/maintenance/sync", json={}).status_code == 200

        manual = client.post("/api/maintenance/work-orders", json={
            "machine_key": plan["machine_key"], "title": "Inspect reported noise",
        })
        assert manual.status_code == 200
        work_order_id = manual.json()["id"]
        detail = client.get(f"/api/maintenance/work-orders/{work_order_id}")
        assert detail.status_code == 200
        assert detail.json()["title"] == "Inspect reported noise"

        assert client.put("/api/maintenance/plans/999999", json={
            "verified": True,
        }).status_code == 404
