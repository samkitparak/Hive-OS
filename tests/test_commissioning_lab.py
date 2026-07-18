"""Assumption-only virtual commissioning without production contamination."""

import copy
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import commissioning_lab
from db import init_db


@pytest.fixture
def conn():
    connection = init_db(":memory:", check_same_thread=False)
    yield connection
    connection.close()


def test_assumption_manifest_is_explicit_and_traceable():
    manifest = commissioning_lab.assumptions()
    assert manifest["status"] == "assumption_only"
    assert manifest["production_eligible"] is False
    assert manifest["machine_count"] == 11
    assert manifest["family_count"] == 5
    assert manifest["reference_units_per_shift"] == 180
    assert len(manifest["sha256"]) == 64
    assert all(source["url"].startswith("https://") for source in manifest["sources"])


def test_analysis_is_reproducible_and_ranks_measurements_and_interventions():
    first = commissioning_lab.analyze(samples=10, seed=17)
    second = commissioning_lab.analyze(samples=10, seed=17)
    for key in ("reference_workload", "baseline", "measurement_priorities", "interventions"):
        assert first[key] == second[key]
    assert sum(first["reference_workload"]["family_units"].values()) == 180
    assert sum(item["bottleneck_probability"] for item in first["baseline"]["constraints"]) == pytest.approx(1)
    assert first["measurement_priorities"][0]["priority_score"] > 0
    assert first["measurement_priorities"][0]["measure_on_site"]
    assert first["interventions"][0]["production_eligible"] is False
    assert all(item["modeled_throughput_uplift_pct"] >= 0 for item in first["interventions"])
    assert all(item["modeled_makespan_reduction_pct"] >= 0 for item in first["interventions"])


def test_run_persists_only_in_isolated_lab_table(conn):
    protected = (
        "machine_events", "cycle_models", "route_observations", "production_forecasts",
        "planning_scenarios", "resource_change_events", "execution_jobs",
    )
    before = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in protected
    }
    result = commissioning_lab.run(conn, samples=10, seed=4, actor="offsite-test")
    after = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in protected
    }
    assert after == before
    assert conn.execute("SELECT COUNT(*) FROM virtual_factory_runs").fetchone()[0] == 1
    assert result["production_eligible"] is False
    snapshot = commissioning_lab.snapshot(conn)
    assert snapshot["latest"]["actor"] == "offsite-test"
    assert snapshot["stale"] is False


def test_snapshot_marks_changed_assumptions_stale(conn, tmp_path):
    commissioning_lab.run(conn, samples=10, seed=1)
    cfg = yaml.safe_load(commissioning_lab.DEFAULT_CONFIG_PATH.read_text())
    cfg["machines"]["action_e"]["cycle_s"]["mode"] += 1
    changed = tmp_path / "changed.yaml"
    changed.write_text(yaml.safe_dump(cfg, sort_keys=False))
    assert commissioning_lab.snapshot(conn, changed)["stale"] is True


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda cfg: cfg.update(status="approved"), "must remain assumption_only"),
        (lambda cfg: cfg["families"]["standard_carcass"].update(share=0.60), "must sum to 1.0"),
        (lambda cfg: cfg["machines"]["action_e"]["availability"].update(max=1.2), "invalid triangular range"),
    ],
)
def test_tampered_or_promoted_assumptions_are_rejected(tmp_path, change, message):
    cfg = yaml.safe_load(commissioning_lab.DEFAULT_CONFIG_PATH.read_text())
    change(cfg)
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(copy.deepcopy(cfg), sort_keys=False))
    with pytest.raises(ValueError, match=message):
        commissioning_lab.analyze(samples=10, config_path=path)


def test_sample_and_seed_bounds_are_enforced():
    with pytest.raises(ValueError, match="between 10 and 100"):
        commissioning_lab.analyze(samples=9)
    with pytest.raises(ValueError, match="between 0"):
        commissioning_lab.analyze(seed=-1)
