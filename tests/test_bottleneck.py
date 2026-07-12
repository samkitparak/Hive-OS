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


def test_empty_window_has_no_current_bottleneck(conn):
    report = bottleneck.detect(conn, now=datetime(2026, 6, 4, tzinfo=timezone.utc))
    assert report.current is None
    assert len(report.machines) == len(bottleneck.PRODUCTION_FLOW)


def test_busy_machine_with_queue_is_current_bottleneck(conn):
    now = datetime(2026, 6, 4, 12, tzinfo=timezone.utc)
    parts = _active_job(conn)
    _event(conn, "morbidelli_cx100", "cycle_start",
           (now - timedelta(hours=1)).isoformat(), parts[0]["id"])
    _event(conn, "morbidelli_cx100", "cycle_end",
           (now - timedelta(minutes=10)).isoformat(), parts[0]["id"])
    _event(conn, "stefani_kd", "idle",
           (now - timedelta(hours=1)).isoformat())

    report = bottleneck.detect(conn, window_hours=2, now=now)
    assert report.current is not None
    assert report.current.machine_key == "morbidelli_cx100"
    assert report.current.queue_depth == 4
    assert report.current.score > 0


def test_alarm_increases_constraint_score(conn):
    now = datetime(2026, 6, 4, 12, tzinfo=timezone.utc)
    _event(conn, "dmc60_rcs135", "alarm", (now - timedelta(minutes=30)).isoformat())
    report = bottleneck.detect(conn, window_hours=1, now=now)
    dmc = next(m for m in report.machines if m.machine_key == "dmc60_rcs135")
    assert dmc.alarms == 1
    assert dmc.score >= 0.1
    assert "alarm" in dmc.recommendation.lower()


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
        assert "current" in data
        assert len(data["machines"]) == len(bottleneck.PRODUCTION_FLOW)
