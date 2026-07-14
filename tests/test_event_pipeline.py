import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import event_pipeline
from db import init_db


@pytest.fixture
def conn():
    connection = init_db(":memory:", check_same_thread=False)
    yield connection
    connection.close()


def payload(**overrides):
    value = {
        "machine_key": "morbidelli_cx100",
        "event_type": "cycle_start",
        "ts": "2026-07-14 09:00:00",
        "cnc_file": "r86b0002.xcs",
        "source": "test",
    }
    value.update(overrides)
    return value


def test_naive_machine_timestamp_is_converted_from_site_timezone(conn):
    result = event_pipeline.ingest_event(
        conn, payload(), received_at="2026-07-14T03:30:00+00:00"
    )
    assert result["status"] == "accepted"
    assert result["event"]["ts"] == "2026-07-14T03:30:00+00:00"


def test_duplicate_event_is_suppressed(conn):
    first = event_pipeline.ingest_event(conn, payload())
    second = event_pipeline.ingest_event(conn, payload())
    assert first["status"] == "accepted"
    assert second["status"] == "duplicate"
    assert conn.execute("SELECT COUNT(*) FROM machine_events").fetchone()[0] == 1


def test_duplicate_identity_is_independent_of_transport_source(conn):
    first = event_pipeline.ingest_event(conn, payload(source="commissioning_replay"))
    second = event_pipeline.ingest_event(conn, payload(source="maestro_log"))
    assert first["status"] == "accepted"
    assert second["status"] == "duplicate"


def test_invalid_site_timezone_is_rejected(conn):
    result = event_pipeline.ingest_event(conn, payload(), site_timezone="Factory/Nowhere")
    assert result["status"] == "rejected"
    assert result["reason"] == "invalid_timestamp"


def test_heartbeat_updates_agent_status_without_polluting_events(conn):
    result = event_pipeline.ingest_event(conn, payload(event_type="heartbeat"))
    assert result["status"] == "heartbeat"
    assert conn.execute("SELECT COUNT(*) FROM machine_events").fetchone()[0] == 0
    status = conn.execute("SELECT last_heartbeat_at FROM agent_status").fetchone()
    assert status["last_heartbeat_at"]


def test_unknown_event_is_rejected_and_audited(conn):
    result = event_pipeline.ingest_event(conn, payload(event_type="make_coffee"))
    assert result["status"] == "rejected"
    assert result["reason"] == "unknown_event_type"
    row = conn.execute("SELECT status,reason FROM event_ingestion_log").fetchone()
    assert dict(row) == {"status": "rejected", "reason": "unknown_event_type"}


def test_cnc_file_links_to_known_part(conn):
    conn.execute("INSERT INTO jobs (job_name) VALUES ('J1')")
    job_id = conn.execute("SELECT id FROM jobs").fetchone()[0]
    conn.execute(
        "INSERT INTO parts (job_id,part_name,cnc_file_front) VALUES (?,?,?)",
        (job_id, "Panel", "r86b0002"),
    )
    conn.commit()
    result = event_pipeline.ingest_event(conn, payload())
    assert result["part_id"] is not None
