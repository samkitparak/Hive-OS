import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from db import init_db
import changeovers
import main
import planning
import production_control
import release_control
import resources


def _model(conn, machine_key="gabbiani_pt80", seconds=20):
    machine_id = conn.execute(
        "SELECT id FROM machines WHERE machine_key=?", (machine_key,)
    ).fetchone()["id"]
    conn.execute(
        """INSERT INTO cycle_models
           (machine_id,version,training_signature,sample_count,train_count,
            validation_count,inlier_count,coefficients_json,identified_features_json,
            confidence,status,trained_at)
           VALUES (?,1,?,30,24,6,24,?,'["base_s"]','high','active',?)""",
        (machine_id, f"release-{machine_key}", json.dumps({"base_s": seconds}),
         datetime.now(timezone.utc).isoformat()),
    )


def _approved_factory(order_count=2, due_days=1):
    conn = init_db(":memory:")
    for index in range(order_count):
        job_name = f"REL-{index + 1}"
        conn.execute("INSERT INTO jobs (job_name,total_parts) VALUES (?,1)", (job_name,))
        job_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
        conn.execute(
            """INSERT INTO parts
               (job_id,part_name,qty,material,length_mm,width_mm)
               VALUES (?,'Panel',1,'A',1000,500)""", (job_id,),
        )
    conn.commit()
    _model(conn)
    production_control.sync_all(conn)
    resources.sync_defaults(conn)
    for material in resources.snapshot(conn, [f"REL-{i + 1}" for i in range(order_count)])["materials"]:
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
    standard = next(item for item in changeovers.snapshot(conn)["machines"]
                    if item["machine_key"] == "gabbiani_pt80")
    changeovers.update_standard(conn, "gabbiani_pt80", {
        "default_setup_s": 60, "verified": True,
        "expected_version": standard["version"], "actor": "test",
    })
    due = (datetime.now(timezone.utc) + timedelta(days=due_days)).isoformat()
    for order in production_control.list_orders(conn):
        part_id = production_control.get_job_routes(conn, order["job_name"])["steps"][0]["part_id"]
        production_control.replace_part_route(conn, part_id, ["gabbiani_pt80"], "planner")
        production_control.update_order(conn, order["id"], {
            "status": "ready", "due_at": due, "actor": "planner",
            "expected_version": order["version"],
        })
    scenario = planning.create_scenario(conn, {
        "created_by": "planner", "policies": ["fifo"],
    })
    planning.decide(conn, scenario["id"], "approve", "supervisor", "fifo")
    return conn


def _commission_policy(conn, norm_minutes=60, **overrides):
    current = release_control.settings(conn)
    release_control.update_settings(conn, {
        "expected_version": current["version"], "verified": True,
        "actor": "supervisor", **overrides,
    })
    norm = next(item for item in release_control.norms(conn)
                if item["machine_key"] == "gabbiani_pt80")
    release_control.update_norm(conn, "gabbiani_pt80", {
        "expected_version": norm["version"], "workload_norm_minutes": norm_minutes,
        "verified": True, "actor": "supervisor",
    })


def test_empty_review_never_invents_releasable_work():
    conn = init_db(":memory:")
    result = release_control.create_review(conn, actor="test")
    assert result["status"] == "waiting_for_schedule"
    assert result["recommendations"] == []
    assert result["summary"]["actionable"] == 0


def test_unverified_policy_is_visible_but_cannot_release():
    conn = _approved_factory()
    result = release_control.create_review(conn, actor="test")
    preview = next(item for item in result["recommendations"]
                   if item["recommendation"] in {"release", "expedite"})
    assert result["status"] == "commissioning"
    assert preview["evidence_ready"] is False
    assert preview["reason_code"] == "commissioning_only_preview"
    with pytest.raises(ValueError, match="Commissioning-only"):
        release_control.act(conn, preview["id"], {
            "action": "approve", "actor": "supervisor",
        })


