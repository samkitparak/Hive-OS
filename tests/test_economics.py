"""Evidence-gated production economics and continuous value assurance."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import economics
import industrial_gateway
from db import init_db


@pytest.fixture
def conn():
    connection = init_db(":memory:", check_same_thread=False)
    yield connection
    connection.close()


def _verify_policy(conn, **changes):
    return economics.update_settings(conn, {
        "expected_version": economics.settings(conn)["version"],
        "verified": True, "actor": "Finance Lead", **changes,
    })


def _rate(conn, key, amount=100):
    return economics.update_rate(conn, key, {
        "expected_version": 0, "amount": amount, "verified": True,
        "scope_type": "factory", "scope_key": "factory", "actor": "Finance Lead",
    })


def _reader(power_w, energy_kwh, power_factor=0.95):
    def read(profile, signals):
        values = {
            signal["key"]: industrial_gateway.SIGNAL_DEFINITIONS[signal["key"]]["simulation"]
            for signal in signals
        }
        values.update({"power_w": power_w, "energy_kwh": energy_kwh,
                       "power_factor": power_factor})
        return values
    return read


def _validated_experiment(conn, *, evaluated_at, outcome="validated"):
    machine_id = conn.execute(
        "SELECT id FROM machines WHERE machine_key='morbidelli_cx100'"
    ).fetchone()["id"]
    cursor = conn.execute(
        """INSERT INTO improvement_recommendations
           (recommendation_key,category,title,action,target_type,target_key,cause_code,
            confidence,metric_hint,target_direction,evidence_json,source_generated_at,
            status,created_at,updated_at)
           VALUES ('economics-throughput','constraint','Protect CNC throughput','Stage work',
                   'machine','morbidelli_cx100','capacity','high','throughput_per_hour',
                   'increase','[]',?,?,?,?)""",
        (evaluated_at.isoformat(), outcome, evaluated_at.isoformat(), evaluated_at.isoformat()),
    )
    recommendation_id = cursor.lastrowid
    baseline_start = evaluated_at - timedelta(hours=8)
    baseline_end = evaluated_at - timedelta(hours=4)
    evaluation_start = baseline_end
    baseline = {
        "metric": "throughput_per_hour", "value": 1, "sample_count": 4,
        "window_start": baseline_start.isoformat(), "window_end": baseline_end.isoformat(),
    }
    evaluation = {
        "metric": "throughput_per_hour", "value": 2, "sample_count": 8,
        "window_start": evaluation_start.isoformat(), "window_end": evaluated_at.isoformat(),
    }
    cursor = conn.execute(
        """INSERT INTO improvement_experiments
           (recommendation_id,status,owner,hypothesis,primary_metric,target_direction,
            target_delta_pct,baseline_hours,evaluation_hours,min_samples,design_type,
            confounders_json,baseline_start,baseline_end,implemented_at,evaluation_due_at,
            baseline_json,evaluation_json,guardrails_json,outcome,effect_pct,ci_lower_pct,
            ci_upper_pct,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,'before_after','[]',?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (recommendation_id, outcome, "CNC Lead", "Staging increases throughput",
         "throughput_per_hour", "increase", 20, 4, 4, 4,
         baseline_start.isoformat(), baseline_end.isoformat(), evaluation_start.isoformat(),
         evaluated_at.isoformat(), __import__("json").dumps(baseline),
         __import__("json").dumps(evaluation), "[]", outcome, 100,
         25 if outcome == "validated" else -5, 160, baseline_start.isoformat(),
         evaluated_at.isoformat()),
    )
    conn.commit()
    return cursor.lastrowid, machine_id


def _cycles(conn, machine_id, start, hours, count_per_hour):
    for hour in range(hours):
        for index in range(count_per_hour):
            point = start + timedelta(hours=hour, minutes=5 + index * 20)
            conn.execute(
                "INSERT INTO machine_events (machine_id,event_type,ts) VALUES (?,'cycle_end',?)",
                (machine_id, point.isoformat()),
            )
    conn.commit()


def test_empty_factory_never_invents_financial_value(conn):
    now = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)
    result = economics.create_review(conn, actor="Test Runner", now=now)
    assert result["status"] == "waiting_for_evidence"
    assert result["claims"] == []
    assert result["summary"]["direct_cost_exposure"] == 0
    assert result["summary"]["constraint_capacity_opportunity"] == 0
    assert result["summary"]["measured_improvement_benefit"] == 0


def test_rates_are_versioned_and_policy_updates_are_optimistic(conn):
    policy = _verify_policy(conn, currency="INR")
    first = _rate(conn, "throughput_contribution_per_unit", 125)
    assert first["version"] == 1 and first["verified"] is True
    second = economics.update_rate(conn, first["rate_key"], {
        "expected_version": first["version"], "amount": 140, "verified": True,
        "scope_type": "factory", "scope_key": "factory", "actor": "Finance Lead",
    })
    assert second["version"] == 2 and second["amount"] == 140
    assert len(economics.rates(conn, active_only=False)) == 2
    with pytest.raises(economics.VersionConflict):
        economics.update_settings(conn, {
            "expected_version": policy["version"] - 1, "window_hours": 8,
            "actor": "Finance Lead",
        })


