"""Warehouse components, usable remnants, and movement-ledger behavior."""

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from db import init_db
import inventory
import production_control
import resources


def _factory(*, edge="E1", qty=2, length=1000, width=500):
    conn = init_db(":memory:", check_same_thread=False)
    conn.execute("INSERT INTO jobs (job_name,total_parts) VALUES ('INV-1',?)", (qty,))
    job_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    conn.execute(
        """INSERT INTO parts
           (job_id,part_name,material,length_mm,width_mm,thickness_mm,qty,grain,
            eb1,eb2,eb3,eb4)
           VALUES (?,'Panel','BOARD A',?,?,18,?,1,?,?,?,?)""",
        (job_id, length, width, qty, edge, edge, edge, edge),
    )
    conn.commit()
    production_control.sync_all(conn)
    resources.sync_defaults(conn)
    order = conn.execute(
        "SELECT po.* FROM production_orders po WHERE po.job_id=?", (job_id,)
    ).fetchone()
    return conn, dict(order)


def _scenario(conn):
    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        """INSERT INTO planning_scenarios
           (name,created_by,request_json,result_json,readiness_json,input_signature,status,created_at)
           VALUES ('test','test',? ,? ,? ,'signature','draft',?)""",
        (json.dumps({}), json.dumps({}), json.dumps({}), now),
    )
    conn.commit()
    return cursor.lastrowid


def _verify_edge_item(conn, stock=10):
    inventory.upsert_item(conn, "E1", {
        "name": "E1", "category": "edge_band", "uom": "m",
        "usage_factor": 1.05, "order_multiple": 5, "verified": True,
    })
    inventory.set_lot_balance(conn, "E1", "ROLL-1", {
        "on_hand_qty": stock, "location": "EDGE-RACK", "verified": True,
        "movement_type": "receipt", "actor": "test",
    })


def test_cv_edges_become_measured_component_demand():
    conn, _ = _factory()
    warehouse = inventory.snapshot(conn, ["INV-1"])
    edge = next(item for item in warehouse["components"] if item["item_key"] == "E1")
    # Two 1000 x 500 panels have six metres of perimeter, plus 5% allowance.
    assert edge["required_qty"] == 6.3
    assert edge["uom"] == "m"
    assert edge["source"] == "cv_edges"
    assert edge["feasible"] is False


def test_open_order_component_readiness_uses_open_demand():
    conn, _ = _factory()
    warehouse = inventory.snapshot(conn)
    edge = next(item for item in warehouse["components"] if item["item_key"] == "E1")
    assert edge["required_qty"] == 0
    assert edge["open_required_qty"] == 6.3
    assert edge["open_feasible"] is False
    assert warehouse["component_ready"] is False


def test_suspected_shifted_cnc_value_is_an_issue_not_edge_demand():
    conn, _ = _factory(edge="*r81bg021*")
    warehouse = inventory.snapshot(conn, ["INV-1"])
    assert warehouse["components"] == []
    assert warehouse["issues"][0]["issue_code"] == "suspected_shifted_cnc_value"


def test_component_balance_drives_shortage_and_purchase_rounding():
    conn, _ = _factory()
    _verify_edge_item(conn, stock=2)
    edge = next(item for item in inventory.snapshot(conn, ["INV-1"])["components"]
                if item["item_key"] == "E1")
    assert edge["shortage_qty"] == 4.3
    assert edge["suggested_order_qty"] == 5
    assert edge["feasible"] is False
    lot = edge["lots"][0]
    with pytest.raises(ValueError, match="changed"):
        inventory.set_lot_balance(conn, "E1", "ROLL-1", {
            "on_hand_qty": 10, "verified": True,
            "expected_version": lot["version"] + 1, "actor": "test",
        })


