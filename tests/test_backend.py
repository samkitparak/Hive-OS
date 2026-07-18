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
import mqtt_bridge

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


def test_api_prefix_routes_to_backend(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "hive-os", "version": "0.29.0"}
    assert client.get("/api/machines").status_code == 200


def test_learning_and_twin_endpoints(client):
    learning = client.get("/api/learning/status")
    routes = client.get("/api/routing/graph")
    readiness = client.get("/api/digital-twin/readiness")
    assert learning.status_code == 200
    assert routes.status_code == 200
    assert readiness.status_code == 200
    assert "active_models" in learning.json()
    assert "edges" in routes.json()
    assert "model_coverage" in readiness.json()


def test_changeover_standard_and_evidence_endpoints(client):
    snapshot = client.get("/api/changeovers")
    assert snapshot.status_code == 200
    state = snapshot.json()
    saw = next(
        item for item in state["machines"] if item["machine_key"] == "gabbiani_pt80"
    )
    updated = client.put("/api/changeovers/machines/gabbiani_pt80/standard", json={
        "default_setup_s": 480,
        "verified": True,
        "expected_version": saw["version"],
        "actor": "api-test",
        "notes": "Conservative API test standard",
    })
    assert updated.status_code == 200
    assert updated.json()["verified"] is True
    stale = client.put("/api/changeovers/machines/gabbiani_pt80/standard", json={
        "default_setup_s": 500,
        "verified": True,
        "expected_version": saw["version"],
        "actor": "api-test",
    })
    assert stale.status_code == 400

    observed = client.post("/api/changeovers/observations", json={
        "machine_key": "gabbiani_pt80",
        "from_setup_key": "MATERIAL|API-A",
        "to_setup_key": "MATERIAL|API-B",
        "duration_s": 321,
        "observed_at": "2026-07-18T10:30:00+05:30",
        "source": "manual_time_study",
        "quality_confirmed": True,
        "actor": "api-test",
    })
    assert observed.status_code == 200
    assert observed.json()["status"] == "accepted"
    observation_id = observed.json()["observation_id"]
    excluded = client.post(
        f"/api/changeovers/observations/{observation_id}/exclude",
        json={"reason": "API test cleanup", "actor": "api-test"},
    )
    assert excluded.status_code == 200
    assert excluded.json()["status"] == "excluded"
    assert client.post("/api/changeovers/sync", json={
        "include_downtime": False, "actor": "api-test",
    }).status_code == 200


def test_assumption_only_commissioning_lab_endpoints(client, mem_conn):
    snapshot = client.get("/api/commissioning-lab")
    assert snapshot.status_code == 200
    assert snapshot.json()["assumptions"]["production_eligible"] is False
    run = client.post("/api/commissioning-lab/run", json={
        "samples": 10, "seed": 19, "actor": "api-test",
    })
    assert run.status_code == 200
    assert run.json()["status"] == "assumption_only"
    assert client.get("/api/commissioning-lab/history?limit=1").json()[0]["id"] == run.json()["run_id"]
    assert client.post("/api/commissioning-lab/run", json={"samples": 9}).status_code == 422
    assert mem_conn.execute("SELECT COUNT(*) FROM virtual_factory_runs").fetchone()[0] >= 1


def test_guided_commissioning_evidence_endpoints_and_pack(client):
    snapshot = client.get("/api/commissioning-evidence")
    assert snapshot.status_code == 200
    assert snapshot.json()["production_eligible"] is False
    assert len(snapshot.json()["protocols"]) == 11
    pack = client.get("/api/commissioning-evidence/pack")
    assert pack.status_code == 200
    assert pack.headers["content-type"] == "application/zip"
    assert len(pack.headers["x-hive-pack-sha256"]) == 64
    study = client.post("/api/commissioning-evidence/studies", json={
        "machine_key": "superfici", "target_samples": 5,
        "target_strata": 1, "actor": "api-test",
    })
    assert study.status_code == 200
    study_id = study.json()["id"]
    payload = {
        "source_record_id": "api-001", "measured_at": "2026-08-03T10:00:00+05:30",
        "measurement_method": "stopwatch", "observer": "API tester",
        "product_family": "painted_panel", "process_s": 120, "load_s": 10,
        "unload_s": 8, "actor": "api-test",
    }
    created = client.post(
        f"/api/commissioning-evidence/studies/{study_id}/observations", json=payload,
    )
    assert created.status_code == 200 and created.json()["status"] == "accepted"
    duplicate = client.post(
        f"/api/commissioning-evidence/studies/{study_id}/observations", json=payload,
    )
    assert duplicate.status_code == 200 and duplicate.json()["status"] == "duplicate"
    detail = client.get(f"/api/commissioning-evidence/studies/{study_id}")
    assert detail.status_code == 200
    assert detail.json()["analysis"]["sample_count"] == 1
    analysis = client.post(f"/api/commissioning-evidence/studies/{study_id}/analyze")
    assert analysis.status_code == 200
    assert analysis.json()["production_eligible"] is False
    assert client.get("/api/commissioning-evidence/studies/999999").status_code == 404


