"""Tests for cycle_time.py — part cycle time estimation."""

import sys
import tempfile
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from db import init_db
import cycle_time as ct


# ── Fixture: temp config with known coefficients ──────────────────────────────

@pytest.fixture
def calibrated_cfg(tmp_path):
    cfg = {
        "shift_hours": 9,
        "machines": {
            "gabbiani_pt80": {
                "base_s": 10, "area_coeff": 0, "length_coeff": 0.02, "width_coeff": 0.01
            },
            "morbidelli_cx100": {
                "base_s": 30, "area_coeff": 20, "face_coeff": 45, "groove_coeff": 15
            },
            "stefani_kd": {
                "base_s": 5, "length_coeff": 0.01, "edge_coeff": 8
            },
            "sergiani_gs120": {
                "base_s": 60, "area_coeff": 10
            },
            "dmc60_rcs135": {
                "base_s": 8, "length_coeff": 0.005
            },
            "superfici": {
                "base_s": 15, "length_coeff": 0.008
            },
            "morbidelli_n100":  {"base_s": 0, "area_coeff": 0, "face_coeff": 0, "groove_coeff": 0},
            "nova_si400":       {"base_s": 0, "area_coeff": 0, "length_coeff": 0, "width_coeff": 0},
            "varie_osama":      {"base_s": 0, "area_coeff": 0},
            "dmc90_xrt135":     {"base_s": 0, "length_coeff": 0},
        }
    }
    p = tmp_path / "cycle_times.yaml"
    p.write_text(yaml.dump(cfg))
    return p


@pytest.fixture
def zero_cfg(tmp_path):
    cfg = {
        "shift_hours": 9,
        "machines": {k: {"base_s": 0, "area_coeff": 0, "length_coeff": 0,
                         "width_coeff": 0, "face_coeff": 0, "groove_coeff": 0,
                         "edge_coeff": 0}
                    for k in ct.MACHINE_TYPE_MAP}
    }
    p = tmp_path / "cycle_times.yaml"
    p.write_text(yaml.dump(cfg))
    return p


@pytest.fixture
def conn():
    c = init_db(":memory:", check_same_thread=False)
    yield c
    c.close()


# ── Feature extraction ────────────────────────────────────────────────────────

def test_extract_area():
    part = {"length_mm": 1000, "width_mm": 500, "eb1": None, "eb2": None,
            "eb3": None, "eb4": None, "cnc_file_back": None, "cnc_file_front": None, "has_cnc": 0}
    f = ct.extract_features(part, "gabbiani_pt80")
    assert abs(f.area_m2 - 0.5) < 0.001


def test_extract_edge_count():
    part = {"length_mm": 500, "width_mm": 300, "eb1": "tape", "eb2": "tape",
            "eb3": None, "eb4": None, "cnc_file_back": None, "cnc_file_front": None, "has_cnc": 0}
    f = ct.extract_features(part, "stefani_kd")
    assert f.num_edges == 2


def test_extract_two_faces():
    part = {"length_mm": 500, "width_mm": 300, "eb1": None, "eb2": None,
            "eb3": None, "eb4": None,
            "cnc_file_back": "r86b0001", "cnc_file_front": "r86f0001", "has_cnc": 1}
    f = ct.extract_features(part, "morbidelli_cx100")
    assert f.two_faces is True


def test_extract_single_face():
    part = {"length_mm": 500, "width_mm": 300, "eb1": None, "eb2": None,
            "eb3": None, "eb4": None,
            "cnc_file_back": "r86b0001", "cnc_file_front": None, "has_cnc": 1}
    f = ct.extract_features(part, "morbidelli_cx100")
    assert f.two_faces is False


def test_groove_detection_positive():
    part = {"length_mm": 500, "width_mm": 300, "eb1": None, "eb2": None,
            "eb3": None, "eb4": None,
            "cnc_file_back": "r86bg007", "cnc_file_front": None, "has_cnc": 1}
    f = ct.extract_features(part, "morbidelli_cx100")
    assert f.has_groove is True


