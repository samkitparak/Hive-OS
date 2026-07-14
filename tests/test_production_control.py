from datetime import datetime, timedelta, timezone

import pytest

from db import init_db
import production_control


def _factory(qty=1, cnc=False):
    conn = init_db(":memory:")
    conn.execute("INSERT INTO jobs (job_name, total_parts) VALUES ('ORDER-1', ?)", (qty,))
    job_id = conn.execute("SELECT id FROM jobs").fetchone()["id"]
    conn.execute(
        """INSERT INTO parts
           (job_id, part_name, qty, length_mm, width_mm, has_cnc, cnc_file_back)
           VALUES (?, 'Panel', ?, 1000, 500, ?, ?)""",
        (job_id, qty, int(cnc), "r1b0001.xcs" if cnc else None),
    )
    conn.commit()
    production_control.sync_all(conn)
    return conn, job_id, conn.execute("SELECT id FROM parts").fetchone()["id"]


def _due():
    return (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()


def test_order_sync_is_idempotent_and_keeps_job_date_separate():
    conn, _, _ = _factory()
    assert production_control.sync_all(conn)["orders"]["created"] == 0
    orders = production_control.list_orders(conn)
    assert len(orders) == 1
    assert orders[0]["status"] == "draft"
    assert orders[0]["due_at"] is None
    assert orders[0]["route"]["coverage"] == 1


def test_order_requires_timezone_due_date_and_optimistic_version():
    conn, _, _ = _factory()
    order = production_control.list_orders(conn)[0]
    with pytest.raises(ValueError, match="timezone"):
        production_control.update_order(conn, order["id"], {
            "due_at": "2026-07-20T18:00:00", "status": "ready",
            "expected_version": order["version"], "actor": "planner",
        })
    ready = production_control.update_order(conn, order["id"], {
        "due_at": _due(), "priority": 80, "status": "ready",
        "expected_version": order["version"], "actor": "planner",
    })
    assert ready["status"] == "ready"
    assert ready["priority"] == 80
    with pytest.raises(production_control.VersionConflict):
        production_control.update_order(conn, order["id"], {
            "priority": 20, "expected_version": order["version"], "actor": "stale-tab",
        })


def test_illegal_lifecycle_jump_is_rejected_and_release_is_audited():
    conn, _, _ = _factory()
    order = production_control.list_orders(conn)[0]
    with pytest.raises(ValueError, match="Cannot move"):
        production_control.update_order(conn, order["id"], {
            "status": "released", "due_at": _due(), "actor": "planner",
        })
    ready = production_control.update_order(conn, order["id"], {
        "status": "ready", "due_at": _due(), "actor": "planner",
    })
    released = production_control.update_order(conn, order["id"], {
        "status": "released", "actor": "supervisor",
        "expected_version": ready["version"],
    })
    assert released["released_by"] == "supervisor"
    events = conn.execute(
        """SELECT event_type, to_status FROM production_order_events
           WHERE production_order_id=? ORDER BY id""",
        (order["id"],),
    ).fetchall()
    assert [(row["event_type"], row["to_status"]) for row in events][-1] == (
        "status_changed", "released"
    )


def test_route_confirmation_counts_physical_quantity_and_is_idempotent():
    conn, _, part_id = _factory(qty=2)
    machine_id = conn.execute(
        "SELECT id FROM machines WHERE machine_key='gabbiani_pt80'"
    ).fetchone()["id"]
    event_ids = []
    for minute in (1, 2):
        event_ids.append(conn.execute(
            """INSERT INTO machine_events
               (machine_id, part_id, event_type, ts) VALUES (?,?, 'cycle_end', ?)""",
            (machine_id, part_id, f"2026-07-14T08:0{minute}:00+00:00"),
        ).lastrowid)
    conn.commit()
    first = production_control.confirm_route_step(
        conn, part_id, "gabbiani_pt80", "cycle_end", "machine_event",
        event_ids[0], "2026-07-14T08:01:00+00:00", "agent",
    )
    production_control.confirm_route_step(
        conn, part_id, "gabbiani_pt80", "cycle_end", "machine_event",
        event_ids[0], "2026-07-14T08:01:00+00:00", "agent",
    )
    step = conn.execute("SELECT * FROM part_route_steps WHERE part_id=?", (part_id,)).fetchone()
    assert first["event_recorded"] is True
    assert step["confirmed_qty"] == 1
    assert step["status"] == "started"
    production_control.confirm_route_step(
        conn, part_id, "gabbiani_pt80", "cycle_end", "machine_event",
        event_ids[1], "2026-07-14T08:02:00+00:00", "agent",
    )
    step = conn.execute("SELECT * FROM part_route_steps WHERE part_id=?", (part_id,)).fetchone()
    assert step["confirmed_qty"] == 2
    assert step["status"] == "confirmed"


def test_out_of_sequence_machine_evidence_creates_exception():
    conn, _, part_id = _factory(cnc=True)
    cnc_id = conn.execute(
        "SELECT id FROM machines WHERE machine_key='morbidelli_cx100'"
    ).fetchone()["id"]
    event_id = conn.execute(
        """INSERT INTO machine_events
           (machine_id, part_id, event_type, ts) VALUES (?,?,'cycle_start',?)""",
        (cnc_id, part_id, "2026-07-14T08:00:00+00:00"),
    ).lastrowid
    conn.commit()
    result = production_control.confirm_route_step(
        conn, part_id, "morbidelli_cx100", "cycle_start", "machine_event",
        event_id, "2026-07-14T08:00:00+00:00", "agent",
    )
    assert result["exception"] == "out_of_sequence"
    exceptions = production_control.list_exceptions(conn)
    assert exceptions[0]["expected_machine"] == "gabbiani_pt80"
    assert exceptions[0]["observed_machine"] == "morbidelli_cx100"
