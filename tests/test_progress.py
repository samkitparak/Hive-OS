"""Tests for progress.py — job progress tracking."""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from db import init_db
import progress as p


CFG = Path(__file__).parent.parent / "config" / "cycle_times.yaml"


@pytest.fixture
def conn():
    c = init_db(":memory:", check_same_thread=False)
    yield c
    c.close()


def _seed(conn, job_name="JOB1", total_parts=10):
    conn.execute(
        "INSERT INTO jobs (job_name, total_parts) VALUES (?,?)",
        (job_name, total_parts)
    )
    job_id = conn.execute("SELECT id FROM jobs WHERE job_name=?", (job_name,)).fetchone()["id"]

    for i in range(total_parts):
        conn.execute(
            "INSERT INTO parts (job_id, part_name, qty) VALUES (?,?,1)",
            (job_id, f"Part{i}")
        )

    conn.commit()
    return job_id


def _add_event(conn, machine_key, event_type, part_id=None, ts=None):
    m = conn.execute("SELECT id FROM machines WHERE machine_key=?", (machine_key,)).fetchone()
    assert m, f"Machine {machine_key} not in DB"
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO machine_events (machine_id, event_type, part_id, ts) VALUES (?,?,?,?)",
        (m["id"], event_type, part_id, ts)
    )
    conn.commit()


def test_no_active_jobs(conn):
    result = p.get_active_jobs(conn, CFG)
    assert result == []


def test_active_job_appears_after_cycle_start(conn):
    job_id = _seed(conn, "ALPHA", total_parts=5)
    parts = conn.execute("SELECT id FROM parts WHERE job_id=?", (job_id,)).fetchall()

    _add_event(conn, "morbidelli_cx100", "cycle_start", part_id=parts[0]["id"])

    result = p.get_active_jobs(conn, CFG)
    assert len(result) == 1
    assert result[0].job_name == "ALPHA"
    assert result[0].total_parts == 5


def test_parts_done_counted(conn):
    job_id = _seed(conn, "BETA", total_parts=4)
    parts = conn.execute("SELECT id FROM parts WHERE job_id=?", (job_id,)).fetchall()

    today = datetime.now(timezone.utc).isoformat()
    _add_event(conn, "morbidelli_cx100", "cycle_start", part_id=parts[0]["id"])
    _add_event(conn, "morbidelli_cx100", "cycle_end",   part_id=parts[0]["id"])
    _add_event(conn, "morbidelli_cx100", "cycle_end",   part_id=parts[1]["id"])

    result = p.get_active_jobs(conn, CFG)
    assert len(result) == 1
    jp = result[0]
    assert jp.parts_done == 2
    assert jp.parts_left == 2
    assert abs(jp.pct_done - 0.5) < 0.01


def test_get_job_progress_unknown_job(conn):
    result = p.get_job_progress(conn, "DOESNOTEXIST", CFG)
    assert result is None


def test_get_job_progress_known_job(conn):
    job_id = _seed(conn, "GAMMA", total_parts=6)
    parts = conn.execute("SELECT id FROM parts WHERE job_id=?", (job_id,)).fetchall()

    for part in parts[:3]:
        _add_event(conn, "gabbiani_pt80", "cycle_end", part_id=part["id"])

    result = p.get_job_progress(conn, "GAMMA", CFG)
    assert result is not None
    assert result.parts_done == 3
    assert result.parts_left == 3
    assert abs(result.pct_done - 0.5) < 0.01


def test_job_progress_uses_coefficient_cycle_time_eta(conn, tmp_path):
    cfg = {
        "shift_hours": 24,
        "shift_start": "00:00",
        "machines": {
            "gabbiani_pt80": {
                "base_s": 10, "length_coeff": 0, "width_coeff": 0, "area_coeff": 0,
            },
        },
    }
    cfg_path = tmp_path / "cycle_times.yaml"
    cfg_path.write_text(yaml.dump(cfg))

    job_id = _seed(conn, "DELTA", total_parts=4)
    conn.execute(
        "UPDATE parts SET length_mm=1000, width_mm=500 WHERE job_id=?",
        (job_id,)
    )
    parts = conn.execute("SELECT id FROM parts WHERE job_id=?", (job_id,)).fetchall()
    for part in parts[:2]:
        _add_event(conn, "gabbiani_pt80", "cycle_end", part_id=part["id"])

    result = p.get_job_progress(conn, "DELTA", cfg_path)
    assert result.eta_seconds == 20
    assert result.on_time == "on_time"
