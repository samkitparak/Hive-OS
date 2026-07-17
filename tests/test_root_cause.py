"""Evidence-ranked diagnosis, confirmation, and learning behavior."""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import optimization
import root_cause
from db import init_db


NOW = datetime(2026, 7, 14, 12, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    connection = init_db(":memory:", check_same_thread=False)
    yield connection
    connection.close()


def _machine(conn):
    return conn.execute(
        "SELECT id FROM machines WHERE machine_key='morbidelli_cx100'"
    ).fetchone()["id"]


def _downtime(conn, code="waiting_material", occurred=NOW):
    reason = conn.execute(
        "SELECT id FROM downtime_reasons WHERE code=?", (code,)
    ).fetchone()["id"]
    cursor = conn.execute(
        """INSERT INTO downtime_events
           (machine_id,reason_id,status,started_at,ended_at,notes)
           VALUES (?,?,'closed',?,?,?)""",
        (_machine(conn), reason, occurred.isoformat(),
         (occurred + timedelta(minutes=30)).isoformat(), "test incident"),
    )
    conn.commit()
    return cursor.lastrowid


def test_material_wait_ranks_material_unavailable_with_visible_gaps(conn):
    _downtime(conn)
    result = root_cause.sync(conn, now=NOW + timedelta(hours=1), actor="test engine")
    case = result["cases"][0]
    assert result["sync"]["created"] == 1
    assert case["top_hypothesis_code"] == "material_unavailable"
    assert case["confidence"] == "high"
    assert case["hypotheses"][0]["evidence"][0]["source"] == "downtime_reason"
    assert "No physical part is linked near the incident" in case["hypotheses"][0]["data_gaps"]


def test_alarm_and_interrupted_cycle_rank_reliability(conn):
    machine_id = _machine(conn)
    conn.execute(
        "INSERT INTO machine_events (machine_id,event_type,cnc_file,ts) VALUES (?,'cycle_start','P100',?)",
        (machine_id, (NOW - timedelta(minutes=2)).isoformat()),
    )
    conn.execute(
        """INSERT INTO machine_events (machine_id,event_type,cnc_file,raw_payload,ts)
           VALUES (?,'alarm','P100',?,?)""",
        (machine_id, json.dumps({"alarm_code": "E42", "message": "Axis overload"}), NOW.isoformat()),
    )
    conn.commit()
    case = root_cause.sync(conn, now=NOW + timedelta(minutes=5))["cases"][0]
    assert case["symptom_code"] == "alarm:E42"
    assert case["top_hypothesis_code"] == "reliability_fault"
    assert case["features"]["incomplete_cycle"] is True


def test_quality_failure_enriches_program_and_process_evidence(conn):
    machine_id = _machine(conn)
    job = conn.execute("INSERT INTO jobs (job_name) VALUES ('RCA-TEST')").lastrowid
    part = conn.execute(
        """INSERT INTO parts (job_id,part_name,material,cnc_file_back)
           VALUES (?,'Panel A','MDF','DRILL-NEW')""", (job,)
    ).lastrowid
    defect = conn.execute("SELECT id FROM defect_types WHERE code='drilling'").fetchone()["id"]
    conn.execute(
        "INSERT INTO machine_events (machine_id,part_id,event_type,cnc_file,ts) VALUES (?,?,'cycle_end','DRILL-NEW',?)",
        (machine_id, part, (NOW - timedelta(minutes=3)).isoformat()),
    )
    conn.execute(
        """INSERT INTO quality_checks (machine_id,part_id,defect_type_id,result,ts)
           VALUES (?,?,?,'fail',?)""", (machine_id, part, defect, NOW.isoformat()),
    )
    conn.commit()
    case = root_cause.sync(conn, now=NOW + timedelta(minutes=5))["cases"][0]
    causes = {item["cause_code"]: item for item in case["hypotheses"]}
    assert case["features"]["program"] == "DRILL-NEW"
    assert case["features"]["new_program"] is True
    assert causes["program_or_recipe"]["evidence"]
    assert causes["quality_process"]["evidence"]


def test_sync_preserves_case_identity_and_versions_analysis(conn):
    _downtime(conn)
    first = root_cause.sync(conn, now=NOW + timedelta(hours=1))
    second = root_cause.sync(conn, now=NOW + timedelta(hours=2))
    assert first["cases"][0]["id"] == second["cases"][0]["id"]
    assert second["sync"] == {
        "sources_seen": 1, "created": 0, "refreshed": 1, "resolved_skipped": 0,
    }
    assert second["cases"][0]["analysis_version"] == 2
    assert conn.execute("SELECT COUNT(*) FROM diagnostic_hypotheses").fetchone()[0] == 10


def test_decisions_require_named_actor_and_protect_versions(conn):
    _downtime(conn)
    case = root_cause.sync(conn, now=NOW + timedelta(hours=1))["cases"][0]
    with pytest.raises(ValueError, match="named operator"):
        root_cause.decide(conn, case["id"], {
            "action": "confirm", "actor": "operator", "actual_cause_code": "material_unavailable",
        })
    confirmed = root_cause.decide(conn, case["id"], {
        "action": "confirm", "actor": "Sam Parak", "actual_cause_code": "material_unavailable",
        "corrective_action": "Reorder earlier", "expected_version": case["version"],
    })
    assert confirmed["status"] == "confirmed"
    assert confirmed["events"][0]["actor"] == "Sam Parak"
    with pytest.raises(ValueError, match="open diagnostic case"):
        root_cause.decide(conn, case["id"], {
            "action": "dismiss", "actor": "Sam Parak", "notes": "Changed mind",
            "expected_version": confirmed["version"],
        })
    with pytest.raises(ValueError, match="changed"):
        root_cause.decide(conn, case["id"], {
            "action": "reopen", "actor": "Sam Parak", "expected_version": case["version"],
        })
    reopened = root_cause.decide(conn, case["id"], {
        "action": "reopen", "actor": "Sam Parak", "expected_version": confirmed["version"],
    })
    dismissed = root_cause.decide(conn, case["id"], {
        "action": "dismiss", "actor": "Sam Parak", "notes": "Duplicate event",
        "expected_version": reopened["version"],
    })
    assert dismissed["status"] == "dismissed"


def test_empirical_prior_activates_after_five_confirmations(conn):
    for index in range(5):
        _downtime(conn, occurred=NOW - timedelta(days=index))
    synced = root_cause.sync(conn, lookback_days=10, now=NOW + timedelta(hours=1))
    for case in synced["cases"]:
        root_cause.decide(conn, case["id"], {
            "action": "confirm", "actor": "Reliability Lead",
            "actual_cause_code": "material_unavailable", "expected_version": case["version"],
        })
    snapshot = root_cause.snapshot(conn, now=NOW + timedelta(hours=2))
    assert snapshot["learning"]["downtime"] == {
        "confirmed_cases": 5, "empirical_prior_active": True,
    }


def test_confirmed_cause_flows_into_optimization(conn):
    _downtime(conn)
    case = root_cause.sync(conn, now=NOW + timedelta(hours=1))["cases"][0]
    root_cause.decide(conn, case["id"], {
        "action": "confirm", "actor": "Shift Lead", "actual_cause_code": "upstream_starvation",
    })
    report = optimization.build(conn, window_hours=8, now=NOW + timedelta(hours=1))
    recommendation = next(item for item in report["recommendations"] if item["category"] == "downtime")
    assert recommendation["cause_code"] == "upstream_starvation"
    assert "Operator-confirmed cause: upstream_starvation" in recommendation["evidence"]


def test_root_cause_api_syncs_and_decides(conn):
    _downtime(conn, occurred=datetime.now(timezone.utc) - timedelta(hours=1))
    import main

    main.set_conn(conn)
    with TestClient(main.app) as client:
        main.set_conn(conn)
        assert client.get("/api/root-causes").json()["summary"]["total"] == 0
        synced = client.post("/api/root-causes/sync", json={
            "lookback_days": 2, "actor": "API test",
        })
        assert synced.status_code == 200
        case = synced.json()["cases"][0]
        decision = client.post(f"/api/root-causes/{case['id']}/decision", json={
            "action": "confirm", "actor": "Test Lead", "actual_cause_code": "material_unavailable",
            "expected_version": case["version"],
        })
        assert decision.status_code == 200
        assert decision.json()["status"] == "confirmed"
        assert client.get(f"/api/root-causes/{case['id']}").status_code == 200
