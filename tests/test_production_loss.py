"""Shift loss waterfall, evidence gates, and reconciliation tests."""

import json
from datetime import datetime, timezone

import pytest

from db import init_db
import production_loss


@pytest.fixture
def conn():
    connection = init_db(":memory:", check_same_thread=False)
    yield connection
    connection.close()


def _calendar(conn, start="09:00", end="10:00", verified=True):
    conn.execute(
        """INSERT INTO work_calendar_windows
           (resource_type,resource_key,weekday,start_time,end_time,capacity,
            timezone,source,verified,active,updated_at)
           VALUES ('factory','factory',1,?,?,1,'UTC','manual',?,1,?)""",
        (start, end, int(verified), "2026-07-14T00:00:00+00:00"),
    )
    conn.commit()


def _machine_id(conn, key="gabbiani_pt80"):
    return conn.execute(
        "SELECT id FROM machines WHERE machine_key=?", (key,)
    ).fetchone()["id"]


def _event(conn, event_type, ts, part_id=None, key="gabbiani_pt80"):
    conn.execute(
        """INSERT INTO machine_events (machine_id,event_type,part_id,ts)
           VALUES (?,?,?,?)""",
        (_machine_id(conn, key), event_type, part_id, ts),
    )


def _part(conn):
    conn.execute("INSERT INTO jobs (job_name,total_parts) VALUES ('LOSS-1',1)")
    job_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    cursor = conn.execute(
        """INSERT INTO parts
           (job_id,part_name,length_mm,width_mm,thickness_mm,qty)
           VALUES (?,'Panel',1000,500,18,1)""",
        (job_id,),
    )
    return cursor.lastrowid


def _active_model(conn, key="gabbiani_pt80", base_s=300):
    conn.execute(
        """INSERT INTO cycle_models
           (machine_id,version,training_signature,sample_count,train_count,
            validation_count,inlier_count,coefficients_json,identified_features_json,
            mae_s,mape,r2,residual_cv,confidence,status,reason,trained_at)
           VALUES (?,1,?,30,24,6,28,?,'["base_s"]',5,0.05,0.9,0.1,
                   'high','active','test','2026-07-14T00:00:00+00:00')""",
        (_machine_id(conn, key), f"loss-{key}", json.dumps({
            "base_s": base_s, "length_coeff": 0, "width_coeff": 0, "area_coeff": 0,
        })),
    )


def test_verified_shift_waterfall_reconciles_without_double_counting(conn):
    _calendar(conn)
    part_id = _part(conn)
    _active_model(conn)
    for event_type, value in (
        ("power_on", "2026-07-14T08:55:00+00:00"),
        ("cycle_start", "2026-07-14T09:05:00+00:00"),
        ("cycle_end", "2026-07-14T09:15:00+00:00"),
        ("cycle_start", "2026-07-14T09:18:00+00:00"),
        ("cycle_end", "2026-07-14T09:28:00+00:00"),
    ):
        _event(conn, event_type, value, part_id if event_type == "cycle_end" else None)
    conn.execute(
        """INSERT INTO quality_checks (part_id,machine_id,result,source,ts)
           VALUES (?,?,'pass','test','2026-07-14T09:16:00+00:00')""",
        (part_id, _machine_id(conn)),
    )
    conn.execute(
        """INSERT INTO quality_checks (part_id,machine_id,result,source,ts)
           VALUES (?,?,'fail','test','2026-07-14T09:29:00+00:00')""",
        (part_id, _machine_id(conn)),
    )
    conn.commit()

    result = production_loss.build(
        conn, now=datetime(2026, 7, 14, 9, 30, tzinfo=timezone.utc),
        machine_key="gabbiani_pt80",
    )
    machine = result["machines"][0]
    categories = {item["category"]: item for item in machine["losses"]}
    assert result["shift"]["active"] is True
    assert result["shift"]["scheduled_end"] == "2026-07-14T10:00:00+00:00"
    assert machine["scheduled_s"] == 1800
    assert machine["running_s"] == 1200
    assert categories["unclassified_idle"]["seconds"] == 300
    assert categories["minor_stop"]["seconds"] == 300
    assert machine["availability"] == pytest.approx(2 / 3, abs=0.0001)
    assert machine["performance"] == 0.5
    assert machine["quality"] == 0.5
    assert machine["oee"] == pytest.approx(1 / 6, abs=0.0001)
    assert machine["waterfall"]["speed_loss_s"] == 600
    assert machine["waterfall"]["quality_loss_s"] == 300
    assert machine["waterfall"]["fully_productive_s"] == 300
    assert machine["reconciliation"] == {"timeline": True, "output_waterfall": True}
    assert machine["decision_ready"] is True


