"""
Beam saw TXT parser (Gabbiani / SCM CncRun files).

Files are UTF-16 LE with BOM. Header lines start with ^.
Part lines: xcs_filename,part_name(abbrev),W,L,T,material,assembly_id,...,face(B/F),...
"""

import re
import sqlite3
from pathlib import Path
from typing import Optional


_HEADER_RE = re.compile(r"\^\s*(\w+)\s*:\s*(.+)")
_DATE_FORMATS = [
    "%A, %b %d %Y",     # Monday, Mar 30 2026
    "%A, %B %d %Y",     # Monday, March 30 2026
    "%d/%m/%Y",
    "%Y-%m-%d",
]


def _parse_date(raw: str) -> Optional[str]:
    """Return ISO date string or None."""
    from datetime import datetime
    raw = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def _extract_run_id(filename: str) -> Optional[str]:
    """Extract numeric run ID from 'CncRun86.txt' → '86'."""
    m = re.search(r"CncRun(\d+)", filename, re.IGNORECASE)
    return m.group(1) if m else None


def parse_beamsaw_txt(txt_path: Path) -> dict:
    """
    Parse a CncRun TXT file.

    Returns:
    {
        "job_name": str,
        "run_id": str,
        "job_date": str,          # ISO date
        "total_parts": int,
        "source_txt": str,
        "parts": [ {"cnc_file": str, "part_name": str, "assembly_cv_id": int, "face": str} ]
    }
    """
    # UTF-16 LE with BOM
    try:
        text = txt_path.read_text(encoding="utf-16")
    except UnicodeError:
        text = txt_path.read_text(encoding="utf-8-sig", errors="replace")

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    job_name = ""
    job_date_raw = ""
    total_parts = 0
    parts = []

    for line in lines:
        m = _HEADER_RE.match(line)
        if m:
            key, val = m.group(1).lower(), m.group(2).strip()
            if key == "job":
                job_name = val
            elif key == "date":
                job_date_raw = val
            elif key == "parts":
                try:
                    total_parts = int(val)
                except ValueError:
                    pass
            continue

        # Part data line: comma-separated, first field is xcs filename
        fields = [f.strip() for f in line.split(",")]
        if len(fields) < 10:
            continue
        cnc_file = fields[0]
        # Field[1]: "Part Name(abbrev)" — extract part name before parenthesis
        name_raw = fields[1]
        part_name = re.sub(r"\(.*?\)", "", name_raw).strip()
        # Field[5]: assembly_cv_id (integer in TXT)
        try:
            assembly_cv_id = int(fields[6])
        except (ValueError, IndexError):
            assembly_cv_id = None
        # Field[9]: face B or F
        face = fields[9].strip() if len(fields) > 9 else None

        parts.append({
            "cnc_file":      cnc_file,
            "part_name":     part_name,
            "assembly_cv_id": assembly_cv_id,
            "face":          face,
        })

    run_id = _extract_run_id(txt_path.name)

    return {
        "job_name":    job_name,
        "run_id":      run_id,
        "job_date":    _parse_date(job_date_raw),
        "total_parts": total_parts,
        "source_txt":  str(txt_path),
        "parts":       parts,
    }


def update_job_from_beamsaw(txt_path: Path, conn: sqlite3.Connection) -> Optional[str]:
    """
    Parse the TXT and back-fill job_date + beamsaw_run_id on the matching job row.
    Returns the job_name if found/updated, else None.
    """
    data = parse_beamsaw_txt(txt_path)
    if not data["job_name"]:
        return None

    conn.execute(
        """UPDATE jobs SET
               job_date = COALESCE(job_date, ?),
               beamsaw_run_id = COALESCE(beamsaw_run_id, ?),
               source_txt = COALESCE(source_txt, ?)
           WHERE job_name = ?""",
        (data["job_date"], data["run_id"], data["source_txt"], data["job_name"])
    )
    conn.commit()
    return data["job_name"] if conn.total_changes > 0 else None
