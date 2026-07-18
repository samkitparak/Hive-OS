"""Guided factory evidence capture and non-production calibration proposals."""

import csv
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import commissioning_evidence as evidence
from db import init_db


@pytest.fixture
def conn():
    connection = init_db(":memory:", check_same_thread=False)
    yield connection
    connection.close()


def observation(index: int, *, source_record_id: str | None = None) -> dict:
    return {
        "source_record_id": source_record_id or f"obs-{index:03d}",
        "measured_at": f"2026-08-{3 + index % 2:02d}T{9 + index % 8:02d}:15:00+05:30",
        "shift_key": f"day-{index % 2}",
        "measurement_method": "stopwatch",
        "observer": "Sam" if index % 2 else "Factory engineer",
        "product_family": "painted_panel" if index % 2 else "routed_panel",
        "program_key": "PAINT-A" if index % 2 else "ROUTE-B",
        "unit_count": 1, "operator_count": 1,
        "queue_s": 12, "setup_s": 8, "load_s": 6,
        "process_s": 101 + index % 5, "blocked_s": 2, "starved_s": 3,
        "unload_s": 5, "quality_s": 4, "rework_s": 0,
        "good_units": 1, "reject_units": 0, "actor": "test",
    }


def create(conn, **overrides):
    payload = {
        "machine_key": "superfici", "target_samples": 20,
        "target_strata": 2, "actor": "test",
    }
    payload.update(overrides)
    return evidence.create_study(conn, payload)


def test_protocols_and_pack_are_traceable_and_hash_verified(conn):
    protocols = evidence.protocols(conn)
    assert len(protocols) == 11
    assert all(item["production_eligible"] is False for item in protocols)
    assert all(item["measurement_instruction"] for item in protocols)
    bundle, metadata = evidence.build_pack(conn)
    assert metadata["production_eligible"] is False
    assert metadata["bundle_sha256"] == hashlib.sha256(bundle).hexdigest()
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        names = set(archive.namelist())
        assert {"README.md", "manifest.json", "SHA256SUMS", "machine-protocols.csv"}.issubset(names)
        assert "templates/superfici.csv" in names
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["format"] == evidence.PACK_FORMAT
        assert manifest["production_eligible"] is False
        for item in manifest["files"]:
            content = archive.read(item["path"])
            assert len(content) == item["size"]
            assert hashlib.sha256(content).hexdigest() == item["sha256"]


def test_observation_is_idempotent_conflict_safe_and_isolated(conn):
    study = create(conn)
    protected = (
        "machine_events", "cycle_observations", "cycle_models", "route_observations",
        "production_forecasts", "planning_scenarios", "execution_jobs",
    )
    before = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in protected}
    first = evidence.add_observation(conn, study["id"], observation(1))
    duplicate = evidence.add_observation(conn, study["id"], observation(1))
    assert first["status"] == "accepted"
    assert duplicate["status"] == "duplicate"
    median_check = next(
        item for item in first["study"]["analysis"]["checks"] if item["key"] == "median_uncertainty"
    )
    assert median_check["passed"] is False
    assert "At least 5" in median_check["detail"]
    assert conn.execute("SELECT COUNT(*) FROM commissioning_evidence_observations").fetchone()[0] == 1
    changed = observation(1)
    changed["process_s"] = 500
    with pytest.raises(ValueError, match="conflicts with existing evidence"):
        evidence.add_observation(conn, study["id"], changed)
    after = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in protected}
    assert after == before


def test_validation_requires_timezone_exclusive_segments_and_quality_bounds(conn):
    study = create(conn)
    invalid_time = observation(1)
    invalid_time["measured_at"] = "2026-08-03T10:00:00"
    with pytest.raises(ValueError, match="include a timezone"):
        evidence.add_observation(conn, study["id"], invalid_time)
    invalid_total = observation(2)
    invalid_total["total_s"] = 1
    with pytest.raises(ValueError, match="less than the sum"):
        evidence.add_observation(conn, study["id"], invalid_total)
    invalid_quality = observation(3)
    invalid_quality.update(unit_count=1, good_units=1, reject_units=1)
    with pytest.raises(ValueError, match="cannot exceed"):
        evidence.add_observation(conn, study["id"], invalid_quality)


def test_csv_dry_run_apply_and_replay_are_atomic(conn):
    study = create(conn)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=evidence.CSV_FIELDS)
    writer.writeheader()
    for index in range(3):
        row = observation(index)
        row.pop("actor")
        writer.writerow(row)
    preview = evidence.import_csv(conn, study["id"], output.getvalue(), apply=False, actor="test")
    assert preview["ready_to_apply"] is True
    assert conn.execute("SELECT COUNT(*) FROM commissioning_evidence_observations").fetchone()[0] == 0
    applied = evidence.import_csv(conn, study["id"], output.getvalue(), apply=True, actor="test")
    assert applied["accepted"] == 3 and applied["duplicates"] == 0
    replayed = evidence.import_csv(conn, study["id"], output.getvalue(), apply=True, actor="test")
    assert replayed["accepted"] == 0 and replayed["duplicates"] == 3
    bad = output.getvalue() + "broken,not-a-time,,,,,,,,,,,,,,,,,,,,,,,,\n"
    preview_bad = evidence.import_csv(conn, study["id"], bad, apply=False, actor="test")
    assert preview_bad["ready_to_apply"] is False
    with pytest.raises(ValueError, match="pass validation"):
        evidence.import_csv(conn, study["id"], bad, apply=True, actor="test")
    assert conn.execute("SELECT COUNT(*) FROM commissioning_evidence_observations").fetchone()[0] == 3