def test_absent_carry_in_is_unknown_not_invented_downtime(conn):
    _calendar(conn)
    result = production_loss.build(
        conn, now=datetime(2026, 7, 14, 9, 30, tzinfo=timezone.utc),
        machine_key="gabbiani_pt80",
    )
    machine = result["machines"][0]
    assert machine["telemetry_unknown_s"] == 1800
    assert machine["measured_availability_loss_s"] == 0
    assert machine["availability"] is None
    assert machine["oee"] is None
    assert machine["decision_ready"] is False
    assert result["recommendation"]["category"] == "telemetry_unknown"


def test_stale_prior_state_is_not_carried_into_shift(conn):
    _calendar(conn)
    _event(conn, "power_off", "2026-07-13T08:00:00+00:00")
    conn.commit()
    result = production_loss.build(
        conn, now=datetime(2026, 7, 14, 9, 30, tzinfo=timezone.utc),
        machine_key="gabbiani_pt80",
    )
    machine = result["machines"][0]
    assert machine["event_count"] == 0
    assert machine["telemetry_unknown_s"] == 1800


def test_planned_unavailability_is_excluded_before_loss_accounting(conn):
    _calendar(conn)
    _event(conn, "state_on", "2026-07-14T08:59:00+00:00")
    conn.execute(
        """INSERT INTO resource_unavailability
           (resource_type,resource_key,starts_at,ends_at,reason,source,created_by,created_at)
           VALUES ('machine','gabbiani_pt80',?,?,?,'manual','planner',?)""",
        ("2026-07-14T09:10:00+00:00", "2026-07-14T09:20:00+00:00",
         "Planned maintenance", "2026-07-14T08:00:00+00:00"),
    )
    conn.commit()
    result = production_loss.build(
        conn, now=datetime(2026, 7, 14, 9, 30, tzinfo=timezone.utc),
        machine_key="gabbiani_pt80",
    )
    machine = result["machines"][0]
    assert machine["scheduled_s"] == 1800
    assert machine["planned_stop_s"] == 600
    assert machine["planned_production_s"] == 1200
    assert machine["running_s"] == 1200
    assert machine["availability"] == 1.0
    assert machine["reconciliation"]["timeline"] is True


def test_reviewed_downtime_reason_overrides_raw_machine_state(conn):
    _calendar(conn)
    _event(conn, "state_on", "2026-07-14T08:59:00+00:00")
    reason_id = conn.execute(
        "SELECT id FROM downtime_reasons WHERE code='waiting_material'"
    ).fetchone()["id"]
    conn.execute(
        """INSERT INTO downtime_events
           (machine_id,reason_id,status,started_at,ended_at)
           VALUES (?,?,'closed',?,?)""",
        (_machine_id(conn), reason_id, "2026-07-14T09:10:00+00:00",
         "2026-07-14T09:20:00+00:00"),
    )
    conn.commit()
    result = production_loss.build(
        conn, now=datetime(2026, 7, 14, 9, 30, tzinfo=timezone.utc),
        machine_key="gabbiani_pt80",
    )
    machine = result["machines"][0]
    loss = next(item for item in machine["losses"]
                if item["category"] == "material_starvation")
    assert loss["seconds"] == 600
    assert loss["confidence"] == "high"
    assert loss["source"] == ["downtime:waiting_material"]
    assert machine["running_s"] == 1200
    assert machine["top_measured_loss"]["category"] == "material_starvation"