def test_groove_detection_negative():
    part = {"length_mm": 500, "width_mm": 300, "eb1": None, "eb2": None,
            "eb3": None, "eb4": None,
            "cnc_file_back": "r86b0007", "cnc_file_front": None, "has_cnc": 1}
    f = ct.extract_features(part, "morbidelli_cx100")
    assert f.has_groove is False


# ── Estimation ────────────────────────────────────────────────────────────────

def test_uncalibrated_returns_none(zero_cfg):
    part = {"length_mm": 1000, "width_mm": 500, "eb1": None, "eb2": None,
            "eb3": None, "eb4": None, "cnc_file_back": None, "cnc_file_front": None, "has_cnc": 0}
    f = ct.extract_features(part, "gabbiani_pt80")
    assert ct.estimate(f, zero_cfg) is None


def test_beam_saw_estimate(calibrated_cfg):
    # t = 10 + 1000*0.02 + 500*0.01 = 10 + 20 + 5 = 35
    part = {"length_mm": 1000, "width_mm": 500, "eb1": None, "eb2": None,
            "eb3": None, "eb4": None, "cnc_file_back": None, "cnc_file_front": None, "has_cnc": 0}
    f = ct.extract_features(part, "gabbiani_pt80")
    t = ct.estimate(f, calibrated_cfg)
    assert t == pytest.approx(35.0, abs=0.1)


def test_cnc_single_face_no_groove(calibrated_cfg):
    # area = 0.5m², t = 30 + 0.5*20 = 30 + 10 = 40
    part = {"length_mm": 1000, "width_mm": 500, "eb1": None, "eb2": None,
            "eb3": None, "eb4": None,
            "cnc_file_back": "r86b0001", "cnc_file_front": None, "has_cnc": 1}
    f = ct.extract_features(part, "morbidelli_cx100")
    t = ct.estimate(f, calibrated_cfg)
    assert t == pytest.approx(40.0, abs=0.1)


def test_cnc_two_faces_adds_face_coeff(calibrated_cfg):
    # t = 30 + 0.5*20 + 45 = 85
    part = {"length_mm": 1000, "width_mm": 500, "eb1": None, "eb2": None,
            "eb3": None, "eb4": None,
            "cnc_file_back": "r86b0001", "cnc_file_front": "r86f0001", "has_cnc": 1}
    f = ct.extract_features(part, "morbidelli_cx100")
    t = ct.estimate(f, calibrated_cfg)
    assert t == pytest.approx(85.0, abs=0.1)


def test_cnc_groove_adds_groove_coeff(calibrated_cfg):
    # t = 30 + 0.5*20 + 15 = 55
    part = {"length_mm": 1000, "width_mm": 500, "eb1": None, "eb2": None,
            "eb3": None, "eb4": None,
            "cnc_file_back": "r86bg007", "cnc_file_front": None, "has_cnc": 1}
    f = ct.extract_features(part, "morbidelli_cx100")
    t = ct.estimate(f, calibrated_cfg)
    assert t == pytest.approx(55.0, abs=0.1)


def test_edge_bander_four_edges(calibrated_cfg):
    # per_pass = 5 + 1000*0.01 = 15s; 4 passes + 3 returns*8 = 15*4 + 24 = 84
    part = {"length_mm": 1000, "width_mm": 500,
            "eb1": "tape", "eb2": "tape", "eb3": "tape", "eb4": "tape",
            "cnc_file_back": None, "cnc_file_front": None, "has_cnc": 0}
    f = ct.extract_features(part, "stefani_kd")
    t = ct.estimate(f, calibrated_cfg)
    assert t == pytest.approx(84.0, abs=0.1)