def test_verified_review_releases_one_order_with_named_approval():
    conn = _approved_factory()
    _commission_policy(conn)
    review = release_control.create_review(conn, actor="release-worker")
    recommendation = next(item for item in review["recommendations"]
                          if item["recommendation"] in {"release", "expedite"})
    assert review["status"] == "actionable"
    assert recommendation["evidence_ready"] is True
    result = release_control.act(conn, recommendation["id"], {
        "action": "approve", "actor": "Shift Supervisor", "notes": "Release reviewed",
    })
    assert result["order_status"] == "released"
    order = next(item for item in production_control.list_orders(conn)
                 if item["id"] == recommendation["production_order_id"])
    assert order["released_by"] == "Shift Supervisor"
    assert conn.execute(
        "SELECT COUNT(*) count FROM release_control_actions WHERE action='approve'"
    ).fetchone()["count"] == 1
    assert all(item["status"] != "open" for item in release_control.snapshot(conn)["current"]["recommendations"])


def test_station_workload_norm_holds_an_order_that_would_overload():
    conn = _approved_factory(order_count=1)
    _commission_policy(conn, norm_minutes=0.1)
    review = release_control.create_review(conn, actor="test")
    item = review["recommendations"][0]
    assert item["recommendation"] == "hold"
    assert item["reason_code"] == "workload_norm_exceeded"
    projected = item["workload"]["projected_stations"][0]
    assert projected["projected_ratio"] > 1


def test_corrected_load_divides_later_operations_by_route_position():
    conn = _approved_factory(order_count=1)
    _model(conn, "morbidelli_cx100", 20)
    order = production_control.list_orders(conn)[0]
    part_id = production_control.get_job_routes(conn, order["job_name"])["steps"][0]["part_id"]
    machine_id = conn.execute(
        "SELECT id FROM machines WHERE machine_key='morbidelli_cx100'"
    ).fetchone()["id"]
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO part_route_steps
           (part_id,step_index,machine_id,source,confidence,required_qty,created_at,updated_at)
           VALUES (?,2,?,'manual','confirmed',1,?,?)""",
        (part_id, machine_id, now, now),
    )
    conn.commit()
    review = release_control.create_review(conn, actor="test")
    projected = {
        item["machine_key"]: item["order_contribution_minutes"]
        for item in review["recommendations"][0]["workload"]["projected_stations"]
    }
    assert projected["gabbiani_pt80"] == pytest.approx(20 / 60, abs=0.01)
    assert projected["morbidelli_cx100"] == pytest.approx(10 / 60, abs=0.01)


def test_high_load_applies_adaptive_work_ahead_limit():
    conn = _approved_factory(order_count=2, due_days=3)
    first = production_control.list_orders(conn)[0]
    production_control.update_order(conn, first["id"], {
        "status": "released", "actor": "supervisor", "expected_version": first["version"],
    })
    _commission_policy(
        conn, norm_minutes=0.5, overload_threshold_ratio=0.5,
        work_ahead_hours=1, max_releases_per_review=2,
    )
    review = release_control.create_review(conn, actor="test")
    assert review["policy_state"]["overloaded"] is True
    assert review["recommendations"][0]["reason_code"] == "outside_adaptive_work_ahead"


def test_policy_change_makes_open_recommendation_stale():
    conn = _approved_factory(order_count=1)
    _commission_policy(conn)
    review = release_control.create_review(conn, actor="test")
    recommendation = review["recommendations"][0]
    current = release_control.settings(conn)
    release_control.update_settings(conn, {
        "expected_version": current["version"], "work_ahead_hours": 48,
        "actor": "planner",
    })
    with pytest.raises(ValueError, match="fresh release review"):
        release_control.act(conn, recommendation["id"], {
            "action": "approve", "actor": "planner",
        })
    stored = conn.execute(
        "SELECT status FROM release_control_recommendations WHERE id=?",
        (recommendation["id"],),
    ).fetchone()
    assert stored["status"] == "stale"


def test_release_control_endpoints():
    conn = init_db(":memory:", check_same_thread=False)
    main.set_conn(conn)
    with TestClient(main.app) as client:
        main.set_conn(conn)
        response = client.post("/release-control/sync", json={"actor": "test"})
        assert response.status_code == 200
        assert response.json()["method_version"] == release_control.METHOD_VERSION
        snapshot = client.get("/release-control")
        assert snapshot.status_code == 200
        assert snapshot.json()["current"]["status"] == "waiting_for_schedule"
        assert main.APP_VERSION == "0.34.0"
