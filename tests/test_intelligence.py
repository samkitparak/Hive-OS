import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import commissioning
import data_quality
import event_pipeline
import optimization
from db import init_db
from maestro_agent import _parse_log_line, _simulated_log_lines


@pytest.fixture
def conn():
    connection = init_db(":memory:", check_same_thread=False)
    yield connection
    connection.close()


def _seed_cycles(conn, now, cycles=10):
    for index in range(cycles):
        start = now - timedelta(hours=7) + timedelta(minutes=index * 40)
        for event_type, ts in (
            ("cycle_start", start),
            ("cycle_end", start + timedelta(minutes=20)),
        ):
            value = ts.isoformat()
            event_pipeline.ingest_event(conn, {
                "machine_key": "morbidelli_cx100",
                "event_type": event_type,
                "ts": value,
                "source": "test",
            }, received_at=value)


def test_flexible_maestro_parser_recognizes_common_log_shape():
    parsed = _parse_log_line(
        "14/07/2026 09:42:11 INFO PROGRAM COMPLETED file=C:/jobs/door.xcs"
    )
    assert parsed["event_type"] == "cycle_end"
    assert parsed["program"] == "door.xcs"
    assert parsed["ts"] == "2026-07-14 09:42:11"


def test_flexible_parser_normalizes_windows_program_and_uppercase_extension():
    parsed = _parse_log_line(
        r"14.07.2026T09:42:11 INFO PROGRAM START file=C:\jobs\DOOR.XCS"
    )
    assert parsed["event_type"] == "cycle_start"
    assert parsed["program"] == "DOOR.XCS"
    assert parsed["ts"] == "2026-07-14 09:42:11"


def test_commissioning_analyzer_passes_complete_simulated_evidence():
    text = "".join(_simulated_log_lines("morbidelli_cx100", cycles=5))
    result = commissioning.analyze_log("morbidelli_cx100", text)
    assert result["ready_to_replay"] is True
    assert result["event_counts"]["cycle_start"] == 5
    assert result["recognition_rate"] == 1.0


def test_commissioning_replay_is_dry_run_by_default(conn):
    text = "".join(_simulated_log_lines("morbidelli_cx100", cycles=5))
    result = commissioning.replay_log(conn, "morbidelli_cx100", text)
    assert result["persisted"] is False
    assert conn.execute("SELECT COUNT(*) FROM machine_events").fetchone()[0] == 0


def test_commissioning_replay_persists_validated_events_idempotently(conn):
    text = "".join(_simulated_log_lines("morbidelli_cx100", cycles=5))
    first = commissioning.replay_log(conn, "morbidelli_cx100", text, persist=True)
    second = commissioning.replay_log(conn, "morbidelli_cx100", text, persist=True)
    assert first["ingestion"]["accepted"] > 0
    assert second["ingestion"]["duplicate"] == first["ingestion"]["accepted"]


def test_data_quality_reaches_high_confidence_for_clean_complete_cycles(conn):
    now = datetime(2026, 7, 14, 12, tzinfo=timezone.utc)
    _seed_cycles(conn, now)
    result = data_quality.build(conn, 8, now)
    machine = next(item for item in result["machines"] if item["machine_key"] == "morbidelli_cx100")
    assert machine["confidence"] == "high"
    assert machine["cycle_anomalies"] == 0
    assert machine["ingestion_acceptance"] == 1.0


def test_data_quality_flags_unmatched_cycles(conn):
    now = datetime(2026, 7, 14, 12, tzinfo=timezone.utc)
    value = (now - timedelta(hours=1)).isoformat()
    event_pipeline.ingest_event(conn, {
        "machine_key": "morbidelli_cx100", "event_type": "cycle_end",
        "ts": value, "source": "test",
    }, received_at=value)
    result = data_quality.build(conn, 8, now)
    machine = next(item for item in result["machines"] if item["machine_key"] == "morbidelli_cx100")
    assert machine["cycle_anomalies"] == 1
    assert any("unmatched" in issue for issue in machine["issues"])


def test_optimizer_stays_in_commissioning_mode_without_evidence(conn):
    now = datetime(2026, 7, 14, 12, tzinfo=timezone.utc)
    result = optimization.build(conn, 8, now)
    assert result["status"] == "commissioning"
    assert result["recommendations"][0]["category"] == "commissioning"
    assert result["guardrail"]


def test_intelligence_endpoints(conn):
    from fastapi.testclient import TestClient
    import main

    main.set_conn(conn)
    with TestClient(main.app) as client:
        main.set_conn(conn)
        assert client.get("/data-quality").status_code == 200
        assert client.get("/optimization").status_code == 200
        text = "".join(_simulated_log_lines("morbidelli_cx100", cycles=5))
        response = client.post("/commissioning/log/analyze", json={
            "machine_key": "morbidelli_cx100", "log_text": text,
        })
        assert response.status_code == 200
        assert response.json()["ready_to_replay"] is True
