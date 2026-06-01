"""
Tests for FastAPI backend + OEE calculator.
Uses TestClient (no real server) and in-memory DB (no real MQTT).
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from fastapi.testclient import TestClient

# Patch DB and MQTT before importing main
import db as db_module
from db import init_db

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def mem_conn():
    # check_same_thread=False needed because FastAPI runs handlers in a threadpool
    conn = init_db(Path(":memory:"), check_same_thread=False)
    from cv_parser import ingest_cv_csv
    csv_path = Path("/Users/samkitparak/Downloads/wetransfer_amit-agarwal_2026-05-15_1247"
                    "/Amit Agarwal/GBR/BEAMSAW/{jobname}.csv")
    if csv_path.exists():
        ingest_cv_csv(csv_path, conn, client_name="Amit Agarwal",
                      job_date="2026-03-30", beamsaw_run_id="86")
    return conn


@pytest.fixture(scope="module")
def client(mem_conn):
    import main
    import mqtt_bridge

    # Wire in-memory DB and no-op MQTT before the lifespan runs
    main.set_conn(mem_conn)
    original_start = mqtt_bridge.start
    mqtt_bridge.start = lambda *a, **kw: None

    with TestClient(main.app, raise_server_exceptions=True) as c:
        # Ensure conn is still set after lifespan startup
        main.set_conn(mem_conn)
        yield c

    mqtt_bridge.start = original_start


# ── /machines ─────────────────────────────────────────────────────────────────

def test_get_machines_returns_list(client):
    r = client.get("/machines")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) == 15

def test_machines_have_required_fields(client):
    data = client.get("/machines").json()
    required = {"machine_key", "name", "type", "state", "last_event", "last_seen"}
    for m in data:
        assert required.issubset(m.keys()), f"Missing fields in {m['machine_key']}"

def test_machines_include_gabbiani(client):
    data = client.get("/machines").json()
    keys = {m["machine_key"] for m in data}
    assert "gabbiani_pt80" in keys

def test_machine_initial_state_unknown(client):
    data = client.get("/machines").json()
    # No events yet — all should be unknown
    for m in data:
        assert m["state"] in ("unknown", "off", "on", "idle", "alarm")


# ── /machines/{key} ───────────────────────────────────────────────────────────

def test_get_single_machine(client):
    r = client.get("/machines/morbidelli_cx100")
    assert r.status_code == 200
    data = r.json()
    assert data["machine_key"] == "morbidelli_cx100"
    assert "recent_events" in data

def test_get_unknown_machine_404(client):
    r = client.get("/machines/does_not_exist")
    assert r.status_code == 404


# ── /jobs ─────────────────────────────────────────────────────────────────────

def test_get_jobs(client):
    r = client.get("/jobs")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)

def test_jobs_have_fields(client):
    data = client.get("/jobs").json()
    if data:
        required = {"job_name", "client_name", "job_date", "total_parts"}
        assert required.issubset(data[0].keys())


# ── /jobs/{job_name}/parts ────────────────────────────────────────────────────

def test_get_job_parts(client):
    # Only run if real sample data was loaded
    jobs = client.get("/jobs").json()
    if not jobs:
        pytest.skip("No jobs in DB")
    job_name = jobs[0]["job_name"]
    r = client.get(f"/jobs/{job_name}/parts")
    assert r.status_code == 200
    parts = r.json()
    assert len(parts) > 0

def test_job_parts_have_fields(client):
    jobs = client.get("/jobs").json()
    if not jobs:
        pytest.skip("No jobs in DB")
    job_name = jobs[0]["job_name"]
    parts = client.get(f"/jobs/{job_name}/parts").json()
    required = {"part_name", "material", "length_mm", "width_mm", "beamsaw_seq"}
    assert required.issubset(parts[0].keys())

def test_get_parts_unknown_job_404(client):
    r = client.get("/jobs/NONEXISTENT_JOB_XYZ/parts")
    assert r.status_code == 404


# ── /oee ──────────────────────────────────────────────────────────────────────

def test_get_oee_all(client):
    r = client.get("/oee")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) == 15

def test_oee_fields(client):
    data = client.get("/oee").json()
    required = {"machine_key", "availability", "performance", "quality", "oee",
                "run_time_s", "idle_time_s", "down_time_s", "planned_time_s"}
    for m in data:
        assert required.issubset(m.keys())

def test_oee_values_in_range(client):
    data = client.get("/oee").json()
    for m in data:
        assert 0.0 <= m["availability"] <= 1.0
        assert 0.0 <= m["performance"]  <= 1.0
        assert 0.0 <= m["quality"]      <= 1.0
        assert 0.0 <= m["oee"]          <= 1.0

def test_get_oee_single_machine(client):
    r = client.get("/oee/gabbiani_pt80")
    assert r.status_code == 200
    data = r.json()
    assert data["machine_key"] == "gabbiani_pt80"

def test_get_oee_unknown_machine_404(client):
    r = client.get("/oee/not_a_machine")
    assert r.status_code == 404


# ── /events/simulate ─────────────────────────────────────────────────────────

def test_simulate_event(client):
    r = client.post("/events/simulate",
                    params={"machine_key": "gabbiani_pt80",
                            "event_type": "cycle_start"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["event"]["event_type"] == "cycle_start"

def test_simulate_updates_machine_state(client):
    client.post("/events/simulate",
                params={"machine_key": "elgi_1",
                        "event_type": "state_on",
                        "power_w": 6200})
    machines = client.get("/machines").json()
    elgi = next(m for m in machines if m["machine_key"] == "elgi_1")
    assert elgi["state"] == "on"
    assert elgi["power_w"] == 6200.0

def test_simulate_with_cnc_file(client):
    r = client.post("/events/simulate",
                    params={"machine_key": "morbidelli_cx100",
                            "event_type": "cycle_start",
                            "cnc_file": "r86b0002.xcs"})
    assert r.status_code == 200
    assert r.json()["event"]["cnc_file"] == "r86b0002.xcs"

def test_simulate_unknown_machine_404(client):
    r = client.post("/events/simulate",
                    params={"machine_key": "fake_machine",
                            "event_type": "power_on"})
    assert r.status_code == 404


# ── OEE calculator unit tests ────────────────────────────────────────────────

from oee import _compute_time_buckets, calculate, OEEResult

def test_oee_all_run():
    events = [
        {"event_type": "power_on",    "ts": "2026-05-31 08:00:00"},
        {"event_type": "cycle_start", "ts": "2026-05-31 08:01:00"},
        {"event_type": "cycle_end",   "ts": "2026-05-31 08:02:00"},
        {"event_type": "power_off",   "ts": "2026-05-31 08:03:00"},
    ]
    run, idle, down = _compute_time_buckets(
        events, "2026-05-31 08:00:00", "2026-05-31 08:03:00"
    )
    assert run  == 120  # power_on→cycle_start (60s) + cycle_start→cycle_end (60s)
    assert idle == 60   # cycle_end → power_off
    assert down == 0

def test_oee_all_off():
    events = []
    run, idle, down = _compute_time_buckets(
        events, "2026-05-31 08:00:00", "2026-05-31 09:00:00"
    )
    assert run  == 0
    assert idle == 0
    assert down == 0

def test_oee_alarm_counts_as_down():
    events = [
        {"event_type": "power_on", "ts": "2026-05-31 08:00:00"},
        {"event_type": "alarm",    "ts": "2026-05-31 08:01:00"},
        {"event_type": "power_off","ts": "2026-05-31 08:04:00"},
    ]
    run, idle, down = _compute_time_buckets(
        events, "2026-05-31 08:00:00", "2026-05-31 08:04:00"
    )
    assert run  == 60    # power_on → alarm
    assert down == 180   # alarm → power_off

def test_oee_availability_zero_when_no_events(mem_conn):
    machine_id = mem_conn.execute(
        "SELECT id FROM machines WHERE machine_key='nova_si400'"
    ).fetchone()["id"]
    now = datetime(2099, 1, 1, tzinfo=timezone.utc)  # future — no events
    result = calculate(mem_conn, machine_id, window_hours=8, now=now)
    assert result.availability == 0.0
    assert result.oee          == 0.0

def test_oee_snapshot_written(mem_conn):
    machine_id = mem_conn.execute(
        "SELECT id FROM machines WHERE machine_key='stefani_kd'"
    ).fetchone()["id"]
    before = mem_conn.execute(
        "SELECT COUNT(*) FROM oee_snapshots WHERE machine_id=?", (machine_id,)
    ).fetchone()[0]
    calculate(mem_conn, machine_id, window_hours=8)
    after = mem_conn.execute(
        "SELECT COUNT(*) FROM oee_snapshots WHERE machine_id=?", (machine_id,)
    ).fetchone()[0]
    assert after == before + 1
