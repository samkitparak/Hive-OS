"""Evidence-ranked incident diagnosis with operator-confirmed ground truth."""

import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Optional


CAUSES = {
    "reliability_fault": {"label": "Machine reliability fault", "domain": "maintenance"},
    "maintenance_overdue": {"label": "Overdue or incomplete maintenance", "domain": "maintenance"},
    "tooling_condition": {"label": "Tooling wear or condition", "domain": "tooling"},
    "setup_changeover": {"label": "Setup or changeover loss", "domain": "method"},
    "program_or_recipe": {"label": "Program, recipe, or parameter issue", "domain": "method"},
    "material_unavailable": {"label": "Material unavailable or late", "domain": "material"},
    "material_quality": {"label": "Material quality problem", "domain": "material"},
    "staffing_constraint": {"label": "Operator or staffing constraint", "domain": "labor"},
    "quality_process": {"label": "Process quality instability", "domain": "quality"},
    "planning_gap": {"label": "Planning or job-release gap", "domain": "planning"},
    "capacity_overload": {"label": "Capacity overload", "domain": "flow"},
    "upstream_starvation": {"label": "Upstream starvation", "domain": "flow"},
    "power_or_utility": {"label": "Power or utility disturbance", "domain": "utility"},
    "unknown": {"label": "Cause not yet classified", "domain": "unknown"},
}

DEFAULT_PRIORS = {
    "alarm": {"reliability_fault": .45, "tooling_condition": .12, "maintenance_overdue": .12,
              "power_or_utility": .08, "program_or_recipe": .07, "unknown": .16},
    "downtime": {"reliability_fault": .16, "material_unavailable": .15, "setup_changeover": .14,
                 "staffing_constraint": .10, "planning_gap": .10, "tooling_condition": .09,
                 "upstream_starvation": .08, "maintenance_overdue": .07,
                 "power_or_utility": .04, "unknown": .07},
    "quality": {"quality_process": .25, "tooling_condition": .17, "program_or_recipe": .17,
                "material_quality": .16, "setup_changeover": .08, "reliability_fault": .05,
                "staffing_constraint": .04, "unknown": .08},
}


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _load(value, fallback):
    if value is None:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _dict(row) -> dict:
    result = dict(row)
    for key, fallback in (("source_json", {}), ("features_json", {}),
                          ("evidence_json", []), ("contradictions_json", []),
                          ("data_gaps_json", []), ("payload_json", {})):
        if key in result:
            result[key.removesuffix("_json")] = _load(result.pop(key), fallback)
    return result


