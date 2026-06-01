"""
Job progress tracker.

Answers: for a given job, how many parts are done vs total, and what's the ETA?

Parts are considered "done" when a cycle_end event exists for that part_id
on any machine. When cycle times are zero (not yet measured), ETA is None.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml


CONFIG_PATH = Path(__file__).parent.parent / "config" / "cycle_times.yaml"

_cycle_times: Optional[dict] = None


def _load_cycle_times(cfg_path: Path = CONFIG_PATH) -> dict:
    global _cycle_times
    if _cycle_times is None:
        with open(cfg_path) as f:
            raw = yaml.safe_load(f)
        _cycle_times = raw.get("cycle_times", {})
    return _cycle_times


def reload_cycle_times(cfg_path: Path = CONFIG_PATH):
    global _cycle_times
    _cycle_times = None
    return _load_cycle_times(cfg_path)


@dataclass
class JobProgress:
    job_name:      str
    total_parts:   int
    parts_done:    int
    parts_left:    int
    pct_done:      float           # 0.0–1.0
    active_machines: list[str]     # machine_keys currently running this job
    eta_seconds:   Optional[int]   # None when cycle times unknown
    on_time:       Optional[str]   # "on_time" | "at_risk" | "late" | None


def get_active_jobs(conn: sqlite3.Connection,
                    cfg_path: Path = CONFIG_PATH) -> list[JobProgress]:
    """
    Returns progress for all jobs that have at least one cycle_start today
    but are not yet fully complete.
    """
    today = datetime.now(timezone.utc).date().isoformat()
    cycle_times = _load_cycle_times(cfg_path)

    # Jobs touched today (have a cycle_start event today)
    active_job_ids = conn.execute(
        """SELECT DISTINCT p.job_id
           FROM machine_events me
           JOIN parts p ON me.part_id = p.id
           WHERE me.event_type = 'cycle_start'
             AND me.ts >= ?""",
        (today,)
    ).fetchall()

    if not active_job_ids:
        return []

    results = []
    for (job_id,) in active_job_ids:
        job = conn.execute(
            "SELECT job_name, total_parts FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        if not job:
            continue

        total = job["total_parts"] or 0

        # Parts done = distinct parts with a cycle_end today
        done_row = conn.execute(
            """SELECT COUNT(DISTINCT me.part_id)
               FROM machine_events me
               JOIN parts p ON me.part_id = p.id
               WHERE p.job_id = ?
                 AND me.event_type = 'cycle_end'
                 AND me.ts >= ?""",
            (job_id, today)
        ).fetchone()
        done = done_row[0] if done_row else 0
        left = max(0, total - done)
        pct  = (done / total) if total > 0 else 0.0

        # Active machines = machines that fired cycle_start for this job in last 10 min
        ten_min_ago = datetime.now(timezone.utc).isoformat()[:16].replace("T", " ")
        active_rows = conn.execute(
            """SELECT DISTINCT m.machine_key
               FROM machine_events me
               JOIN machines m ON me.machine_id = m.id
               JOIN parts p ON me.part_id = p.id
               WHERE p.job_id = ?
                 AND me.event_type = 'cycle_start'
                 AND me.ts >= datetime('now', '-10 minutes')""",
            (job_id,)
        ).fetchall()
        active_machines = [r["machine_key"] for r in active_rows]

        # ETA: use slowest active machine's cycle time as the bottleneck
        eta_s = None
        on_time = None
        relevant_keys = active_machines or list(cycle_times.keys())
        known_times = [cycle_times.get(k, 0) for k in relevant_keys]
        max_ct = max(known_times) if known_times else 0

        if max_ct > 0 and left > 0:
            eta_s = left * max_ct
            # Shift ends at shift_hours remaining from now
            with open(cfg_path) as f:
                cfg = yaml.safe_load(f)
            shift_hours = cfg.get("shift_hours", 9)
            shift_start_str = cfg.get("shift_start", "09:00")
            now = datetime.now(timezone.utc)
            # Rough seconds left in shift (local time estimate)
            import time as _time
            local_hour, local_min = map(int, shift_start_str.split(":"))
            shift_end_secs = shift_hours * 3600
            # seconds elapsed since shift start (approximate)
            elapsed = (now.hour - local_hour) * 3600 + now.minute * 60
            secs_left_in_shift = max(0, shift_end_secs - elapsed)

            if eta_s <= secs_left_in_shift * 0.85:
                on_time = "on_time"
            elif eta_s <= secs_left_in_shift:
                on_time = "at_risk"
            else:
                on_time = "late"

        results.append(JobProgress(
            job_name        = job["job_name"],
            total_parts     = total,
            parts_done      = done,
            parts_left      = left,
            pct_done        = round(pct, 4),
            active_machines = active_machines,
            eta_seconds     = eta_s,
            on_time         = on_time,
        ))

    return results


def get_job_progress(conn: sqlite3.Connection, job_name: str,
                     cfg_path: Path = CONFIG_PATH) -> Optional[JobProgress]:
    job = conn.execute(
        "SELECT id, job_name, total_parts FROM jobs WHERE job_name=?", (job_name,)
    ).fetchone()
    if not job:
        return None

    today = datetime.now(timezone.utc).date().isoformat()
    cycle_times = _load_cycle_times(cfg_path)
    total = job["total_parts"] or 0

    done_row = conn.execute(
        """SELECT COUNT(DISTINCT me.part_id)
           FROM machine_events me
           JOIN parts p ON me.part_id = p.id
           WHERE p.job_id = ? AND me.event_type = 'cycle_end'""",
        (job["id"],)
    ).fetchone()
    done = done_row[0] if done_row else 0
    left = max(0, total - done)
    pct  = (done / total) if total > 0 else 0.0

    active_rows = conn.execute(
        """SELECT DISTINCT m.machine_key
           FROM machine_events me
           JOIN machines m ON me.machine_id = m.id
           JOIN parts p ON me.part_id = p.id
           WHERE p.job_id = ?
             AND me.event_type = 'cycle_start'
             AND me.ts >= datetime('now', '-10 minutes')""",
        (job["id"],)
    ).fetchall()
    active_machines = [r["machine_key"] for r in active_rows]

    eta_s = None
    on_time = None
    known_times = [cycle_times.get(k, 0) for k in (active_machines or list(cycle_times.keys()))]
    max_ct = max(known_times) if known_times else 0

    if max_ct > 0 and left > 0:
        eta_s = left * max_ct
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)
        shift_hours = cfg.get("shift_hours", 9)
        shift_start_str = cfg.get("shift_start", "09:00")
        now = datetime.now(timezone.utc)
        local_hour, local_min = map(int, shift_start_str.split(":"))
        elapsed = (now.hour - local_hour) * 3600 + now.minute * 60
        secs_left_in_shift = max(0, shift_hours * 3600 - elapsed)

        if eta_s <= secs_left_in_shift * 0.85:
            on_time = "on_time"
        elif eta_s <= secs_left_in_shift:
            on_time = "at_risk"
        else:
            on_time = "late"

    return JobProgress(
        job_name        = job["job_name"],
        total_parts     = total,
        parts_done      = done,
        parts_left      = left,
        pct_done        = round(pct, 4),
        active_machines = active_machines,
        eta_seconds     = eta_s,
        on_time         = on_time,
    )
