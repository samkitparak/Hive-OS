"""Probabilistic constraint, delivery-risk, persistence, and calibration tests."""

import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from db import init_db
import forecasting
import optimization
import production_control
import resources


def _ready_factory(now: datetime):
    conn = init_db(":memory:", check_same_thread=False)
    conn.execute("INSERT INTO jobs (job_name,total_parts) VALUES ('RISK-1',1)")
    job_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    conn.execute(
        """INSERT INTO parts
           (job_id,part_name,qty,material,length_mm,width_mm)
           VALUES (?,'Panel',1,'A',1000,500)""", (job_id,),
    )
    machine_id = conn.execute(
        "SELECT id FROM machines WHERE machine_key='gabbiani_pt80'"
    ).fetchone()["id"]
    conn.execute(
        """INSERT INTO cycle_models
           (machine_id,version,training_signature,sample_count,train_count,
            validation_count,inlier_count,coefficients_json,identified_features_json,
            mae_s,mape,r2,residual_cv,confidence,status,trained_at)
           VALUES (?,1,'forecast-model',60,48,12,48,?,'["base_s"]',10,.08,.9,.2,
                   'high','active',?)""",
        (machine_id, json.dumps({"base_s": 120}), now.isoformat()),
    )
    conn.commit()
    production_control.sync_all(conn)
    resources.sync_defaults(conn)
    material = resources.snapshot(conn, ["RISK-1"])["materials"][0]
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
    order = production_control.list_orders(conn)[0]
    part_id = conn.execute("SELECT id FROM parts WHERE job_id=?", (job_id,)).fetchone()["id"]
    production_control.replace_part_route(
        conn, part_id, ["gabbiani_pt80"], "planner", "Synthetic confirmed route"
    )
    order = production_control.list_orders(conn)[0]
    order = production_control.update_order(conn, order["id"], {
        "due_at": (now + timedelta(seconds=60)).isoformat(),
        "status": "ready", "actor": "planner", "expected_version": order["version"],
    })
    return conn, order


def test_forecast_quantifies_constraint_and_late_order_risk():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    conn, order = _ready_factory(now)
    result = forecasting.generate(conn, samples=40, seed=7, now=now)

    assert result["status"] == "ready"
    assert result["decision_ready"] is True
    assert result["feasible_probability"] == 1
    assert result["constraints"][0]["machine_key"] == "gabbiani_pt80"
    assert result["constraints"][0]["bottleneck_probability"] == 1
    risk = result["jobs"][0]
    assert risk["production_order_id"] == order["id"]
    assert risk["late_probability"] >= 0.9
    assert risk["completion_s"]["p80"] >= risk["completion_s"]["p50"]
    assert result["uncertainty"]["model_evidence"]["stochastic_machines"] == 1


def test_forecast_refresh_is_idempotent_and_detects_stale_inputs():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    conn, order = _ready_factory(now)
    first = forecasting.refresh(conn, samples=20, seed=3, now=now)
    second = forecasting.refresh(conn, samples=20, seed=3, now=now + timedelta(minutes=1))
    assert first["reused"] is False
    assert second["reused"] is True
    assert conn.execute("SELECT COUNT(*) FROM production_forecasts").fetchone()[0] == 1

    production_control.update_order(conn, order["id"], {
        "priority": 90, "actor": "planner", "expected_version": order["version"],
    })
    state = forecasting.snapshot(conn)
    assert state["stale"] is True
    assert state["decision_ready"] is False


def test_completed_order_calibrates_latest_precompletion_forecast():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    conn, order = _ready_factory(now)
    state = forecasting.refresh(conn, samples=40, seed=11, force=True, now=now)
    prediction = state["latest"]["result"]["jobs"][0]
    completed_at = now + timedelta(seconds=prediction["completion_s"]["p50"])
    conn.execute("UPDATE production_orders SET status='completed' WHERE id=?", (order["id"],))
    conn.execute(
        """INSERT INTO production_order_events
           (production_order_id,event_type,from_status,to_status,actor,payload_json,ts)
           VALUES (?,'status_changed','in_progress','completed','machine','{}',?)""",
        (order["id"], completed_at.isoformat()),
    )
    conn.commit()

    calibration = forecasting.calibration(conn)
    assert calibration["status"] == "collecting"
    assert calibration["outcome_count"] == 1
    assert calibration["p80_coverage"] == 1
    assert calibration["p50_mean_absolute_error_s"] == 0
    assert calibration["late_risk_brier_score"] <= 0.01


def test_forecast_api_exposes_snapshot_and_validates_ensemble_size():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    conn, _ = _ready_factory(now)
    import main
    main.set_conn(conn)
    with TestClient(main.app) as client:
        main.set_conn(conn)
        assert client.get("/forecast").status_code == 200
        response = client.post("/forecast/refresh", json={"samples": 20, "seed": 5})
        assert response.status_code == 200
        assert response.json()["latest"]["result"]["sample_count"] == 20
        invalid = client.post("/forecast/refresh", json={"samples": 5})
        assert invalid.status_code == 422
        seed_overflow = client.post("/forecast/refresh", json={
            "samples": 20, "seed": 2_147_483_448,
        })
        assert seed_overflow.status_code == 422


def test_decision_ready_forecast_drives_optimization_recommendations():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    conn, order = _ready_factory(now)
    forecasting.refresh(conn, samples=20, seed=17, force=True, now=now)

    result = optimization.build(conn, now=now)
    delivery = next(
        item for item in result["recommendations"]
        if item["category"] == "delivery_risk"
    )
    future_constraint = next(
        item for item in result["recommendations"]
        if item["category"] == "forecast_constraint"
    )

    assert result["forecast"]["decision_ready"] is True
    assert delivery["target_key"] == str(order["id"])
    assert "simulated late risk" in delivery["evidence"][0]
    assert future_constraint["target_key"] == "gabbiani_pt80"