def test_review_signature_tracks_mutable_operational_dependencies(conn):
    industrial_gateway.sync_defaults(conn)
    before = economics.input_signature(conn)
    profile = next(item for item in industrial_gateway.snapshot(conn)["profiles"]
                   if item["profile_key"] == "elgi_1_energy")
    industrial_gateway.update_profile(conn, profile["profile_key"], {
        "expected_version": profile["version"], "endpoint": "10.10.0.51",
        "settings": {**profile["settings"], "tariff_per_kwh": 10},
    })
    assert economics.input_signature(conn) != before


def test_quality_exposure_requires_rate_identity_and_attribution(conn):
    now = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)
    _verify_policy(conn)
    _rate(conn, "internal_failure_cost_per_unit", 300)
    machine_id = conn.execute(
        "SELECT id FROM machines WHERE machine_key='morbidelli_cx100'"
    ).fetchone()["id"]
    conn.execute("INSERT INTO jobs (job_name,total_parts) VALUES ('VALUE-Q',1)")
    job_id = conn.execute("SELECT id FROM jobs WHERE job_name='VALUE-Q'").fetchone()["id"]
    conn.execute(
        "INSERT INTO parts (job_id,part_name,length_mm,width_mm) VALUES (?,'Panel',500,300)",
        (job_id,),
    )
    part_id = conn.execute("SELECT id FROM parts WHERE job_id=?", (job_id,)).fetchone()["id"]
    conn.execute(
        """INSERT INTO quality_checks
           (job_id,part_id,machine_id,result,inspector,source,ts)
           VALUES (?,?,?,'fail','Quality Lead','manual',?)""",
        (job_id, part_id, machine_id, (now - timedelta(hours=1)).isoformat()),
    )
    conn.commit()
    result = economics.create_review(conn, actor="Test Runner", now=now)
    claim = next(item for item in result["claims"] if item["category"] == "internal_failure")
    assert claim["status"] == "decision_ready"
    assert claim["amount"] == 300
    assert result["summary"]["direct_cost_exposure"] == 300

    conn.execute(
        """INSERT INTO quality_checks (machine_id,result,source,ts)
           VALUES (?,'fail','manual',?)""",
        (machine_id, (now - timedelta(minutes=30)).isoformat()),
    )
    conn.commit()
    preview = economics.create_review(
        conn, actor="Test Runner", now=now + timedelta(minutes=5)
    )
    claim = next(item for item in preview["claims"] if item["category"] == "internal_failure")
    assert claim["status"] == "preview_only"
    assert any("physical part" in gap for gap in claim["blocked_by"])
    assert preview["summary"]["direct_cost_exposure"] == 0


def test_idle_energy_cost_requires_approved_contract_and_tariff(conn):
    now = datetime.now(timezone.utc)
    _verify_policy(conn, window_hours=2)
    industrial_gateway.sync_defaults(conn)
    profile = next(item for item in industrial_gateway.snapshot(conn)["profiles"]
                   if item["profile_key"] == "elgi_1_energy")
    configured = industrial_gateway.update_profile(conn, profile["profile_key"], {
        "expected_version": profile["version"], "endpoint": "10.10.0.51",
        "poll_interval_s": 300,
        "settings": {**profile["settings"], "tariff_per_kwh": 10},
    })
    probe = industrial_gateway.probe_profile(
        conn, profile["profile_key"], reader=_reader(1000, 100), actor="Test Runner"
    )
    industrial_gateway.approve_run(
        conn, profile["profile_key"], probe["run_id"],
        expected_version=configured["version"], actor="Test Runner", enable=True,
    )
    start = now - timedelta(minutes=20)
    for index, (power, energy) in enumerate(((100, 100), (1000, 100.03),
                                              (1000, 100.11), (6000, 100.2))):
        industrial_gateway.poll_profile(
            conn, profile["profile_key"], reader=_reader(power, energy),
            source_ts=(start + timedelta(minutes=index * 5)).isoformat(),
        )
    result = economics.create_review(conn, actor="Test Runner", now=now)
    claim = next(item for item in result["claims"] if item["category"] == "idle_energy")
    assert claim["status"] == "decision_ready"
    assert claim["quantity"] > 0
    assert claim["amount"] > 0


