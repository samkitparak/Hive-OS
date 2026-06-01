"""
Batch ingest all Cabinet Vision CSV + beam saw TXT files from a root folder.

Usage:
    python src/ingest.py /path/to/wetransfer_folder
"""

import sys
from pathlib import Path

from db import init_db, DB_PATH
from cv_parser import ingest_cv_csv
from beamsaw_parser import parse_beamsaw_txt, update_job_from_beamsaw


def ingest_folder(root: Path, db_path=DB_PATH):
    conn = init_db(db_path)

    # Walk all BEAMSAW/BEAM SAW directories
    beamsaw_dirs = [p for p in root.rglob("*") if p.is_dir() and "BEAMSAW" in p.name.upper().replace(" ", "")]

    ingested_jobs = 0
    for beamsaw_dir in beamsaw_dirs:
        # Find the TXT first to get job metadata
        txts = list(beamsaw_dir.glob("CncRun*.txt"))
        csvs = list(beamsaw_dir.glob("*.csv"))

        # CV exports cut-lists as "{jobname}.csv" — that IS the real file, not a template
        # No filtering needed; take all CSVs in the folder

        if not txts and not csvs:
            continue

        # Derive client name from top-level folder
        rel_parts = beamsaw_dir.relative_to(root).parts
        client_name = rel_parts[0] if rel_parts else None

        txt_data = parse_beamsaw_txt(txts[0]) if txts else {}
        job_name  = txt_data.get("job_name", "")
        job_date  = txt_data.get("job_date")
        run_id    = txt_data.get("run_id")

        # Match CSV: prefer one in same beamsaw_dir, else check parent
        target_csv = csvs[0] if csvs else None

        # If no CSV in this folder, try PRODUCTION/BEAMSAW sibling
        if target_csv is None:
            production_csvs = list(beamsaw_dir.parent.rglob("*.csv"))
            production_csvs = [c for c in production_csvs if "{" not in c.name]
            target_csv = production_csvs[0] if production_csvs else None

        if target_csv and target_csv.exists():
            job_id = ingest_cv_csv(
                target_csv, conn,
                client_name=client_name,
                job_date=job_date,
                beamsaw_run_id=run_id,
                source_txt=str(txts[0]) if txts else None,
            )
            print(f"  Ingested CSV: {target_csv.name} → job_id={job_id}")
            ingested_jobs += 1
        elif job_name and txts:
            # No CSV — just ingest metadata from TXT
            update_job_from_beamsaw(txts[0], conn)
            print(f"  TXT-only update: {txts[0].name} (job={job_name})")

    print(f"\nDone. {ingested_jobs} job(s) ingested into {db_path}")
    return conn


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    ingest_folder(root)