def _source_incidents(conn: sqlite3.Connection, start: str, end: str) -> list[dict]:
    incidents = []
    for row in conn.execute(
        """SELECT de.id,de.machine_id,de.event_id,de.started_at occurred_at,de.ended_at,
                  dr.code reason_code,dr.label reason_label,dr.category,de.notes,
                  m.machine_key,m.name machine_name
           FROM downtime_events de LEFT JOIN downtime_reasons dr ON dr.id=de.reason_id
           LEFT JOIN machines m ON m.id=de.machine_id
           WHERE de.started_at>=? AND de.started_at<=?""", (start, end)
    ).fetchall():
        item = dict(row)
        item.update({"source_type": "downtime", "incident_type": "downtime",
                     "source_id": item.pop("id"), "part_id": None,
                     "symptom_code": item.get("reason_code") or "unknown_downtime",
                     "symptom_label": item.get("reason_label") or "Unclassified downtime"})
        duration = (_parse(item["ended_at"]) - _parse(item["occurred_at"])).total_seconds() if item.get("ended_at") else 0
        item["severity"] = "high" if duration >= 3600 or item.get("reason_code") == "breakdown" else "medium"
        incidents.append(item)
    for row in conn.execute(
        """SELECT me.id,me.machine_id,me.part_id,me.ts occurred_at,me.raw_payload,me.cnc_file,
                  m.machine_key,m.name machine_name
           FROM machine_events me JOIN machines m ON m.id=me.machine_id
           WHERE me.event_type='alarm' AND me.ts>=? AND me.ts<=?""", (start, end)
    ).fetchall():
        item = dict(row)
        raw = _load(item.get("raw_payload"), {})
        code = str(raw.get("alarm_code") or raw.get("code") or "unclassified")
        message = raw.get("message") or raw.get("msg") or raw.get("raw")
        item.update({"source_type": "machine_event", "incident_type": "alarm",
                     "source_id": item.pop("id"), "ended_at": None,
                     "symptom_code": f"alarm:{code}",
                     "symptom_label": f"Alarm {code}" + (f": {message}" if message else ""),
                     "severity": "high", "alarm_code": code, "alarm_message": message})
        incidents.append(item)
    for row in conn.execute(
        """SELECT qc.id,qc.machine_id,qc.part_id,qc.ts occurred_at,qc.result,qc.notes,qc.inspector,
                  dt.code defect_code,dt.label defect_label,dt.process defect_process,
                  m.machine_key,m.name machine_name,p.material,p.cnc_file_back,p.cnc_file_front
           FROM quality_checks qc LEFT JOIN defect_types dt ON dt.id=qc.defect_type_id
           LEFT JOIN machines m ON m.id=qc.machine_id LEFT JOIN parts p ON p.id=qc.part_id
           WHERE qc.result IN ('fail','rework') AND qc.ts>=? AND qc.ts<=?""", (start, end)
    ).fetchall():
        item = dict(row)
        item.update({"source_type": "quality_check", "incident_type": "quality",
                     "source_id": item.pop("id"), "ended_at": None,
                     "symptom_code": item.get("defect_code") or "unclassified_defect",
                     "symptom_label": item.get("defect_label") or "Unclassified quality failure",
                     "severity": "high" if item.get("result") == "fail" else "medium"})
        incidents.append(item)
    return incidents


def _utility_anomaly(conn: sqlite3.Connection, machine_id: int, occurred: datetime) -> Optional[dict]:
    rows = conn.execute(
        """SELECT signal_key,value_num,source_ts FROM telemetry_samples
           WHERE machine_id=? AND quality='good' AND value_num IS NOT NULL
             AND source_ts>=? AND source_ts<=?
             AND (lower(signal_key) LIKE '%power%' OR lower(signal_key) LIKE '%voltage%'
                  OR lower(signal_key) LIKE '%frequency%' OR lower(signal_key) LIKE '%current%')""",
        (machine_id, _iso(occurred - timedelta(hours=24)), _iso(occurred + timedelta(minutes=15))),
    ).fetchall()
    by_signal = {}
    for row in rows:
        by_signal.setdefault(row["signal_key"], []).append((_parse(row["source_ts"]), float(row["value_num"])))
    strongest = None
    for signal, samples in by_signal.items():
        baseline = [value for ts, value in samples if ts < occurred - timedelta(minutes=15)]
        incident = [value for ts, value in samples if occurred - timedelta(minutes=15) <= ts <= occurred + timedelta(minutes=15)]
        if len(baseline) < 8 or not incident:
            continue
        center = median(baseline)
        mad = median([abs(value - center) for value in baseline])
        scale = max(1.4826 * mad, abs(center) * .05, 1e-6)
        score = abs(median(incident) - center) / scale
        candidate = {"signal_key": signal, "robust_z": round(score, 3),
                     "baseline_median": round(center, 4), "incident_median": round(median(incident), 4),
                     "baseline_samples": len(baseline), "incident_samples": len(incident)}
        if score >= 3.5 and (strongest is None or score > strongest["robust_z"]):
            strongest = candidate
    return strongest


