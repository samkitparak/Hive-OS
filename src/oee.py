"""
OEE calculator.

OEE = Availability × Performance × Quality

Availability comes from machine-state duration. Performance uses calibrated
ideal time for linked completed parts when available. Quality uses recorded
machine quality checks. Missing performance/quality evidence is represented as
provisional instead of inventing a value from availability.

Snapshots are written to oee_snapshots table every time calculate() is called.
"""

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import cycle_time


PLANNED_SHIFT_HOURS = 8  # assume one 8-hour shift; tune per factory
_calculation_lock = threading.RLock()


def _parse_dt(ts: str) -> datetime:
    """Parse an event timestamp into an aware UTC-compatible datetime."""
    text = ts.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass
class OEEResult:
    machine_key:   str
    machine_id:    int
    window_start:  str
    window_end:    str
    planned_time_s: int
    run_time_s:    int
    idle_time_s:   int
    down_time_s:   int
    parts_made:    int
    parts_planned: int
    availability:  float
    performance:   float
    quality:       float
    oee:           float
    performance_source: str
    quality_source: str
    provisional: bool


def _events_in_window(conn: sqlite3.Connection, machine_id: int,
                      start: str, end: str) -> list[dict]:
    rows = conn.execute(
        """SELECT event_type, ts FROM machine_events
           WHERE machine_id=? AND ts >= ? AND ts <= ?
           ORDER BY ts""",
        (machine_id, start, end)
    ).fetchall()
    return [dict(r) for r in rows]


def _compute_time_buckets(events: list[dict], window_start: str,
                          window_end: str) -> tuple[int, int, int]:
    """
    Walk events in order, accumulate run/idle/down seconds.
    Returns (run_time_s, idle_time_s, down_time_s).
    """
    run = idle = down = 0
    if not events:
        return 0, 0, 0

    state   = "off"
    prev_ts = _parse_dt(window_start)
    end_ts  = _parse_dt(window_end)

    STATE_MAP = {
        "power_on":    "run",
        "cycle_start": "run",
        "state_on":    "run",
        "cycle_end":   "idle",
        "idle":        "idle",
        "state_idle":  "idle",
        "power_off":   "off",
        "state_off":   "off",
        "alarm":       "down",
    }

    for ev in events:
        ev_ts    = _parse_dt(ev["ts"])
        duration = max(0, int((ev_ts - prev_ts).total_seconds()))

        if state == "run":
            run  += duration
        elif state == "idle":
            idle += duration
        elif state == "down":
            down += duration
        # "off" contributes to down_time (unplanned stop during shift)
        elif state == "off":
            down += duration

        state   = STATE_MAP.get(ev["event_type"], state)
        prev_ts = ev_ts

    # Remaining time to window end
    remaining = max(0, int((end_ts - prev_ts).total_seconds()))
    if state == "run":
        run  += remaining
    elif state == "idle":
        idle += remaining
    elif state == "down":
        down += remaining
    else:
        down += remaining

    return run, idle, down


def _parts_in_window(conn: sqlite3.Connection, machine_id: int,
                     start: str, end: str) -> int:
    row = conn.execute(
        """SELECT COUNT(DISTINCT part_id) FROM machine_events
           WHERE machine_id=? AND event_type='cycle_end'
           AND part_id IS NOT NULL AND ts >= ? AND ts <= ?""",
        (machine_id, start, end)
    ).fetchone()
    return row[0] if row else 0


def _ideal_run_time(conn: sqlite3.Connection, machine_id: int,
                    machine_key: str, start: str, end: str) -> tuple[float, int]:
    rows = conn.execute(
        """SELECT p.* FROM machine_events me
           JOIN parts p ON p.id=me.part_id
           WHERE me.machine_id=? AND me.event_type='cycle_end'
             AND me.ts>=? AND me.ts<=? AND me.part_id IS NOT NULL""",
        (machine_id, start, end),
    ).fetchall()
    estimates = []
    for row in rows:
        estimate = cycle_time.estimate(
            cycle_time.extract_features(dict(row), machine_key)
        )
        if estimate is not None:
            estimates.append(estimate)
    return sum(estimates), len(estimates)


