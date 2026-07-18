"""Shift-scoped, evidence-gated production loss accounting.

The timeline ledger and the output waterfall are deliberately separate:

* timeline categories partition scheduled machine time exactly once;
* speed and quality are equivalent output-time losses inside running time;
* missing telemetry, cycle models, or quality disposition remains unknown.

This module is read-only. Raw events and approved factory calendars remain the
authoritative evidence and the result can be recalculated for any retained day.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import cycle_time


METHOD_VERSION = "production-loss-waterfall-v1"
MINOR_STOP_THRESHOLD_S = 300
CARRY_IN_MAX_AGE_S = 12 * 3600
PRODUCTION_MACHINE_KEYS = (
    "gabbiani_pt80", "nova_si400", "morbidelli_cx100", "morbidelli_n100",
    "stefani_kd", "sergiani_gs120", "varie_osama", "dmc60_rcs135",
    "dmc90_xrt135", "superfici", "action_e",
)

STATE_BY_EVENT = {
    "cycle_start": "running",
    "state_on": "running",
    "cycle_end": "idle",
    "idle": "idle",
    "state_idle": "idle",
    "power_on": "idle",
    "alarm": "down",
    "power_off": "down",
    "state_off": "down",
}

REASON_CATEGORY = {
    "setup": "setup_adjustment",
    "breakdown": "breakdown",
    "waiting_material": "material_starvation",
    "tool_change": "tooling_stop",
    "no_operator": "staffing_loss",
    "quality_issue": "quality_stop",
    "no_job": "no_demand",
    "unknown": "unclassified_downtime",
}

LOSS_META = {
    "planned_stop": ("Planned stop", "Scheduled exclusion", "high"),
    "running": ("Running", "Machine state", "medium"),
    "minor_stop": ("Minor stops", "Machine state", "medium"),
    "unclassified_idle": ("Unclassified idle", "Machine state", "medium"),
    "unclassified_downtime": ("Unclassified downtime", "Machine state", "medium"),
    "breakdown": ("Breakdowns", "Downtime record", "high"),
    "setup_adjustment": ("Setup and adjustment", "Downtime record", "high"),
    "material_starvation": ("Waiting for material", "Downtime record", "high"),
    "tooling_stop": ("Tooling stops", "Downtime record", "high"),
    "staffing_loss": ("Waiting for operator", "Downtime record", "high"),
    "quality_stop": ("Quality stops", "Downtime record", "high"),
    "no_demand": ("No released work", "Downtime record", "high"),
    "telemetry_unknown": ("Telemetry unknown", "Evidence gap", "low"),
    "speed_loss": ("Reduced speed", "Calibrated cycle model", "medium"),
    "quality_loss": ("Rejected output", "Complete quality disposition", "high"),
}

ACTION_BY_CATEGORY = {
    "breakdown": "Review the dominant failure mode, repair history, and preventive task.",
    "setup_adjustment": "Separate internal and external setup work, then trial the longest repeatable step.",
    "material_starvation": "Trace the shortage to release, staging, transport, or upstream completion.",
    "tooling_stop": "Check tool-life evidence, spare readiness, presetting, and replacement timing.",
    "staffing_loss": "Compare the verified labor calendar with released station demand.",
    "quality_stop": "Contain affected parts and confirm the process, program, material, and tool cause.",
    "no_demand": "Review release timing and upstream flow before adding machine capacity.",
    "minor_stop": "Capture repeated short-stop causes before changing thresholds or equipment.",
    "unclassified_idle": "Classify the longest idle periods with the shift supervisor.",
    "unclassified_downtime": "Assign a reviewed downtime reason before using this loss for optimization.",
    "speed_loss": "Compare actual cycles with the active model by product family and setup state.",
    "quality_loss": "Trace rejected output to machine, material, program, tool, and operator evidence.",
    "telemetry_unknown": "Restore continuous state evidence before using loss totals for decisions.",
}


def _dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _seconds(start: datetime, end: datetime) -> int:
    return max(0, round((end - start).total_seconds()))


def _clock(value: str) -> time:
    return time.fromisoformat(value)


def _calendar_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT weekday,start_time,end_time,timezone,source,verified
           FROM work_calendar_windows
           WHERE resource_type='factory' AND resource_key='factory' AND active=1
           ORDER BY verified DESC,weekday,start_time,end_time"""
    ).fetchall()