def _context(conn: sqlite3.Connection, case: dict) -> tuple[dict, list[str]]:
    source = case["source"]
    occurred = _parse(case["occurred_at"])
    start, end = _iso(occurred - timedelta(hours=1)), _iso(occurred + timedelta(hours=1))
    machine_id = case.get("machine_id")
    gaps = []
    features = {"reason_code": source.get("reason_code"), "defect_code": source.get("defect_code"),
                "alarm_code": source.get("alarm_code"), "incident_type": case["incident_type"]}
    if not machine_id:
        gaps.append("Incident is not linked to a machine")
        return features, gaps

    events = conn.execute(
        """SELECT event_type,part_id,cnc_file,ts FROM machine_events
           WHERE machine_id=? AND ts>=? AND ts<=? ORDER BY ts,id""", (machine_id, start, end)
    ).fetchall()
    features["alarm_count"] = sum(row["event_type"] == "alarm" for row in events)
    features["cycle_end_count"] = sum(row["event_type"] == "cycle_end" for row in events)
    before = [row for row in events if _parse(row["ts"]) <= occurred and row["event_type"] in ("cycle_start", "cycle_end")]
    features["incomplete_cycle"] = bool(before and before[-1]["event_type"] == "cycle_start")
    nearest = conn.execute(
        """SELECT part_id,cnc_file FROM machine_events WHERE machine_id=? AND ts<=?
           ORDER BY ts DESC,id DESC LIMIT 1""", (machine_id, _iso(occurred))
    ).fetchone()
    part_id = case.get("part_id") or (nearest["part_id"] if nearest else None)
    program = source.get("cnc_file") or (nearest["cnc_file"] if nearest else None)
    features["part_id"] = part_id
    features["program"] = program
    if program:
        prior_count = conn.execute(
            """SELECT COUNT(*) count FROM machine_events WHERE machine_id=? AND cnc_file=?
               AND ts>=? AND ts<?""",
            (machine_id, program, _iso(occurred - timedelta(days=7)), _iso(occurred)),
        ).fetchone()["count"]
        features["program_prior_count"] = int(prior_count)
        features["new_program"] = prior_count < 2
    else:
        features["new_program"] = False
        gaps.append("No CNC program or recipe is linked near the incident")
    features["program_changes"] = len({row["cnc_file"] for row in events if row["cnc_file"]})
    if part_id:
        part = conn.execute("SELECT material,part_name FROM parts WHERE id=?", (part_id,)).fetchone()
        features["material"] = part["material"] if part else None
        features["part_name"] = part["part_name"] if part else None
    else:
        gaps.append("No physical part is linked near the incident")

    work = conn.execute(
        """SELECT COUNT(*) count,
                  SUM(CASE WHEN priority IN ('high','urgent') THEN 1 ELSE 0 END) critical
           FROM maintenance_work_orders WHERE machine_id=? AND status IN ('open','in_progress')""",
        (machine_id,),
    ).fetchone()
    features["open_work_orders"] = int(work["count"] or 0)
    features["critical_work_orders"] = int(work["critical"] or 0)
    features["triggered_conditions"] = conn.execute(
        """SELECT COUNT(*) FROM maintenance_condition_signals WHERE machine_id=? AND triggered=1
           AND observed_at>=? AND observed_at<=?""",
        (machine_id, _iso(occurred - timedelta(hours=24)), _iso(occurred + timedelta(hours=1))),
    ).fetchone()[0]
    features["spare_shortages"] = conn.execute(
        """SELECT COUNT(*) FROM maintenance_spare_reservations msr
           JOIN maintenance_work_orders wo ON wo.id=msr.work_order_id
           WHERE wo.machine_id=? AND wo.status IN ('open','in_progress')
             AND msr.required=1 AND msr.status='shortage'""", (machine_id,)
    ).fetchone()[0]
    features["overdue_plans"] = conn.execute(
        """SELECT COUNT(*) FROM maintenance_plans WHERE machine_id=? AND active=1 AND verified=1
           AND interval_days IS NOT NULL AND julianday(?) > julianday(COALESCE(last_completed_at,anchor_at))+interval_days""",
        (machine_id, _iso(occurred)),
    ).fetchone()[0]
    defect_code = source.get("defect_code")
    if defect_code:
        features["similar_defects_8h"] = conn.execute(
            """SELECT COUNT(*) FROM quality_checks qc JOIN defect_types dt ON dt.id=qc.defect_type_id
               WHERE qc.machine_id=? AND dt.code=? AND qc.result IN ('fail','rework') AND qc.ts>=? AND qc.ts<=?""",
            (machine_id, defect_code, _iso(occurred - timedelta(hours=8)), _iso(occurred)),
        ).fetchone()[0]
    else:
        features["similar_defects_8h"] = 0
    features["utility_anomaly"] = _utility_anomaly(conn, machine_id, occurred)
    telemetry_count = conn.execute(
        "SELECT COUNT(*) FROM telemetry_samples WHERE machine_id=? AND source_ts>=? AND source_ts<=?",
        (machine_id, start, end),
    ).fetchone()[0]
    features["telemetry_samples"] = telemetry_count
    if not telemetry_count:
        gaps.append("No commissioned utility or process telemetry exists around the incident")
    if not source.get("reason_code") and case["incident_type"] == "downtime":
        gaps.append("Downtime has no classified reason")
    if not source.get("defect_code") and case["incident_type"] == "quality":
        gaps.append("Quality failure has no defect classification")
    return features, gaps