def test_unverified_calendar_and_incomplete_quality_keep_oee_provisional(conn):
    _calendar(conn, verified=False)
    part_id = _part(conn)
    _active_model(conn)
    _event(conn, "cycle_start", "2026-07-14T09:00:00+00:00")
    _event(conn, "cycle_end", "2026-07-14T09:10:00+00:00", part_id)
    _event(conn, "cycle_start", "2026-07-14T09:10:00+00:00")
    conn.commit()
    result = production_loss.build(
        conn, now=datetime(2026, 7, 14, 9, 30, tzinfo=timezone.utc),
        machine_key="gabbiani_pt80",
    )
    machine = result["machines"][0]
    assert machine["performance"] is not None
    assert machine["quality"] is None
    assert machine["oee"] is None
    assert machine["decision_ready"] is False
    assert next(item for item in machine["gates"] if item["key"] == "calendar")["passed"] is False
    assert next(item for item in machine["gates"] if item["key"] == "quality")["passed"] is False


def test_equal_quality_row_count_for_a_different_part_is_not_complete(conn):
    _calendar(conn)
    completed_part = _part(conn)
    conn.execute("INSERT INTO jobs (job_name,total_parts) VALUES ('LOSS-OTHER',1)")
    other_job = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    other_part = conn.execute(
        "INSERT INTO parts (job_id,part_name,length_mm,width_mm) VALUES (?,'Other',500,500)",
        (other_job,),
    ).lastrowid
    _active_model(conn)
    _event(conn, "cycle_start", "2026-07-14T09:00:00+00:00")
    _event(conn, "cycle_end", "2026-07-14T09:10:00+00:00", completed_part)
    conn.execute(
        """INSERT INTO quality_checks (part_id,machine_id,result,source,ts)
           VALUES (?,?,'pass','test','2026-07-14T09:11:00+00:00')""",
        (other_part, _machine_id(conn)),
    )
    conn.commit()
    result = production_loss.build(
        conn, now=datetime(2026, 7, 14, 9, 30, tzinfo=timezone.utc),
        machine_key="gabbiani_pt80",
    )
    machine = result["machines"][0]
    assert machine["quality_checks"] == 1
    assert machine["quality"] is None
    assert machine["oee"] is None
    assert machine["decision_ready"] is False


def test_explicit_date_uses_completed_overnight_shift(conn):
    _calendar(conn, start="22:00", end="06:00")
    shift = production_loss.resolve_window(
        conn, now=datetime(2026, 7, 16, 12, tzinfo=timezone.utc),
        local_date="2026-07-14",
    )
    assert shift["window_start"] == "2026-07-14T22:00:00+00:00"
    assert shift["window_end"] == "2026-07-15T06:00:00+00:00"
    assert shift["local_date"] == "2026-07-14"


def test_invalid_or_unscheduled_date_is_rejected(conn):
    _calendar(conn)
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        production_loss.resolve_window(conn, local_date="14-07-2026")
    with pytest.raises(ValueError, match="No factory calendar"):
        production_loss.resolve_window(conn, local_date="2026-07-15")


def test_production_loss_endpoint_supports_machine_filter_and_errors(conn):
    from fastapi.testclient import TestClient
    import main

    _calendar(conn)
    main.set_conn(conn)
    with TestClient(main.app) as client:
        main.set_conn(conn)
        response = client.get("/production-losses?machine_key=gabbiani_pt80")
        assert response.status_code == 200
        assert response.json()["method_version"] == production_loss.METHOD_VERSION
        assert len(response.json()["machines"]) == 1
        missing = client.get("/production-losses?machine_key=not-a-machine")
        assert missing.status_code == 404
        invalid = client.get("/production-losses?date=14-07-2026")
        assert invalid.status_code == 400
