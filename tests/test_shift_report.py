"""Tests for shift_report.py."""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from db import init_db
import shift_report as sr


@pytest.fixture
def conn():
    c = init_db(":memory:", check_same_thread=False)
    yield c
    c.close()


def _add_event(conn, machine_key, event_type, ts=None):
    m = conn.execute(
        "SELECT id FROM machines WHERE machine_key=?", (machine_key,)
    ).fetchone()
    assert m, f"Machine {machine_key} not in DB"
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO machine_events (machine_id, event_type, ts) VALUES (?,?,?)",
        (m["id"], event_type, ts)
    )
    conn.commit()


def test_build_returns_shift_report(conn):
    report = sr.build(conn)
    assert isinstance(report, sr.ShiftReport)
    assert report.date == datetime.now(timezone.utc).date().isoformat()


def test_build_empty_db(conn):
    report = sr.build(conn)
    assert report.total_parts == 0
    assert report.alarms == 0
    assert report.machines == []
    assert report.jobs == []


def test_build_counts_parts(conn):
    today = datetime.now(timezone.utc).isoformat()
    # Add a job and parts
    conn.execute("INSERT INTO jobs (job_name, total_parts) VALUES ('J1', 5)")
    job_id = conn.execute("SELECT id FROM jobs WHERE job_name='J1'").fetchone()["id"]
    for i in range(3):
        conn.execute(
            "INSERT INTO parts (job_id, part_name, qty) VALUES (?,?,1)",
            (job_id, f"Part{i}")
        )
    parts = conn.execute("SELECT id FROM parts WHERE job_id=?", (job_id,)).fetchall()
    conn.commit()

    machine = conn.execute(
        "SELECT id FROM machines WHERE machine_key='morbidelli_cx100'"
    ).fetchone()
    for p in parts:
        conn.execute(
            "INSERT INTO machine_events (machine_id, event_type, part_id, ts) VALUES (?,?,?,?)",
            (machine["id"], "cycle_end", p["id"], today)
        )
    conn.commit()

    report = sr.build(conn)
    assert report.total_parts == 3


def test_build_counts_alarms(conn):
    today = datetime.now(timezone.utc).isoformat()
    _add_event(conn, "morbidelli_cx100", "power_on", today)
    _add_event(conn, "morbidelli_cx100", "alarm",    today)
    _add_event(conn, "morbidelli_cx100", "alarm",    today)

    report = sr.build(conn)
    assert report.alarms == 2


def test_render_html_returns_string(conn):
    report = sr.build(conn)
    html   = sr.render_html(report)
    assert isinstance(html, str)
    assert "HIVE OS" in html
    assert "Shift Report" in html


def test_render_html_contains_date(conn):
    today  = datetime.now(timezone.utc).date().isoformat()
    report = sr.build(conn)
    html   = sr.render_html(report)
    assert today in html


def test_render_html_shows_machine_when_active(conn):
    today = datetime.now(timezone.utc).isoformat()
    _add_event(conn, "morbidelli_cx100", "power_on",  today)
    _add_event(conn, "morbidelli_cx100", "cycle_end", today)

    report = sr.build(conn)
    html   = sr.render_html(report)
    assert "Morbidelli CX100" in html


def test_shift_report_endpoint(conn):
    """FastAPI endpoint returns HTML."""
    from fastapi.testclient import TestClient
    import main
    main.set_conn(conn)

    with TestClient(main.app) as client:
        main.set_conn(conn)
        resp = client.get("/report/shift")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "HIVE OS" in resp.text


def test_shift_report_endpoint_with_date(conn):
    from fastapi.testclient import TestClient
    import main
    main.set_conn(conn)

    with TestClient(main.app) as client:
        main.set_conn(conn)
        resp = client.get("/report/shift?date=2026-06-01")
        assert resp.status_code == 200
        assert "2026-06-01" in resp.text