def _learned_priors(conn: sqlite3.Connection, incident_type: str) -> tuple[dict, int]:
    rows = conn.execute(
        """SELECT actual_cause_code,COUNT(*) count FROM diagnostic_cases
           WHERE status='confirmed' AND incident_type=? AND actual_cause_code IS NOT NULL
           GROUP BY actual_cause_code""", (incident_type,)
    ).fetchall()
    total = sum(int(row["count"]) for row in rows)
    defaults = {cause: DEFAULT_PRIORS.get(incident_type, {}).get(cause, .01) for cause in CAUSES}
    if total < 5:
        return defaults, total
    counts = {row["actual_cause_code"]: int(row["count"]) for row in rows}
    denominator = total + len(CAUSES)
    empirical = {cause: (counts.get(cause, 0) + 1) / denominator for cause in CAUSES}
    return {cause: defaults[cause] * .7 + empirical[cause] * .3 for cause in CAUSES}, total


def _rank(conn: sqlite3.Connection, case: dict, features: dict,
          gaps: list[str]) -> tuple[list[dict], str]:
    priors, training_count = _learned_priors(conn, case["incident_type"])
    evidence = {cause: [] for cause in CAUSES}

    def add(cause, weight, text, source):
        evidence[cause].append({"weight": weight, "text": text, "source": source})

    reason = features.get("reason_code")
    reason_map = {
        "breakdown": ("reliability_fault", .78), "waiting_material": ("material_unavailable", .88),
        "no_operator": ("staffing_constraint", .88), "setup": ("setup_changeover", .82),
        "tool_change": ("tooling_condition", .78), "quality_issue": ("quality_process", .75),
        "no_job": ("planning_gap", .85),
    }
    if reason in reason_map:
        cause, weight = reason_map[reason]
        add(cause, weight, f"Operator downtime reason is '{reason}'", "downtime_reason")
    if case["incident_type"] == "alarm":
        add("reliability_fault", .68, f"Machine emitted {case['symptom_code']}", "machine_alarm")
    defect = features.get("defect_code")
    if defect:
        add("quality_process", .48, f"Quality check classified defect '{defect}'", "quality_check")
        if defect == "material_damage":
            add("material_quality", .55, "Defect classification indicates material damage", "quality_check")
        if defect in ("drilling", "cut_size", "edge_band"):
            add("program_or_recipe", .22, "Defect can be sensitive to program or process parameters", "engineering_prior")
            add("tooling_condition", .20, "Defect can be sensitive to cutting or application tooling", "engineering_prior")
    if features.get("alarm_count", 0) >= 2:
        add("reliability_fault", .24, f"{features['alarm_count']} alarms occurred within the two-hour context", "event_log")
    if features.get("incomplete_cycle"):
        add("reliability_fault", .14, "The latest cycle start had no matching end before the incident", "event_log")
        add("program_or_recipe", .08, "Cycle interruption followed program execution", "event_log")
    if features.get("new_program"):
        add("program_or_recipe", .34, "Program had fewer than two prior events in the preceding seven days", "program_history")
    if features.get("program_changes", 0) >= 2:
        add("setup_changeover", .20, f"{features['program_changes']} programs appeared in the context window", "program_history")
    if features.get("critical_work_orders"):
        add("reliability_fault", .25, f"{features['critical_work_orders']} high-priority maintenance orders are open", "maintenance")
    if features.get("overdue_plans"):
        add("maintenance_overdue", .38, f"{features['overdue_plans']} verified maintenance plans were overdue", "maintenance")
    if features.get("triggered_conditions"):
        add("maintenance_overdue", .30, f"{features['triggered_conditions']} condition thresholds were triggered", "condition_monitoring")
        add("reliability_fault", .18, "Condition evidence indicates abnormal equipment state", "condition_monitoring")
    if features.get("spare_shortages"):
        add("maintenance_overdue", .16, f"{features['spare_shortages']} required maintenance spares are short", "spare_control")
    if features.get("similar_defects_8h", 0) >= 3:
        add("quality_process", .30, f"{features['similar_defects_8h']} similar defects occurred in eight hours", "quality_history")
    if features.get("utility_anomaly"):
        anomaly = features["utility_anomaly"]
        add("power_or_utility", .55, f"{anomaly['signal_key']} shifted {anomaly['robust_z']} robust deviations", "telemetry")
    if features.get("cycle_end_count", 0) >= 8 and reason in ("breakdown", "unknown", None):
        add("capacity_overload", .16, f"{features['cycle_end_count']} cycles completed in the context window", "event_log")

    hypotheses = []
    for cause in CAUSES:
        contradictions = []
        penalty = 0.0
        if cause == "reliability_fault" and not features.get("alarm_count") and case["incident_type"] != "alarm":
            contradictions.append("No machine alarm was recorded in the two-hour context")
            penalty += .06
        if cause == "program_or_recipe" and not features.get("program"):
            contradictions.append("No program or recipe identity is linked")
            penalty += .08
        if cause == "power_or_utility" and not features.get("utility_anomaly"):
            contradictions.append("No statistically unusual commissioned utility signal was found")
            penalty += .05
        score = max(0.0, min(.99, priors.get(cause, .01) * .25 +
                             sum(item["weight"] for item in evidence[cause]) - penalty))
        hypotheses.append({"cause_code": cause, "evidence_score": round(score, 4),
                           "prior_score": round(priors.get(cause, .01), 4),
                           "evidence": evidence[cause], "contradictions": contradictions,
                           "data_gaps": gaps, "training_count": training_count})
    hypotheses.sort(key=lambda item: item["evidence_score"], reverse=True)
    top = hypotheses[0]
    margin = top["evidence_score"] - hypotheses[1]["evidence_score"]
    strongest = max((item["weight"] for item in top["evidence"]), default=0)
    if top["evidence_score"] >= .7 and margin >= .15 and strongest >= .65:
        confidence = "high"
    elif top["evidence_score"] >= .42 and margin >= .06:
        confidence = "medium"
    else:
        confidence = "low"
    return hypotheses[:5], confidence