def test_csv_rejects_unknown_columns_and_internal_source_conflicts(conn):
    study = create(conn)
    unknown = "measured_at,measurement_method,product_family,process_s,proces_seconds\n2026-08-03T10:00:00+05:30,stopwatch,painted_panel,10,10\n"
    with pytest.raises(ValueError, match="unknown columns"):
        evidence.import_csv(conn, study["id"], unknown, apply=False, actor="test")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=evidence.CSV_FIELDS)
    writer.writeheader()
    first = observation(1, source_record_id="same-id")
    second = observation(2, source_record_id="same-id")
    first.pop("actor"); second.pop("actor")
    writer.writerow(first); writer.writerow(second)
    preview = evidence.import_csv(conn, study["id"], output.getvalue(), apply=False, actor="test")
    assert preview["ready_to_apply"] is False
    assert "conflicts within this CSV" in preview["issues"][0]["detail"]


def test_credible_study_proposes_prior_but_never_production_model(conn):
    study = create(conn)
    for index in range(20):
        evidence.add_observation(conn, study["id"], observation(index))
    result = evidence.analyze(conn, study["id"])
    assert result["review_ready"] is True
    assert result["proposal"]["status"] == "review_ready"
    assert result["proposal"]["production_eligible"] is False
    assert result["proposal"]["availability"] is None
    assert result["bootstrap_median_90"]["samples"] == 500
    saved = evidence.persist_analysis(conn, study["id"], "test")
    evidence.persist_analysis(conn, study["id"], "test")
    assert saved["analysis_id"]
    assert conn.execute("SELECT COUNT(*) FROM commissioning_evidence_analyses").fetchone()[0] == 1
    current = evidence.study_detail(conn, study["id"])
    submitted = evidence.action(conn, study["id"], {
        "action": "submit_review", "expected_version": current["version"], "actor": "reviewer",
    })
    assert submitted["status"] == "review_ready"
    approved = evidence.action(conn, study["id"], {
        "action": "approve_proposal", "expected_version": submitted["version"],
        "actor": "reviewer", "notes": "Approved for prior-file review only",
    })
    assert approved["status"] == "proposal_approved"
    assert approved["production_eligible"] is False
    assert conn.execute("SELECT COUNT(*) FROM cycle_models").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM cycle_observations").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM virtual_factory_runs").fetchone()[0] == 0


def test_failed_review_has_no_analysis_or_transition_side_effect(conn):
    study = create(conn)
    evidence.add_observation(conn, study["id"], observation(1))
    current = evidence.study_detail(conn, study["id"])
    with pytest.raises(ValueError, match="credibility check"):
        evidence.action(conn, study["id"], {
            "action": "submit_review", "expected_version": current["version"], "actor": "reviewer",
        })
    assert evidence.study_detail(conn, study["id"])["status"] == "collecting"
    assert conn.execute("SELECT COUNT(*) FROM commissioning_evidence_analyses").fetchone()[0] == 0


def test_outliers_are_flagged_until_explicitly_excluded(conn):
    study = create(conn, target_samples=5, target_strata=1)
    for index in range(6):
        payload = observation(index)
        if index == 5:
            payload["process_s"] = 2000
        evidence.add_observation(conn, study["id"], payload)
    result = evidence.analyze(conn, study["id"])
    assert len(result["outliers"]) == 1
    outlier_id = result["outliers"][0]["observation_id"]
    detail = evidence.exclude_observation(conn, study["id"], outlier_id, "Timer left running", "reviewer")
    excluded = next(item for item in detail["observations"] if item["id"] == outlier_id)
    assert excluded["validity"] == "excluded"
    assert excluded["exclusion_reason"] == "Timer left running"
    assert detail["analysis"]["sample_count"] == 5


def test_concurrent_review_version_is_rejected(conn):
    study = create(conn)
    started = evidence.action(conn, study["id"], {
        "action": "start", "expected_version": study["version"], "actor": "test",
    })
    with pytest.raises(ValueError, match="Study changed"):
        evidence.action(conn, study["id"], {
            "action": "archive", "expected_version": study["version"], "actor": "stale-user",
        })
    assert started["status"] == "collecting"


def test_review_detects_evidence_change_during_analysis(conn, monkeypatch):
    study = create(conn, target_samples=5)
    for index in range(6):
        evidence.add_observation(conn, study["id"], observation(index))
    current = evidence.study_detail(conn, study["id"])
    original = evidence.persist_analysis

    def racing_persist(connection, study_id, actor):
        result = original(connection, study_id, actor)
        connection.execute(
            "UPDATE commissioning_evidence_studies SET version=version+1 WHERE id=?",
            (study_id,),
        )
        connection.commit()
        return result

    monkeypatch.setattr(evidence, "persist_analysis", racing_persist)
    with pytest.raises(ValueError, match="changed while"):
        evidence.action(conn, study["id"], {
            "action": "submit_review", "expected_version": current["version"], "actor": "reviewer",
        })
    assert evidence.study_detail(conn, study["id"])["status"] == "collecting"