def test_lot_receipt_is_idempotent_and_zero_balance_is_depleted():
    conn, _ = _factory()
    inventory.upsert_item(conn, "HINGE", {
        "name": "Hinge", "category": "hardware", "uom": "each", "verified": True,
    })
    first = inventory.set_lot_balance(conn, "HINGE", "BOX-1", {
        "on_hand_qty": 50, "verified": True, "movement_type": "receipt",
        "idempotency_key": "receipt-1", "actor": "test",
    })
    repeated = inventory.set_lot_balance(conn, "HINGE", "BOX-1", {
        "on_hand_qty": 999, "verified": True, "movement_type": "receipt",
        "idempotency_key": "receipt-1", "actor": "test",
    })
    assert repeated["on_hand_qty"] == first["on_hand_qty"] == 50
    assert conn.execute("SELECT COUNT(*) FROM inventory_movements").fetchone()[0] == 1
    empty = inventory.set_lot_balance(conn, "HINGE", "BOX-2", {
        "on_hand_qty": 0, "verified": True, "actor": "test",
    })
    assert empty["status"] == "depleted"


def test_verified_remnant_credits_one_fitting_part_conservatively():
    conn, _ = _factory(edge=None, qty=1)
    before = resources.snapshot(conn, ["INV-1"])["materials"][0]
    assert before["gross_required_sheets"] == 1
    assert before["required_sheets"] == 1
    inventory.create_remnant(conn, {
        "material_key": "BOARD A", "length_mm": 1100, "width_mm": 600,
        "thickness_mm": 18, "grain_direction": "length", "location": "REM-RACK",
        "verified": True, "actor": "test",
    })
    after = resources.snapshot(conn, ["INV-1"])["materials"][0]
    assert after["gross_required_sheets"] == 1
    assert after["required_sheets"] == 0
    assert after["remnant_credit_area_m2"] == 0.5


def test_unverified_or_too_small_remnant_never_reduces_sheet_demand():
    conn, _ = _factory(edge=None, qty=1)
    small = inventory.create_remnant(conn, {
        "material_key": "BOARD A", "length_mm": 100, "width_mm": 100,
        "verified": True, "actor": "test",
    })
    assert small["status"] == "hold"
    inventory.create_remnant(conn, {
        "material_key": "BOARD A", "length_mm": 1100, "width_mm": 600,
        "verified": False, "actor": "test",
    })
    material = resources.snapshot(conn, ["INV-1"])["materials"][0]
    assert material["required_sheets"] == 1


def test_component_and_remnant_reservations_release_or_consume():
    conn, order = _factory(qty=1)
    _verify_edge_item(conn, stock=10)
    remnant = inventory.create_remnant(conn, {
        "material_key": "BOARD A", "length_mm": 1100, "width_mm": 600,
        "verified": True, "actor": "test",
    })
    scenario_id = _scenario(conn)
    credits = inventory.reserve_remnants(conn, scenario_id, [order["id"]])
    inventory.reserve_components(conn, scenario_id, [order["id"]])
    assert sum(credits.values()) == 0.5
    assert inventory.remnant_row(conn, remnant["remnant_key"])["status"] == "reserved"
    assert conn.execute("SELECT reserved_qty FROM inventory_lots").fetchone()[0] == 3.15

    inventory.settle_order(conn, order["id"], completed=False, actor="test")
    assert inventory.remnant_row(conn, remnant["remnant_key"])["status"] == "available"
    assert conn.execute("SELECT reserved_qty FROM inventory_lots").fetchone()[0] == 0

    scenario_id = _scenario(conn)
    inventory.reserve_remnants(conn, scenario_id, [order["id"]])
    inventory.reserve_components(conn, scenario_id, [order["id"]])
    inventory.settle_order(conn, order["id"], completed=True, actor="test")
    assert inventory.remnant_row(conn, remnant["remnant_key"])["status"] == "consumed"
    assert conn.execute("SELECT on_hand_qty FROM inventory_lots").fetchone()[0] == pytest.approx(6.85)
    movement_types = {row[0] for row in conn.execute("SELECT movement_type FROM inventory_movements")}
    assert {"receipt", "reservation", "release", "issue", "create"} <= movement_types


def test_inventory_api_exposes_snapshot_and_remnant_creation():
    conn, _ = _factory(edge=None, qty=1)
    import main

    main.set_conn(conn)
    with TestClient(main.app) as client:
        main.set_conn(conn)
        snapshot = client.get("/api/inventory/snapshot", params={"job_name": "INV-1"})
        assert snapshot.status_code == 200
        created = client.post("/api/inventory/remnants", json={
            "material_key": "BOARD A", "length_mm": 1100,
            "width_mm": 600, "verified": True, "actor": "test",
        })
        assert created.status_code == 200
        assert created.json()["status"] == "available"
