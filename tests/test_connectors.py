"""Commissioning contracts for vendor-specific factory data."""

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import connectors
from db import init_db
from maestro_agent import _simulated_log_lines


@pytest.fixture
def conn():
    connection = init_db(":memory:", check_same_thread=False)
    connectors.sync_defaults(connection)
    yield connection
    connection.close()


def _profile(conn, key):
    return next(item for item in connectors.snapshot(conn)["profiles"]
                if item["connector_key"] == key)


def test_defaults_are_disabled_and_unverified(conn):
    profiles = connectors.snapshot(conn)["profiles"]
    assert {item["connector_key"] for item in profiles} == {
        "cabinet_vision_sql", "ottimo_barcode", "maestro_logs",
    }
    assert all(not item["enabled"] and not item["verified"] for item in profiles)


def test_cv_mapping_approval_and_import_are_versioned_and_idempotent(conn):
    rows = [{
        "JobName": "CV-100", "ClientName": "Amit", "PartName": "Left side",
        "MaterialName": "HDHMR", "Length": "1000.5", "Width": 500,
        "Thickness": 18, "Quantity": "2", "Program": "r100.xcs",
    }]
    analysis = connectors.analyze_records(
        conn, "cabinet_vision_sql", rows, file_name="cv-sample.json", actor="test"
    )
    assert analysis["ready_to_approve"] is True
    assert analysis["mapping"]["fields"]["job_name"] == "JobName"
    assert analysis["raw_sample_retained"] is False

    approved = connectors.approve_run(
        conn, "cabinet_vision_sql", analysis["run_id"],
        expected_version=1, actor="test", enable=True,
    )
    assert approved["verified"] is True
    assert approved["enabled"] is True
    assert approved["active_mapping"]["version"] == 1

    first = connectors.import_records(conn, "cabinet_vision_sql", rows, actor="test")
    second = connectors.import_records(conn, "cabinet_vision_sql", rows, actor="test")
    assert first["status"] == "imported"
    assert first["parts_imported"] == 1
    assert second["status"] == "duplicate"
    assert second["records_duplicate"] == 1
    part = conn.execute("SELECT * FROM parts WHERE part_name='Left side'").fetchone()
    assert part["length_mm"] == 1000.5
    assert part["qty"] == 2


def test_import_rejects_all_rows_before_mutation(conn):
    sample = [{"JobName": "GOOD", "PartName": "Top"}]
    analysis = connectors.analyze_records(conn, "cabinet_vision_sql", sample)
    connectors.approve_run(conn, "cabinet_vision_sql", analysis["run_id"],
                           expected_version=1, actor="test", enable=True)
    with pytest.raises(ValueError, match="audit run"):
        connectors.import_records(
            conn, "cabinet_vision_sql", [{"JobName": "BAD", "PartName": ""}]
        )
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE job_name='BAD'").fetchone()[0] == 0
    run = conn.execute(
        "SELECT status,records_rejected FROM connector_commissioning_runs ORDER BY id DESC"
    ).fetchone()
    assert dict(run) == {"status": "rejected", "records_rejected": 1}


def test_ottimo_mapping_normalizes_values_without_retaining_raw_sample(conn):
    rows = [{
        "Barcode": "CV-100|Left side", "Status": "QC_OK",
        "Station": "packing", "Operator": "Amit",
    }]
    analysis = connectors.analyze_records(conn, "ottimo_barcode", rows)
    assert analysis["mapping"]["values"]["event_type"] == {"QC_OK": "qc_pass"}
    connectors.approve_run(conn, "ottimo_barcode", analysis["run_id"],
                           expected_version=1, actor="test", enable=True)
    result = connectors.import_records(conn, "ottimo_barcode", rows, actor="test")
    assert result["events_imported"] == 1
    event = conn.execute("SELECT event_type,source,raw_payload FROM barcode_events").fetchone()
    assert event["event_type"] == "qc_pass"
    assert event["source"] == "ottimo_commissioned"
    assert event["raw_payload"] in (None, "null")
    summary = conn.execute(
        "SELECT summary_json FROM connector_commissioning_runs WHERE mode='analyze'"
    ).fetchone()[0]
    assert "CV-100|Left side" not in summary


def test_maestro_evidence_requires_explicit_approval(conn):
    log_text = "".join(_simulated_log_lines("morbidelli_cx100", cycles=5))
    analysis = connectors.analyze_maestro(
        conn, "morbidelli_cx100", log_text, file_name="machine.log", actor="test"
    )
    assert analysis["ready_to_replay"] is True
    assert _profile(conn, "maestro_logs")["verified"] is False
    approved = connectors.approve_run(
        conn, "maestro_logs", analysis["run_id"], expected_version=1,
        actor="test", enable=False,
    )
    assert approved["verified"] is False
    assert approved["enabled"] is False
    assert approved["approved_scopes"] == ["morbidelli_cx100"]
    assert len(approved["required_scopes"]) == 10
    stored = conn.execute(
        "SELECT summary_json FROM connector_commissioning_runs WHERE id=?",
        (analysis["run_id"],),
    ).fetchone()[0]
    assert "MACHINE_ON" not in stored


def test_profiles_reject_secrets_and_sql_discovery_is_safe_offsite(conn, monkeypatch):
    with pytest.raises(ValueError, match="credentials"):
        connectors.update_profile(conn, "cabinet_vision_sql", {
            "expected_version": 1,
            "settings": {"database": {"password": "do-not-store"}},
        })
    profile = connectors.update_profile(conn, "cabinet_vision_sql", {
        "expected_version": 1,
        "credential_env": "HIVE_CV_SQL_CONNECTION",
        "settings": {"source_object": "dbo.HiveJobParts", "max_rows": 2500},
    })
    assert profile["credential_env"] == "HIVE_CV_SQL_CONNECTION"
    monkeypatch.delenv("HIVE_CV_SQL_CONNECTION", raising=False)
    discovery = connectors.discover_sql(conn)
    assert discovery["connected"] is False
    assert discovery["credential_available"] is False


def test_connector_api_commissions_sample(conn):
    import main

    main.set_conn(conn)
    with TestClient(main.app) as client:
        main.set_conn(conn)
        snapshot = client.get("/api/connectors/snapshot")
        assert snapshot.status_code == 200
        response = client.post("/api/connectors/ottimo_barcode/analyze", json={
            "records": [{"barcode": "J1|P1", "event": "PACKED"}],
            "file_name": "ottimo.json", "actor": "test",
        })
        assert response.status_code == 200
        result = response.json()
        assert result["ready_to_approve"] is True
        approved = client.post("/api/connectors/ottimo_barcode/approve", json={
            "run_id": result["run_id"], "expected_version": 1,
            "actor": "test", "enable": True,
        })
        assert approved.status_code == 200
        imported = client.post("/api/connectors/ottimo_barcode/import", json={
            "records": [{"barcode": "J1|P1", "event": "PACKED"}],
            "file_name": "ottimo.json", "actor": "test",
        })
        assert imported.status_code == 200
        assert imported.json()["events_imported"] == 1
