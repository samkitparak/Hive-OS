import json
from datetime import datetime, timezone

from db import init_db
import digital_twin


def _model(conn, machine_key, coefficients):
    machine_id = conn.execute(
        "SELECT id FROM machines WHERE machine_key=?", (machine_key,)
    ).fetchone()["id"]
    conn.execute(
        """INSERT INTO cycle_models
           (machine_id, version, training_signature, sample_count, train_count,
            validation_count, inlier_count, coefficients_json,
            identified_features_json, confidence, status, trained_at)
           VALUES (?,1,?,30,24,6,24,?,'[]','medium','active',?)""",
        (machine_id, f"test-{machine_key}", json.dumps(coefficients),
         datetime.now(timezone.utc).isoformat()),
    )


def _factory():
    conn = init_db(":memory:")
    _model(conn, "gabbiani_pt80", {"base_s": 10})
    materials = ["A", "B", "A"]
    for index, material in enumerate(materials):
        conn.execute(
            "INSERT INTO jobs (job_name, job_date, total_parts) VALUES (?,?,1)",
            (f"J{index + 1}", f"2026-07-{14 + index:02d}"),
        )
        job_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
        conn.execute(
            """INSERT INTO parts
               (job_id, part_name, material, length_mm, width_mm, thickness_mm)
               VALUES (?, 'Panel', ?, 1000, 500, 18)""", (job_id, material),
        )
    conn.commit()
    return conn


def test_digital_twin_is_deterministic_and_gates_recommendation_on_routes():
    conn = _factory()
    first = digital_twin.compare(conn, policies=["fifo", "material_batch"], seed=7)
    second = digital_twin.compare(conn, policies=["fifo", "material_batch"], seed=7)
    assert first["scenarios"] == second["scenarios"]
    assert first["readiness"]["model_coverage"] == 1
    assert first["recommendation"] is None
    fifo, batching = first["scenarios"]
    assert batching["setup_count"] < fifo["setup_count"]
    assert batching["makespan_s"] < fifo["makespan_s"]


def test_setup_aware_policy_uses_directional_machine_families():
    conn = _factory()
    result = digital_twin.compare(conn, policies=["fifo", "setup_aware"], seed=11)
    fifo, setup_aware = result["scenarios"]
    assert setup_aware["policy"] == "setup_aware"
    assert setup_aware["setup_count"] < fifo["setup_count"]
    assert setup_aware["setup_time_s"] < fifo["setup_time_s"]
    assert setup_aware["setup_by_machine"]["gabbiani_pt80"]["count"] == 1


def test_digital_twin_blocks_scenarios_when_cycle_models_are_missing():
    conn = init_db(":memory:")
    conn.execute("INSERT INTO jobs (job_name, total_parts) VALUES ('J1', 1)")
    job_id = conn.execute("SELECT id FROM jobs").fetchone()["id"]
    conn.execute(
        "INSERT INTO parts (job_id, part_name, length_mm, width_mm) VALUES (?, 'P', 1000, 500)",
        (job_id,),
    )
    conn.commit()
    result = digital_twin.compare(conn)
    assert result["scenarios"] == []
    assert "gabbiani_pt80" in result["readiness"]["missing_models"]
