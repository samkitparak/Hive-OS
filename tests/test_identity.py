import pytest

from db import init_db
import identity
import operations
import production_control


def _factory(qty=2):
    conn = init_db(":memory:")
    conn.execute("INSERT INTO jobs (job_name, total_parts) VALUES ('LABEL-1',?)", (qty,))
    job_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    conn.execute(
        """INSERT INTO parts
           (job_id, part_name, part_cv_id, material, length_mm, width_mm,
            thickness_mm, qty) VALUES (?, 'Side Panel', 42, 'MDF 18', 1000, 500, 18, ?)""",
        (job_id, qty),
    )
    conn.commit()
    production_control.sync_all(conn)
    return conn


def test_order_materialization_is_stable_and_quantity_aware():
    conn = _factory(2)
    order_id = production_control.list_orders(conn)[0]["id"]
    first = identity.materialize_order(conn, order_id, "planner")
    second = identity.materialize_order(conn, order_id, "planner")
    units = identity.list_order_units(conn, order_id)
    assert first["created"] == 2
    assert second["created"] == 0
    assert [unit["ordinal"] for unit in units] == [1, 2]
    assert len({unit["unit_key"] for unit in units}) == 2
    assert all(unit["qr_payload"] == f"HIVE:U:{unit['unit_key']}" for unit in units)

    conn.execute("UPDATE parts SET qty=1")
    conn.commit()
    result = identity.materialize_order(conn, order_id, "planner")
    assert result["voided"] == 1
    assert len(identity.list_order_units(conn, order_id)) == 1


def test_cancelling_voids_untouched_units_and_reopening_restores_same_ids():
    conn = _factory(2)
    order_id = production_control.list_orders(conn)[0]["id"]
    identity.materialize_order(conn, order_id, "planner")
    original_keys = [unit["unit_key"] for unit in identity.list_order_units(conn, order_id)]

    conn.execute("UPDATE production_orders SET status='cancelled' WHERE id=?", (order_id,))
    conn.commit()
    cancelled = identity.materialize_order(conn, order_id, "planner")
    assert cancelled["voided"] == 2
    assert identity.list_order_units(conn, order_id) == []
    assert all(
        unit["status"] == "void"
        for unit in identity.list_order_units(conn, order_id, include_void=True)
    )

    conn.execute("UPDATE production_orders SET status='ready' WHERE id=?", (order_id,))
    conn.commit()
    reopened = identity.materialize_order(conn, order_id, "planner")
    assert reopened["created"] == 0
    assert reopened["status_changed"] == 2
    assert [unit["unit_key"] for unit in identity.list_order_units(conn, order_id)] == original_keys


def test_aliases_resolve_and_cannot_cross_units():
    conn = _factory(2)
    order_id = production_control.list_orders(conn)[0]["id"]
    identity.materialize_order(conn, order_id, "planner")
    first, second = identity.list_order_units(conn, order_id)
    resolved = identity.add_alias(
        conn, first["unit_key"], "ottimo", "OTTIMO-0001", "planner", "ottimo"
    )
    assert resolved["unit"]["id"] == first["id"]
    assert identity.resolve_identifier(conn, first["qr_payload"])["unit"]["id"] == first["id"]
    assert identity.resolve_identifier(conn, first["unit_key"].lower())["unit"]["id"] == first["id"]
    with pytest.raises(ValueError, match="another unit"):
        identity.add_alias(conn, second["unit_key"], "ottimo", "OTTIMO-0001", "planner")


def test_print_job_renders_browser_svg_and_native_zpl():
    conn = _factory(2)
    order_id = production_control.list_orders(conn)[0]["id"]
    job = identity.create_print_job(conn, order_id, "planner")
    assert job["unit_count"] == 2
    assert job["status"] == "ready"
    svg = identity.unit_label_svg(conn, job["units"][0]["unit_key"])
    html = identity.print_job_html(conn, job["id"])
    zpl = identity.print_job_zpl(conn, job["id"])
    assert svg.startswith("<svg")
    assert "data:image/svg+xml;base64" in svg
    assert "@page { size: 100mm 50mm" in html
    assert html.count('class="label"') == 2
    assert zpl.count("^XA") == 2
    assert "^BQN,2,5" in zpl
    assert job["units"][0]["qr_payload"] in zpl
    with pytest.raises(ValueError, match="No units"):
        identity.create_print_job(conn, order_id, "planner")
    printed = identity.mark_printed(conn, job["id"], "printer-operator")
    assert printed["status"] == "printed"
    assert all(unit["label_print_count"] == 1 for unit in printed["units"])


