"""Demand-aware, evidence-backed factory constraint intelligence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Optional

import data_quality
import oee


METHOD_VERSION = "constraint-intelligence-v2"
MIN_EPISODE_SAMPLES = 2
MIN_SAMPLE_GAP_S = 300
DEFAULT_RUNTIME_INTERVAL_S = 300

PRODUCTION_FLOW = [
    "gabbiani_pt80", "nova_si400", "morbidelli_cx100", "morbidelli_n100",
    "stefani_kd", "sergiani_gs120", "varie_osama", "dmc60_rcs135",
    "dmc90_xrt135", "superfici", "action_e",
]

ELIGIBLE_CONSTRAINT_STATES = {"capacity_constraint", "reliability_constraint"}
READY_EXECUTION_STATES = {"available", "dispatched", "acknowledged", "running"}


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
    active_ratio: float = 0.0
    average_active_period_s: float = 0.0
    longest_active_period_s: float = 0.0
    throughput_per_hour: float = 0.0
    data_quality_score: float = 0.0
    primary_cause: str = "insufficient_data"
    evidence: list[str] = field(default_factory=list)
    counter_evidence: list[str] = field(default_factory=list)
    state: str = "insufficient_data"
    demand_qty: int = 0
    ready_qty: int = 0
    starved_qty: int = 0
    held_qty: int = 0
    demand_source: str = "none"
    route_confidence: str = "low"
    downstream_buffer_full: bool = False
    downstream_buffers_verified: bool = False
    open_downtime: int = 0
    downtime_s: float = 0.0
    cycle_time_s: Optional[float] = None
    recoverable_minutes: Optional[float] = None
    estimated_recoverable_units: Optional[float] = None
    action_rank: int = 0


@dataclass
class BottleneckReport:
    generated_at: str
    window_hours: int
    current: Optional[MachineConstraint]
    candidate: Optional[MachineConstraint]
    focus: Optional[MachineConstraint] = None
    machines: list[MachineConstraint] = field(default_factory=list)
    method_version: str = METHOD_VERSION
    evidence_sha256: str = ""
    episode: Optional[dict] = None
    guardrail: str = (
        "Only repeated medium/high-confidence capacity or reliability evidence "
        "opens a constraint episode; starvation, blocking, and absent demand are "
        "reported as flow states, not bottlenecks."
    )


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _active_periods(events: list[dict], start: str, end: str) -> tuple[float, float, float]:
    """Return running share, mean running period, and longest running period."""
    start_dt, end_dt = oee._parse_dt(start), oee._parse_dt(end)
    active_start = None
    periods: list[float] = []
    for event in events:
        event_dt = oee._parse_dt(event["ts"])
        if event["event_type"] == "cycle_start" and active_start is None:
            active_start = max(start_dt, event_dt)
        elif event["event_type"] in {
            "cycle_end", "idle", "power_off", "state_off", "state_idle", "alarm"
        } and active_start is not None:
            periods.append(max(0.0, (event_dt - active_start).total_seconds()))
            active_start = None
    if active_start is not None:
        periods.append(max(0.0, (end_dt - active_start).total_seconds()))
    total = sum(periods)
    window_s = max(1.0, (end_dt - start_dt).total_seconds())
    return total / window_s, total / len(periods) if periods else 0.0, max(periods, default=0.0)


def _demand_profile(conn: sqlite3.Connection, machine_id: int) -> dict:
    execution_rows = conn.execute(
        """SELECT ej.state, ej.required_qty, ej.completed_qty, ej.in_process_qty,
                  prs.step_index,
                  (SELECT prev.state FROM part_route_steps previous_step
                   JOIN execution_jobs prev ON prev.route_step_id=previous_step.id
                   WHERE previous_step.part_id=prs.part_id AND previous_step.required=1
                     AND previous_step.step_index < prs.step_index
                   ORDER BY previous_step.step_index DESC LIMIT 1) predecessor_state
           FROM execution_jobs ej
           JOIN production_orders po ON po.id=ej.production_order_id
           JOIN part_route_steps prs ON prs.id=ej.route_step_id
           WHERE ej.machine_id=? AND ej.state NOT IN ('completed','cancelled')
             AND po.status IN ('released','in_progress')""",
        (machine_id,),
    ).fetchall()
    if execution_rows:
        demand = ready = starved = held = 0
        for row in execution_rows:
            remaining = max(0, int(row["required_qty"]) - int(row["completed_qty"]))
            demand += remaining
            if row["state"] in READY_EXECUTION_STATES:
                ready += remaining
            elif row["state"] == "held":
                held += remaining
            elif int(row["step_index"]) > 1 and row["predecessor_state"] != "completed":
                starved += remaining
        return {
            "demand_qty": demand, "ready_qty": ready, "starved_qty": starved,
            "held_qty": held, "source": "execution", "confidence": "confirmed",
        }

    route_rows = conn.execute(
        """SELECT prs.required_qty, prs.confirmed_qty, prs.step_index,
                  prs.confidence,
                  EXISTS(
                    SELECT 1 FROM part_route_steps previous_step
                    WHERE previous_step.part_id=prs.part_id AND previous_step.required=1
                      AND previous_step.step_index < prs.step_index
                      AND previous_step.status NOT IN ('confirmed','skipped')
                  ) predecessor_open
           FROM part_route_steps prs
           JOIN parts p ON p.id=prs.part_id
           JOIN production_orders po ON po.job_id=p.job_id
           WHERE prs.machine_id=? AND prs.required=1
             AND prs.status NOT IN ('confirmed','skipped')
             AND po.status IN ('released','in_progress')""",
        (machine_id,),
    ).fetchall()
    if not route_rows:
        return {
            "demand_qty": 0, "ready_qty": 0, "starved_qty": 0, "held_qty": 0,
            "source": "none", "confidence": "low",
        }
    demand = ready = starved = 0
    confidences = set()
    for row in route_rows:
        remaining = max(0, int(row["required_qty"]) - int(row["confirmed_qty"]))
        demand += remaining
        confidences.add(row["confidence"])
        if row["predecessor_open"]:
            starved += remaining
        else:
            ready += remaining
    route_confidence = (
        "confirmed" if confidences <= {"confirmed"}
        else "high" if confidences <= {"confirmed", "high"}
        else "medium" if "low" not in confidences else "low"
    )
    return {
        "demand_qty": demand, "ready_qty": ready, "starved_qty": starved,
        "held_qty": 0, "source": "planned_route", "confidence": route_confidence,
    }


def _downstream_buffers(conn: sqlite3.Connection, machine_id: int) -> dict:
    rows = conn.execute(
        """SELECT DISTINCT wb.capacity_qty, wb.current_qty, wb.verified
           FROM part_route_steps current_step
           JOIN parts p ON p.id=current_step.part_id
           JOIN production_orders po ON po.job_id=p.job_id
           JOIN part_route_steps next_step ON next_step.part_id=current_step.part_id
             AND next_step.required=1 AND next_step.step_index=(
               SELECT MIN(candidate.step_index) FROM part_route_steps candidate
               WHERE candidate.part_id=current_step.part_id AND candidate.required=1
                 AND candidate.step_index>current_step.step_index)
           LEFT JOIN wip_buffers wb ON wb.machine_id=next_step.machine_id
           WHERE current_step.machine_id=? AND current_step.required=1
             AND current_step.status NOT IN ('confirmed','skipped')
             AND po.status IN ('released','in_progress')""",
        (machine_id,),
    ).fetchall()
    configured = [row for row in rows if row["capacity_qty"] is not None]
    verified = bool(configured) and all(bool(row["verified"]) for row in configured)
    full = verified and all(int(row["current_qty"]) >= int(row["capacity_qty"]) for row in configured)
    return {"verified": verified, "full": full, "count": len(configured)}


def _downtime(conn: sqlite3.Connection, machine_id: int, start: str, end: str) -> tuple[int, float]:
    rows = conn.execute(
        """SELECT status,started_at,COALESCE(ended_at,?) ended_at
           FROM downtime_events
           WHERE machine_id=? AND started_at<=? AND COALESCE(ended_at,?)>=?
           ORDER BY started_at""",
        (end, machine_id, end, end, start),
    ).fetchall()
    window_start, window_end = _dt(start), _dt(end)
    intervals = sorted(
        (max(window_start, _dt(row["started_at"])),
         min(window_end, _dt(row["ended_at"])))
        for row in rows
    )
    merged: list[tuple[datetime, datetime]] = []
    for interval_start, interval_end in intervals:
        if interval_end <= interval_start:
            continue
        if merged and interval_start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], interval_end))
        else:
            merged.append((interval_start, interval_end))
    seconds = sum((interval_end - interval_start).total_seconds()
                  for interval_start, interval_end in merged)
    return sum(1 for row in rows if row["status"] == "open"), seconds


def _calibrated_cycle(conn: sqlite3.Connection, machine_id: int) -> Optional[float]:
    model = conn.execute(
        """SELECT id FROM cycle_models WHERE machine_id=? AND status='active'
             AND confidence IN ('medium','high') ORDER BY version DESC LIMIT 1""",
        (machine_id,),
    ).fetchone()
    if not model:
        return None
    rows = conn.execute(
        """SELECT duration_s FROM cycle_observations
           WHERE machine_id=? AND validity='valid' AND duration_s>0
           ORDER BY ended_at DESC LIMIT 100""",
        (machine_id,),
    ).fetchall()
    return float(median(row["duration_s"] for row in rows)) if rows else None


def _state(machine: dict) -> str:
    if machine["demand_qty"] <= 0:
        return "demand_absent"
    if machine["open_downtime"] or machine["alarms"]:
        return "reliability_constraint"
    if machine["downstream_buffer_full"]:
        return "blocked"
    if machine["ready_qty"] <= 0 and machine["starved_qty"] > 0:
        return "starved"
    if machine["event_count"] < 2:
        return "insufficient_data"
    if machine["active_ratio"] >= 0.70 or machine["utilisation"] >= 0.75:
        return "capacity_constraint"
    if machine["ready_qty"] > 0:
        return "flow_or_staffing"
    return "insufficient_data"


def _confidence(machine: dict, telemetry: dict) -> str:
    confidence = telemetry.get("confidence", "low")
    if (machine["open_downtime"] and machine["demand_source"] == "execution"
            and machine["route_confidence"] == "confirmed"):
        return "high" if confidence == "high" else "medium"
    return confidence


def _recommendation(machine: dict) -> str:
    state = machine["state"]
    if state == "capacity_constraint":
        return "Protect running time, stage the ready queue, and test the largest evidenced loss before adding capacity."
    if state == "reliability_constraint":
        return "Restore the open downtime condition and confirm its cause before releasing more work."
    if state == "starved":
        return "Clear the named predecessor operations; increasing this machine's capacity will not improve output."
    if state == "blocked":
        return "Clear the verified downstream buffer before feeding or accelerating this machine."
    if state == "flow_or_staffing":
        return "Ready work exists but activity is low; check dispatch, staffing, setup, and tooling in that order."
    if state == "demand_absent":
        return "No released route demand exists for this machine; do not infer a bottleneck from activity alone."
    return "Collect complete machine states and connect released route demand before changing the schedule."


def _cause(state: str) -> str:
    return {
        "capacity_constraint": "capacity", "reliability_constraint": "reliability",
        "starved": "upstream_supply", "blocked": "downstream_blocking",
        "flow_or_staffing": "flow_or_staffing", "demand_absent": "no_demand",
    }.get(state, "insufficient_data")


def _latest_episode(conn: sqlite3.Connection) -> Optional[dict]:
    row = conn.execute(
        """SELECT ce.*,m.machine_key,m.name machine_name
           FROM constraint_episodes ce JOIN machines m ON m.id=ce.machine_id
           WHERE ce.status IN ('open','observing')
           ORDER BY CASE ce.status WHEN 'open' THEN 0 ELSE 1 END, ce.last_seen_at DESC LIMIT 1"""
    ).fetchone()
    return dict(row) if row else None


def _shift_context(conn: sqlite3.Connection, now: datetime) -> dict:
    rows = conn.execute(
        """SELECT weekday,start_time,end_time,timezone,source,verified
           FROM work_calendar_windows
           WHERE resource_type='factory' AND resource_key='factory' AND active=1
           ORDER BY verified DESC,start_time,end_time"""
    ).fetchall()
    for row in rows:
        try:
            local = now.astimezone(ZoneInfo(row["timezone"]))
        except ZoneInfoNotFoundError:
            continue
        start_time = datetime.strptime(row["start_time"], "%H:%M").time()
        end_time = datetime.strptime(row["end_time"], "%H:%M").time()
        weekday = int(row["weekday"])
        if start_time < end_time:
            active = local.weekday() == weekday and start_time <= local.time() < end_time
            anchor = local.date()
        else:
            starts_today = local.weekday() == weekday and local.time() >= start_time
            ends_today = local.weekday() == (weekday + 1) % 7 and local.time() < end_time
            active = starts_today or ends_today
            anchor = local.date() if starts_today else local.date() - timedelta(days=1)
        if not active:
            continue
        label = f"{row['start_time']}-{row['end_time']}"
        verified = bool(row["verified"])
        return {
            "shift_key": f"{anchor.isoformat()}:{label}@{row['timezone']}",
            "shift_label": label,
            "shift_source": "verified_calendar" if verified else "calendar_assumption",
            "timezone": row["timezone"], "local_date": anchor.isoformat(),
            "active_shift": True, "calendar_verified": verified,
        }
    if rows:
        row = rows[0]
        try:
            local = now.astimezone(ZoneInfo(row["timezone"]))
            timezone_name = row["timezone"]
        except ZoneInfoNotFoundError:
            local, timezone_name = now.astimezone(timezone.utc), "UTC"
        return {
            "shift_key": f"{local.date().isoformat()}:off_shift@{timezone_name}",
            "shift_label": "Off shift", "shift_source": "calendar",
            "timezone": timezone_name, "local_date": local.date().isoformat(),
            "active_shift": False,
            "calendar_verified": any(bool(item["verified"]) for item in rows),
        }
    utc = now.astimezone(timezone.utc)
    return {
        "shift_key": f"{utc.date().isoformat()}:calendar_missing@UTC",
        "shift_label": "Calendar missing", "shift_source": "calendar_missing",
        "timezone": "UTC", "local_date": utc.date().isoformat(),
        "active_shift": False, "calendar_verified": False,
    }


def _record_shift_context(conn: sqlite3.Connection, snapshot_id: int,
                          now: datetime) -> dict:
    context = _shift_context(conn, now)
    conn.execute(
        """INSERT INTO constraint_snapshot_contexts
           (snapshot_id,shift_key,shift_label,shift_source,timezone,local_date,
            active_shift,calendar_verified,created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (snapshot_id, context["shift_key"], context["shift_label"],
         context["shift_source"], context["timezone"], context["local_date"],
         int(context["active_shift"]), int(context["calendar_verified"]),
         now.isoformat()),
    )
    return context


