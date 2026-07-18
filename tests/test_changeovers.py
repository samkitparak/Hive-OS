"""Sequence-dependent setup standards, evidence learning, and readiness gates."""

from datetime import datetime, timedelta, timezone

from db import init_db
import changeovers


def _parts():
    return [{
        "operations": [
            {"machine_key": "gabbiani_pt80", "setup_key": "MATERIAL|A"},
            {"machine_key": "gabbiani_pt80", "setup_key": "MATERIAL|B"},
        ],
    }]


def test_setup_families_use_only_available_process_fields():
    saw = changeovers.setup_family("gabbiani_pt80", {"material": " hdhmr 18mm "})
    assert saw["key"] == "MATERIAL|HDHMR 18MM"
    edge = changeovers.setup_family("stefani_kd", {
        "eb1": "White ABS", "eb2": "white abs", "eb3": "Black ABS", "eb4": None,
    })
    assert edge["key"] == "EDGE|BLACK ABS + WHITE ABS"
    cnc = changeovers.setup_family("morbidelli_cx100", {
        "cnc_file_back": "r86bg007.xcs", "cnc_file_front": "r86f0007.xcs", "has_cnc": 1,
    })
    assert cnc["key"] == "CNC|FACES=2|GROOVE=1"
    assert changeovers.setup_family("superfici", {"material": "A"}) is None


def test_verified_fallback_is_required_for_unseen_scope_transitions():
    conn = init_db(":memory:")
    changeovers.sync_defaults(conn)
    before = changeovers.readiness_for_parts(conn, _parts())
    assert before["applicable"] is True
    assert before["ready"] is False
    standard = changeovers.snapshot(conn)["machines"]
    saw = next(item for item in standard if item["machine_key"] == "gabbiani_pt80")
    updated = changeovers.update_standard(conn, "gabbiani_pt80", {
        "default_setup_s": 420, "verified": True,
        "expected_version": saw["version"], "actor": "planner",
        "notes": "Conservative observed shop standard",
    })
    assert updated["verified"] is True
    assert changeovers.readiness_for_parts(conn, _parts())["ready"] is True
    estimate = changeovers.estimate(conn, "gabbiani_pt80", "MATERIAL|A", "MATERIAL|B")
    assert estimate["seconds"] == 420
    assert estimate["source"] == "verified_standard"
    assert estimate["production_eligible"] is True


def test_directional_model_promotes_at_evidence_gate_and_exclusion_retracts_it():
    conn = init_db(":memory:")
    changeovers.sync_defaults(conn)
    start = datetime(2026, 7, 1, 9, tzinfo=timezone.utc)
    observation_ids = []
    for index, duration in enumerate((100, 102, 104, 101, 103)):
        result = changeovers.record_observation(conn, {
            "machine_key": "gabbiani_pt80",
            "from_setup_key": "MATERIAL|A", "to_setup_key": "MATERIAL|B",
            "duration_s": duration,
            "observed_at": (start + timedelta(days=index % 2, minutes=index)).isoformat(),
            "source": "manual_time_study", "quality_confirmed": True,
            "actor": "industrial-engineer",
        })
        observation_ids.append(result["observation_id"])
    estimate = changeovers.estimate(conn, "gabbiani_pt80", "MATERIAL|A", "MATERIAL|B")
    assert estimate["source"] == "learned_p90"
    assert estimate["confidence"] == "medium"
    assert estimate["sample_count"] == 5
    reverse = changeovers.estimate(conn, "gabbiani_pt80", "MATERIAL|B", "MATERIAL|A")
    assert reverse["source"] == "engineering_assumption"

    changeovers.exclude_observation(
        conn, observation_ids[-1], "Stopwatch was stopped late", "industrial-engineer",
    )
    retracted = changeovers.estimate(conn, "gabbiani_pt80", "MATERIAL|A", "MATERIAL|B")
    assert retracted["source"] == "engineering_assumption"
    assert conn.execute(
        "SELECT COUNT(*) FROM changeover_models WHERE status='active'"
    ).fetchone()[0] == 0


def test_explicit_setup_downtime_derives_repeat_safe_observation():
    conn = init_db(":memory:")
    machine_id = conn.execute(
        "SELECT id FROM machines WHERE machine_key='gabbiani_pt80'"
    ).fetchone()["id"]
    part_ids = []
    for index, material in enumerate(("A", "B"), start=1):
        conn.execute("INSERT INTO jobs (job_name,total_parts) VALUES (?,1)", (f"CO-{index}",))
        job_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
        cursor = conn.execute(
            """INSERT INTO parts (job_id,part_name,material,length_mm,width_mm)
               VALUES (?,'Panel',?,1000,500)""", (job_id, material),
        )
        part_ids.append(cursor.lastrowid)
    reason_id = conn.execute(
        "SELECT id FROM downtime_reasons WHERE code='setup'"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO machine_events (machine_id,event_type,part_id,ts) VALUES (?,'cycle_end',?,?)",
        (machine_id, part_ids[0], "2026-07-01T09:00:00+00:00"),
    )
    conn.execute(
        """INSERT INTO downtime_events
           (machine_id,reason_id,status,started_at,ended_at)
           VALUES (?,?,'closed',?,?)""",
        (machine_id, reason_id, "2026-07-01T09:01:00+00:00", "2026-07-01T09:06:00+00:00"),
    )
    conn.execute(
        "INSERT INTO machine_events (machine_id,event_type,part_id,ts) VALUES (?,'cycle_start',?,?)",
        (machine_id, part_ids[1], "2026-07-01T09:07:00+00:00"),
    )
    conn.commit()
    first = changeovers.sync_downtime_observations(conn)
    second = changeovers.sync_downtime_observations(conn)
    assert first["accepted"] == 1
    assert second["duplicates"] == 1
    row = conn.execute("SELECT * FROM changeover_observations").fetchone()
    assert row["duration_s"] == 300
    assert row["from_setup_key"] == "MATERIAL|A"
    assert row["to_setup_key"] == "MATERIAL|B"
    assert row["quality_confirmed"] == 0


def test_read_paths_do_not_open_a_write_transaction():
    conn = init_db(":memory:")
    changeovers.sync_defaults(conn)
    assert conn.in_transaction is False
    changeovers.snapshot(conn)
    changeovers.readiness_for_parts(conn, _parts())
    changeovers.estimate(conn, "gabbiani_pt80", "MATERIAL|A", "MATERIAL|B")
    assert conn.in_transaction is False