def test_hive_scan_resolves_job_part_and_updates_unit_disposition():
    conn = _factory(1)
    order_id = production_control.list_orders(conn)[0]["id"]
    identity.materialize_order(conn, order_id, "planner")
    unit = identity.list_order_units(conn, order_id)[0]
    event = operations.create_barcode_event(conn, {
        "barcode": unit["qr_payload"], "event_type": "packed",
        "station": "packing", "operator": "packer", "source": "scanner",
    })
    assert event["job_id"] == unit["job_id"]
    assert event["part_id"] == unit["part_id"]
    assert event["resolution"]["unit_key"] == unit["unit_key"]
    assert event["execution"]["unit"]["status"] == "packed"
    trace = conn.execute(
        "SELECT object_type, object_key FROM traceability_events"
    ).fetchone()
    assert dict(trace) == {"object_type": "unit", "object_key": unit["unit_key"]}


def test_hive_qc_scan_inherits_unit_job_and_part_context():
    conn = _factory(1)
    order_id = production_control.list_orders(conn)[0]["id"]
    identity.materialize_order(conn, order_id, "planner")
    unit = identity.list_order_units(conn, order_id)[0]
    event = operations.create_barcode_event(conn, {
        "barcode": unit["qr_payload"], "event_type": "qc_fail",
        "station": "quality", "operator": "inspector", "source": "scanner",
    })
    check = conn.execute("SELECT job_id, part_id, result FROM quality_checks").fetchone()
    assert dict(check) == {
        "job_id": unit["job_id"], "part_id": unit["part_id"], "result": "fail",
    }
    assert event["resolution"]["status"] == "applied"
    assert identity.get_unit(conn, unit["unit_key"])["status"] == "non_conforming"


def test_serialized_duplicate_is_suppressed_without_an_approved_schedule():
    conn = _factory(2)
    order_id = production_control.list_orders(conn)[0]["id"]
    identity.materialize_order(conn, order_id, "planner")
    unit = identity.list_order_units(conn, order_id)[0]
    step = conn.execute(
        """SELECT prs.id, m.machine_key FROM part_route_steps prs
           JOIN machines m ON m.id=prs.machine_id
           WHERE prs.part_id=? ORDER BY prs.step_index LIMIT 1""",
        (unit["part_id"],),
    ).fetchone()
    payload = {
        "barcode": unit["qr_payload"], "event_type": "operation_complete",
        "station": step["machine_key"], "operator": "scanner", "source": "scanner",
    }
    first = operations.create_barcode_event(conn, payload)
    duplicate = operations.create_barcode_event(conn, payload)
    confirmed_qty = conn.execute(
        "SELECT confirmed_qty FROM part_route_steps WHERE id=?", (step["id"],)
    ).fetchone()["confirmed_qty"]
    assert first["resolution"]["status"] == "applied"
    assert duplicate["execution"]["duplicate"] is True
    assert duplicate["resolution"]["status"] == "duplicate"
    assert confirmed_qty == 1


def test_conflicting_scan_context_is_retained_but_not_applied():
    conn = _factory(1)
    conn.execute("INSERT INTO jobs (job_name, total_parts) VALUES ('OTHER',1)")
    other_job = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    conn.execute("INSERT INTO parts (job_id, part_name, qty) VALUES (?, 'Other', 1)", (other_job,))
    conn.commit()
    order_id = production_control.list_orders(conn)[0]["id"]
    identity.materialize_order(conn, order_id, "planner")
    unit = identity.list_order_units(conn, order_id)[0]
    event = operations.create_barcode_event(conn, {
        "barcode": unit["qr_payload"], "job_name": "OTHER", "part_name": "Other",
        "event_type": "packed", "station": "packing", "source": "scanner",
    })
    assert event["resolution"]["status"] == "conflict"
    assert event["execution"]["accepted"] is False
    assert identity.get_unit(conn, unit["unit_key"])["status"] == "planned"
    assert conn.execute("SELECT COUNT(*) count FROM barcode_events").fetchone()["count"] == 1
