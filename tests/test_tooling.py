"""Tool registry, usage evidence, service, and planning integration tests."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import alerting
import operations
import resources
import tooling
from db import init_db


@pytest.fixture
def conn():
    connection = init_db(Path(":memory:"))
    resources.sync_defaults(connection)
    return connection


def _create_tool(conn, key="SAW-001", **overrides):
    payload = {
        "tool_key": key, "pool_key": "cutting_tooling", "name": "Main saw blade",
        "tool_type": "panel_saw_blade", "life_basis": "cycles", "rated_life": 10,
        "warning_remaining": 2, "verified": True, "actor": "test",
    }
    payload.update(overrides)
    return tooling.create_asset(conn, payload)


def test_usage_threshold_controls_capacity_and_service_work(conn):
    _create_tool(conn)
    initial = resources.snapshot(conn)
    cutting = next(row for row in initial["tool_pools"] if row["pool_key"] == "cutting_tooling")
    assert cutting["capacity_source"] == "asset_registry"
    assert cutting["effective_available_qty"] == 1

    usage = tooling.record_usage(conn, "SAW-001", {
        "event_key": "manual:1", "delta_cycles": 8, "actor": "tester",
    })
    assert usage["tool"]["status"] == "service_due"
    assert usage["tool"]["remaining_life"] == 2
    duplicate = tooling.record_usage(conn, "SAW-001", {
        "event_key": "manual:1", "delta_cycles": 8, "actor": "tester",
    })
    assert duplicate["duplicate"] is True
    assert duplicate["tool"]["cycles_used"] == 8

    cutting = next(row for row in resources.snapshot(conn)["tool_pools"]
                   if row["pool_key"] == "cutting_tooling")
    assert cutting["effective_available_qty"] == 0
    assert tooling.sync_service_work(conn)["work_orders_created"] == 1
    assert tooling.sync_service_work(conn)["work_orders_created"] == 0
    work = conn.execute("SELECT * FROM maintenance_work_orders WHERE source='tool_lifecycle'").fetchone()
    assert work and work["priority"] == "high"
    assert any(item["rule_key"] == "tool_service_due" for item in alerting.collect_candidates(conn))

    serviced = tooling.record_service(conn, "SAW-001", {
        "action": "recondition", "end_reason": "worn", "actor": "technician",
    })
    assert serviced["status"] == "available"
    assert serviced["cycles_used"] == 0
    assert serviced["recondition_count"] == 1
    assert conn.execute("SELECT status FROM maintenance_work_orders WHERE id=?", (work["id"],)).fetchone()[0] == "done"


def test_verified_program_mapping_imports_each_machine_event_once(conn):
    _create_tool(conn)
    tooling.upsert_program_mapping(conn, "SAW-001", {
        "machine_key": "gabbiani_pt80", "cnc_file": r"C:\Programs\DOOR-A.XCS",
        "parts_per_cycle": 2, "cycles_per_event": 1, "verified": True, "actor": "test",
    })
    machine_id = conn.execute("SELECT id FROM machines WHERE machine_key='gabbiani_pt80'").fetchone()[0]
    conn.execute(
        "INSERT INTO machine_events (machine_id,event_type,cnc_file,ts) VALUES (?,'cycle_end',?,?)",
        (machine_id, "/controller/programs/door-a.xcs", "2026-07-17T10:00:00+00:00"),
    )
    conn.commit()
    first = tooling.sync_machine_usage(conn)
    second = tooling.sync_machine_usage(conn)
    assert first["usage_events_imported"] == 1
    assert second["usage_events_imported"] == 0
    tool = tooling.get_asset(conn, "SAW-001")
    assert tool["parts_used"] == 2
    assert tool["cycles_used"] == 1


def test_quality_attribution_is_conservative(conn):
    _create_tool(conn, rated_life=100, warning_remaining=5)
    tooling.action(conn, "SAW-001", {
        "action": "install", "machine_key": "gabbiani_pt80", "pocket": "1", "actor": "test",
    })
    for index in range(3):
        operations.create_quality_check(conn, {
            "result": "fail", "machine_key": "gabbiani_pt80", "inspector": "qa",
            "notes": f"edge chip {index}", "source": "test",
        })
    tool = tooling.get_asset(conn, "SAW-001")
    assert tool["quality_failures_this_life"] == 3
    assert tool["status"] == "service_due"
    tooling.record_service(conn, "SAW-001", {
        "action": "recondition", "end_reason": "quality", "actor": "test",
    })
    tooling.action(conn, "SAW-001", {
        "action": "install", "machine_key": "gabbiani_pt80", "pocket": "1", "actor": "test",
    })

    _create_tool(conn, key="SAW-002", rated_life=100, warning_remaining=5)
    tooling.action(conn, "SAW-002", {
        "action": "install", "machine_key": "gabbiani_pt80", "pocket": "2", "actor": "test",
    })
    check = operations.create_quality_check(conn, {
        "result": "fail", "machine_key": "gabbiani_pt80", "inspector": "qa", "source": "test",
    })
    assert conn.execute(
        "SELECT 1 FROM tool_quality_links WHERE quality_check_id=?", (check["id"],)
    ).fetchone() is None


def test_local_life_estimate_requires_five_wear_outcomes(conn):
    _create_tool(conn, rated_life=None, warning_remaining=None)
    for index, life in enumerate((80, 90, 100, 110, 120), start=1):
        tooling.record_usage(conn, "SAW-001", {
            "event_key": f"life:{index}", "delta_cycles": life, "actor": "test",
        })
        tool = tooling.record_service(conn, "SAW-001", {
            "action": "recondition", "end_reason": "worn", "actor": "test",
        })
    assert tool["learning"]["status"] == "available"
    assert tool["learning"]["sample_count"] == 5
    assert tool["learning"]["conservative_life"] == 88
    assert tool["life_limit_source"] == "conservative_local_evidence"
    expired = tooling.record_usage(conn, "SAW-001", {
        "event_key": "learned-limit", "delta_cycles": 88, "actor": "test",
    })
    assert expired["tool"]["status"] == "expired"


def test_inspection_does_not_count_as_end_of_life_evidence(conn):
    _create_tool(conn, rated_life=100, warning_remaining=None)
    tooling.record_usage(conn, "SAW-001", {
        "event_key": "inspection-usage", "delta_cycles": 40, "actor": "test",
    })
    tooling.record_service(conn, "SAW-001", {
        "action": "inspect", "end_reason": "worn", "actor": "test",
    })

    tool = tooling.get_asset(conn, "SAW-001")
    assert tool["learning"]["sample_count"] == 0
