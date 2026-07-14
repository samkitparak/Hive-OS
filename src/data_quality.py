"""Telemetry completeness and trust scoring for HIVE analytics."""

import sqlite3
import math
from datetime import datetime, timedelta, timezone
from typing import Optional


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _cycle_integrity(events: list[sqlite3.Row]) -> tuple[int, int, int]:
    starts = ends = anomalies = 0
    open_cycle = False
    for event in events:
        if event["event_type"] == "cycle_start":
            starts += 1
            if open_cycle:
                anomalies += 1
            open_cycle = True
        elif event["event_type"] == "cycle_end":
            ends += 1
            if not open_cycle:
                anomalies += 1
            open_cycle = False
    if open_cycle:
        anomalies += 1
    return starts, ends, anomalies


def _machine_report(conn: sqlite3.Connection, machine: sqlite3.Row,
                    start: str, end: str, window_s: int,
                    now: datetime) -> dict:
    events = conn.execute(
        """SELECT event_type,part_id,ts FROM machine_events
           WHERE machine_id=? AND ts>=? AND ts<=? AND event_type!='heartbeat'
           ORDER BY ts,id""",
        (machine["id"], start, end),
    ).fetchall()
    event_count = len(events)
    starts, ends, anomalies = _cycle_integrity(events)
    cycles = max(starts, ends)
    pair_score = 1.0 if cycles == 0 and event_count >= 2 else (
        max(0.0, 1.0 - anomalies / max(1, cycles)) if cycles else 0.0
    )

    linked = sum(1 for event in events if event["event_type"] == "cycle_end" and event["part_id"])
    link_score = linked / ends if ends else (1.0 if machine["has_maestro"] == 0 else 0.0)
    first_ts = events[0]["ts"] if events else None
    last_ts = events[-1]["ts"] if events else None
    span_s = max(0.0, (_dt(last_ts) - _dt(first_ts)).total_seconds()) if event_count >= 2 else 0.0
    temporal_score = min(1.0, span_s / max(1, window_s * 0.75))

    ledger = conn.execute(
        """SELECT status,COUNT(*) count FROM event_ingestion_log
           WHERE machine_id=? AND received_at>=? AND received_at<=?
           GROUP BY status""",
        (machine["id"], start, end),
    ).fetchall()
    dispositions = {row["status"]: row["count"] for row in ledger}
    ledger_total = sum(dispositions.values())
    ingestion_score = dispositions.get("accepted", 0) / ledger_total if ledger_total else (
        1.0 if event_count else 0.0
    )

    agent = conn.execute(
        """SELECT last_heartbeat_at,last_event_at,last_received_at,clock_skew_s
           FROM agent_status WHERE machine_id=?""", (machine["id"],)
    ).fetchone()
    clock_skew_s = abs(agent["clock_skew_s"]) if agent and agent["clock_skew_s"] is not None else None
    clock_score = 1.0 if clock_skew_s is not None and clock_skew_s <= 60 else (
        0.5 if clock_skew_s is not None and clock_skew_s <= 300 else 0.0
    )
    latest_signal = None
    if agent:
        latest_signal = agent["last_heartbeat_at"] or agent["last_event_at"] or agent["last_received_at"]
    latest_signal = latest_signal or last_ts
    age_s = max(0, int((now - _dt(latest_signal)).total_seconds())) if latest_signal else None

    score = (
        ingestion_score * 0.20
        + temporal_score * 0.25
        + pair_score * 0.25
        + link_score * 0.15
        + clock_score * 0.15
    )
    score = round(score, 3) if event_count else 0.0
    if score >= 0.8 and event_count >= 20 and (cycles == 0 or cycles >= 5):
        confidence = "high"
    elif score >= 0.5 and event_count >= 5:
        confidence = "medium"
    else:
        confidence = "low"

    issues = []
    if event_count == 0:
        issues.append("No production events in this window")
    if anomalies:
        issues.append(f"{anomalies} unmatched or overlapping cycle events")
    if ends and link_score < 0.8:
        issues.append(f"Only {round(link_score * 100)}% of completed cycles link to a known part")
    if dispositions.get("duplicate", 0):
        issues.append(f"{dispositions['duplicate']} duplicate events suppressed")
    if dispositions.get("rejected", 0):
        issues.append(f"{dispositions['rejected']} invalid events rejected")
    if clock_skew_s is not None and clock_skew_s > 60:
        issues.append(f"Machine clock differs from central time by {round(clock_skew_s)} seconds")
    if temporal_score < 0.5 and event_count:
        issues.append("Events cover less than half of the analysis window")

    return {
        "machine_key": machine["machine_key"],
        "machine_name": machine["name"],
        "score": score,
        "confidence": confidence,
        "event_count": event_count,
        "cycle_starts": starts,
        "cycle_ends": ends,
        "cycle_anomalies": anomalies,
        "part_link_rate": round(link_score, 3),
        "temporal_coverage": round(temporal_score, 3),
        "ingestion_acceptance": round(ingestion_score, 3),
        "duplicates_suppressed": dispositions.get("duplicate", 0),
        "rejected_events": dispositions.get("rejected", 0),
        "clock_skew_s": round(clock_skew_s, 1) if clock_skew_s is not None else None,
        "last_signal_at": latest_signal,
        "last_signal_age_s": age_s,
        "issues": issues,
    }


def build(conn: sqlite3.Connection, window_hours: int = 8,
          now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    start_dt = now - timedelta(hours=window_hours)
    start, end = start_dt.isoformat(), now.isoformat()
    machines = conn.execute(
        """SELECT id,machine_key,name,has_maestro FROM machines
           WHERE active=1 ORDER BY id"""
    ).fetchall()
    reports = [
        _machine_report(conn, machine, start, end, window_hours * 3600, now)
        for machine in machines
    ]
    reporting = [report for report in reports if report["event_count"] > 0]
    overall = round(sum(report["score"] for report in reporting) / len(reporting), 3) if reporting else 0.0
    minimum_reporting = max(1, math.ceil(len(reports) * 0.6))
    intelligence_ready = (
        len(reporting) >= minimum_reporting
        and all(report["confidence"] in ("medium", "high") for report in reporting)
    )
    return {
        "generated_at": now.isoformat(),
        "window_hours": window_hours,
        "overall_score": overall,
        "intelligence_ready": intelligence_ready,
        "summary": {
            "total_machines": len(reports),
            "reporting_machines": len(reporting),
            "high_confidence": sum(1 for report in reports if report["confidence"] == "high"),
            "medium_confidence": sum(1 for report in reports if report["confidence"] == "medium"),
            "low_confidence": sum(1 for report in reports if report["confidence"] == "low"),
            "minimum_reporting_for_factory_optimization": minimum_reporting,
            "rejected_events": sum(report["rejected_events"] for report in reports),
            "duplicates_suppressed": sum(report["duplicates_suppressed"] for report in reports),
        },
        "machines": reports,
    }