def test_factory_readiness_endpoints_pack_passport_import_and_probe_preview(client):
    snapshot = client.get("/api/factory-readiness")
    assert snapshot.status_code == 200
    state = snapshot.json()
    assert state["summary"]["machines"] == 15
    action = next(item for item in state["machines"] if item["machine_key"] == "action_e")
    assert action["research"]["preferred_strategy"] == "operator_evidence"
    assert action["research"]["assumption_only"] is True

    pack = client.get("/api/factory-readiness/pack")
    assert pack.status_code == 200
    assert pack.headers["content-type"] == "application/zip"
    assert len(pack.headers["x-hive-pack-sha256"]) == 64

    updated = client.put("/api/factory-readiness/machines/action_e", json={
        "expected_version": action["passport"]["version"],
        "status": "inventory", "asset_tag": "API-ACTION-E",
        "physical_location": "Assembly bay", "telemetry_strategy": "operator_evidence",
        "actor": "api-test",
    })
    assert updated.status_code == 200
    assert updated.json()["version"] == action["passport"]["version"] + 1
    stale = client.put("/api/factory-readiness/machines/action_e", json={
        "expected_version": action["passport"]["version"], "notes": "stale",
    })
    assert stale.status_code == 400

    inventory = client.post("/api/factory-readiness/import", json={
        "csv_text": (
            "machine_key,expected_version,status,notes\n"
            f"action_e,{updated.json()['version']},inventory,CSV preview only\n"
        ),
        "apply": False, "actor": "api-test",
    })
    assert inventory.status_code == 200
    assert inventory.json()["valid"] is True
    assert inventory.json()["rows_changed"] == 1

    probe = client.post("/api/factory-readiness/machines/action_e/probe", json={
        "probe_type": "tcp", "host": "127.0.0.1", "port": 22,
        "execute": False, "actor": "api-test",
    })
    assert probe.status_code == 200
    assert probe.json()["status"] == "preview_ready"
    assert probe.json()["will_write_device"] is False

    mission = client.post("/api/factory-readiness/machines/action_e/mission", json={
        "actor": "api-test", "notes": "Assembly commissioning",
    })
    assert mission.status_code == 200
    assert mission.json()["status"] == "in_progress"
    assert mission.json()["current_step"]["key"] == "passport"
    paused = client.post("/api/factory-readiness/machines/action_e/mission/action", json={
        "action": "pause", "expected_version": mission.json()["version"], "actor": "api-test",
    })
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"


def test_twin_rejects_unknown_policy(client):
    response = client.post("/api/digital-twin/compare", json={"policies": ["magic"]})
    assert response.status_code == 400


def test_production_control_and_planning_endpoints(client):
    assert client.get("/api/production/orders").status_code == 200
    readiness = client.get("/api/production/readiness")
    assert readiness.status_code == 200
    assert "checks" in readiness.json()
    assert client.get("/api/production/route-exceptions").status_code == 200
    assert client.get("/api/planning/scenarios").status_code == 200
    assert client.get("/api/planning/active-schedule").status_code == 200
    recovery = client.get("/api/recovery")
    assert recovery.status_code == 200
    assert recovery.json()["status"] == "waiting_for_schedule"
    scenario = client.post("/api/planning/scenarios", json={
        "created_by": "test", "policies": ["fifo"], "seed": 1,
    })
    assert scenario.status_code == 200
    assert "readiness" in scenario.json()


def test_unknown_production_route_is_404(client):
    assert client.get("/api/production/routes/NOT-A-JOB").status_code == 404


def test_resource_endpoints_expose_defaults_and_validate_capacity(client):
    snapshot = client.get("/api/resources/snapshot")
    assert snapshot.status_code == 200
    assert {"materials", "labor_roles", "tool_pools", "machine_profiles", "calendar"}.issubset(
        snapshot.json()
    )
    labor = client.put("/api/resources/labor/cutting_operator", json={
        "headcount": 1, "verified": False, "actor": "test",
    })
    assert labor.status_code == 200
    invalid_tooling = client.put("/api/resources/tooling/cutting_tooling", json={
        "total_qty": 1, "available_qty": 2, "verified": True, "actor": "test",
    })
    assert invalid_tooling.status_code == 400
    invalid_window = client.post("/api/resources/unavailability", json={
        "resource_key": "gabbiani_pt80",
        "starts_at": "2026-07-14T10:00:00+00:00",
        "ends_at": "2026-07-14T09:00:00+00:00",
        "reason": "invalid", "actor": "test",
    })
    assert invalid_window.status_code == 400


def test_execution_endpoints_wait_safely_without_an_approved_schedule(client):
    snapshot = client.get("/api/execution/snapshot")
    assert snapshot.status_code == 200
    assert snapshot.json()["status"] == "waiting_for_approved_schedule"
    assert snapshot.json()["jobs"] == []
    assert client.post("/api/execution/sync").status_code == 200
    assert client.get("/api/execution/events").json() == []
    assert client.get("/api/traceability/events").json() == []
    missing = client.post("/api/execution/jobs/999/action", json={
        "action": "dispatch", "actor": "test",
    })
    assert missing.status_code == 404


def test_identity_and_label_endpoints_start_safely(client):
    snapshot = client.get("/api/identity/snapshot")
    assert snapshot.status_code == 200
    assert {"summary", "orders", "print_jobs", "identity_policy"}.issubset(snapshot.json())
    assert client.get("/api/identity/units/HU-NOT-REAL").status_code == 404
    missing_order = client.post("/api/labels/jobs", json={
        "order_id": 999999, "requested_by": "test",
    })
    assert missing_order.status_code == 404


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


def test_event_broadcast_reaches_all_subscribers():
    first = mqtt_bridge.subscribe_events()
    second = mqtt_bridge.subscribe_events()
    event = {"machine_key": "gabbiani_pt80", "event_type": "cycle_start"}
    try:
        mqtt_bridge.publish_event(event)
        assert first.get_nowait() == event
        assert second.get_nowait() == event
    finally:
        mqtt_bridge.unsubscribe_events(first)
        mqtt_bridge.unsubscribe_events(second)


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
