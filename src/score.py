"""
Daily score and streak calculator.

Score (0–100) = weighted average of:
  - OEE component   (60 pts max): today's average OEE across production machines
  - On-time rate    (40 pts max): jobs completed on time / total jobs completed today

Streak = consecutive calendar days the score was >= the rolling 7-day average.

Historical scores are stored in the oee_snapshots table (we reuse it, filtering
by window_start = today's date). If no data exists yet, returns zeroed result.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import oee as oee_module
import yaml


# Utility machines excluded from production score
UTILITY_KEYS = {"elgi_1", "elgi_2", "aarco_1", "aarco_2"}
CYCLE_CONFIG_PATH = Path(__file__).parent.parent / "config" / "cycle_times.yaml"


@dataclass
class DailyScore:
    date:          str          # ISO date
    score:         float        # 0–100
    oee_avg:       float        # average OEE across production machines
    on_time_rate:  float        # fraction of jobs finished on time today
    jobs_done:     int          # jobs completed today
    jobs_on_time:  int          # of those, how many were on time
    streak:        int          # consecutive days >= rolling avg
    rolling_avg:   float        # 7-day rolling score average
    vs_avg:        float        # today_score - rolling_avg (positive = beating it)
    trend:         str          # "up" | "down" | "same"


def _today_oee(conn: sqlite3.Connection) -> float:
    """Average OEE for production machines over today's shift window."""
    results = oee_module.calculate_all(conn, window_hours=9)
    prod = [r for r in results if r.machine_key not in UTILITY_KEYS]
    if not prod:
        return 0.0
    return sum(r.oee for r in prod) / len(prod)


def _shift_context(cfg_path: Path, now: datetime) -> tuple[datetime, datetime, datetime]:
    cfg = yaml.safe_load(cfg_path.read_text()) or {}
    local_tz = ZoneInfo(cfg.get("timezone", "UTC"))
    local_now = now.astimezone(local_tz)
    shift_start = time.fromisoformat(str(cfg.get("shift_start", "09:00")))
    start = datetime.combine(local_now.date(), shift_start, tzinfo=local_tz)
    if cfg.get("shift_end"):
        end = datetime.combine(
            local_now.date(), time.fromisoformat(str(cfg["shift_end"])), tzinfo=local_tz
        )
    else:
        end = start + timedelta(hours=float(cfg.get("shift_hours", 9)))
    return local_now, start, end


def _jobs_completed_today(conn: sqlite3.Connection,
                          now: Optional[datetime] = None,
                          cfg_path: Path = CYCLE_CONFIG_PATH) -> tuple[int, int]:
    """
    Returns (total_completed, on_time_count).

    A job is "completed today" if all its parts have a cycle_end event
    and the last cycle_end was today.

    On-time = job finished before the configured local shift end.
    """
    now = now or datetime.now(timezone.utc)
    local_now, _shift_start, shift_end = _shift_context(cfg_path, now)
    local_day_start = datetime.combine(local_now.date(), time.min, tzinfo=local_now.tzinfo)
    local_day_end = local_day_start + timedelta(days=1)

    # Jobs where total parts == parts with cycle_end events
    rows = conn.execute(
        """SELECT j.job_name, j.total_parts,
                  COUNT(DISTINCT me.part_id) as done_count,
                  MAX(me.ts) as last_event_ts
           FROM jobs j
           JOIN parts p ON p.job_id = j.id
           JOIN machine_events me ON me.part_id = p.id
           WHERE me.event_type = 'cycle_end'
             AND me.ts >= ? AND me.ts < ?
           GROUP BY j.id""",
        (local_day_start.astimezone(timezone.utc).isoformat(),
         local_day_end.astimezone(timezone.utc).isoformat())
    ).fetchall()

    total_done = 0
    on_time    = 0
    for row in rows:
        if row["done_count"] >= (row["total_parts"] or 0) > 0:
            total_done += 1
            try:
                last_ts = datetime.fromisoformat(row["last_event_ts"].replace("Z", "+00:00"))
                if last_ts.tzinfo is None:
                    last_ts = last_ts.replace(tzinfo=timezone.utc)
                if last_ts.astimezone(shift_end.tzinfo) <= shift_end:
                    on_time += 1
            except (TypeError, ValueError):
                pass

    return total_done, on_time


def _past_scores(conn: sqlite3.Connection, days: int = 7) -> list[float]:
    """
    Pull the last `days` daily score values from oee_snapshots.
    We use the average daily OEE as a proxy (score storage comes later).
    Returns a list of floats (most recent last).
    """
    scores = []
    now = datetime.now(timezone.utc)
    for d in range(days, 0, -1):
        day_start = (now - timedelta(days=d)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        day_end = day_start + timedelta(days=1)
        rows = conn.execute(
            """SELECT AVG(oee) FROM oee_snapshots
               WHERE window_start >= ? AND window_start < ?
                 AND machine_id NOT IN (
                     SELECT id FROM machines WHERE machine_key IN ('elgi_1','elgi_2','aarco_1','aarco_2')
                 )""",
            (day_start.isoformat(), day_end.isoformat())
        ).fetchone()
        val = rows[0] if rows and rows[0] is not None else None
        if val is not None:
            scores.append(round(val * 100, 1))
    return scores


def _streak(conn: sqlite3.Connection, today_score: float, rolling_avg: float) -> int:
    """Count consecutive days (including today) where score >= rolling_avg."""
    if rolling_avg == 0:
        return 1 if today_score > 0 else 0

    streak = 1 if today_score >= rolling_avg else 0
    if streak == 0:
        return 0

    now = datetime.now(timezone.utc)
    for d in range(1, 30):
        day_start = (now - timedelta(days=d)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        day_end = day_start + timedelta(days=1)
        row = conn.execute(
            """SELECT AVG(oee) FROM oee_snapshots
               WHERE window_start >= ? AND window_start < ?
                 AND machine_id NOT IN (
                     SELECT id FROM machines WHERE machine_key IN ('elgi_1','elgi_2','aarco_1','aarco_2')
                 )""",
            (day_start.isoformat(), day_end.isoformat())
        ).fetchone()
        val = row[0] if row and row[0] is not None else None
        if val is None:
            break
        day_score = val * 100
        if day_score >= rolling_avg:
            streak += 1
        else:
            break

    return streak


def get_daily_score(conn: sqlite3.Connection) -> DailyScore:
    today     = datetime.now(timezone.utc).date().isoformat()
    oee_avg   = _today_oee(conn)
    jobs_done, jobs_on_time = _jobs_completed_today(conn)

    on_time_rate = (jobs_on_time / jobs_done) if jobs_done > 0 else 0.0
    score = round(oee_avg * 60 + on_time_rate * 40, 1)

    past = _past_scores(conn, days=7)
    rolling_avg = round(sum(past) / len(past), 1) if past else 0.0

    streak  = _streak(conn, score, rolling_avg)
    vs_avg  = round(score - rolling_avg, 1)
    trend   = "up" if vs_avg > 1 else ("down" if vs_avg < -1 else "same")

    return DailyScore(
        date         = today,
        score        = score,
        oee_avg      = round(oee_avg, 4),
        on_time_rate = round(on_time_rate, 4),
        jobs_done    = jobs_done,
        jobs_on_time = jobs_on_time,
        streak       = streak,
        rolling_avg  = rolling_avg,
        vs_avg       = vs_avg,
        trend        = trend,
    )
