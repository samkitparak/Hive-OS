"""Tests for sequencer.py — job sequencing algorithm."""

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from db import init_db
import sequencer as sq


@pytest.fixture
def conn():
    c = init_db(":memory:", check_same_thread=False)
    yield c
    c.close()


@pytest.fixture
def zero_cfg(tmp_path):
    cfg = {
        "shift_hours": 9,
        "machines": {k: {"base_s": 0, "area_coeff": 0, "length_coeff": 0,
                         "width_coeff": 0, "face_coeff": 0, "groove_coeff": 0,
                         "edge_coeff": 0}
                    for k in ["gabbiani_pt80","nova_si400","morbidelli_cx100",
                              "morbidelli_n100","stefani_kd","sergiani_gs120",
                              "varie_osama","dmc60_rcs135","dmc90_xrt135","superfici"]}
    }
    p = tmp_path / "cycle_times.yaml"
    p.write_text(yaml.dump(cfg))
    return p


@pytest.fixture
def calibrated_cfg(tmp_path):
    cfg = {
        "shift_hours": 9,
        "machines": {
            "gabbiani_pt80":   {"base_s": 10, "area_coeff": 0, "length_coeff": 0.02, "width_coeff": 0.01},
            "morbidelli_cx100":{"base_s": 30, "area_coeff": 20, "face_coeff": 45, "groove_coeff": 15},
            "stefani_kd":      {"base_s": 5,  "length_coeff": 0.01, "edge_coeff": 8},
            "sergiani_gs120":  {"base_s": 60, "area_coeff": 10},
            "dmc60_rcs135":    {"base_s": 8,  "length_coeff": 0.005},
            "superfici":       {"base_s": 15, "length_coeff": 0.008},
            "morbidelli_n100": {"base_s": 0, "area_coeff": 0, "face_coeff": 0, "groove_coeff": 0},
            "nova_si400":      {"base_s": 0, "area_coeff": 0, "length_coeff": 0, "width_coeff": 0},
            "varie_osama":     {"base_s": 0, "area_coeff": 0},
            "dmc90_xrt135":    {"base_s": 0, "length_coeff": 0},
        }
    }
    p = tmp_path / "cycle_times.yaml"
    p.write_text(yaml.dump(cfg))
    return p