def _calendar_intervals(rows: list[sqlite3.Row], local_start: date,
                        local_end: date) -> list[dict]:
    intervals = []
    cursor = local_start
    while cursor <= local_end:
        for row in rows:
            if cursor.weekday() != int(row["weekday"]):
                continue
            try:
                zone = ZoneInfo(row["timezone"])
            except ZoneInfoNotFoundError:
                continue
            start_local = datetime.combine(cursor, _clock(row["start_time"]), zone)
            end_local = datetime.combine(cursor, _clock(row["end_time"]), zone)
            if end_local <= start_local:
                end_local += timedelta(days=1)
            intervals.append({
                "start": start_local.astimezone(timezone.utc),
                "end": end_local.astimezone(timezone.utc),
                "anchor": cursor,
                "timezone": row["timezone"],
                "source": row["source"],
                "verified": bool(row["verified"]),
                "label": f"{row['start_time']}-{row['end_time']}",
            })
        cursor += timedelta(days=1)
    return sorted(intervals, key=lambda item: item["start"])


def resolve_window(conn: sqlite3.Connection, now: Optional[datetime] = None,
                   local_date: Optional[str] = None) -> dict:
    """Resolve the active, most recent, or explicitly dated factory shift."""
    now = _dt(now or datetime.now(timezone.utc))
    rows = _calendar_rows(conn)
    if not rows:
        start = now - timedelta(hours=8)
        return {
            "shift_key": f"{start.date().isoformat()}:rolling-8h@UTC",
            "label": "Rolling 8 hours", "timezone": "UTC", "local_date": start.date().isoformat(),
            "source": "calendar_missing", "verified": False, "active": True,
            "window_start": _iso(start), "window_end": _iso(now),
            "scheduled_end": _iso(now),
            "intervals": [{"start": _iso(start), "end": _iso(now)}],
        }

    timezone_name = rows[0]["timezone"]
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        zone, timezone_name = timezone.utc, "UTC"

    if local_date:
        try:
            requested = date.fromisoformat(local_date)
        except ValueError as error:
            raise ValueError("date must use YYYY-MM-DD") from error
        matches = [item for item in _calendar_intervals(rows, requested, requested)
                   if item["anchor"] == requested]
        if not matches:
            raise ValueError(f"No factory calendar window exists for {local_date}")
        selected = matches
        scheduled_end = selected[-1]["end"]
        active = any(item["start"] <= now < item["end"] for item in selected)
        window_end = min(now, selected[-1]["end"]) if active else selected[-1]["end"]
        selected = [{**item, "end": min(item["end"], window_end)} for item in selected
                    if item["start"] < window_end]
    else:
        local_now = now.astimezone(zone)
        candidates = _calendar_intervals(
            rows, local_now.date() - timedelta(days=8), local_now.date() + timedelta(days=1)
        )
        active_items = [item for item in candidates if item["start"] <= now < item["end"]]
        if active_items:
            selected = active_items
            scheduled_end = selected[-1]["end"]
            active = True
            window_end = now
            selected = [{**item, "end": now} for item in selected]
        else:
            completed = [item for item in candidates if item["end"] <= now]
            if not completed:
                raise ValueError("No completed or active factory calendar window was found")
            selected = [completed[-1]]
            scheduled_end = selected[-1]["end"]
            active = False
            window_end = selected[-1]["end"]

    first, last = selected[0], selected[-1]
    verified = all(item["verified"] for item in selected)
    label = " + ".join(dict.fromkeys(item["label"] for item in selected))
    anchor = first["anchor"].isoformat()
    return {
        "shift_key": f"{anchor}:{label}@{first['timezone']}",
        "label": label, "timezone": first["timezone"], "local_date": anchor,
        "source": "verified_calendar" if verified else "calendar_assumption",
        "verified": verified, "active": active,
        "window_start": _iso(first["start"]), "window_end": _iso(window_end),
        "scheduled_end": _iso(scheduled_end),
        "intervals": [{"start": _iso(item["start"]), "end": _iso(item["end"])}
                      for item in selected],
    }


def _overlap(start: datetime, end: datetime, interval: dict) -> bool:
    return _dt(interval["start"]) < end and _dt(interval["end"]) > start


