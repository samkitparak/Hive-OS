"""
Cabinet Vision CSV parser.

Reads the cut-list CSVs that CV exports to BEAMSAW folders.
File naming convention: {jobname}.csv (literally that string — CV uses it as a template token).
"""

import csv
import re
import sqlite3
from pathlib import Path
from typing import Optional

# Cabinet Vision CSV columns (0-indexed)
_COL_JOB       = 0
_COL_ROOM      = 1
_COL_ASSEMBLY  = 2
_COL_ASSY_ID   = 3
_COL_PART      = 4
_COL_PART_ID   = 5
_COL_MATERIAL  = 6
_COL_LENGTH    = 7
_COL_WIDTH     = 8
_COL_THICK     = 9
_COL_QTY       = 10
_COL_GRAIN     = 11
_COL_EB1       = 12
_COL_EB2       = 13
_COL_EB3       = 14
_COL_EB4       = 15
_COL_CNC_BACK  = 16   # face column (r…b… file)
_COL_CNC_FRONT = 17   # face column (r…f… file)


def _clean(val: str) -> Optional[str]:
    v = val.strip()
    return v if v else None


def _float(val: str) -> Optional[float]:
    v = val.strip()
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _int(val: str) -> Optional[int]:
    v = val.strip()
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _extract_cnc_filename(barcode_field: str) -> Optional[str]:
    """Strip asterisks from barcode-style CNC references like *r86b0002*."""
    v = barcode_field.strip().strip("*")
    return v if v else None


def parse_cv_csv(csv_path: Path) -> dict:
    """
    Parse a Cabinet Vision cut-list CSV.

    Returns a dict:
    {
        "job_name": str,
        "room_name": str | None,
        "source_csv": str,
        "assemblies": { assembly_name: cv_assembly_id },
        "parts": [ {part fields...} ]
    }
    """
    rows = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader, None)  # skip header row
        for row in reader:
            if not row or not any(c.strip() for c in row):
                continue
            rows.append(row)

    if not rows:
        return {}

    job_name = _clean(rows[0][_COL_JOB]) or ""
    room_name = _clean(rows[0][_COL_ROOM])

    assemblies: dict[str, int] = {}
    parts = []
    beamsaw_seq = 0

    for row in rows:
        if len(row) < 16:
            continue

        assy_name = _clean(row[_COL_ASSEMBLY]) or ""
        assy_id   = _int(row[_COL_ASSY_ID]) or 0
        if assy_name and assy_name not in assemblies:
            assemblies[assy_name] = assy_id

        cnc_back  = _extract_cnc_filename(row[_COL_CNC_BACK]  if len(row) > _COL_CNC_BACK  else "")
        cnc_front = _extract_cnc_filename(row[_COL_CNC_FRONT] if len(row) > _COL_CNC_FRONT else "")
        has_cnc   = 1 if (cnc_back or cnc_front) else 0

        beamsaw_seq += 1
        parts.append({
            "assembly_name": assy_name,
            "assembly_cv_id": assy_id,
            "part_cv_id":   _int(row[_COL_PART_ID]),
            "part_name":    _clean(row[_COL_PART]) or "",
            "material":     _clean(row[_COL_MATERIAL]),
            "length_mm":    _float(row[_COL_LENGTH]),
            "width_mm":     _float(row[_COL_WIDTH]),
            "thickness_mm": _float(row[_COL_THICK]),
            "qty":          _int(row[_COL_QTY]) or 1,
            "grain":        _int(row[_COL_GRAIN]),
            "eb1":          _clean(row[_COL_EB1]),
            "eb2":          _clean(row[_COL_EB2]),
            "eb3":          _clean(row[_COL_EB3]),
            "eb4":          _clean(row[_COL_EB4]),
            "cnc_file_back":  cnc_back,
            "cnc_file_front": cnc_front,
            "has_cnc":      has_cnc,
            "beamsaw_seq":  beamsaw_seq,
        })

    return {
        "job_name":    job_name,
        "room_name":   room_name,
        "source_csv":  str(csv_path),
        "assemblies":  assemblies,
        "parts":       parts,
    }


def ingest_cv_csv(csv_path: Path, conn: sqlite3.Connection,
                  client_name: Optional[str] = None,
                  job_date: Optional[str] = None,
                  beamsaw_run_id: Optional[str] = None,
                  source_txt: Optional[str] = None) -> int:
    """
    Parse csv_path and write to DB. Returns the job.id created.
    Idempotent: skips if job_name already exists.
    """
    data = parse_cv_csv(csv_path)
    if not data:
        raise ValueError(f"No data parsed from {csv_path}")

    job_name = data["job_name"]

    # Check existing
    row = conn.execute("SELECT id FROM jobs WHERE job_name = ?", (job_name,)).fetchone()
    if row:
        return row["id"]

    # Upsert client
    if client_name:
        conn.execute(
            "INSERT OR IGNORE INTO clients (name) VALUES (?)", (client_name,)
        )
        client_id = conn.execute(
            "SELECT id FROM clients WHERE name = ?", (client_name,)
        ).fetchone()["id"]
    else:
        client_id = None

    # Insert job
    conn.execute(
        """INSERT INTO jobs
           (job_name, client_id, room_name, beamsaw_run_id, job_date,
            total_parts, source_csv, source_txt)
           VALUES (?,?,?,?,?,?,?,?)""",
        (job_name, client_id, data["room_name"], beamsaw_run_id,
         job_date, len(data["parts"]), data["source_csv"], source_txt)
    )
    job_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Insert assemblies
    assy_id_map: dict[str, int] = {}
    for assy_name, cv_id in data["assemblies"].items():
        conn.execute(
            "INSERT INTO assemblies (job_id, assembly_name, assembly_cv_id) VALUES (?,?,?)",
            (job_id, assy_name, cv_id)
        )
        db_assy_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        assy_id_map[assy_name] = db_assy_id

    # Insert parts
    for p in data["parts"]:
        db_assy_id = assy_id_map.get(p["assembly_name"])
        conn.execute(
            """INSERT INTO parts
               (job_id, assembly_id, part_cv_id, part_name, material,
                length_mm, width_mm, thickness_mm, qty, grain,
                eb1, eb2, eb3, eb4,
                cnc_file_back, cnc_file_front, has_cnc, beamsaw_seq)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (job_id, db_assy_id, p["part_cv_id"], p["part_name"], p["material"],
             p["length_mm"], p["width_mm"], p["thickness_mm"], p["qty"], p["grain"],
             p["eb1"], p["eb2"], p["eb3"], p["eb4"],
             p["cnc_file_back"], p["cnc_file_front"], p["has_cnc"], p["beamsaw_seq"])
        )

    conn.commit()
    return job_id
