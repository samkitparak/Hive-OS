from db import init_db
import routing


def test_route_graph_requires_same_part_adjacent_machine_transition():
    conn = init_db(":memory:")
    conn.execute("INSERT INTO jobs (job_name) VALUES ('ROUTE')")
    job_id = conn.execute("SELECT id FROM jobs").fetchone()["id"]
    conn.execute("INSERT INTO parts (job_id, part_name) VALUES (?, 'Panel')", (job_id,))
    part_id = conn.execute("SELECT id FROM parts").fetchone()["id"]
    saw = conn.execute("SELECT id FROM machines WHERE machine_key='gabbiani_pt80'").fetchone()["id"]
    cnc = conn.execute("SELECT id FROM machines WHERE machine_key='morbidelli_cx100'").fetchone()["id"]
    conn.execute(
        "INSERT INTO machine_events (machine_id, part_id, event_type, ts) VALUES (?,?,?,?)",
        (saw, part_id, "cycle_end", "2026-07-14T08:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO machine_events (machine_id, part_id, event_type, ts) VALUES (?,?,?,?)",
        (cnc, part_id, "cycle_start", "2026-07-14T08:02:00+00:00"),
    )
    conn.commit()

    assert routing.refresh_observations(conn)["created"] == 1
    assert routing.refresh_observations(conn)["created"] == 0
    report = routing.graph(conn)
    assert report["edge_count"] == 1
    assert report["edges"][0]["from_machine"] == "gabbiani_pt80"
    assert report["edges"][0]["to_machine"] == "morbidelli_cx100"
    assert report["edges"][0]["median_transfer_s"] == 120
    assert report["edges"][0]["confidence"] == "low"
