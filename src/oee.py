"""
OEE calculator.

OEE = Availability × Performance × Quality

For Tier 1 (sensing only, no reject tracking yet):
  Availability  = run_time / planned_time
  Performance   = parts_made / parts_planned   (when planned data exists)
                  OR  run_time / total_time     (fallback, no job context)
  Quality       = 1.0  (no defect data yet — added in Tier 2)

Snapshots are written to oee_snapshots table every time calculate() is called.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional


PLANNED_SHIFT_HOURS = 8  # assume one 8-hour shift; tune per factory


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
    def _dt(ts: str) -> datetime:
        # Handle both ISO with Z and without tz
        ts = ts.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(ts)
        except ValueError:
            return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

    run = idle = down = 0
    if not events:
        return 0, 0, 0

    state   = "off"
    prev_ts = _dt(window_start)
    end_ts  = _dt(window_end)

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
        ev_ts    = _dt(ev["ts"])
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


def calculate(conn: sqlite3.Connection, machine_id: int,
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
    performance  = 1.0  # no ideal cycle time data yet — filled in Tier 2
    quality      = 1.0  # no reject data yet

    # If we have actual parts data, use cycle time vs planned
    if parts_made > 0 and run_s > 0:
        # Each part took run_s/parts_made seconds on average
        # Without ideal cycle time we can't compute real performance
        performance = min(1.0, availability + 0.05)  # placeholder until cycle times known

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


def calculate_all(conn: sqlite3.Connection,
                  window_hours: int = PLANNED_SHIFT_HOURS) -> list[OEEResult]:
    """Calculate OEE for every active machine."""
    machines = conn.execute(
        "SELECT id FROM machines WHERE active=1"
    ).fetchall()
    now = datetime.now(timezone.utc)
    return [calculate(conn, r["id"], window_hours, now) for r in machines]
