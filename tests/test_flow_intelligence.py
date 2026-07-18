"""Tests for sampled WIP, revisioned shift close, and flow evidence gates."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import flow_intelligence as flow
from db import init_db


@pytest.fixture
def conn():
    connection = init_db(":memory:", check_same_thread=False)
    yield connection
    connection.close()


def _calendar(conn):
    conn.execute("DELETE FROM work_calendar_windows")
    conn.execute(
        """INSERT INTO work_calendar_windows
           (resource_type,resource_key,weekday,start_time,end_time,capacity,timezone,
            source,verified,active,updated_at)
           VALUES ('factory','factory',0,'08:00','09:00',1,'UTC','site',1,1,?)""",
        ("2026-07-01T00:00:00+00:00",),
    )
    conn.commit()


def _machine(conn, key):
    return conn.execute("SELECT id FROM machines WHERE machine_key=?", (key,)).fetchone()["id"]


def _seed_route(conn, *, second_state="available"):
    created = "2026-07-13T07:30:00+00:00"
    first_done = "2026-07-13T08:00:00+00:00"
    conn.execute("INSERT INTO jobs (job_name,total_parts) VALUES ('FLOW-1',1)")
    job_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    conn.execute("INSERT INTO parts (job_id,part_name,qty) VALUES (?,'Panel',1)", (job_id,))
    part_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    conn.execute(
        """INSERT INTO production_orders
           (job_id,status,priority,source,version,released_by,released_at,created_at,updated_at)
           VALUES (?,'released',50,'test',1,'planner',?,?,?)""",
        (job_id, created, created, created),
    )
    order_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    conn.execute(
        """INSERT INTO planning_scenarios
           (name,created_by,request_json,result_json,readiness_json,input_signature,status,
            selected_policy,approved_by,approved_at,created_at)
           VALUES ('Flow','planner','{}','{}','{}','flow-test','approved','fifo',
                   'planner',?,?)""", (created, created),
    )
    scenario_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    conn.execute(
        """INSERT INTO production_schedule_items
           (scenario_id,production_order_id,position) VALUES (?,?,1)""",
        (scenario_id, order_id),
    )
    schedule_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    saw, cnc = _machine(conn, "gabbiani_pt80"), _machine(conn, "morbidelli_cx100")
    conn.execute(
        """INSERT INTO part_route_steps
           (part_id,step_index,machine_id,source,confidence,required,required_qty,
            confirmed_qty,status,confirmed_at,created_at,updated_at)
           VALUES (?,1,?,'manual','confirmed',1,1,1,'confirmed',?,?,?)""",
        (part_id, saw, first_done, created, first_done),
    )
    first_step = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    conn.execute(
        """INSERT INTO part_route_steps
           (part_id,step_index,machine_id,source,confidence,required,required_qty,
            confirmed_qty,status,created_at,updated_at)
           VALUES (?,2,?,'manual','confirmed',1,1,0,'planned',?,?)""",
        (part_id, cnc, created, first_done),
    )
    second_step = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    conn.execute(
        """INSERT INTO execution_jobs
           (scenario_id,schedule_item_id,production_order_id,route_step_id,machine_id,
            dispatch_sequence,state,required_qty,completed_qty,started_at,completed_at,
            created_at,updated_at)
           VALUES (?,?,?,?,?,1,'completed',1,1,?,?,?,?)""",
        (scenario_id, schedule_id, order_id, first_step, saw,
         "2026-07-13T07:50:00+00:00", first_done, created, first_done),
    )
    first_job = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    conn.execute(
        """INSERT INTO execution_job_events
           (execution_job_id,event_type,from_state,to_state,good_qty,scrap_qty,source,
            actor,ts) VALUES (?,'completed','running','completed',1,0,'machine_event',
                              'agent',?)""", (first_job, first_done),
    )
    conn.execute(
        """INSERT INTO execution_jobs
           (scenario_id,schedule_item_id,production_order_id,route_step_id,machine_id,
            dispatch_sequence,state,required_qty,created_at,updated_at)
           VALUES (?,?,?,?,?,2,?,1,?,?)""",
        (scenario_id, schedule_id, order_id, second_step, cnc,
         second_state, created, first_done),
    )
    second_job = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    conn.execute(
        """INSERT INTO execution_job_events
           (execution_job_id,event_type,from_state,to_state,source,actor,ts)
           VALUES (?,'availability_changed','queued','available','system','system',?)""",
        (second_job, first_done),
    )
    conn.execute(
        """INSERT INTO wip_buffers
           (machine_id,capacity_qty,current_qty,source,verified,updated_at)
           VALUES (?,10,1,'execution',1,?)
           ON CONFLICT(machine_id) DO UPDATE SET current_qty=1,source='execution',
               verified=1,updated_at=excluded.updated_at""", (cnc, first_done),
    )
    conn.execute(
        "INSERT INTO machine_events (machine_id,event_type,part_id,ts) VALUES (?,'cycle_end',?,?)",
        (saw, part_id, first_done),
    )
    conn.commit()
    return {"part_id": part_id, "first_job": first_job, "second_job": second_job,
            "saw": saw, "cnc": cnc}


def test_empty_snapshot_does_not_invent_wip(conn):
    current = flow.current_snapshot(conn, datetime(2026, 7, 13, 8, 30, tzinfo=timezone.utc))
    assert current["status"] == "waiting_for_schedule"
    assert current["summary"]["ready_wip_qty"] == 0
    assert current["summary"]["released_queue_qty"] == 0
    assert current["summary"]["physical_evidence_ratio"] is None


def test_downstream_ready_work_is_wip_with_physical_predecessor_evidence(conn):
    _calendar(conn)
    _seed_route(conn)
    current = flow.current_snapshot(conn, datetime(2026, 7, 13, 8, 30, tzinfo=timezone.utc))
    cnc = next(item for item in current["machines"] if item["machine_key"] == "morbidelli_cx100")
    assert cnc["released_queue_qty"] == 0
    assert cnc["ready_wip_qty"] == 1
    assert cnc["physically_observed_qty"] == 1
    assert cnc["ready_age_p90_s"] == 1800
    assert cnc["buffer"]["reconciled"] is True
    assert current["summary"]["physical_evidence_ratio"] == 1
    assert current["summary"]["decision_ready"] is True


def test_first_operation_release_is_queue_intent_not_wip(conn):
    _calendar(conn)
    seeded = _seed_route(conn)
    created = "2026-07-13T08:05:00+00:00"
    first_step = conn.execute(
        "SELECT id FROM part_route_steps WHERE part_id=? AND step_index=1", (seeded["part_id"],)
    ).fetchone()["id"]
    conn.execute(
        """UPDATE execution_jobs SET state='available',completed_qty=0,started_at=NULL,
              completed_at=NULL,updated_at=? WHERE route_step_id=?""", (created, first_step),
    )
    conn.commit()
    current = flow.current_snapshot(conn, datetime(2026, 7, 13, 8, 30, tzinfo=timezone.utc))
    saw = next(item for item in current["machines"] if item["machine_key"] == "gabbiani_pt80")
    assert saw["released_queue_qty"] == 1
    assert saw["ready_wip_qty"] == 0


def test_sampling_is_idempotent_within_five_minute_bucket(conn):
    _calendar(conn)
    _seed_route(conn)
    first = flow.capture_sample(conn, datetime(2026, 7, 13, 8, 1, tzinfo=timezone.utc))
    second = flow.capture_sample(conn, datetime(2026, 7, 13, 8, 4, tzinfo=timezone.utc))
    assert first["created"] is True
    assert second["created"] is False
    assert first["sample_id"] == second["sample_id"]
    assert conn.execute("SELECT COUNT(*) FROM flow_samples").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM flow_machine_samples").fetchone()[0] == 11


def test_shift_close_is_decision_ready_idempotent_and_revisioned(conn):
    _calendar(conn)
    seeded = _seed_route(conn)
    for minute in range(0, 60, 5):
        flow.capture_sample(
            conn, datetime(2026, 7, 13, 8, minute, tzinfo=timezone.utc)
        )
    completed = "2026-07-13T08:40:00+00:00"
    conn.execute(
        """UPDATE execution_jobs SET state='completed',completed_qty=1,in_process_qty=0,
              started_at=?,completed_at=?,updated_at=? WHERE id=?""",
        ("2026-07-13T08:10:00+00:00", completed, completed, seeded["second_job"]),
    )
    conn.execute(
        """INSERT INTO execution_job_events
           (execution_job_id,event_type,from_state,to_state,good_qty,scrap_qty,source,actor,ts)
           VALUES (?,'completed','running','completed',1,0,'machine_event','agent',?)""",
        (seeded["second_job"], completed),
    )
    conn.commit()
    now = datetime(2026, 7, 13, 10, tzinfo=timezone.utc)
    first = flow.archive_shift(conn, "2026-07-13", now, "test")
    same = flow.archive_shift(conn, "2026-07-13", now, "test")
    assert first["created"] is True
    assert first["summary"]["sample_coverage"] == 1
    assert first["summary"]["decision_ready"] is True
    assert same["created"] is False
    assert same["revision"] == 1

    conn.execute(
        "INSERT INTO machine_events (machine_id,event_type,part_id,ts) VALUES (?,'alarm',?,?)",
        (seeded["cnc"], seeded["part_id"], "2026-07-13T08:25:00+00:00"),
    )
    conn.commit()
    revised = flow.archive_shift(conn, "2026-07-13", now, "late-evidence")
    assert revised["created"] is True
    assert revised["revision"] == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM flow_shift_snapshots WHERE is_current=1"
    ).fetchone()[0] == 1


def test_active_shift_cannot_be_closed(conn):
    _calendar(conn)
    with pytest.raises(ValueError, match="completed"):
        flow.archive_shift(
            conn, "2026-07-13", datetime(2026, 7, 13, 8, 30, tzinfo=timezone.utc), "test"
        )


def _history_shift(index, throughput=10.0, wip=5.0):
    return {
        "summary": {
            "decision_ready": True, "average_wip": wip, "queue_time_p90_s": 600,
            "operation_flow_p90_s": 900, "throughput_per_hour": throughput,
        },
        "top_flow_pressure": {
            "machine_key": "morbidelli_cx100", "machine_name": "Morbidelli CX100",
            "average_pressure_score": 65,
        },
        "machines": [{
            "machine_key": "morbidelli_cx100", "machine_name": "Morbidelli CX100",
            "average_wip": wip, "throughput_per_hour": throughput,
        }],
        "local_date": f"2026-06-{index + 1:02d}",
    }


def test_little_law_requires_stable_history():
    learning = flow._historical_intelligence([_history_shift(index) for index in range(29)])
    assert learning["little_law"][0]["estimated_flow_time_h"] is None
    ready = flow._historical_intelligence([_history_shift(index) for index in range(30)])
    assert ready["little_law"][0]["status"] == "decision_support"
    assert ready["little_law"][0]["estimated_flow_time_h"] == 0.5
    assert ready["baselines"]["average_wip"]["control_limit_ready"] is False


def test_flow_endpoints(conn):
    import main

    main.set_conn(conn)
    with TestClient(main.app) as client:
        main.set_conn(conn)
        response = client.get("/flow-intelligence?days=30")
        assert response.status_code == 200
        assert response.json()["method_version"] == flow.METHOD_VERSION
        sync = client.post("/flow-intelligence/sync", json={"actor": "Test Supervisor"})
        assert sync.status_code == 200
        assert sync.json()["sample"]["sample_id"] > 0