def detect(conn: sqlite3.Connection, window_hours: int = 8,
           now: Optional[datetime] = None) -> BottleneckReport:
    now = now or datetime.now(timezone.utc)
    start = (now - timedelta(hours=window_hours)).isoformat()
    end = now.isoformat()
    quality = data_quality.build(conn, window_hours, now)
    quality_by_key = {item["machine_key"]: item for item in quality["machines"]}
    rows = conn.execute(
        """SELECT id,machine_key,name FROM machines
           WHERE active=1 AND machine_key IN ({}) ORDER BY id""".format(
               ",".join("?" * len(PRODUCTION_FLOW))
           ), PRODUCTION_FLOW,
    ).fetchall()

    raw = []
    for row in rows:
        events = oee._events_in_window(conn, row["id"], start, end)
        run_s, idle_s, down_s = oee._compute_time_buckets(events, start, end)
        observed_s = run_s + idle_s + down_s
        active_ratio, average_active_s, longest_active_s = _active_periods(events, start, end)
        demand = _demand_profile(conn, row["id"])
        buffers = _downstream_buffers(conn, row["id"])
        open_downtime, downtime_s = _downtime(conn, row["id"], start, end)
        cycle_time_s = _calibrated_cycle(conn, row["id"])
        machine = {
            "machine_id": row["id"], "machine_key": row["machine_key"],
            "machine_name": row["name"],
            "utilisation": run_s / observed_s if observed_s else 0.0,
            "active_ratio": active_ratio, "average_active_period_s": average_active_s,
            "longest_active_period_s": longest_active_s,
            "alarms": sum(1 for event in events if event["event_type"] == "alarm"),
            "event_count": len(events),
            "throughput_per_hour": sum(1 for event in events if event["event_type"] == "cycle_end") / window_hours,
            "downstream_buffer_full": buffers["full"],
            "downstream_buffers_verified": buffers["verified"],
            "open_downtime": open_downtime, "downtime_s": downtime_s,
            "cycle_time_s": cycle_time_s, **demand,
        }
        machine["demand_source"] = machine.pop("source")
        machine["route_confidence"] = machine.pop("confidence")
        machine["state"] = _state(machine)
        raw.append(machine)

    max_ready = max((item["ready_qty"] for item in raw), default=0)
    max_active = max((item["average_active_period_s"] for item in raw), default=0)
    max_loss = max((item["downtime_s"] for item in raw), default=0)
    results: list[MachineConstraint] = []
    for machine in raw:
        quality_item = quality_by_key.get(machine["machine_key"], {})
        machine["confidence"] = _confidence(machine, quality_item)
        ready_pressure = machine["ready_qty"] / max_ready if max_ready else 0.0
        active_pressure = machine["average_active_period_s"] / max_active if max_active else 0.0
        loss_pressure = machine["downtime_s"] / max_loss if max_loss else 0.0
        score = (
            0.30 * machine["active_ratio"] + 0.20 * active_pressure
            + 0.25 * ready_pressure + 0.15 * loss_pressure
            + (0.10 if machine["state"] in ELIGIBLE_CONSTRAINT_STATES else 0.0)
        )
        if machine["state"] == "demand_absent":
            score = 0.0
        evidence = []
        counter = []
        if machine["demand_qty"]:
            evidence.append(
                f"{machine['demand_qty']} routed units remain; {machine['ready_qty']} are ready"
            )
        else:
            counter.append("No released route demand exists")
        if machine["average_active_period_s"]:
            evidence.append(f"Average uninterrupted run {round(machine['average_active_period_s'])}s")
        if machine["starved_qty"]:
            evidence.append(f"{machine['starved_qty']} units wait on predecessor operations")
        if machine["downstream_buffer_full"]:
            evidence.append("All observed successor input buffers are verified full")
        elif not machine["downstream_buffers_verified"]:
            counter.append("Downstream buffer capacity is not fully verified")
        if machine["downtime_s"]:
            evidence.append(f"{round(machine['downtime_s'] / 60)} downtime minutes overlap the window")
        if machine["alarms"]:
            evidence.append(f"{machine['alarms']} alarms in the window")
        if machine["event_count"] < 2:
            counter.append("Fewer than two machine-state events in the window")
        if machine["confidence"] == "low":
            counter.append("Telemetry confidence is below the decision gate")
        recoverable_minutes = (
            round(machine["downtime_s"] / 60, 1)
            if machine["downtime_s"] and machine["demand_qty"] else None
        )
        recoverable_units = (
            round(min(machine["demand_qty"], machine["downtime_s"] / machine["cycle_time_s"]), 1)
            if machine["downtime_s"] and machine["cycle_time_s"] and machine["demand_qty"] else None
        )
        results.append(MachineConstraint(
            machine_key=machine["machine_key"], machine_name=machine["machine_name"],
            score=round(score, 4), utilisation=round(machine["utilisation"], 4),
            queue_depth=machine["ready_qty"], downstream_starvation=round(
                machine["starved_qty"] / machine["demand_qty"], 4
            ) if machine["demand_qty"] else 0.0,
            alarms=machine["alarms"], event_count=machine["event_count"],
            confidence=machine["confidence"], recommendation=_recommendation(machine),
            active_ratio=round(machine["active_ratio"], 4),
            average_active_period_s=round(machine["average_active_period_s"], 1),
            longest_active_period_s=round(machine["longest_active_period_s"], 1),
            throughput_per_hour=round(machine["throughput_per_hour"], 3),
            data_quality_score=quality_item.get("score", 0.0),
            primary_cause=_cause(machine["state"]), evidence=evidence,
            counter_evidence=counter, state=machine["state"],
            demand_qty=machine["demand_qty"], ready_qty=machine["ready_qty"],
            starved_qty=machine["starved_qty"], held_qty=machine["held_qty"],
            demand_source=machine["demand_source"], route_confidence=machine["route_confidence"],
            downstream_buffer_full=machine["downstream_buffer_full"],
            downstream_buffers_verified=machine["downstream_buffers_verified"],
            open_downtime=machine["open_downtime"], downtime_s=round(machine["downtime_s"], 1),
            cycle_time_s=round(machine["cycle_time_s"], 1) if machine["cycle_time_s"] else None,
            recoverable_minutes=recoverable_minutes,
            estimated_recoverable_units=recoverable_units,
        ))

    results.sort(key=lambda item: (item.score, item.ready_qty, item.machine_key), reverse=True)
    for rank, item in enumerate(results, 1):
        item.action_rank = rank
    eligible = [
        item for item in results
        if item.state in ELIGIBLE_CONSTRAINT_STATES and item.demand_qty > 0 and item.score >= 0.25
    ]
    candidate = eligible[0] if eligible else None
    current = candidate if candidate and candidate.confidence in ("medium", "high") else None
    focus = current or candidate or next(
        (item for item in results if item.demand_qty > 0), None
    )
    core = [{
        key: getattr(item, key) for key in (
            "machine_key", "state", "score", "demand_qty", "ready_qty", "starved_qty",
            "active_ratio", "alarms", "downtime_s", "confidence"
        )
    } for item in sorted(results, key=lambda item: item.machine_key)]
    evidence_sha = hashlib.sha256(
        json.dumps({"method": METHOD_VERSION, "window_hours": window_hours, "machines": core},
                   sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return BottleneckReport(
        generated_at=end, window_hours=window_hours, current=current,
        candidate=candidate, focus=focus, machines=results,
        evidence_sha256=evidence_sha, episode=_latest_episode(conn),
    )


def _sample_due(last_evaluated_at: str, now: str, _last_hash: str, _evidence_hash: str) -> bool:
    return (_dt(now) - _dt(last_evaluated_at)).total_seconds() >= MIN_SAMPLE_GAP_S


def sync(conn: sqlite3.Connection, actor: str = "operator", window_hours: int = 8,
         now: Optional[datetime] = None) -> dict:
    """Persist one snapshot and advance episodes from repeated qualified evidence."""
    now = now or datetime.now(timezone.utc)
    report = detect(conn, window_hours, now)
    generated_at = report.generated_at
    window_start = (now - timedelta(hours=window_hours)).isoformat()
    current = report.current
    machine_ids = {
        row["machine_key"]: row["id"] for row in conn.execute(
            "SELECT id,machine_key FROM machines"
        ).fetchall()
    }
    report_payload = asdict(report)
    cursor = conn.execute(
        """INSERT INTO constraint_snapshots
           (method_version,window_start,window_end,evidence_sha256,current_machine_id,
            current_state,current_confidence,report_json,actor,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (METHOD_VERSION, window_start, generated_at, report.evidence_sha256,
         machine_ids.get(current.machine_key) if current else None,
         current.state if current else None, current.confidence if current else None,
         json.dumps(report_payload, sort_keys=True), actor, generated_at),
    )
    snapshot_id = cursor.lastrowid
    shift_context = _record_shift_context(conn, snapshot_id, now)
    for item in report.machines:
        conn.execute(
            """INSERT INTO constraint_machine_snapshots
               (snapshot_id,machine_id,state,score,demand_qty,ready_qty,starved_qty,
                active_ratio,alarm_count,downtime_s,evidence_json,counter_evidence_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (snapshot_id, machine_ids[item.machine_key], item.state, item.score,
             item.demand_qty, item.ready_qty, item.starved_qty, item.active_ratio,
             item.alarms, item.downtime_s, json.dumps(item.evidence),
             json.dumps(item.counter_evidence)),
        )

    progressed = False
    if current:
        machine_id = machine_ids[current.machine_key]
        episode = conn.execute(
            """SELECT * FROM constraint_episodes
               WHERE machine_id=? AND constraint_state=? AND status IN ('open','observing')
               ORDER BY id DESC LIMIT 1""", (machine_id, current.state),
        ).fetchone()
        if not episode:
            conn.execute(
                """INSERT INTO constraint_episodes
                   (machine_id,constraint_state,status,started_at,last_seen_at,
                    last_evaluated_at,confidence,peak_score,first_snapshot_id,
                    last_snapshot_id,last_evidence_sha256,created_by,updated_at)
                   VALUES (?,?,'observing',?,?,?,?,?,?,?,?,?,?)""",
                (machine_id, current.state, generated_at, generated_at, generated_at,
                 current.confidence, current.score, snapshot_id, snapshot_id,
                 report.evidence_sha256, actor, generated_at),
            )
            progressed = True
        elif _sample_due(
            episode["last_evaluated_at"], generated_at,
            episode["last_evidence_sha256"], report.evidence_sha256,
        ):
            samples = int(episode["consecutive_samples"]) + 1
            target_status = "open" if samples >= MIN_EPISODE_SAMPLES else episode["status"]
            confirmed_at = generated_at if target_status == "open" and not episode["confirmed_at"] else episode["confirmed_at"]
            conn.execute(
                """UPDATE constraint_episodes SET status=?,confirmed_at=?,last_seen_at=?,
                     last_evaluated_at=?,consecutive_samples=?,snapshot_count=snapshot_count+1,
                     miss_count=0,confidence=?,peak_score=MAX(peak_score,?),last_snapshot_id=?,
                     last_evidence_sha256=?,updated_at=? WHERE id=?""",
                (target_status, confirmed_at, generated_at, generated_at, samples,
                 current.confidence, current.score, snapshot_id, report.evidence_sha256,
                 generated_at, episode["id"]),
            )
            if target_status == "open":
                conn.execute(
                    """UPDATE constraint_episodes SET status='closed',ended_at=?,
                         close_reason='constraint_migrated',updated_at=?
                       WHERE status='open' AND id!=?""",
                    (generated_at, generated_at, episode["id"]),
                )
                conn.execute(
                    """UPDATE constraint_episodes SET status='closed',ended_at=?,
                         close_reason='candidate_superseded',updated_at=?
                       WHERE status='observing' AND id!=?""",
                    (generated_at, generated_at, episode["id"]),
                )
            progressed = True
    else:
        active = conn.execute(
            """SELECT * FROM constraint_episodes WHERE status IN ('open','observing')
               ORDER BY id"""
        ).fetchall()
        for episode in active:
            if not _sample_due(
                episode["last_evaluated_at"], generated_at,
                episode["last_evidence_sha256"], report.evidence_sha256,
            ):
                continue
            misses = int(episode["miss_count"]) + 1
            should_close = misses >= MIN_EPISODE_SAMPLES or episode["status"] == "observing"
            conn.execute(
                """UPDATE constraint_episodes SET status=?,miss_count=?,last_evaluated_at=?,
                     last_snapshot_id=?,last_evidence_sha256=?,ended_at=?,close_reason=?,
                     updated_at=? WHERE id=?""",
                ("closed" if should_close else episode["status"], misses, generated_at,
                 snapshot_id, report.evidence_sha256, generated_at if should_close else None,
                 "evidence_cleared" if should_close else None, generated_at, episode["id"]),
            )
            progressed = True

    conn.commit()
    return {
        "snapshot_id": snapshot_id, "progressed": progressed,
        "report": asdict(detect(conn, window_hours, now)),
        "episode": _latest_episode(conn), "shift_context": shift_context,
    }


def _runtime_event(conn: sqlite3.Connection, event_type: str, actor: str,
                   payload: dict, now: datetime) -> None:
    conn.execute(
        """INSERT INTO constraint_runtime_events
           (event_type,actor,payload_json,ts) VALUES (?,?,?,?)""",
        (event_type, actor, json.dumps(payload, sort_keys=True), now.isoformat()),
    )


def runtime_settings(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT * FROM constraint_runtime_settings WHERE id=1").fetchone()
    if not row:
        raise RuntimeError("Constraint runtime settings are not initialized")
    result = dict(row)
    result["auto_sync"] = bool(result["auto_sync"])
    return result


def update_runtime_settings(conn: sqlite3.Connection, payload: dict,
                            now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    actor = (payload.get("actor") or "").strip()
    if not actor or actor.lower() in {"operator", "system"}:
        raise ValueError("A named operator is required")
    current = runtime_settings(conn)
    if (payload.get("expected_version") is not None
            and int(payload["expected_version"]) != int(current["version"])):
        raise ValueError("Constraint settings changed; refresh before saving")
    interval = int(payload.get("interval_seconds", current["interval_seconds"]))
    window_hours = int(payload.get("window_hours", current["window_hours"]))
    retention_days = int(payload.get("retention_days", current["retention_days"]))
    if not 300 <= interval <= 3600:
        raise ValueError("Constraint sampling interval must be between 300 and 3600 seconds")
    if not 1 <= window_hours <= 24:
        raise ValueError("Constraint analysis window must be between 1 and 24 hours")
    if not 7 <= retention_days <= 3650:
        raise ValueError("Constraint snapshot retention must be between 7 and 3650 days")
    auto_sync = bool(payload.get("auto_sync", current["auto_sync"]))
    conn.execute(
        """UPDATE constraint_runtime_settings SET auto_sync=?,interval_seconds=?,
             window_hours=?,retention_days=?,version=version+1,updated_by=?,updated_at=?
           WHERE id=1""",
        (int(auto_sync), interval, window_hours, retention_days, actor, now.isoformat()),
    )
    _runtime_event(conn, "settings_updated", actor, {
        "auto_sync": auto_sync, "interval_seconds": interval,
        "window_hours": window_hours, "retention_days": retention_days,
    }, now)
    conn.commit()
    return runtime_settings(conn)


def _prune_snapshots(conn: sqlite3.Connection, retention_days: int,
                     now: datetime) -> int:
    cutoff = (now - timedelta(days=retention_days)).isoformat()
    protected = {
        int(row["snapshot_id"])
        for row in conn.execute(
            """SELECT first_snapshot_id snapshot_id FROM constraint_episodes
               UNION SELECT last_snapshot_id FROM constraint_episodes
               UNION SELECT last_snapshot_id FROM constraint_runtime_settings
                     WHERE last_snapshot_id IS NOT NULL"""
        ).fetchall()
    }
    rows = conn.execute(
        "SELECT id FROM constraint_snapshots WHERE created_at<? ORDER BY id", (cutoff,)
    ).fetchall()
    removable = [int(row["id"]) for row in rows if int(row["id"]) not in protected]
    for offset in range(0, len(removable), 500):
        batch = removable[offset:offset + 500]
        marks = ",".join("?" for _ in batch)
        conn.execute(f"DELETE FROM constraint_snapshot_contexts WHERE snapshot_id IN ({marks})", batch)
        conn.execute(f"DELETE FROM constraint_machine_snapshots WHERE snapshot_id IN ({marks})", batch)
        conn.execute(f"DELETE FROM constraint_snapshots WHERE id IN ({marks})", batch)
    conn.execute("DELETE FROM constraint_runtime_events WHERE ts<?", (cutoff,))
    return len(removable)


def automatic_sync(conn: sqlite3.Connection,
                   now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    settings = runtime_settings(conn)
    if not settings["auto_sync"]:
        return {"status": "disabled", "snapshot_id": None}
    result = sync(
        conn, actor="hive-constraint-worker",
        window_hours=int(settings["window_hours"]), now=now,
    )
    pruned = _prune_snapshots(conn, int(settings["retention_days"]), now)
    conn.execute(
        """UPDATE constraint_runtime_settings SET last_run_at=?,last_success_at=?,
             last_snapshot_id=?,consecutive_failures=0,last_error=NULL WHERE id=1""",
        (now.isoformat(), now.isoformat(), result["snapshot_id"]),
    )
    _runtime_event(conn, "automatic_sample", "hive-constraint-worker", {
        "snapshot_id": result["snapshot_id"], "progressed": result["progressed"],
        "shift_key": result["shift_context"]["shift_key"], "pruned": pruned,
    }, now)
    conn.commit()
    return {"status": "sampled", "pruned": pruned, **result}


def record_runtime_failure(conn: sqlite3.Connection, error: Exception,
                           now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    message = str(error)[:1000] or error.__class__.__name__
    conn.execute(
        """UPDATE constraint_runtime_settings SET last_run_at=?,
             consecutive_failures=consecutive_failures+1,last_error=? WHERE id=1""",
        (now.isoformat(), message),
    )
    _runtime_event(conn, "automatic_sample_failed", "hive-constraint-worker", {
        "error": message,
    }, now)
    conn.commit()
    return runtime_settings(conn)


def _runtime_health(settings: dict, now: datetime) -> dict:
    if not settings["auto_sync"]:
        return {"status": "disabled", "age_seconds": None, "next_run_at": None}
    last_success = settings.get("last_success_at")
    age = max(0, int((now - _dt(last_success)).total_seconds())) if last_success else None
    if int(settings["consecutive_failures"]):
        status = "degraded"
    elif age is None:
        status = "starting"
    elif age > max(900, int(settings["interval_seconds"]) * 3):
        status = "stale"
    else:
        status = "healthy"
    anchor = settings.get("last_run_at")
    next_run = (
        (_dt(anchor) + timedelta(seconds=int(settings["interval_seconds"]))).isoformat()
        if anchor else now.isoformat()
    )
    return {"status": status, "age_seconds": age, "next_run_at": next_run}


def timeline(conn: sqlite3.Connection, days: int = 30, limit: int = 100,
             now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    start = (now - timedelta(days=days)).isoformat()
    settings = runtime_settings(conn)
    episodes = []
    for row in conn.execute(
        """SELECT ce.*,m.machine_key,m.name machine_name,
                  first_ctx.shift_key first_shift_key,
                  last_ctx.shift_key last_shift_key
           FROM constraint_episodes ce JOIN machines m ON m.id=ce.machine_id
           LEFT JOIN constraint_snapshot_contexts first_ctx
             ON first_ctx.snapshot_id=ce.first_snapshot_id
           LEFT JOIN constraint_snapshot_contexts last_ctx
             ON last_ctx.snapshot_id=ce.last_snapshot_id
           WHERE ce.last_seen_at>=? OR ce.status IN ('open','observing')
           ORDER BY ce.last_seen_at DESC,ce.id DESC LIMIT ?""",
        (start, limit),
    ).fetchall():
        item = dict(row)
        end = item["ended_at"] or item["last_seen_at"]
        item["duration_s"] = max(0, int((_dt(end) - _dt(item["started_at"])).total_seconds()))
        episodes.append(item)

    samples = [dict(row) for row in conn.execute(
        """SELECT cs.id,cs.window_end,cs.current_state,cs.current_confidence,
                  m.machine_key,m.name machine_name,ctx.shift_key,ctx.shift_label,
                  ctx.shift_source,ctx.timezone,ctx.local_date,ctx.active_shift,
                  ctx.calendar_verified
           FROM constraint_snapshots cs
           LEFT JOIN machines m ON m.id=cs.current_machine_id
           LEFT JOIN constraint_snapshot_contexts ctx ON ctx.snapshot_id=cs.id
           WHERE cs.created_at>=? ORDER BY cs.created_at DESC,cs.id DESC""",
        (start,),
    ).fetchall()]
    grouped: dict[str, dict] = {}
    for sample in samples:
        key = sample["shift_key"] or "legacy:unclassified"
        group = grouped.setdefault(key, {
            "shift_key": key, "shift_label": sample["shift_label"] or "Legacy samples",
            "timezone": sample["timezone"] or "UTC",
            "local_date": sample["local_date"],
            "calendar_verified": bool(sample["calendar_verified"]),
            "sample_count": 0, "constraint_samples": 0, "candidates": Counter(),
            "first_sample_at": sample["window_end"], "last_sample_at": sample["window_end"],
        })
        group["sample_count"] += 1
        group["first_sample_at"] = min(group["first_sample_at"], sample["window_end"])
        group["last_sample_at"] = max(group["last_sample_at"], sample["window_end"])
        if sample["machine_key"]:
            group["constraint_samples"] += 1
            group["candidates"][(sample["machine_key"], sample["machine_name"],
                                 sample["current_state"])] += 1
    shifts = []
    for group in grouped.values():
        candidates = group.pop("candidates")
        dominant = candidates.most_common(1)[0] if candidates else None
        group["dominant"] = ({
            "machine_key": dominant[0][0], "machine_name": dominant[0][1],
            "state": dominant[0][2], "sample_count": dominant[1],
            "share": round(dominant[1] / group["sample_count"], 4),
        } if dominant else None)
        shifts.append(group)
    shifts.sort(key=lambda item: item["last_sample_at"], reverse=True)
    recent_samples = samples[:50]
    for item in recent_samples:
        item["active_shift"] = bool(item["active_shift"])
        item["calendar_verified"] = bool(item["calendar_verified"])
    counts = conn.execute(
        """SELECT COUNT(*) total,
                  SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) open,
                  SUM(CASE WHEN status='observing' THEN 1 ELSE 0 END) observing,
                  SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) closed
           FROM constraint_episodes"""
    ).fetchone()
    snapshot_count = conn.execute("SELECT COUNT(*) FROM constraint_snapshots").fetchone()[0]
    runtime_events = [dict(row) for row in conn.execute(
        "SELECT * FROM constraint_runtime_events ORDER BY ts DESC,id DESC LIMIT 50"
    ).fetchall()]
    return {
        "generated_at": now.isoformat(), "days": days,
        "runtime": {**settings, **_runtime_health(settings, now)},
        "summary": {
            "snapshots": snapshot_count, "episodes": int(counts["total"] or 0),
            "open": int(counts["open"] or 0),
            "observing": int(counts["observing"] or 0),
            "closed": int(counts["closed"] or 0),
            "shifts_sampled": len(shifts),
        },
        "episodes": episodes, "shifts": shifts,
        "recent_samples": recent_samples, "runtime_events": runtime_events,
        "guardrail": (
            "Automatic sampling appends analytical evidence only. It cannot dispatch work, "
            "change schedules, acknowledge alerts, or write to machine controllers."
        ),
    }
