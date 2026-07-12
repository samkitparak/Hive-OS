"""
Current factory constraint detector.

Scores production machines over a recent window using:
  - utilisation: share of observed time spent running
  - queue depth: machine-specific parts remaining in recently active jobs
  - downstream starvation: idle share of the next process machines
  - alarm pressure: alarm frequency in the window

The detector reports confidence separately because queue and starvation signals
only become trustworthy once real machine events are flowing consistently.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import oee


PRODUCTION_FLOW = [
    "gabbiani_pt80",
    "nova_si400",
    "morbidelli_cx100",
    "morbidelli_n100",
    "stefani_kd",
    "sergiani_gs120",
    "varie_osama",
    "dmc60_rcs135",
    "dmc90_xrt135",
    "superfici",
    "action_e",
]

DOWNSTREAM = {
    "gabbiani_pt80": ["morbidelli_cx100", "morbidelli_n100", "stefani_kd"],
    "nova_si400": ["morbidelli_cx100", "morbidelli_n100", "stefani_kd"],
    "morbidelli_cx100": ["stefani_kd"],
    "morbidelli_n100": ["stefani_kd"],
    "stefani_kd": ["sergiani_gs120", "varie_osama", "dmc60_rcs135"],
    "sergiani_gs120": ["dmc60_rcs135", "dmc90_xrt135"],
    "varie_osama": ["dmc60_rcs135", "dmc90_xrt135"],
    "dmc60_rcs135": ["dmc90_xrt135", "superfici"],
    "dmc90_xrt135": ["superfici"],
    "superfici": ["action_e"],
    "action_e": [],
}


@dataclass
class MachineConstraint:
    machine_key: str
    machine_name: str
    score: float
    utilisation: float
    queue_depth: int
    downstream_starvation: float
    alarms: int
    event_count: int
    confidence: str
    recommendation: str


@dataclass
class BottleneckReport:
    generated_at: str
    window_hours: int
    current: Optional[MachineConstraint]
    machines: list[MachineConstraint] = field(default_factory=list)


def _active_job_ids(conn: sqlite3.Connection, start: str, end: str) -> list[int]:
    rows = conn.execute(
        """SELECT DISTINCT p.job_id
           FROM machine_events me
           JOIN parts p ON p.id=me.part_id
           WHERE me.ts >= ? AND me.ts <= ?""",
        (start, end),
    ).fetchall()
    return [row["job_id"] for row in rows]


def _planned_parts(conn: sqlite3.Connection, job_ids: list[int],
                   machine_key: str) -> int:
    if not job_ids:
        return 0
    placeholders = ",".join("?" * len(job_ids))
    condition = ""
    if machine_key in ("morbidelli_cx100", "morbidelli_n100"):
        condition = "AND p.has_cnc=1"
    elif machine_key == "stefani_kd":
        condition = "AND (p.eb1 IS NOT NULL OR p.eb2 IS NOT NULL OR p.eb3 IS NOT NULL OR p.eb4 IS NOT NULL)"
    row = conn.execute(
        f"""SELECT COUNT(*) FROM parts p
            WHERE p.job_id IN ({placeholders}) {condition}""",
        job_ids,
    ).fetchone()
    return row[0] if row else 0


def _queue_depth(conn: sqlite3.Connection, job_ids: list[int],
                 machine_id: int, machine_key: str) -> int:
    planned = _planned_parts(conn, job_ids, machine_key)
    if planned == 0:
        return 0
    placeholders = ",".join("?" * len(job_ids))
    row = conn.execute(
        f"""SELECT COUNT(DISTINCT me.part_id)
            FROM machine_events me
            JOIN parts p ON p.id=me.part_id
            WHERE me.machine_id=? AND me.event_type='cycle_end'
              AND p.job_id IN ({placeholders})""",
        [machine_id, *job_ids],
    ).fetchone()
    completed = row[0] if row else 0
    return max(0, planned - completed)


def _recommendation(machine: dict) -> str:
    if machine["alarms"] > 0:
        return "Resolve recent alarms before feeding more work."
    if machine["queue_depth"] > 0 and machine["utilisation"] >= 0.75:
        return "Protect uptime and prioritise this machine's waiting queue."
    if machine["downstream_starvation"] >= 0.5:
        return "Keep this machine fed; downstream processes are frequently idle."
    if machine["queue_depth"] > 0:
        return "Review staffing and setup time to clear the waiting queue."
    return "Collect more live events before taking scheduling action."


def detect(conn: sqlite3.Connection, window_hours: int = 8,
           now: Optional[datetime] = None) -> BottleneckReport:
    now = now or datetime.now(timezone.utc)
    start = (now - timedelta(hours=window_hours)).isoformat()
    end = now.isoformat()
    job_ids = _active_job_ids(conn, start, end)

    rows = conn.execute(
        """SELECT id, machine_key, name FROM machines
           WHERE active=1 AND machine_key IN ({})
           ORDER BY id""".format(",".join("?" * len(PRODUCTION_FLOW))),
        PRODUCTION_FLOW,
    ).fetchall()

    raw = []
    for row in rows:
        events = oee._events_in_window(conn, row["id"], start, end)
        run_s, idle_s, down_s = oee._compute_time_buckets(events, start, end)
        observed_s = run_s + idle_s + down_s
        utilisation = run_s / observed_s if observed_s else 0.0
        alarms = sum(1 for event in events if event["event_type"] == "alarm")
        raw.append({
            "machine_id": row["id"],
            "machine_key": row["machine_key"],
            "machine_name": row["name"],
            "utilisation": utilisation,
            "idle_fraction": idle_s / observed_s if observed_s else 0.0,
            "queue_depth": _queue_depth(conn, job_ids, row["id"], row["machine_key"]),
            "alarms": alarms,
            "event_count": len(events),
        })

    by_key = {machine["machine_key"]: machine for machine in raw}
    max_queue = max((machine["queue_depth"] for machine in raw), default=0)
    max_alarms = max((machine["alarms"] for machine in raw), default=0)
    results = []

    for machine in raw:
        downstream = [
            by_key[key]["idle_fraction"]
            for key in DOWNSTREAM.get(machine["machine_key"], [])
            if key in by_key and by_key[key]["event_count"] > 0
        ]
        starvation = sum(downstream) / len(downstream) if downstream else 0.0
        queue_score = machine["queue_depth"] / max_queue if max_queue else 0.0
        alarm_score = machine["alarms"] / max_alarms if max_alarms else 0.0
        score = (
            machine["utilisation"] * 0.45
            + queue_score * 0.30
            + starvation * 0.15
            + alarm_score * 0.10
        )
        if machine["event_count"] >= 20 and job_ids:
            confidence = "high"
        elif machine["event_count"] >= 5 or job_ids:
            confidence = "medium"
        else:
            confidence = "low"
        machine["downstream_starvation"] = starvation
        machine["score"] = score
        machine["confidence"] = confidence
        machine["recommendation"] = _recommendation(machine)
        results.append(MachineConstraint(
            machine_key=machine["machine_key"],
            machine_name=machine["machine_name"],
            score=round(score, 4),
            utilisation=round(machine["utilisation"], 4),
            queue_depth=machine["queue_depth"],
            downstream_starvation=round(starvation, 4),
            alarms=machine["alarms"],
            event_count=machine["event_count"],
            confidence=confidence,
            recommendation=machine["recommendation"],
        ))

    results.sort(key=lambda machine: machine.score, reverse=True)
    current = results[0] if results and results[0].score > 0 else None
    return BottleneckReport(
        generated_at=now.isoformat(),
        window_hours=window_hours,
        current=current,
        machines=results,
    )
