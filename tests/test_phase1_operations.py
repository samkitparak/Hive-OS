"""Tests for Phase 1 placeholder integrations and operations workflows."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from db import init_db
import operations
import ottimo_connector
import cv_sql_connector


@pytest.fixture
def conn():
    connection = init_db(":memory:", check_same_thread=False)
    yield connection
    connection.close()


def test_create_downtime_and_close(conn):
    event = operations.create_downtime(conn, {
        "machine_key": "stefani_kd",
        "reason_code": "setup",
        "notes": "edge tape colour change",
    })
    assert event["id"] > 0
    assert operations.summary(conn)["open_downtime"] == 1
    closed = operations.close_downtime(conn, event["id"])
    assert closed["status"] == "closed"


def test_quality_fail_creates_rework_task(conn):
    check = operations.create_quality_check(conn, {
        "result": "fail",
        "defect_code": "edge_band",
        "assigned_area": "edge_banding",
        "notes": "lifted tape",
    })
    assert check["id"] > 0
    rework = operations.list_rework(conn, "open")
    assert len(rework) == 1
    assert rework[0]["assigned_area"] == "edge_banding"


def test_ottimo_placeholder_maps_to_barcode_event(conn):
    normalized = ottimo_connector.parse_placeholder_event({
        "barcode": "AA-GBR|Fixed Shelf",
        "event": "QC_FAIL",
        "station": "packing",
        "operator": "Amit",
    })
    event = operations.create_barcode_event(conn, normalized)
    assert event["event_type"] == "qc_fail"
    assert operations.summary(conn)["open_rework"] == 1


def test_cv_sql_placeholder_imports_job_and_parts(conn):
    result = cv_sql_connector.upsert_normalized_rows(conn, [
        {
            "job_name": "SQL_JOB",
            "client_name": "Demo Client",
            "part_name": "Side Panel",
            "length_mm": 1000,
            "width_mm": 500,
            "thickness_mm": 18,
            "cnc_file_back": "r99b0001",
        },
    ])
    assert result == {"jobs_imported": 1, "parts_imported": 1}
    job = conn.execute("SELECT total_parts FROM jobs WHERE job_name='SQL_JOB'").fetchone()
    assert job["total_parts"] == 1


def test_phase1_api_endpoints(conn):
    import main

    main.set_conn(conn)
    with TestClient(main.app) as client:
        main.set_conn(conn)
        assert client.get("/operations/summary").status_code == 200

        r = client.post("/downtime", json={
            "machine_key": "morbidelli_cx100",
            "reason_code": "breakdown",
        })
        assert r.status_code == 200
        downtime_id = r.json()["id"]
        assert client.post(f"/downtime/{downtime_id}/close", json={}).status_code == 200

        r = client.post("/quality/checks", json={
            "result": "rework",
            "defect_code": "drilling",
        })
        assert r.status_code == 200
        assert client.get("/rework?status=open").json()

        r = client.post("/connectors/ottimo/placeholder", json={
            "barcode": "SQL_JOB|Side Panel",
            "event": "QC_OK",
        })
        assert r.status_code == 200

        r = client.post("/connectors/cabinet-vision-sql/placeholder", json=[{
            "job_name": "API_SQL_JOB",
            "part_name": "Top",
        }])
        assert r.status_code == 200
        assert r.json()["parts_imported"] == 1


def test_phase1_api_rejects_invalid_writes(conn):
    import main

    main.set_conn(conn)
    with TestClient(main.app) as client:
        main.set_conn(conn)
        assert client.post("/quality/checks", json={"result": "maybe"}).status_code == 422
        assert client.post("/downtime", json={
            "machine_key": "missing_machine",
            "reason_code": "setup",
        }).status_code == 400
        assert client.post("/downtime/999999/close", json={}).status_code == 404
        assert client.post("/rework/999999/close", json={}).status_code == 404