def _case_event(conn, case_id: int, event_type: str, from_status: Optional[str],
                to_status: str, actor: str, notes: Optional[str], payload: dict,
                now: datetime):
    conn.execute(
        """INSERT INTO diagnostic_case_events
           (case_id,event_type,from_status,to_status,actor,notes,payload_json,ts)
           VALUES (?,?,?,?,?,?,?,?)""",
        (case_id, event_type, from_status, to_status, actor, notes,
         json.dumps(payload, sort_keys=True), _iso(now)),
    )


def _analyze(conn: sqlite3.Connection, case_id: int, now: datetime, actor: str):
    case = _dict(conn.execute(
        "SELECT * FROM diagnostic_cases WHERE id=?", (case_id,)
    ).fetchone())
    features, gaps = _context(conn, case)
    hypotheses, confidence = _rank(conn, case, features, gaps)
    analysis_version = int(case["analysis_version"]) + 1
    for rank, hypothesis in enumerate(hypotheses, start=1):
        conn.execute(
            """INSERT INTO diagnostic_hypotheses
               (case_id,analysis_version,cause_code,rank,evidence_score,prior_score,
                evidence_json,contradictions_json,data_gaps_json,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (case_id, analysis_version, hypothesis["cause_code"], rank,
             hypothesis["evidence_score"], hypothesis["prior_score"],
             json.dumps(hypothesis["evidence"]), json.dumps(hypothesis["contradictions"]),
             json.dumps(hypothesis["data_gaps"]), _iso(now)),
        )
    top = hypotheses[0]
    conn.execute(
        """UPDATE diagnostic_cases SET features_json=?,top_hypothesis_code=?,confidence=?,
           analysis_version=?,version=version+1,updated_at=? WHERE id=?""",
        (json.dumps(features, sort_keys=True), top["cause_code"], confidence,
         analysis_version, _iso(now), case_id),
    )
    _case_event(conn, case_id, "analyzed", case["status"], case["status"], actor, None,
                {"analysis_version": analysis_version, "top_hypothesis": top["cause_code"],
                 "confidence": confidence}, now)


def sync(conn: sqlite3.Connection, lookback_days: int = 30, actor: str = "diagnostic-engine",
         now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    incidents = _source_incidents(conn, _iso(now - timedelta(days=lookback_days)), _iso(now))
    created = refreshed = skipped = 0
    for incident in incidents:
        row = conn.execute(
            "SELECT id,status FROM diagnostic_cases WHERE source_type=? AND source_id=?",
            (incident["source_type"], incident["source_id"]),
        ).fetchone()
        source_json = json.dumps({key: value for key, value in incident.items()
                                  if key not in ("source_type", "source_id", "incident_type",
                                                 "machine_id", "part_id", "occurred_at", "ended_at",
                                                 "severity", "symptom_code", "symptom_label")}, sort_keys=True)
        if row:
            conn.execute(
                """UPDATE diagnostic_cases SET ended_at=?,severity=?,symptom_code=?,symptom_label=?,
                   source_json=?,updated_at=? WHERE id=?""",
                (incident.get("ended_at"), incident["severity"], incident["symptom_code"],
                 incident["symptom_label"], source_json, _iso(now), row["id"]),
            )
            case_id = row["id"]
            if row["status"] != "open":
                skipped += 1
                continue
            refreshed += 1
        else:
            cursor = conn.execute(
                """INSERT INTO diagnostic_cases
                   (case_key,incident_type,source_type,source_id,machine_id,part_id,occurred_at,ended_at,
                    severity,symptom_code,symptom_label,source_json,status,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'open',?,?)""",
                (f"{incident['source_type']}:{incident['source_id']}", incident["incident_type"],
                 incident["source_type"], incident["source_id"], incident.get("machine_id"),
                 incident.get("part_id"), incident["occurred_at"], incident.get("ended_at"),
                 incident["severity"], incident["symptom_code"], incident["symptom_label"],
                 source_json, _iso(now), _iso(now)),
            )
            case_id = cursor.lastrowid
            _case_event(conn, case_id, "created", None, "open", actor, None,
                        {"source_type": incident["source_type"], "source_id": incident["source_id"]}, now)
            created += 1
        _analyze(conn, case_id, now, actor)
    conn.commit()
    result = snapshot(conn, now=now)
    result["sync"] = {"sources_seen": len(incidents), "created": created,
                      "refreshed": refreshed, "resolved_skipped": skipped}
    return result


def case_detail(conn: sqlite3.Connection, case_id: int) -> dict:
    row = conn.execute(
        """SELECT dc.*,m.machine_key,m.name machine_name,p.part_name,p.material
           FROM diagnostic_cases dc LEFT JOIN machines m ON m.id=dc.machine_id
           LEFT JOIN parts p ON p.id=dc.part_id WHERE dc.id=?""", (case_id,)
    ).fetchone()
    if not row:
        raise KeyError(f"Diagnostic case {case_id} not found")
    result = _dict(row)
    hypotheses = conn.execute(
        """SELECT * FROM diagnostic_hypotheses WHERE case_id=? AND analysis_version=?
           ORDER BY rank""", (case_id, result["analysis_version"])
    ).fetchall()
    result["hypotheses"] = [_dict(item) | CAUSES.get(item["cause_code"], {}) for item in hypotheses]
    result["events"] = [_dict(item) for item in conn.execute(
        "SELECT * FROM diagnostic_case_events WHERE case_id=? ORDER BY id DESC LIMIT 30", (case_id,)
    ).fetchall()]
    return result


def decide(conn: sqlite3.Connection, case_id: int, payload: dict,
           now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    row = conn.execute("SELECT * FROM diagnostic_cases WHERE id=?", (case_id,)).fetchone()
    if not row:
        raise KeyError(f"Diagnostic case {case_id} not found")
    current = dict(row)
    expected = payload.get("expected_version")
    if expected is not None and int(expected) != int(current["version"]):
        raise ValueError("Diagnostic case changed; refresh before deciding")
    action = payload["action"]
    actor = payload.get("actor", "").strip()
    if not actor or actor.lower() in ("operator", "system", "diagnostic-engine"):
        raise ValueError("A named operator is required for diagnostic decisions")
    if action == "confirm":
        if current["status"] != "open":
            raise ValueError("Only an open diagnostic case can be confirmed")
        cause = payload.get("actual_cause_code")
        if cause not in CAUSES or cause == "unknown":
            raise ValueError("Confirming a case requires a classified cause")
        target = "confirmed"
        conn.execute(
            """UPDATE diagnostic_cases SET status=?,actual_cause_code=?,corrective_action=?,
               resolution_notes=?,version=version+1,updated_at=? WHERE id=?""",
            (target, cause, payload.get("corrective_action"), payload.get("notes"), _iso(now), case_id),
        )
    elif action == "dismiss":
        if current["status"] != "open":
            raise ValueError("Only an open diagnostic case can be dismissed")
        if not payload.get("notes", "").strip():
            raise ValueError("Dismissing a case requires a reason")
        target = "dismissed"
        conn.execute(
            """UPDATE diagnostic_cases SET status=?,actual_cause_code=NULL,corrective_action=NULL,
               resolution_notes=?,version=version+1,updated_at=? WHERE id=?""",
            (target, payload["notes"], _iso(now), case_id),
        )
    elif action == "reopen":
        if current["status"] not in ("confirmed", "dismissed"):
            raise ValueError("Only a resolved diagnostic case can be reopened")
        target = "open"
        conn.execute(
            """UPDATE diagnostic_cases SET status='open',actual_cause_code=NULL,corrective_action=NULL,
               resolution_notes=?,version=version+1,updated_at=? WHERE id=?""",
            (payload.get("notes"), _iso(now), case_id),
        )
    else:
        raise ValueError(f"Unsupported diagnostic action '{action}'")
    _case_event(conn, case_id, action, current["status"], target,
                actor, payload.get("notes"),
                {"actual_cause_code": payload.get("actual_cause_code"),
                 "corrective_action": payload.get("corrective_action")}, now)
    conn.commit()
    return case_detail(conn, case_id)


def snapshot(conn: sqlite3.Connection, status: Optional[str] = None,
             now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    where = "WHERE status=?" if status else ""
    params = (status,) if status else ()
    rows = conn.execute(
        f"SELECT id,status,incident_type,actual_cause_code FROM diagnostic_cases {where} ORDER BY occurred_at DESC,id DESC",
        params,
    ).fetchall()
    cases = [case_detail(conn, row["id"]) for row in rows]
    confirmed = [item for item in cases if item["status"] == "confirmed"]
    training = Counter(item["incident_type"] for item in confirmed)
    return {
        "generated_at": _iso(now),
        "summary": {"total": len(cases), "open": sum(item["status"] == "open" for item in cases),
                    "confirmed": len(confirmed), "dismissed": sum(item["status"] == "dismissed" for item in cases),
                    "high_confidence_open": sum(item["status"] == "open" and item["confidence"] == "high" for item in cases)},
        "cause_catalog": [{"code": code, **value} for code, value in CAUSES.items()],
        "learning": {kind: {"confirmed_cases": training.get(kind, 0),
                            "empirical_prior_active": training.get(kind, 0) >= 5}
                     for kind in ("alarm", "downtime", "quality")},
        "cases": cases,
        "guardrail": "Rankings are diagnostic hypotheses, not causal findings. Only named operator confirmation creates training evidence or changes optimization cause labels.",
    }