def _add_job(conn, job_name, total_parts=10, job_date=None, material="HDHMR_18mm"):
    conn.execute(
        "INSERT INTO jobs (job_name, total_parts, job_date) VALUES (?,?,?)",
        (job_name, total_parts, job_date)
    )
    job_id = conn.execute("SELECT id FROM jobs WHERE job_name=?", (job_name,)).fetchone()["id"]
    for i in range(total_parts):
        conn.execute(
            """INSERT INTO parts (job_id, part_name, length_mm, width_mm, thickness_mm,
               qty, eb1, eb2, eb3, eb4, cnc_file_back, cnc_file_front, has_cnc, material)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (job_id, f"Part{i}", 800, 500, 18, 1,
             "tape", "tape", "tape", "tape",
             f"r86b{i:04d}", None, 1, material)
        )
    conn.commit()
    return job_id


# ── Basic behaviour ───────────────────────────────────────────────────────────

def test_empty_db_returns_empty_plan(conn, zero_cfg):
    plan = sq.sequence(conn, cfg_path=zero_cfg)
    assert plan.total_jobs == 0
    assert plan.jobs == []


def test_single_job_sequenced(conn, zero_cfg):
    _add_job(conn, "JOB_A")
    plan = sq.sequence(conn, cfg_path=zero_cfg)
    assert plan.total_jobs == 1
    assert plan.jobs[0].job_name == "JOB_A"
    assert plan.jobs[0].position == 1


def test_positions_are_sequential(conn, zero_cfg):
    _add_job(conn, "JOB_A")
    _add_job(conn, "JOB_B")
    _add_job(conn, "JOB_C")
    plan = sq.sequence(conn, cfg_path=zero_cfg)
    positions = [j.position for j in plan.jobs]
    assert positions == list(range(1, len(positions) + 1))


def test_specific_jobs_only(conn, zero_cfg):
    _add_job(conn, "JOB_A")
    _add_job(conn, "JOB_B")
    _add_job(conn, "JOB_C")
    plan = sq.sequence(conn, job_names=["JOB_A", "JOB_C"], cfg_path=zero_cfg)
    names = [j.job_name for j in plan.jobs]
    assert "JOB_A" in names
    assert "JOB_C" in names
    assert "JOB_B" not in names


# ── Urgency ordering ──────────────────────────────────────────────────────────

def test_overdue_job_comes_first(conn, zero_cfg):
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    future    = (datetime.now(timezone.utc).date() + timedelta(days=10)).isoformat()
    _add_job(conn, "FUTURE_JOB",   job_date=future)
    _add_job(conn, "OVERDUE_JOB",  job_date=yesterday)
    plan = sq.sequence(conn, cfg_path=zero_cfg)
    assert plan.jobs[0].job_name == "OVERDUE_JOB"
    assert plan.jobs[0].urgency  == "overdue"


def test_urgent_job_before_normal(conn, zero_cfg):
    tomorrow  = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
    next_week = (datetime.now(timezone.utc).date() + timedelta(days=7)).isoformat()
    _add_job(conn, "NORMAL_JOB", job_date=next_week)
    _add_job(conn, "URGENT_JOB", job_date=tomorrow)
    plan = sq.sequence(conn, cfg_path=zero_cfg)
    assert plan.jobs[0].job_name == "URGENT_JOB"


# ── WSPT with calibrated times ────────────────────────────────────────────────

def test_short_job_before_long_same_urgency(conn, calibrated_cfg):
    """With same urgency, shorter job (fewer parts) should score higher."""
    future = (datetime.now(timezone.utc).date() + timedelta(days=7)).isoformat()
    _add_job(conn, "SHORT_JOB", total_parts=5,  job_date=future)
    _add_job(conn, "LONG_JOB",  total_parts=50, job_date=future)
    plan = sq.sequence(conn, cfg_path=calibrated_cfg)
    assert plan.jobs[0].job_name == "SHORT_JOB"


# ── Material batching ─────────────────────────────────────────────────────────

def test_material_group_normalises_thickness():
    assert sq._material_group("HDHMR_18mm_6987 SUD") == "18mm"
    assert sq._material_group("HDHMR_8mm_6987_SUD")  == "8mm"


def test_urgency_labels(conn, zero_cfg):
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    tomorrow  = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
    future    = (datetime.now(timezone.utc).date() + timedelta(days=7)).isoformat()
    _add_job(conn, "J_OVERDUE", job_date=yesterday)
    _add_job(conn, "J_URGENT",  job_date=tomorrow)
    _add_job(conn, "J_NORMAL",  job_date=future)
    _add_job(conn, "J_UNKNOWN", job_date=None)
    plan = sq.sequence(conn, cfg_path=zero_cfg)
    urgencies = {j.job_name: j.urgency for j in plan.jobs}
    assert urgencies["J_OVERDUE"] == "overdue"
    assert urgencies["J_URGENT"]  == "urgent"
    assert urgencies["J_NORMAL"]  == "normal"
    assert urgencies["J_UNKNOWN"] == "unknown"


# ── API endpoint ──────────────────────────────────────────────────────────────

def test_sequence_endpoint(conn, zero_cfg):
    from fastapi.testclient import TestClient
    import main
    main.set_conn(conn)
    _add_job(conn, "TEST_JOB")

    with TestClient(main.app) as client:
        main.set_conn(conn)
        resp = client.get("/sequence")
        assert resp.status_code == 200
        data = resp.json()
        assert "jobs" in data
        assert data["total_jobs"] >= 1