def _state_segments(conn: sqlite3.Connection, machine_id: int,
                    start: datetime, end: datetime) -> tuple[list[dict], int]:
    event_types = tuple(STATE_BY_EVENT)
    placeholders = ",".join("?" for _ in event_types)
    prior = conn.execute(
        f"""SELECT id,event_type,ts FROM machine_events
            WHERE machine_id=? AND event_type IN ({placeholders}) AND ts<=?
            ORDER BY ts DESC,id DESC LIMIT 1""",
        (machine_id, *event_types, _iso(start)),
    ).fetchone()
    rows = conn.execute(
        f"""SELECT id,event_type,ts FROM machine_events
            WHERE machine_id=? AND event_type IN ({placeholders}) AND ts>? AND ts<?
            ORDER BY ts,id""",
        (machine_id, *event_types, _iso(start), _iso(end)),
    ).fetchall()
    state = None
    source_event = None
    state_started = start
    carry_accepted = False
    if prior:
        prior_ts = _dt(prior["ts"])
        if _seconds(prior_ts, start) <= CARRY_IN_MAX_AGE_S:
            carry_accepted = True
            state = STATE_BY_EVENT[prior["event_type"]]
            source_event = prior["event_type"]
            state_started = prior_ts
    segments = []
    cursor = start
    for row in rows:
        point = max(start, min(end, _dt(row["ts"])))
        if point > cursor:
            segments.append({
                "start": cursor, "end": point, "state": state,
                "source_event": source_event, "state_started": state_started,
            })
        new_state = STATE_BY_EVENT[row["event_type"]]
        if new_state != state:
            state_started = point
        state = new_state
        source_event = row["event_type"]
        cursor = point
    if cursor < end:
        segments.append({
            "start": cursor, "end": end, "state": state,
            "source_event": source_event, "state_started": state_started,
        })

    merged = []
    for segment in segments:
        if (merged and merged[-1]["state"] == segment["state"]
                and merged[-1]["end"] == segment["start"]):
            merged[-1]["end"] = segment["end"]
            merged[-1]["source_event"] = segment["source_event"] or merged[-1]["source_event"]
        else:
            merged.append(segment)
    return merged, len(rows) + int(carry_accepted)


def _overlays(conn: sqlite3.Connection, machine_id: int, machine_key: str,
              start: datetime, end: datetime) -> tuple[list[dict], list[dict]]:
    unavailability = [dict(row) for row in conn.execute(
        """SELECT starts_at start,ends_at end,reason,source
           FROM resource_unavailability
           WHERE ((resource_type='factory' AND resource_key='factory')
              OR (resource_type='machine' AND resource_key=?))
             AND starts_at<? AND ends_at>?""",
        (machine_key, _iso(end), _iso(start)),
    ).fetchall()]
    downtime = [dict(row) for row in conn.execute(
        """SELECT de.started_at start,COALESCE(de.ended_at,?) end,
                  COALESCE(dr.code,'unknown') reason_code,
                  COALESCE(dr.label,'Unknown') reason_label
           FROM downtime_events de LEFT JOIN downtime_reasons dr ON dr.id=de.reason_id
           WHERE de.machine_id=? AND de.started_at<? AND COALESCE(de.ended_at,?)>?""",
        (_iso(end), machine_id, _iso(end), _iso(end), _iso(start)),
    ).fetchall()]
    return unavailability, downtime


def _base_category(segment: dict) -> tuple[str, str, str]:
    state = segment["state"]
    if state == "running":
        return "running", f"machine_event:{segment['source_event']}", "medium"
    if state == "idle":
        duration = _seconds(segment["state_started"], segment["end"])
        category = "minor_stop" if duration <= MINOR_STOP_THRESHOLD_S else "unclassified_idle"
        return category, f"machine_event:{segment['source_event']}", "medium"
    if state == "down":
        return "unclassified_downtime", f"machine_event:{segment['source_event']}", "medium"
    return "telemetry_unknown", "no_state_evidence", "low"


