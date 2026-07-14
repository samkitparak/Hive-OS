"""Procurement ordering, receiving, and ERP exchange behavior."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from db import init_db
import inventory
import procurement
import production_control
import resources


def _factory():
    conn = init_db(":memory:", check_same_thread=False)
    conn.execute("INSERT INTO jobs (job_name,total_parts) VALUES ('BUY-1',2)")
    job_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    conn.execute(
        """INSERT INTO parts
           (job_id,part_name,material,length_mm,width_mm,thickness_mm,qty,grain,eb1,eb2,eb3,eb4)
           VALUES (?,'Panel','BOARD A',1000,500,18,2,1,'E1','E1','E1','E1')""",
        (job_id,),
    )
    conn.commit()
    production_control.sync_all(conn)
    due = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    conn.execute("UPDATE production_orders SET due_at=?", (due,))
    conn.commit()
    resources.sync_defaults(conn)
    order = dict(conn.execute("SELECT * FROM production_orders WHERE job_id=?", (job_id,)).fetchone())
    return conn, order


def _supplier(conn, *, verified=True, lead=5):
    return procurement.upsert_supplier(conn, "EDGECO", {
        "name": "Edge Co", "currency": "INR", "lead_time_days": lead,
        "verified": verified, "actor": "test",
    })


def _mapping(conn, *, verified=True, preferred=True, conversion=50):
    return procurement.upsert_mapping(conn, "EDGECO", "component", "E1", {
        "supplier_sku": "ROLL-E1", "purchase_uom": "roll",
        "conversion_factor": conversion, "order_multiple": 1,
        "min_order_qty": 1, "unit_price": 600, "currency": "INR",
        "preferred": preferred, "verified": verified, "actor": "test",
    })


def _draft(conn):
    return procurement.draft_recommendations(conn, {"actor": "test"})["orders"][0]


def _approve(conn, order):
    return procurement.decide_order(conn, order["id"], {
        "action": "approve", "expected_version": order["version"], "actor": "test",
    })


def test_recommendations_round_supplier_units_and_remove_open_inbound():
    conn, _ = _factory()
    _supplier(conn)
    _mapping(conn)
    edge = next(item for item in procurement.recommendations(conn) if item["object_key"] == "E1")
    assert edge["shortage_qty"] == 6.3
    assert edge["recommended_purchase_qty"] == 1
    assert edge["recommended_internal_qty"] == 50
    assert edge["at_risk"] is True
    assert edge["status"] == "ready_to_draft"

    order = _draft(conn)
    assert order["lines"][0]["ordered_qty"] == 1
    covered = next(item for item in procurement.recommendations(conn) if item["object_key"] == "E1")
    assert covered["open_inbound_qty"] == 50
    assert covered["uncovered_qty"] == 0
    assert covered["status"] == "covered_by_inbound"
    with pytest.raises(ValueError, match="No mapped uncovered"):
        procurement.draft_recommendations(conn, {"actor": "test"})


def test_approval_requires_verified_master_data_and_outbox_ack_marks_sent():
    conn, _ = _factory()
    _supplier(conn, verified=False)
    _mapping(conn, verified=False)
    order = _draft(conn)
    with pytest.raises(ValueError, match="Verify the supplier"):
        _approve(conn, order)
    supplier = procurement.upsert_supplier(conn, "EDGECO", {
        "name": "Edge Co", "currency": "INR", "lead_time_days": 5,
        "verified": True, "expected_version": 1, "actor": "test",
    })
    mapping = procurement.upsert_mapping(conn, "EDGECO", "component", "E1", {
        "supplier_sku": "ROLL-E1", "purchase_uom": "roll", "conversion_factor": 50,
        "order_multiple": 1, "min_order_qty": 1, "unit_price": 600,
        "currency": "INR", "preferred": True, "verified": True,
        "expected_version": 1, "actor": "test",
    })
    assert supplier["verified"] == 1 and mapping["verified"] == 1
    approved = _approve(conn, procurement.order_detail(conn, order["id"]))
    queued = procurement.decide_order(conn, order["id"], {
        "action": "queue", "expected_version": approved["version"], "actor": "test",
    })
    assert queued["status"] == "queued"
    document = procurement.outbox(conn, "pending")[0]
    assert document["payload"]["document_type"] == "Order"
    ack = procurement.acknowledge_outbox(conn, document["id"], {
        "success": True, "external_id": "ERP-PO-9", "actor": "test",
    })
    assert ack["status"] == "delivered"
    assert ack["payload"]["document_type"] == "Order"
    sent = procurement.order_detail(conn, order["id"])
    assert sent["status"] == "sent"
    assert sent["external_id"] == "ERP-PO-9"


def test_receipt_posts_only_accepted_component_quantity_and_is_idempotent():
    conn, _ = _factory()
    _supplier(conn)
    _mapping(conn)
    order = _approve(conn, _draft(conn))
    receipt_payload = {
        "receipt_key": "GRN-1", "purchase_order_id": order["id"],
        "location": "EDGE-RACK", "verified": True, "actor": "test",
        "lines": [{"line_number": 1, "lot_code": "ROLL-BATCH-1",
                   "accepted_qty": 0.5, "rejected_qty": 0.5,
                   "rejection_reason": "Damaged roll"}],
    }
    receipt = procurement.receive_order(conn, receipt_payload)
    assert receipt["status"] == "posted_with_exceptions"
    assert receipt["lines"][0]["accepted_internal_qty"] == 25
    lot = conn.execute("SELECT * FROM inventory_lots").fetchone()
    assert lot["on_hand_qty"] == 25
    assert lot["verified"] == 1
    assert procurement.order_detail(conn, order["id"])["status"] == "received_with_exceptions"

    duplicate = procurement.receive_order(conn, receipt_payload)
    assert duplicate["duplicate"] is True
    assert conn.execute("SELECT on_hand_qty FROM inventory_lots").fetchone()[0] == 25
    assert conn.execute("SELECT COUNT(*) FROM inventory_movements WHERE movement_type='receipt'").fetchone()[0] == 1


def test_partial_receipt_then_completion_updates_order_and_stock():
    conn, _ = _factory()
    _supplier(conn)
    _mapping(conn, conversion=10)
    order = procurement.create_order(conn, {
        "supplier_key": "EDGECO", "actor": "test",
        "lines": [{"object_type": "component", "object_key": "E1", "ordered_qty": 2}],
    })
    order = _approve(conn, order)
    first = procurement.receive_order(conn, {
        "receipt_key": "GRN-A", "purchase_order_id": order["id"], "verified": True,
        "actor": "test", "lines": [{"line_number": 1, "lot_code": "E1-A", "accepted_qty": 1}],
    })
    assert first["status"] == "posted"
    assert procurement.order_detail(conn, order["id"])["status"] == "partially_received"
    procurement.receive_order(conn, {
        "receipt_key": "GRN-B", "purchase_order_id": order["id"], "verified": True,
        "actor": "test", "lines": [{"line_number": 1, "lot_code": "E1-B", "accepted_qty": 1}],
    })
    assert procurement.order_detail(conn, order["id"])["status"] == "received"
    assert conn.execute("SELECT SUM(on_hand_qty) FROM inventory_lots").fetchone()[0] == 20


def test_sheet_receipt_posts_to_material_lot_and_movement_ledger():
    conn, _ = _factory()
    procurement.upsert_supplier(conn, "BOARDCO", {
        "name": "Board Co", "currency": "INR", "verified": True, "actor": "test",
    })
    procurement.upsert_mapping(conn, "BOARDCO", "sheet", "BOARD A", {
        "supplier_sku": "BOARD-A-18", "purchase_uom": "sheet", "conversion_factor": 1,
        "order_multiple": 1, "min_order_qty": 1, "unit_price": 1800,
        "currency": "INR", "preferred": True, "verified": True, "actor": "test",
    })
    order = procurement.create_order(conn, {
        "supplier_key": "BOARDCO", "actor": "test",
        "lines": [{"object_type": "sheet", "object_key": "BOARD A", "ordered_qty": 3}],
    })
    order = _approve(conn, order)
    procurement.receive_order(conn, {
        "receipt_key": "GRN-SHEET", "purchase_order_id": order["id"], "verified": True,
        "location": "SHEET-RACK", "actor": "test",
        "lines": [{"line_number": 1, "lot_code": "BOARD-LOT-1", "accepted_qty": 3}],
    })
    lot = conn.execute("SELECT * FROM material_lots").fetchone()
    assert lot["on_hand_sheets"] == 3
    assert conn.execute(
        "SELECT object_type FROM inventory_movements WHERE movement_type='receipt'"
    ).fetchone()[0] == "sheet_lot"


def test_supplier_catalog_csv_validates_applies_and_deduplicates():
    conn, _ = _factory()
    text = "\n".join([
        "supplier_key,supplier_name,object_type,object_key,supplier_sku,purchase_uom,conversion_factor,order_multiple,min_order_qty,unit_price,currency,lead_time_days,preferred",
        "EDGECO,Edge Co,component,E1,ROLL-E1,roll,50,1,1,600,INR,5,true",
    ])
    preview = procurement.import_csv(conn, {
        "document_type": "supplier_catalog", "mode": "validate", "csv_text": text,
        "file_name": "catalog.csv", "actor": "test",
    })
    assert preview["status"] == "passed"
    assert preview["ready_to_apply"] is True
    applied = procurement.import_csv(conn, {
        "document_type": "supplier_catalog", "mode": "apply", "csv_text": text,
        "file_name": "catalog.csv", "approve_master_data": True, "actor": "test",
    })
    assert applied["records_imported"] == 1
    assert procurement.snapshot(conn)["summary"]["verified_suppliers"] == 1
    duplicate = procurement.import_csv(conn, {
        "document_type": "supplier_catalog", "mode": "apply", "csv_text": text,
        "approve_master_data": True, "actor": "test",
    })
    assert duplicate["duplicate"] is True


def test_invalid_draft_is_atomic_and_invalid_catalog_never_becomes_ready():
    conn, _ = _factory()
    _supplier(conn)
    _mapping(conn)
    with pytest.raises(KeyError, match="Unknown component"):
        procurement.create_order(conn, {
            "supplier_key": "EDGECO", "actor": "test",
            "lines": [
                {"object_type": "component", "object_key": "E1", "ordered_qty": 1},
                {"object_type": "component", "object_key": "MISSING", "ordered_qty": 1},
            ],
        })
    assert conn.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0] == 0
    invalid = "\n".join([
        "supplier_key,supplier_name,object_type,object_key,supplier_sku,conversion_factor",
        "EDGECO,Edge Co,component,E1,ROLL-E1,0",
    ])
    preview = procurement.import_csv(conn, {
        "document_type": "supplier_catalog", "mode": "validate", "csv_text": invalid,
        "actor": "test",
    })
    assert preview["status"] == "failed"
    assert preview["ready_to_apply"] is False


def test_goods_receipt_csv_requires_approval_then_applies_once():
    conn, _ = _factory()
    _supplier(conn)
    _mapping(conn, conversion=10)
    order = procurement.create_order(conn, {
        "supplier_key": "EDGECO", "actor": "test",
        "lines": [{"object_type": "component", "object_key": "E1", "ordered_qty": 1}],
    })
    text = "\n".join([
        "receipt_key,po_number,line_number,lot_code,accepted_qty,rejected_qty,location,verified",
        f"GRN-CSV,{order['po_number']},1,E1-CSV,1,0,EDGE-RACK,true",
    ])
    blocked = procurement.import_csv(conn, {
        "document_type": "goods_receipt", "mode": "validate", "csv_text": text,
        "actor": "test",
    })
    assert blocked["ready_to_apply"] is False
    _approve(conn, order)
    preview = procurement.import_csv(conn, {
        "document_type": "goods_receipt", "mode": "validate", "csv_text": text,
        "actor": "test",
    })
    assert preview["ready_to_apply"] is True
    applied = procurement.import_csv(conn, {
        "document_type": "goods_receipt", "mode": "apply", "csv_text": text,
        "actor": "test",
    })
    assert applied["records_imported"] == 1
    assert conn.execute("SELECT on_hand_qty FROM inventory_lots").fetchone()[0] == 10
    duplicate = procurement.import_csv(conn, {
        "document_type": "goods_receipt", "mode": "apply", "csv_text": text,
        "actor": "test",
    })
    assert duplicate["duplicate"] is True
    assert conn.execute("SELECT on_hand_qty FROM inventory_lots").fetchone()[0] == 10


def test_procurement_api_exposes_snapshot_draft_and_export():
    conn, _ = _factory()
    _supplier(conn)
    _mapping(conn)
    import main

    main.set_conn(conn)
    with TestClient(main.app) as client:
        main.set_conn(conn)
        snapshot = client.get("/api/procurement/snapshot")
        assert snapshot.status_code == 200
        assert snapshot.json()["recommendations"]
        drafted = client.post("/api/procurement/orders/draft-recommendations", json={"actor": "test"})
        assert drafted.status_code == 200
        order = drafted.json()["orders"][0]
        exported = client.get(f"/api/procurement/orders/{order['id']}/export.csv")
        assert exported.status_code == 200
        assert "ROLL-E1" in exported.text
        assert exported.headers["content-type"].startswith("text/csv")