def test_edge_bander_no_edges_returns_none(calibrated_cfg):
    part = {"length_mm": 1000, "width_mm": 500,
            "eb1": None, "eb2": None, "eb3": None, "eb4": None,
            "cnc_file_back": None, "cnc_file_front": None, "has_cnc": 0}
    f = ct.extract_features(part, "stefani_kd")
    assert ct.estimate(f, calibrated_cfg) is None


def test_sander_estimate(calibrated_cfg):
    # t = 8 + 1000*0.005 = 13
    part = {"length_mm": 1000, "width_mm": 500, "eb1": None, "eb2": None,
            "eb3": None, "eb4": None, "cnc_file_back": None, "cnc_file_front": None, "has_cnc": 0}
    f = ct.extract_features(part, "dmc60_rcs135")
    t = ct.estimate(f, calibrated_cfg)
    assert t == pytest.approx(13.0, abs=0.1)


# ── Job estimation ────────────────────────────────────────────────────────────

def test_estimate_job_unknown(conn, calibrated_cfg):
    result = ct.estimate_job(conn, "DOESNOTEXIST", calibrated_cfg)
    assert result == {}


def test_estimate_job_returns_machines(conn, calibrated_cfg):
    conn.execute("INSERT INTO jobs (job_name, total_parts) VALUES ('J1', 2)")
    job_id = conn.execute("SELECT id FROM jobs WHERE job_name='J1'").fetchone()["id"]
    conn.execute(
        """INSERT INTO parts (job_id, part_name, length_mm, width_mm, thickness_mm,
           qty, eb1, eb2, eb3, eb4, cnc_file_back, cnc_file_front, has_cnc)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (job_id, "Top", 1000, 500, 18, 1, "tape", "tape", "tape", "tape",
         "r86b0001", "r86f0001", 1)
    )
    conn.commit()
    result = ct.estimate_job(conn, "J1", calibrated_cfg)
    assert result["job_name"] == "J1"
    assert "morbidelli_cx100" in result["machines"]
    assert "stefani_kd"       in result["machines"]
    assert result["critical_machine"] is not None


def test_estimate_job_critical_path(conn, calibrated_cfg):
    conn.execute("INSERT INTO jobs (job_name, total_parts) VALUES ('J2', 1)")
    job_id = conn.execute("SELECT id FROM jobs WHERE job_name='J2'").fetchone()["id"]
    # Large panel with 4 edges — edge bander likely critical
    for i in range(10):
        conn.execute(
            """INSERT INTO parts (job_id, part_name, length_mm, width_mm, thickness_mm,
               qty, eb1, eb2, eb3, eb4, cnc_file_back, cnc_file_front, has_cnc)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (job_id, f"Part{i}", 2000, 600, 18, 1, "tape", "tape", "tape", "tape",
             None, None, 0)
        )
    conn.commit()
    result = ct.estimate_job(conn, "J2", calibrated_cfg)
    assert result["critical_path_s"] is not None
    assert result["critical_machine"] is not None


# ── Calibration ───────────────────────────────────────────────────────────────

def test_calibrate_beam_saw():
    records = [
        {"length_mm": 1000, "width_mm": 500, "eb1": None, "eb2": None,
         "eb3": None, "eb4": None, "cnc_file_back": None, "cnc_file_front": None,
         "has_cnc": 0, "actual_seconds": 35},
        {"length_mm": 2000, "width_mm": 600, "eb1": None, "eb2": None,
         "eb3": None, "eb4": None, "cnc_file_back": None, "cnc_file_front": None,
         "has_cnc": 0, "actual_seconds": 60},
        {"length_mm": 500,  "width_mm": 300, "eb1": None, "eb2": None,
         "eb3": None, "eb4": None, "cnc_file_back": None, "cnc_file_front": None,
         "has_cnc": 0, "actual_seconds": 22},
    ]
    result = ct.calibrate(records, "gabbiani_pt80")
    if "error" in result and "numpy" in result["error"]:
        pytest.skip("numpy not installed")
    assert "base_s"        in result
    assert "length_coeff"  in result
    assert all(v >= 0 for v in result.values())
