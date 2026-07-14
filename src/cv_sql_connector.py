"""Normalized Cabinet Vision job/part upsert boundary.

The commissioned SQL connector validates and maps vendor rows before calling
this module. The legacy placeholder endpoint also uses the same normalized
shape for demo compatibility.
"""

from datetime import datetime, timezone


NORMALIZED_FIELDS = {
    "job_name", "client_name", "room_name", "job_date", "part_name",
    "material", "length_mm", "width_mm", "thickness_mm", "qty",
    "cnc_file_back", "cnc_file_front", "has_cnc",
}


def normalize_placeholder_rows(rows: list[dict]) -> list[dict]:
    normalized = []
    for row in rows:
        item = {field: row.get(field) for field in NORMALIZED_FIELDS}
        item["job_name"] = item["job_name"] or row.get("JobName")
        item["client_name"] = item["client_name"] or row.get("ClientName")
        item["part_name"] = item["part_name"] or row.get("PartName")
        item["qty"] = item["qty"] or 1
        item["has_cnc"] = int(bool(item.get("has_cnc") or item.get("cnc_file_back") or item.get("cnc_file_front")))
        normalized.append(item)
    return normalized


def upsert_normalized_rows(conn, rows: list[dict]) -> dict:
    imported_jobs = set()
    imported_parts = 0
    for row in normalize_placeholder_rows(rows):
        if not row.get("job_name") or not row.get("part_name"):
            continue

        client_id = None
        if row.get("client_name"):
            conn.execute("INSERT OR IGNORE INTO clients (name) VALUES (?)", (row["client_name"],))
            client_id = conn.execute(
                "SELECT id FROM clients WHERE name=?", (row["client_name"],)
            ).fetchone()["id"]

        conn.execute(
            """INSERT OR IGNORE INTO jobs
               (job_name, client_id, room_name, job_date, total_parts, imported_at)
               VALUES (?,?,?,?,0,?)""",
            (row["job_name"], client_id, row.get("room_name"),
             row.get("job_date"), datetime.now(timezone.utc).isoformat()),
        )
        job = conn.execute(
            "SELECT id,total_parts FROM jobs WHERE job_name=?", (row["job_name"],)
        ).fetchone()

        existing = conn.execute(
            """SELECT id FROM parts
               WHERE job_id=? AND part_name=? AND IFNULL(cnc_file_back,'')=IFNULL(?, '')
               LIMIT 1""",
            (job["id"], row["part_name"], row.get("cnc_file_back")),
        ).fetchone()
        if existing:
            continue

        conn.execute(
            """INSERT INTO parts
               (job_id, part_name, material, length_mm, width_mm, thickness_mm,
                qty, cnc_file_back, cnc_file_front, has_cnc)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (job["id"], row["part_name"], row.get("material"),
             row.get("length_mm"), row.get("width_mm"), row.get("thickness_mm"),
             row.get("qty") or 1, row.get("cnc_file_back"), row.get("cnc_file_front"),
             row.get("has_cnc") or 0),
        )
        imported_jobs.add(row["job_name"])
        imported_parts += 1

    for job_name in imported_jobs:
        job_id = conn.execute("SELECT id FROM jobs WHERE job_name=?", (job_name,)).fetchone()["id"]
        total = conn.execute("SELECT COUNT(*) FROM parts WHERE job_id=?", (job_id,)).fetchone()[0]
        conn.execute("UPDATE jobs SET total_parts=? WHERE id=?", (total, job_id))

    conn.execute(
        """INSERT INTO connector_sync_state
           (connector_key, status, last_sync_at, last_cursor, updated_at)
           VALUES ('cabinet_vision_sql','placeholder_synced',?,?,?)
           ON CONFLICT(connector_key) DO UPDATE SET
             status=excluded.status,
             last_sync_at=excluded.last_sync_at,
             last_cursor=excluded.last_cursor,
             updated_at=excluded.updated_at""",
        (datetime.now(timezone.utc).isoformat(), f"{imported_parts} parts",
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return {"jobs_imported": len(imported_jobs), "parts_imported": imported_parts}
