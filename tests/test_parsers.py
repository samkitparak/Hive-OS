"""Tests against the real Amit Agarwal / Ranjeeth sample data."""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from db import init_db
from cv_parser import parse_cv_csv, ingest_cv_csv
from beamsaw_parser import parse_beamsaw_txt

SAMPLE_ROOT = Path("/Users/samkitparak/Downloads/wetransfer_amit-agarwal_2026-05-15_1247")
AA_GBR_CSV  = SAMPLE_ROOT / "Amit Agarwal/GBR/BEAMSAW/{jobname}.csv"
AA_GBR_TXT  = SAMPLE_ROOT / "Amit Agarwal/GBR/BEAMSAW/CncRun86.txt"
RAN_KIT_TXT = SAMPLE_ROOT / "RANJEETH/KITCHEN/BEAMSAW/CncRun63.txt"


# --- CSV parser ---

def test_cv_csv_job_name():
    data = parse_cv_csv(AA_GBR_CSV)
    assert data["job_name"] == "AA-GBR"

def test_cv_csv_room_name():
    data = parse_cv_csv(AA_GBR_CSV)
    assert data["room_name"] == "AA-GBR"

def test_cv_csv_has_parts():
    data = parse_cv_csv(AA_GBR_CSV)
    assert len(data["parts"]) > 50

def test_cv_csv_has_assemblies():
    data = parse_cv_csv(AA_GBR_CSV)
    assert "GBR-WB1-1100" in data["assemblies"]
    assert "GBR-WB2-1100" in data["assemblies"]

def test_cv_csv_part_fields():
    data = parse_cv_csv(AA_GBR_CSV)
    first = data["parts"][0]
    assert first["part_name"] == "Finished Left End"
    assert first["material"] == "HDHMR_18mm_6968 SUD"
    assert first["length_mm"] == 2249.0
    assert first["width_mm"] == 579.0
    assert first["thickness_mm"] == 18.0
    assert first["qty"] == 1

def test_cv_csv_cnc_files():
    data = parse_cv_csv(AA_GBR_CSV)
    # Part 2 (Unfinished Right End) has CNC file r86b0002
    part2 = data["parts"][1]
    assert part2["cnc_file_back"] == "r86b0002"
    assert part2["has_cnc"] == 1

def test_cv_csv_no_cnc_on_door_slab():
    data = parse_cv_csv(AA_GBR_CSV)
    # Door Slab at row index 7 (part_cv_id=8) has no CNC files
    door_slabs = [p for p in data["parts"] if p["part_name"] == "Door Slab" and p["part_cv_id"] == 8]
    assert door_slabs
    assert door_slabs[0]["has_cnc"] == 0

def test_cv_csv_beamsaw_seq_increments():
    data = parse_cv_csv(AA_GBR_CSV)
    seqs = [p["beamsaw_seq"] for p in data["parts"]]
    assert seqs == list(range(1, len(seqs) + 1))


# --- Beam saw TXT parser ---

def test_beamsaw_txt_job_name():
    data = parse_beamsaw_txt(AA_GBR_TXT)
    assert data["job_name"] == "AA-GBR"

def test_beamsaw_txt_date():
    data = parse_beamsaw_txt(AA_GBR_TXT)
    assert data["job_date"] == "2026-03-30"

def test_beamsaw_txt_run_id():
    data = parse_beamsaw_txt(AA_GBR_TXT)
    assert data["run_id"] == "86"

def test_beamsaw_txt_total_parts():
    data = parse_beamsaw_txt(AA_GBR_TXT)
    assert data["total_parts"] == 80

def test_beamsaw_txt_parts_list():
    data = parse_beamsaw_txt(AA_GBR_TXT)
    assert len(data["parts"]) > 0
    first = data["parts"][0]
    assert first["cnc_file"].startswith("r86")
    assert first["face"] in ("B", "F")

def test_beamsaw_txt_ranjeeth_kitchen():
    data = parse_beamsaw_txt(RAN_KIT_TXT)
    assert data["job_name"] == "RANJEETH"
    assert data["job_date"] == "2026-03-16"
    assert data["run_id"] == "63"


# --- DB ingestion ---

@pytest.fixture
def mem_db():
    conn = init_db(Path(":memory:"))
    return conn

def test_ingest_creates_job(mem_db):
    job_id = ingest_cv_csv(AA_GBR_CSV, mem_db, client_name="Amit Agarwal",
                           job_date="2026-03-30", beamsaw_run_id="86")
    assert job_id > 0
    row = mem_db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["job_name"] == "AA-GBR"
    assert row["beamsaw_run_id"] == "86"
    assert row["job_date"] == "2026-03-30"

def test_ingest_idempotent(mem_db):
    id1 = ingest_cv_csv(AA_GBR_CSV, mem_db, client_name="Amit Agarwal")
    id2 = ingest_cv_csv(AA_GBR_CSV, mem_db, client_name="Amit Agarwal")
    assert id1 == id2

def test_ingest_parts_count(mem_db):
    job_id = ingest_cv_csv(AA_GBR_CSV, mem_db, client_name="Amit Agarwal")
    count = mem_db.execute("SELECT COUNT(*) FROM parts WHERE job_id=?", (job_id,)).fetchone()[0]
    assert count > 50

def test_ingest_assemblies(mem_db):
    job_id = ingest_cv_csv(AA_GBR_CSV, mem_db, client_name="Amit Agarwal")
    assemblies = mem_db.execute(
        "SELECT assembly_name FROM assemblies WHERE job_id=?", (job_id,)
    ).fetchall()
    names = {r["assembly_name"] for r in assemblies}
    assert "GBR-WB1-1100" in names

def test_ingest_cnc_parts_flagged(mem_db):
    job_id = ingest_cv_csv(AA_GBR_CSV, mem_db, client_name="Amit Agarwal")
    cnc_count = mem_db.execute(
        "SELECT COUNT(*) FROM parts WHERE job_id=? AND has_cnc=1", (job_id,)
    ).fetchone()[0]
    assert cnc_count > 10

def test_machines_seeded(mem_db):
    count = mem_db.execute("SELECT COUNT(*) FROM machines").fetchone()[0]
    assert count == 15
    beam_saw = mem_db.execute(
        "SELECT * FROM machines WHERE machine_key='gabbiani_pt80'"
    ).fetchone()
    assert beam_saw["type"] == "Beam Saw"
    sergiani = mem_db.execute(
        "SELECT * FROM machines WHERE machine_key='sergiani_gs120'"
    ).fetchone()
    assert sergiani["has_opcua"] == 1