def _quality_rate(conn: sqlite3.Connection, machine_id: int,
                  start: str, end: str) -> tuple[float, int]:
    row = conn.execute(
        """SELECT COUNT(*) total,
                  SUM(CASE WHEN result='pass' THEN 1 ELSE 0 END) good
           FROM quality_checks WHERE machine_id=? AND ts>=? AND ts<=?""",
        (machine_id, start, end),
    ).fetchone()
    total = row["total"] if row else 0
    good = row["good"] if row and row["good"] is not None else 0
    return (good / total if total else 1.0), total


def _calculate_unlocked(conn: sqlite3.Connection, machine_id: int,
                        window_hours: int = PLANNED_SHIFT_HOURS,
                        now: Optional[datetime] = None) -> OEEResult:
    """Calculate OEE for machine_id over the last window_hours."""

    if now is None:
        now = datetime.now(timezone.utc)

    window_end   = now
    window_start = now - timedelta(hours=window_hours)
    ws = window_start.isoformat()
    we = window_end.isoformat()

    machine = conn.execute(
        "SELECT machine_key FROM machines WHERE id=?", (machine_id,)
    ).fetchone()
    machine_key = machine["machine_key"] if machine else str(machine_id)

    planned_time_s = window_hours * 3600
    events         = _events_in_window(conn, machine_id, ws, we)
    run_s, idle_s, down_s = _compute_time_buckets(events, ws, we)
    parts_made     = _parts_in_window(conn, machine_id, ws, we)

    # Parts planned: count parts in jobs that were active this window
    # (simplified: count distinct parts with cnc files linked to this machine type)
    # Tier 2 will tie this to actual job scheduling
    parts_planned = parts_made  # conservative: don't penalise performance without schedule data

    availability = run_s / planned_time_s if planned_time_s > 0 else 0.0
    ideal_run_s, estimated_parts = _ideal_run_time(
        conn, machine_id, machine_key, ws, we
    )
    if run_s > 0 and estimated_parts == parts_made and estimated_parts > 0:
        performance = min(1.0, ideal_run_s / run_s)
        performance_source = "calibrated_cycle_times"
    else:
        performance = 1.0
        performance_source = "unavailable"

    quality, quality_checks = _quality_rate(conn, machine_id, ws, we)
    quality_source = (
        "quality_checks" if quality_checks >= 10
        else ("insufficient_quality_checks" if quality_checks else "unavailable")
    )
    provisional = performance_source == "unavailable" or quality_source == "unavailable"

    oee = availability * performance * quality

    result = OEEResult(
        machine_key    = machine_key,
        machine_id     = machine_id,
        window_start   = ws,
        window_end     = we,
        planned_time_s = planned_time_s,
        run_time_s     = run_s,
        idle_time_s    = idle_s,
        down_time_s    = down_s,
        parts_made     = parts_made,
        parts_planned  = parts_planned,
        availability   = round(availability, 4),
        performance    = round(performance, 4),
        quality        = round(quality, 4),
        oee            = round(oee, 4),
        performance_source = performance_source,
        quality_source = quality_source,
        provisional    = provisional,
    )

    # Persist snapshot
    conn.execute(
        """INSERT INTO oee_snapshots
           (machine_id, window_start, window_end,
            planned_time_s, run_time_s, idle_time_s, down_time_s,
            parts_planned, parts_made,
            availability, performance, quality, oee)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (machine_id, ws, we, planned_time_s, run_s, idle_s, down_s,
         parts_planned, parts_made,
         result.availability, result.performance, result.quality, result.oee)
    )
    conn.commit()

    return result


def calculate(conn: sqlite3.Connection, machine_id: int,
              window_hours: int = PLANNED_SHIFT_HOURS,
              now: Optional[datetime] = None) -> OEEResult:
    with _calculation_lock:
        return _calculate_unlocked(conn, machine_id, window_hours, now)


def calculate_all(conn: sqlite3.Connection,
                  window_hours: int = PLANNED_SHIFT_HOURS) -> list[OEEResult]:
    """Calculate OEE for every active machine."""
    with _calculation_lock:
        machines = conn.execute(
            "SELECT id FROM machines WHERE active=1"
        ).fetchall()
        now = datetime.now(timezone.utc)
        return [_calculate_unlocked(conn, r["id"], window_hours, now) for r in machines]