def test_constraint_opportunity_requires_confirmed_constraint_demand_and_loss_evidence(
        conn, monkeypatch):
    now = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)
    _verify_policy(conn)
    _rate(conn, "constraint_minute_value", 50)
    current = SimpleNamespace(
        machine_key="morbidelli_cx100", machine_name="Morbidelli CX100",
        state="constraint", confidence="high", demand_qty=10,
        throughput_per_hour=2,
    )
    episode = {
        "id": 7, "status": "open", "machine_key": "morbidelli_cx100",
        "constraint_state": "constraint",
    }
    monkeypatch.setattr(economics.bottleneck, "detect", lambda *_args: SimpleNamespace(
        current=current, episode=episode,
    ))
    monkeypatch.setattr(economics.production_loss, "build", lambda *_args, **_kwargs: {
        "machines": [{
            "decision_ready": True,
            "losses": [{
                "category": "breakdown", "seconds": 600,
                "machine_minutes": 10, "label": "Breakdown",
            }],
        }],
    })

    result = economics.create_review(conn, actor="Test Runner", now=now)
    claim = next(item for item in result["claims"]
                 if item["claim_type"] == "constraint_capacity_opportunity")
    assert claim["status"] == "decision_ready"
    assert claim["quantity"] == 10
    assert claim["amount"] == 500
    assert result["summary"]["constraint_capacity_opportunity"] == 500

    current.demand_qty = 0
    preview = economics.create_review(
        conn, actor="Test Runner", now=now + timedelta(minutes=5)
    )
    claim = next(item for item in preview["claims"]
                 if item["claim_type"] == "constraint_capacity_opportunity")
    assert claim["status"] == "preview_only"
    assert any("Released demand is absent" in gap for gap in claim["blocked_by"])
    assert preview["summary"]["constraint_capacity_opportunity"] == 0


def test_validated_experiment_becomes_measured_benefit_not_annualized(conn):
    now = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)
    _verify_policy(conn)
    _rate(conn, "throughput_contribution_per_unit", 100)
    experiment_id, _ = _validated_experiment(conn, evaluated_at=now)
    result = economics.create_review(
        conn, actor="Test Runner", now=now + timedelta(minutes=1)
    )
    claim = next(item for item in result["claims"]
                 if item["source_key"] == str(experiment_id))
    assert claim["status"] == "measured"
    assert claim["quantity"] == 4
    assert claim["amount"] == 400
    assert result["summary"]["measured_improvement_benefit"] == 400


def test_promising_experiment_remains_preview_only(conn):
    now = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)
    _verify_policy(conn)
    _rate(conn, "throughput_contribution_per_unit", 100)
    experiment_id, _ = _validated_experiment(conn, evaluated_at=now, outcome="promising")
    result = economics.create_review(conn, actor="Test Runner", now=now + timedelta(minutes=1))
    claim = next(item for item in result["claims"] if item["source_key"] == str(experiment_id))
    assert claim["status"] == "preview_only"
    assert result["summary"]["measured_improvement_benefit"] == 0


def test_sustained_value_requires_follow_up_adjustment_reviews(conn):
    now = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)
    _verify_policy(conn, persistence_window_days=1, minimum_persistence_reviews=2)
    _rate(conn, "throughput_contribution_per_unit", 100)
    evaluated_at = now - timedelta(days=2)
    experiment_id, machine_id = _validated_experiment(conn, evaluated_at=evaluated_at)
    _cycles(conn, machine_id, evaluated_at, 48, 2)

    initial = economics.create_review(conn, actor="Test Runner", now=now)
    claim = next(item for item in initial["claims"] if item["source_key"] == str(experiment_id))
    assert claim["status"] == "measured"
    assert len(claim["persistence"]) == 2
    assert all(item["status"] == "adjustment_required" for item in claim["persistence"])

    for window in claim["persistence"]:
        economics.record_adjustment(conn, experiment_id, {
            "expected_version": 0, "window_start": window["window_start"],
            "window_end": window["window_end"], "adjustment_amount": 0,
            "reason": "Product mix and scheduled hours unchanged",
            "verified": True, "actor": "Finance Lead",
        })
    sustained = economics.create_review(
        conn, actor="Test Runner", now=now + timedelta(minutes=5)
    )
    claim = next(item for item in sustained["claims"] if item["source_key"] == str(experiment_id))
    assert claim["status"] == "sustained"
    assert claim["amount"] == 5200
    assert sustained["summary"]["sustained_claims"] == 1


def test_economics_api_and_version(conn):
    import main

    main.set_conn(conn)
    with TestClient(main.app) as client:
        main.set_conn(conn)
        assert client.get("/api/health").json()["version"] == "0.34.0"
        state = client.get("/api/economics")
        assert state.status_code == 200
        version = state.json()["settings"]["version"]
        updated = client.put("/api/economics/settings", json={
            "expected_version": version, "window_hours": 12,
            "actor": "Finance Lead",
        })
        assert updated.status_code == 200
        rate = client.put("/api/economics/rates/rework_cost_per_unit", json={
            "expected_version": 0, "amount": 250, "verified": False,
            "actor": "Finance Lead",
        })
        assert rate.status_code == 200
        reviewed = client.post("/api/economics/sync", json={"actor": "Finance Lead"})
        assert reviewed.status_code == 200
