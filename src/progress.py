"""
Job progress tracker.

Answers: for a given job, how many parts are done vs total, and what's the ETA?

Parts are considered "done" when a cycle_end event exists for that part_id
on any machine. When cycle times are zero (not yet measured), ETA is None.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yaml
import cycle_time


CONFIG_PATH = Path(__file__).parent.parent / "config" / "cycle_times.yaml"

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
    due_at:         Optional[str] = None


def _eta_for_job(conn: sqlite3.Connection, job_name: str, total: int,
                 left: int, cfg_path: Path) -> Optional[int]:
    if total <= 0 or left <= 0:
        return None
    estimate = cycle_time.estimate_job(conn, job_name, cfg_path)
    critical_path_s = estimate.get("critical_path_s")
    if not critical_path_s:
        return None
    return round(critical_path_s * (left / total))


def _on_time_status(eta_s: Optional[int], cfg_path: Path,
                    due_at: Optional[str] = None,
                    controlled: bool = False) -> Optional[str]:
    if eta_s is None:
        return None
    if due_at:
        due = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        finish = datetime.now(timezone.utc) + timedelta(seconds=eta_s)
        if finish > due.astimezone(timezone.utc):
            return "late"
        if finish + timedelta(hours=1) > due.astimezone(timezone.utc):
            return "at_risk"
        return "on_time"
    if controlled:
        return None
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)
    shift_hours = cfg.get("shift_hours", 9)
    shift_start_str = cfg.get("shift_start", "09:00")
    now = datetime.now().astimezone()
    local_hour, local_min = map(int, shift_start_str.split(":"))
    elapsed = ((now.hour - local_hour) * 3600
               + (now.minute - local_min) * 60
               + now.second)
    secs_left_in_shift = max(0, shift_hours * 3600 - elapsed)

    if eta_s <= secs_left_in_shift * 0.85:
        return "on_time"
    if eta_s <= secs_left_in_shift:
        return "at_risk"
    return "late"


def get_active_jobs(conn: sqlite3.Connection,
                    cfg_path: Path = CONFIG_PATH) -> list[JobProgress]:
    """
    Returns progress for all jobs that have at least one cycle_start today
    but are not yet fully complete.
    """
    today = datetime.now(timezone.utc).date().isoformat()
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
            """SELECT j.job_name, j.total_parts, po.due_at,
                      CASE WHEN po.id IS NULL THEN 0 ELSE 1 END controlled
               FROM jobs j LEFT JOIN production_orders po ON po.job_id=j.id
               WHERE j.id=?""", (job_id,)
        ).fetchone()
        if not job:
            continue

        total = job["total_parts"] or 0

        # Parts done = distinct parts with a cycle_end today
        done_row = conn.execute(
            """SELECT COUNT(*)
               FROM machine_events me
               JOIN parts p ON me.part_id = p.id
               WHERE p.job_id = ?
                 AND me.event_type = 'cycle_end'
                 AND me.ts >= ?""",
            (job_id, today)
        ).fetchone()
        done = min(total, done_row[0] if done_row else 0)
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

        eta_s = _eta_for_job(conn, job["job_name"], total, left, cfg_path)
        on_time = _on_time_status(eta_s, cfg_path, job["due_at"], bool(job["controlled"]))

        results.append(JobProgress(
            job_name        = job["job_name"],
            total_parts     = total,
            parts_done      = done,
            parts_left      = left,
            pct_done        = round(pct, 4),
            active_machines = active_machines,
            eta_seconds     = eta_s,
            on_time         = on_time,
            due_at          = job["due_at"],
        ))

    return results


def get_job_progress(conn: sqlite3.Connection, job_name: str,
                     cfg_path: Path = CONFIG_PATH) -> Optional[JobProgress]:
    job = conn.execute(
        """SELECT j.id, j.job_name, j.total_parts, po.due_at,
                  CASE WHEN po.id IS NULL THEN 0 ELSE 1 END controlled
           FROM jobs j LEFT JOIN production_orders po ON po.job_id=j.id
           WHERE j.job_name=?""", (job_name,)
    ).fetchone()
    if not job:
        return None

    total = job["total_parts"] or 0

    done_row = conn.execute(
        """SELECT COUNT(*)
           FROM machine_events me
           JOIN parts p ON me.part_id = p.id
           WHERE p.job_id = ? AND me.event_type = 'cycle_end'""",
        (job["id"],)
    ).fetchone()
    done = min(total, done_row[0] if done_row else 0)
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

    eta_s = _eta_for_job(conn, job["job_name"], total, left, cfg_path)
    on_time = _on_time_status(eta_s, cfg_path, job["due_at"], bool(job["controlled"]))

    return JobProgress(
        job_name        = job["job_name"],
        total_parts     = total,
        parts_done      = done,
        parts_left      = left,
        pct_done        = round(pct, 4),
        active_machines = active_machines,
        eta_seconds     = eta_s,
        on_time         = on_time,
        due_at          = job["due_at"],
    )