def _partition(conn: sqlite3.Connection, machine: dict, intervals: list[dict]) -> tuple[list[dict], int]:
    pieces = []
    event_count = 0
    for interval in intervals:
        start, end = _dt(interval["start"]), _dt(interval["end"])
        base_segments, events = _state_segments(conn, machine["id"], start, end)
        event_count += events
        unavailability, downtime = _overlays(
            conn, machine["id"], machine["machine_key"], start, end
        )
        for base in base_segments:
            boundaries = {base["start"], base["end"]}
            for overlay in [*unavailability, *downtime]:
                overlay_start, overlay_end = _dt(overlay["start"]), _dt(overlay["end"])
                if overlay_start < base["end"] and overlay_end > base["start"]:
                    boundaries.add(max(base["start"], overlay_start))
                    boundaries.add(min(base["end"], overlay_end))
            ordered = sorted(boundaries)
            for left, right in zip(ordered, ordered[1:]):
                if right <= left:
                    continue
                planned = next((item for item in unavailability
                                if _overlap(left, right, item)), None)
                active_downtime = [item for item in downtime if _overlap(left, right, item)]
                if planned:
                    category, source, confidence = (
                        "planned_stop", f"resource_unavailability:{planned['reason']}", "high"
                    )
                elif active_downtime:
                    chosen = sorted(
                        active_downtime, key=lambda item: item["reason_code"] == "unknown"
                    )[0]
                    category = REASON_CATEGORY.get(
                        chosen["reason_code"], "unclassified_downtime"
                    )
                    source = f"downtime:{chosen['reason_code']}"
                    confidence = "high" if chosen["reason_code"] != "unknown" else "medium"
                else:
                    category, source, confidence = _base_category(base)
                pieces.append({
                    "start": left, "end": right, "category": category,
                    "source": source, "confidence": confidence,
                })

    merged = []
    for piece in pieces:
        if (merged and merged[-1]["category"] == piece["category"]
                and merged[-1]["source"] == piece["source"]
                and merged[-1]["end"] == piece["start"]):
            merged[-1]["end"] = piece["end"]
        else:
            merged.append(piece)
    return merged, event_count


def _inside(ts: datetime, intervals: list[dict]) -> bool:
    return any(_dt(interval["start"]) <= ts <= _dt(interval["end"])
               for interval in intervals)


def _cycle_and_quality(conn: sqlite3.Connection, machine: dict, intervals: list[dict],
                       pieces: list[dict], running_s: int) -> dict:
    start = min(_dt(item["start"]) for item in intervals)
    end = max(_dt(item["end"]) for item in intervals)
    def in_production(point: datetime) -> bool:
        return any(piece["start"] <= point <= piece["end"]
                   and piece["category"] != "planned_stop" for piece in pieces)

    rows = [dict(row) for row in conn.execute(
        """SELECT me.id,me.part_id,me.ts,p.*
           FROM machine_events me LEFT JOIN parts p ON p.id=me.part_id
           WHERE me.machine_id=? AND me.event_type='cycle_end' AND me.ts>=? AND me.ts<=?
           ORDER BY me.ts,me.id""",
        (machine["id"], _iso(start), _iso(end)),
    ).fetchall() if _inside(_dt(row["ts"]), intervals) and in_production(_dt(row["ts"]))]
    cycle_count = len(rows)
    linked_count = sum(1 for row in rows if row["part_id"] is not None)
    model = cycle_time.active_model(conn, machine["machine_key"])
    model_eligible = bool(model and model["confidence"] in {"medium", "high"})
    predictions = []
    if model_eligible and cycle_count and linked_count == cycle_count:
        for row in rows:
            features = cycle_time.extract_features(row, machine["machine_key"])
            value = cycle_time.estimate_from_coefficients(features, model["coefficients"])
            if value is None:
                predictions = []
                break
            predictions.append(float(value))
    ideal_s = sum(predictions) if len(predictions) == cycle_count and cycle_count else None
    model_consistent = ideal_s is not None and running_s > 0 and ideal_s <= running_s * 1.05
    performance = min(1.0, ideal_s / running_s) if model_consistent else None
    speed_loss_s = max(0.0, running_s - ideal_s) if model_consistent else None

    checks = [dict(row) for row in conn.execute(
        """SELECT part_id,result,ts FROM quality_checks
           WHERE machine_id=? AND ts>=? AND ts<=? ORDER BY ts,id""",
        (machine["id"], _iso(start), _iso(end)),
    ).fetchall() if _inside(_dt(row["ts"]), intervals) and in_production(_dt(row["ts"]))]
    good = sum(1 for row in checks if row["result"] == "pass")
    bad = sum(1 for row in checks if row["result"] in {"fail", "rework"})
    cycle_parts = Counter(row["part_id"] for row in rows)
    check_parts = Counter(row["part_id"] for row in checks)
    complete_disposition = (
        cycle_count > 0 and linked_count == cycle_count
        and len(checks) == cycle_count and good + bad == cycle_count
        and None not in check_parts and check_parts == cycle_parts
    )
    quality = good / cycle_count if complete_disposition else None
    quality_loss_s = ideal_s * (1 - quality) if model_consistent and quality is not None else None
    productive_s = ideal_s - quality_loss_s if quality_loss_s is not None else None
    return {
        "completed_cycles": cycle_count, "linked_cycles": linked_count,
        "quality_checks": len(checks), "good_units": good if complete_disposition else None,
        "bad_units": bad if complete_disposition else None,
        "model_version": model["version"] if model_eligible else None,
        "model_confidence": model["confidence"] if model_eligible else None,
        "ideal_time_s": round(ideal_s, 1) if ideal_s is not None else None,
        "performance": round(performance, 4) if performance is not None else None,
        "quality": round(quality, 4) if quality is not None else None,
        "speed_loss_s": round(speed_loss_s, 1) if speed_loss_s is not None else None,
        "quality_loss_s": round(quality_loss_s, 1) if quality_loss_s is not None else None,
        "fully_productive_s": round(productive_s, 1) if productive_s is not None else None,
        "performance_source": (
            "active_cycle_model" if model_consistent else
            "model_inconsistent" if ideal_s is not None else
            "incomplete_part_linkage" if model_eligible and cycle_count else
            "active_model_missing"
        ),
        "quality_source": "complete_disposition" if complete_disposition else "incomplete_disposition",
    }


