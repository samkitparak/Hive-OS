"""Closed-loop recommendation lifecycle and outcome-learning behavior."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import improvement
from db import init_db


@pytest.fixture
def conn():
    connection = init_db(":memory:", check_same_thread=False)
    yield connection
    connection.close()


def _recommendation(conn, now, metric="throughput_per_hour"):
    cursor = conn.execute(
        """INSERT INTO improvement_recommendations
           (recommendation_key,category,title,action,target_type,target_key,cause_code,confidence,
            metric_hint,target_direction,evidence_json,source_generated_at,status,created_at,updated_at)
           VALUES ('constraint-test','constraint','Protect the CNC','Keep the CNC fed','machine',
                   'morbidelli_cx100','capacity','high',?,?,'[]',?,'proposed',?,?)""",
        (metric, "increase" if metric == "throughput_per_hour" else "decrease",
         now.isoformat(), now.isoformat(), now.isoformat()),
    )
    conn.commit()
    return cursor.lastrowid


def _cycles(conn, start, counts):
    machine_id = conn.execute(
        "SELECT id FROM machines WHERE machine_key='morbidelli_cx100'"
    ).fetchone()["id"]
    for hour, count in enumerate(counts):
        for index in range(count):
            ts = start + timedelta(hours=hour, minutes=10 + index * 10)
            conn.execute(
                "INSERT INTO machine_events (machine_id,event_type,ts) VALUES (?,'cycle_end',?)",
                (machine_id, ts.isoformat()),
            )
    conn.commit()


def test_sync_is_explicit_and_preserves_stable_recommendation_identity(conn):
    now = datetime(2026, 7, 14, 12, tzinfo=timezone.utc)
    assert improvement.snapshot(conn, now)["summary"]["total"] == 0
    first = improvement.sync(conn, actor="test", now=now)
    second = improvement.sync(conn, actor="test", now=now + timedelta(minutes=5))
    assert first["sync"] == {"created": 1, "refreshed": 0, "source_count": 1}
    assert second["sync"] == {"created": 0, "refreshed": 1, "source_count": 1}
    assert second["summary"]["total"] == 1
    assert len(second["recommendations"][0]["events"]) == 1


def test_non_measurable_action_can_be_owned_and_completed(conn):
    now = datetime(2026, 7, 14, 12, tzinfo=timezone.utc)
    recommendation = improvement.sync(conn, now=now)["recommendations"][0]
    accepted = improvement.act(conn, recommendation["id"], {
        "action": "accept", "actor": "sam", "owner": "site engineer"
    }, now)
    assert accepted["status"] == "accepted"
    assert accepted["latest_experiment"] is None
    completed = improvement.act(conn, recommendation["id"], {
        "action": "complete", "actor": "sam", "notes": "Agent commissioned"
    }, now + timedelta(hours=1))
    assert completed["status"] == "completed"
    assert [event["event_type"] for event in completed["events"][:2]] == ["completed", "accepted"]


def test_experiment_freezes_baseline_and_validates_clear_improvement(conn):
    implemented_at = datetime(2026, 7, 14, 12, tzinfo=timezone.utc)
    recommendation_id = _recommendation(conn, implemented_at)
    _cycles(conn, implemented_at - timedelta(hours=4), [1, 1, 1, 1])

    accepted = improvement.act(conn, recommendation_id, {
        "action": "accept", "actor": "planner", "owner": "cnc lead",
        "target_delta_pct": 20, "baseline_hours": 4,
        "evaluation_hours": 4, "min_samples": 4,
        "hypothesis": "Keeping the queue staged will increase hourly CNC throughput.",
    }, implemented_at - timedelta(minutes=1))
    assert accepted["latest_experiment"]["status"] == "accepted"
    running = improvement.act(conn, recommendation_id, {
        "action": "implement", "actor": "cnc lead", "confounders": ["same product family"]
    }, implemented_at)
    baseline = running["latest_experiment"]["baseline"]
    assert baseline["sample_count"] == 4
    assert baseline["value"] == 1

    _cycles(conn, implemented_at, [2, 2, 2, 2])
    result = improvement.act(conn, recommendation_id, {
        "action": "evaluate", "actor": "planner"
    }, implemented_at + timedelta(hours=4))
    experiment = result["latest_experiment"]
    assert result["status"] == "validated"
    assert experiment["evaluation"]["value"] == 2
    assert experiment["effect_pct"] == 100
    assert experiment["ci_lower_pct"] > 0
    assert all(item["status"] != "fail" for item in experiment["guardrails"])


def test_implementation_blocks_an_underpowered_baseline(conn):
    now = datetime(2026, 7, 14, 12, tzinfo=timezone.utc)
    recommendation_id = _recommendation(conn, now)
    improvement.act(conn, recommendation_id, {
        "action": "accept", "actor": "test", "baseline_hours": 4, "min_samples": 6,
    }, now - timedelta(minutes=1))
    with pytest.raises(ValueError, match="4 samples; 6 required"):
        improvement.act(conn, recommendation_id, {"action": "implement", "actor": "test"}, now)
    assert improvement.recommendation_detail(conn, recommendation_id, now)["status"] == "accepted"


def test_promotion_requires_three_validations_on_two_dates(conn):
    now = datetime(2026, 7, 14, 12, tzinfo=timezone.utc)
    recommendation_id = _recommendation(conn, now)
    for index, day in enumerate(("2026-07-14", "2026-07-15", "2026-07-16"), start=1):
        conn.execute(
            """INSERT INTO improvement_experiments
               (recommendation_id,status,owner,hypothesis,primary_metric,target_direction,target_delta_pct,
                baseline_hours,evaluation_hours,min_samples,outcome,effect_pct,implemented_at,created_at,updated_at)
               VALUES (?,'validated','lead','test','throughput_per_hour','increase',5,4,4,4,'validated',10,?,?,?)""",
            (recommendation_id, f"{day}T08:00:00+00:00", f"{day}T07:00:00+00:00", f"{day}T12:00:00+00:00"),
        )
    conn.commit()
    learned = improvement.snapshot(conn, now)["learned_patterns"][0]
    assert learned["experiment_count"] == 3
    assert learned["success_rate"] == 1
    assert learned["promoted"] is True
    assert learned["advisory_only"] is True


def test_improvement_api_exposes_sync_and_actions(conn):
    import main

    main.set_conn(conn)
    with TestClient(main.app) as client:
        main.set_conn(conn)
        assert client.get("/api/improvements").json()["summary"]["total"] == 0
        synced = client.post("/api/improvements/sync", json={"actor": "test"})
        assert synced.status_code == 200
        recommendation = synced.json()["recommendations"][0]
        accepted = client.post(
            f"/api/improvements/recommendations/{recommendation['id']}/action",
            json={"action": "accept", "actor": "test", "expected_version": recommendation["version"]},
        )
        assert accepted.status_code == 200
        assert accepted.json()["status"] == "accepted"
