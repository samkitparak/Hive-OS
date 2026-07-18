"""Tests for current factory constraint detection."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import bottleneck
from db import init_db


@pytest.fixture
def conn():
    connection = init_db(":memory:", check_same_thread=False)
    yield connection
    connection.close()


def _event(conn, machine_key, event_type, ts, part_id=None):
    machine_id = conn.execute(
        "SELECT id FROM machines WHERE machine_key=?", (machine_key,)
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO machine_events (machine_id,event_type,part_id,ts) VALUES (?,?,?,?)",
        (machine_id, event_type, part_id, ts),
    )
    conn.commit()


def _active_job(conn, total=5):
    conn.execute("INSERT INTO jobs (job_name,total_parts) VALUES ('ACTIVE',?)", (total,))
    job_id = conn.execute("SELECT id FROM jobs WHERE job_name='ACTIVE'").fetchone()["id"]
    for index in range(total):
        conn.execute(
            """INSERT INTO parts
               (job_id,part_name,length_mm,width_mm,qty,has_cnc,eb1)
               VALUES (?,?,?,?,1,1,'tape')""",
            (job_id, f"Part {index}", 1000, 500),
        )
    conn.commit()
    return conn.execute("SELECT id FROM parts WHERE job_id=?", (job_id,)).fetchall()


def _release_route(conn, parts, machine_key="morbidelli_cx100"):
    now = "2026-06-04T08:00:00+00:00"
    job_id = conn.execute("SELECT job_id FROM parts WHERE id=?", (parts[0]["id"],)).fetchone()["job_id"]
    conn.execute(
        """INSERT INTO production_orders
           (job_id,status,priority,source,created_at,updated_at,released_by,released_at)
           VALUES (?,'released',50,'test',?,?,'test',?)""",
        (job_id, now, now, now),
    )
    machine_id = conn.execute(
        "SELECT id FROM machines WHERE machine_key=?", (machine_key,)
    ).fetchone()["id"]
    for part in parts:
        conn.execute(
            """INSERT INTO part_route_steps
               (part_id,step_index,machine_id,source,confidence,required,required_qty,
                confirmed_qty,status,created_at,updated_at)
               VALUES (?,1,?,'manual','confirmed',1,1,0,'planned',?,?)""",
            (part["id"], machine_id, now, now),
        )
    conn.commit()


def _seed_busy_cycles(conn, machine_key, parts, now):
    for index in range(6):
        start = now - timedelta(minutes=115 - index * 19)
        _event(conn, machine_key, "cycle_start", start.isoformat(), parts[index % len(parts)]["id"])
        _event(conn, machine_key, "cycle_end", (start + timedelta(minutes=17)).isoformat(), parts[index % len(parts)]["id"])


def test_empty_window_has_no_current_bottleneck(conn):
    report = bottleneck.detect(conn, now=datetime(2026, 6, 4, tzinfo=timezone.utc))
    assert report.current is None
    assert len(report.machines) == len(bottleneck.PRODUCTION_FLOW)


def test_busy_machine_with_queue_is_current_bottleneck(conn):
    now = datetime(2026, 6, 4, 12, tzinfo=timezone.utc)
    parts = _active_job(conn)
    _release_route(conn, parts)
    _event(conn, "morbidelli_cx100", "cycle_start",
           (now - timedelta(hours=1, minutes=50)).isoformat(), parts[0]["id"])
    _event(conn, "morbidelli_cx100", "cycle_end",
           (now - timedelta(minutes=10)).isoformat(), parts[0]["id"])
    conn.execute(
        """UPDATE part_route_steps SET confirmed_qty=1,status='confirmed'
           WHERE part_id=?""", (parts[0]["id"],),
    )
    conn.commit()

    report = bottleneck.detect(conn, window_hours=2, now=now)
    assert report.current is None
    assert report.candidate is not None
    assert report.candidate.machine_key == "morbidelli_cx100"
    assert report.candidate.queue_depth == 4
    assert report.candidate.demand_source == "planned_route"
    assert report.candidate.state == "capacity_constraint"
    assert report.candidate.score > 0


def test_alarm_without_released_demand_is_not_a_constraint(conn):
    now = datetime(2026, 6, 4, 12, tzinfo=timezone.utc)
    _event(conn, "dmc60_rcs135", "alarm", (now - timedelta(minutes=30)).isoformat())
    report = bottleneck.detect(conn, window_hours=1, now=now)
    dmc = next(m for m in report.machines if m.machine_key == "dmc60_rcs135")
    assert dmc.alarms == 1
    assert dmc.state == "demand_absent"
    assert report.candidate is None


def test_overlapping_downtime_is_counted_once_and_has_no_gain_without_demand(conn):
    now = datetime(2026, 6, 4, 12, tzinfo=timezone.utc)
    machine_id = conn.execute(
        "SELECT id FROM machines WHERE machine_key='stefani_kd'"
    ).fetchone()["id"]
    conn.execute(
        """INSERT INTO downtime_events (machine_id,status,started_at)
           VALUES (?,'open',?)""", (machine_id, (now - timedelta(hours=2)).isoformat()),
    )
    conn.execute(
        """INSERT INTO downtime_events (machine_id,status,started_at)
           VALUES (?,'open',?)""", (machine_id, (now - timedelta(hours=1)).isoformat()),
    )
    conn.commit()
    report = bottleneck.detect(conn, window_hours=4, now=now)
    machine = next(item for item in report.machines if item.machine_key == "stefani_kd")
    assert machine.downtime_s == 2 * 60 * 60
    assert machine.score == 0
    assert machine.recoverable_minutes is None
    assert machine.estimated_recoverable_units is None


def test_route_demand_does_not_inflate_unrouted_machines(conn):
    now = datetime(2026, 6, 4, 12, tzinfo=timezone.utc)
    parts = _active_job(conn, total=3)
    _release_route(conn, parts, "morbidelli_cx100")
    report = bottleneck.detect(conn, window_hours=2, now=now)
    cnc = next(m for m in report.machines if m.machine_key == "morbidelli_cx100")
    edge = next(m for m in report.machines if m.machine_key == "stefani_kd")
    assert cnc.demand_qty == 3
    assert edge.demand_qty == 0
    assert edge.state == "demand_absent"


def test_incomplete_predecessor_is_starvation_not_capacity(conn):
    now = datetime(2026, 6, 4, 12, tzinfo=timezone.utc)
    parts = _active_job(conn, total=1)
    _release_route(conn, parts, "gabbiani_pt80")
    downstream_id = conn.execute(
        "SELECT id FROM machines WHERE machine_key='morbidelli_cx100'"
    ).fetchone()["id"]
    conn.execute(
        """INSERT INTO part_route_steps
           (part_id,step_index,machine_id,source,confidence,required,required_qty,
            confirmed_qty,status,created_at,updated_at)
           VALUES (?,2,?,'manual','confirmed',1,1,0,'planned',?,?)""",
        (parts[0]["id"], downstream_id, now.isoformat(), now.isoformat()),
    )
    conn.commit()
    report = bottleneck.detect(conn, window_hours=2, now=now)
    downstream = next(m for m in report.machines if m.machine_key == "morbidelli_cx100")
    assert downstream.demand_qty == 1
    assert downstream.ready_qty == 0
    assert downstream.starved_qty == 1
    assert downstream.state == "starved"
    assert report.candidate is None


def test_get_is_read_only_and_two_samples_open_episode(conn):
    now = datetime(2026, 6, 4, 12, tzinfo=timezone.utc)
    parts = _active_job(conn, total=6)
    _release_route(conn, parts)
    _seed_busy_cycles(conn, "morbidelli_cx100", parts, now)
    _event(conn, "morbidelli_cx100", "cycle_start",
           (now - timedelta(minutes=1)).isoformat(), parts[0]["id"])
    _event(conn, "morbidelli_cx100", "cycle_end",
           (now + timedelta(minutes=5)).isoformat(), parts[0]["id"])

    report = bottleneck.detect(conn, window_hours=2, now=now)
    assert report.current is not None
    assert conn.execute("SELECT COUNT(*) FROM constraint_snapshots").fetchone()[0] == 0

    first = bottleneck.sync(conn, actor="test", window_hours=2, now=now)
    assert first["episode"]["status"] == "observing"
    second = bottleneck.sync(
        conn, actor="test", window_hours=2, now=now + timedelta(minutes=6)
    )
    assert second["episode"]["status"] == "open"
    assert second["episode"]["machine_key"] == "morbidelli_cx100"
    assert conn.execute("SELECT COUNT(*) FROM constraint_snapshots").fetchone()[0] == 2


def test_bottleneck_endpoint(conn):
    from fastapi.testclient import TestClient
    import main

    main.set_conn(conn)
    with TestClient(main.app) as client:
        main.set_conn(conn)
        response = client.get("/bottlenecks?window_hours=4")
        assert response.status_code == 200
        data = response.json()
        assert data["window_hours"] == 4
        assert data["method_version"] == bottleneck.METHOD_VERSION
        assert "current" in data
        assert len(data["machines"]) == len(bottleneck.PRODUCTION_FLOW)
        sync = client.post("/constraints/sync", json={"window_hours": 4, "actor": "test"})
        assert sync.status_code == 200
        assert sync.json()["snapshot_id"] > 0