def _loss_rows(totals: dict[str, int], planned_production_s: int,
               occurrences: dict[str, int], sources: dict[str, set[str]],
               confidences: dict[str, set[str]]) -> list[dict]:
    result = []
    for category, seconds in totals.items():
        if not seconds:
            continue
        label, default_source, default_confidence = LOSS_META[category]
        confidence_set = confidences.get(category, set())
        confidence = "low" if "low" in confidence_set else (
            "medium" if "medium" in confidence_set else default_confidence
        )
        result.append({
            "category": category, "label": label, "seconds": seconds,
            "minutes": round(seconds / 60, 1),
            "share_of_planned": round(seconds / planned_production_s, 4)
            if planned_production_s else None,
            "occurrences": occurrences.get(category, 0),
            "source": sorted(sources.get(category, {default_source})),
            "confidence": confidence,
            "action": ACTION_BY_CATEGORY.get(category),
        })
    return sorted(result, key=lambda item: (-item["seconds"], item["category"]))


def analyze_machine(conn: sqlite3.Connection, machine: dict, shift: dict) -> dict:
    intervals = shift["intervals"]
    pieces, event_count = _partition(conn, machine, intervals)
    totals: dict[str, int] = defaultdict(int)
    occurrences: dict[str, int] = defaultdict(int)
    sources: dict[str, set[str]] = defaultdict(set)
    confidences: dict[str, set[str]] = defaultdict(set)
    previous = None
    for piece in pieces:
        duration = _seconds(piece["start"], piece["end"])
        totals[piece["category"]] += duration
        sources[piece["category"]].add(piece["source"])
        confidences[piece["category"]].add(piece["confidence"])
        if piece["category"] != previous:
            occurrences[piece["category"]] += 1
        previous = piece["category"]

    scheduled_s = sum(_seconds(_dt(item["start"]), _dt(item["end"])) for item in intervals)
    planned_stop_s = totals.get("planned_stop", 0)
    planned_production_s = max(0, scheduled_s - planned_stop_s)
    running_s = totals.get("running", 0)
    unknown_s = totals.get("telemetry_unknown", 0)
    measured_loss_s = sum(
        value for key, value in totals.items()
        if key not in {"running", "planned_stop", "telemetry_unknown"}
    )
    coverage = (planned_production_s - unknown_s) / planned_production_s if planned_production_s else 0.0
    availability = running_s / planned_production_s if planned_production_s and coverage >= 0.9 else None
    output = _cycle_and_quality(conn, machine, intervals, pieces, running_s)
    oee = None
    if availability is not None and output["performance"] is not None and output["quality"] is not None:
        oee = availability * output["performance"] * output["quality"]

    timeline_total = sum(totals.values())
    output_reconciled = False
    if output["fully_productive_s"] is not None:
        output_total = (
            measured_loss_s + unknown_s + output["speed_loss_s"]
            + output["quality_loss_s"] + output["fully_productive_s"]
        )
        output_reconciled = abs(output_total - planned_production_s) <= 1.0

    gates = [
        {"key": "calendar", "passed": bool(shift["verified"]),
         "detail": shift["source"]},
        {"key": "telemetry", "passed": coverage >= 0.9,
         "detail": f"{round(coverage * 100)}% of planned production time classified"},
        {"key": "cycle_model", "passed": output["performance"] is not None,
         "detail": output["performance_source"]},
        {"key": "quality", "passed": output["quality"] is not None,
         "detail": output["quality_source"]},
        {"key": "reconciliation", "passed": timeline_total == scheduled_s,
         "detail": f"{timeline_total} of {scheduled_s} scheduled seconds accounted"},
    ]
    decision_ready = all(item["passed"] for item in gates) and oee is not None and output_reconciled

    losses = _loss_rows(totals, planned_production_s, occurrences, sources, confidences)
    if output["speed_loss_s"] is not None and output["speed_loss_s"] > 0:
        losses += _loss_rows(
            {"speed_loss": round(output["speed_loss_s"])}, planned_production_s,
            {"speed_loss": output["completed_cycles"]},
            {"speed_loss": {"active_cycle_model"}},
            {"speed_loss": {"medium"}},
        )
    if output["quality_loss_s"] is not None and output["quality_loss_s"] > 0:
        losses += _loss_rows(
            {"quality_loss": round(output["quality_loss_s"])}, planned_production_s,
            {"quality_loss": output["bad_units"] or 0},
            {"quality_loss": {"quality_checks"}},
            {"quality_loss": {"high"}},
        )
    losses.sort(key=lambda item: (-item["seconds"], item["category"]))
    actionable = [item for item in losses if item["category"] not in {
        "running", "planned_stop", "telemetry_unknown"
    }]

    fingerprint = {
        "method": METHOD_VERSION, "machine": machine["machine_key"],
        "shift": shift["shift_key"], "totals": dict(totals), "output": output,
    }
    return {
        "machine_key": machine["machine_key"], "machine_name": machine["name"],
        "status": "decision_ready" if decision_ready else (
            "provisional" if event_count else "evidence_gap"
        ),
        "decision_ready": decision_ready, "event_count": event_count,
        "scheduled_s": scheduled_s, "planned_stop_s": planned_stop_s,
        "planned_production_s": planned_production_s,
        "running_s": running_s, "measured_availability_loss_s": measured_loss_s,
        "telemetry_unknown_s": unknown_s, "telemetry_coverage": round(coverage, 4),
        "availability": round(availability, 4) if availability is not None else None,
        "performance": output["performance"], "quality": output["quality"],
        "oee": round(oee, 4) if oee is not None else None,
        "completed_cycles": output["completed_cycles"],
        "linked_cycles": output["linked_cycles"], "quality_checks": output["quality_checks"],
        "good_units": output["good_units"], "bad_units": output["bad_units"],
        "model_version": output["model_version"],
        "model_confidence": output["model_confidence"],
        "losses": losses, "top_measured_loss": actionable[0] if actionable else None,
        "waterfall": {
            "scheduled_s": scheduled_s, "planned_stop_s": planned_stop_s,
            "planned_production_s": planned_production_s,
            "availability_loss_s": measured_loss_s, "telemetry_unknown_s": unknown_s,
            "operating_time_s": running_s,
            "speed_loss_s": output["speed_loss_s"],
            "quality_loss_s": output["quality_loss_s"],
            "fully_productive_s": output["fully_productive_s"],
            "unquantified_running_s": running_s if output["performance"] is None else 0,
            "unquantified_quality_s": output["ideal_time_s"]
            if output["performance"] is not None and output["quality"] is None else 0,
        },
        "gates": gates,
        "reconciliation": {
            "timeline": timeline_total == scheduled_s,
            "output_waterfall": output_reconciled,
        },
        "evidence_sha256": hashlib.sha256(
            json.dumps(fingerprint, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }


def build(conn: sqlite3.Connection, now: Optional[datetime] = None,
          local_date: Optional[str] = None, machine_key: Optional[str] = None) -> dict:
    now = _dt(now or datetime.now(timezone.utc))
    shift = resolve_window(conn, now, local_date)
    placeholders = ",".join("?" for _ in PRODUCTION_MACHINE_KEYS)
    params: list[object] = list(PRODUCTION_MACHINE_KEYS)
    where = f"active=1 AND machine_key IN ({placeholders})"
    if machine_key:
        where += " AND machine_key=?"
        params.append(machine_key)
    machines = [dict(row) for row in conn.execute(
        f"SELECT id,machine_key,name,type FROM machines WHERE {where} ORDER BY id", params
    ).fetchall()]
    if machine_key and not machines:
        raise KeyError(f"Production machine '{machine_key}' not found")
    reports = [analyze_machine(conn, machine, shift) for machine in machines]

    category_totals: dict[str, int] = defaultdict(int)
    category_machines: dict[str, set[str]] = defaultdict(set)
    for report in reports:
        for loss in report["losses"]:
            if loss["category"] in {"running", "planned_stop"}:
                continue
            category_totals[loss["category"]] += int(loss["seconds"])
            category_machines[loss["category"]].add(report["machine_key"])
    total_loss_s = sum(category_totals.values())
    pareto = []
    cumulative = 0
    for category, seconds in sorted(category_totals.items(), key=lambda item: (-item[1], item[0])):
        cumulative += seconds
        label = LOSS_META[category][0]
        pareto.append({
            "category": category, "label": label, "seconds": seconds,
            "machine_minutes": round(seconds / 60, 1),
            "share": round(seconds / total_loss_s, 4) if total_loss_s else 0.0,
            "cumulative_share": round(cumulative / total_loss_s, 4) if total_loss_s else 0.0,
            "machine_count": len(category_machines[category]),
            "action": ACTION_BY_CATEGORY.get(category),
        })
    measured = [item for item in pareto if item["category"] != "telemetry_unknown"]
    unknown = next((item for item in pareto if item["category"] == "telemetry_unknown"), None)
    top_measured = measured[0] if measured else None
    attention = unknown if unknown and (not top_measured or unknown["seconds"] > top_measured["seconds"]) else top_measured

    ready = [report for report in reports if report["decision_ready"]]
    ready_planned = sum(report["planned_production_s"] for report in ready)
    ready_productive = sum(
        report["waterfall"]["fully_productive_s"] or 0 for report in ready
    )
    recommendation = None
    if attention:
        recommendation = {
            "category": attention["category"], "title": attention["label"],
            "machine_minutes": attention["machine_minutes"],
            "action": attention["action"],
            "basis": "largest_factory_machine_time_loss",
            "decision_ready": attention["category"] != "telemetry_unknown",
        }
    return {
        "generated_at": _iso(now), "method_version": METHOD_VERSION,
        "shift": shift,
        "summary": {
            "production_machines": len(reports),
            "reporting_machines": sum(1 for report in reports if report["event_count"]),
            "decision_ready_machines": len(ready),
            "scheduled_machine_hours": round(sum(report["scheduled_s"] for report in reports) / 3600, 2),
            "planned_production_machine_hours": round(
                sum(report["planned_production_s"] for report in reports) / 3600, 2
            ),
            "classified_coverage": round(
                sum(report["planned_production_s"] - report["telemetry_unknown_s"] for report in reports)
                / max(1, sum(report["planned_production_s"] for report in reports)), 4
            ),
            "decision_ready_oee": round(ready_productive / ready_planned, 4)
            if ready_planned else None,
        },
        "recommendation": recommendation, "pareto": pareto, "machines": reports,
        "guardrail": (
            "Machine minutes are additive equipment exposure, not wall-clock factory delay. "
            "OEE is decision-ready only with a verified calendar, at least 90% state coverage, "
            "an active cycle model, complete quality disposition, and a reconciled waterfall."
        ),
    }
