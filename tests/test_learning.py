import json
from datetime import datetime, timedelta, timezone

from db import init_db
import learning


def _fixture():
    conn = init_db(":memory:", check_same_thread=False)
    conn.execute("INSERT INTO jobs (job_name, total_parts) VALUES ('LEARN', 1)")
    job_id = conn.execute("SELECT id FROM jobs WHERE job_name='LEARN'").fetchone()["id"]
    conn.execute(
        """INSERT INTO parts
           (job_id, part_name, length_mm, width_mm, thickness_mm, has_cnc)
           VALUES (?, 'Panel', 1000, 500, 18, 0)""", (job_id,)
    )
    return conn, conn.execute("SELECT id FROM parts").fetchone()["id"], conn.execute(
        "SELECT id FROM machines WHERE machine_key='gabbiani_pt80'"
    ).fetchone()["id"]


def _event(conn, machine_id, part_id, event_type, ts, payload=None):
    cursor = conn.execute(
        """INSERT INTO machine_events
           (machine_id, part_id, event_type, ts, raw_payload) VALUES (?,?,?,?,?)""",
        (machine_id, part_id, event_type, ts, json.dumps(payload) if payload else None),
    )
    return cursor.lastrowid


def test_cycle_pairing_is_idempotent_and_rejects_part_mismatch():
    conn, part_id, machine_id = _fixture()
    other_part = conn.execute(
        """INSERT INTO parts (job_id, part_name, length_mm, width_mm, thickness_mm)
           SELECT job_id, 'Other', 500, 300, 18 FROM parts WHERE id=?""", (part_id,)
    ).lastrowid
    start = "2026-07-14T08:00:00+00:00"
    _event(conn, machine_id, part_id, "cycle_start", start)
    _event(conn, machine_id, part_id, "cycle_end", "2026-07-14T08:01:00+00:00")
    _event(conn, machine_id, part_id, "cycle_start", "2026-07-14T08:02:00+00:00")
    _event(conn, machine_id, other_part, "cycle_end", "2026-07-14T08:03:00+00:00")
    conn.commit()

    first = learning.refresh_cycle_observations(conn)
    second = learning.refresh_cycle_observations(conn)
    assert first == {"created": 2, "valid": 1, "rejected": 1}
    assert second["created"] == 0
    rows = conn.execute("SELECT * FROM cycle_observations ORDER BY id").fetchall()
    assert rows[0]["duration_s"] == 60
    assert rows[1]["rejection_reason"] == "part_mismatch"


def test_robust_learning_activates_good_model_and_preserves_it_from_bad_candidate():
    conn, part_id, machine_id = _fixture()
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    for index in range(30):
        length = 400 + index * 55
        width = 300 + (index % 7) * 65
        conn.execute("UPDATE parts SET length_mm=?, width_mm=? WHERE id=?", (length, width, part_id))
        part = dict(conn.execute("SELECT * FROM parts WHERE id=?", (part_id,)).fetchone())
        duration = 12 + length * 0.02 + width * 0.01
        if index == 5:
            duration += 500
        end_event_id = _event(
            conn, machine_id, part_id, "cycle_end",
            (base + timedelta(minutes=index)).isoformat(),
        )
        conn.execute(
            """INSERT INTO cycle_observations
               (machine_id, part_id, end_event_id, ended_at, duration_s,
                duration_source, validity, features_json)
               VALUES (?,?,?,?,?,'event_pair','valid',?)""",
            (machine_id, part_id, end_event_id, (base + timedelta(minutes=index)).isoformat(),
             duration, json.dumps(part)),
        )
    conn.commit()
    result = learning.train_machine(conn, machine_id, "gabbiani_pt80")
    assert result["status"] == "active"
    assert result["confidence"] in ("medium", "high")
    active_id = conn.execute(
        "SELECT id FROM cycle_models WHERE machine_id=? AND status='active'", (machine_id,)
    ).fetchone()["id"]

    for index in range(30, 40):
        part = dict(conn.execute("SELECT * FROM parts WHERE id=?", (part_id,)).fetchone())
        end_event_id = _event(
            conn, machine_id, part_id, "cycle_end",
            (base + timedelta(minutes=index)).isoformat(),
        )
        conn.execute(
            """INSERT INTO cycle_observations
               (machine_id, part_id, end_event_id, ended_at, duration_s,
                duration_source, validity, features_json)
               VALUES (?,?,?,?,?,'event_pair','valid',?)""",
            (machine_id, part_id, end_event_id, (base + timedelta(minutes=index)).isoformat(),
             50 if index % 2 else 1500, json.dumps(part)),
        )
    conn.commit()
    candidate = learning.train_machine(conn, machine_id, "gabbiani_pt80")
    assert candidate["status"] == "candidate"
    assert conn.execute(
        "SELECT id FROM cycle_models WHERE machine_id=? AND status='active'", (machine_id,)
    ).fetchone()["id"] == active_id
