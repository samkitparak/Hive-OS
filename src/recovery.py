"""Event-driven, stability-aware rolling-horizon schedule recovery."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

import digital_twin
import forecasting
import planning


SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _settings() -> dict:
    configured = digital_twin._config().get("rescheduling", {})
    defaults = {
        "freeze_horizon_jobs": 2,
        "schedule_overrun_grace_s": 900,
        "minimum_tardiness_recovery_s": 900,
        "minimum_makespan_recovery_s": 600,
        "stability_penalty_s_per_position": 120,
        "maximum_moved_job_share": 0.5,
        "automatic_refresh_interval_s": 900,
    }
    return {**defaults, **configured}


def _active_schedule(conn: sqlite3.Connection) -> dict | None:
    scenario = conn.execute(
        """SELECT id,approved_at,selected_policy,input_signature
           FROM planning_scenarios WHERE status='approved'
           ORDER BY approved_at DESC,id DESC LIMIT 1"""
    ).fetchone()
    if not scenario:
        return None
    result = dict(scenario)
    result["items"] = [dict(row) for row in conn.execute(
        """SELECT psi.production_order_id,psi.position,psi.planned_end_s,
                  po.status,po.due_at,po.priority,j.job_name,j.imported_at
           FROM production_schedule_items psi
           JOIN production_orders po ON po.id=psi.production_order_id
           JOIN jobs j ON j.id=po.job_id
           WHERE psi.scenario_id=? ORDER BY psi.position""",
        (result["id"],),
    ).fetchall()]
    return result


def detect(conn: sqlite3.Connection, now: Optional[datetime] = None) -> dict:
    """Return current event-driven recovery triggers without mutating state."""
    now = now or _now()
    now = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
    active = _active_schedule(conn)
    if not active:
        return {
            "active_scenario_id": None, "status": "waiting_for_schedule",
            "triggers": [], "trigger_signature": hashlib.sha256(b"no-schedule").hexdigest(),
        }
    settings = _settings()
    triggers: list[dict] = []
    open_names = {
        item["job_name"] for item in active["items"]
        if item["status"] not in ("completed", "cancelled")
    }

    downtime = conn.execute(
        """SELECT DISTINCT de.id,m.machine_key,m.name machine_name,j.job_name
           FROM downtime_events de JOIN machines m ON m.id=de.machine_id
           JOIN execution_jobs ej ON ej.machine_id=m.id
           JOIN production_orders po ON po.id=ej.production_order_id
           JOIN jobs j ON j.id=po.job_id
           WHERE de.status='open' AND ej.state NOT IN ('completed','cancelled')
             AND po.status NOT IN ('completed','cancelled')
           ORDER BY de.id,j.job_name"""
    ).fetchall()
    for row in downtime:
        triggers.append({
            "key": f"machine_down:{row['id']}:{row['job_name']}",
            "type": "machine_down", "severity": "critical",
            "job_name": row["job_name"], "machine_key": row["machine_key"],
            "title": f"{row['machine_name']} is down with unfinished work",
            "detail": f"{row['job_name']} still requires this machine.",
        })

    held = conn.execute(
        """SELECT ej.id,j.job_name,m.machine_key,m.name machine_name,ej.held_reason
           FROM execution_jobs ej JOIN production_orders po ON po.id=ej.production_order_id
           JOIN jobs j ON j.id=po.job_id JOIN machines m ON m.id=ej.machine_id
           WHERE ej.state='held' ORDER BY ej.id"""
    ).fetchall()
    for row in held:
        triggers.append({
            "key": f"held_execution:{row['id']}", "type": "held_execution",
            "severity": "warning", "job_name": row["job_name"],
            "machine_key": row["machine_key"],
            "title": f"{row['job_name']} is held at {row['machine_name']}",
            "detail": row["held_reason"] or "Station work is held.",
        })

    approved_at = _parse(active["approved_at"])
    grace = float(settings["schedule_overrun_grace_s"])
    for item in active["items"]:
        if item["job_name"] not in open_names or item["planned_end_s"] is None:
            continue
        planned_end = approved_at + timedelta(seconds=float(item["planned_end_s"]))
        delay_s = (now - planned_end).total_seconds()
        if delay_s > grace:
            triggers.append({
                "key": f"schedule_overrun:{item['production_order_id']}",
                "type": "schedule_overrun", "severity": "warning",
                "job_name": item["job_name"],
                "title": f"{item['job_name']} exceeded its planned completion",
                "detail": f"Schedule overrun is {round(delay_s / 60)} minutes.",
                "delay_s": round(delay_s, 1), "planned_end_at": _iso(planned_end),
            })

    scheduled_ids = {item["production_order_id"] for item in active["items"]}
    unscheduled = conn.execute(
        """SELECT po.id,j.job_name,po.priority,po.due_at FROM production_orders po
           JOIN jobs j ON j.id=po.job_id
           WHERE po.status IN ('ready','released','in_progress') ORDER BY po.priority DESC,po.id"""
    ).fetchall()
    for row in unscheduled:
        if row["id"] in scheduled_ids:
            continue
        triggers.append({
            "key": f"unscheduled_order:{row['id']}", "type": "unscheduled_order",
            "severity": "critical" if int(row["priority"] or 0) >= 80 else "warning",
            "job_name": row["job_name"],
            "title": f"{row['job_name']} is ready but absent from the active schedule",
            "detail": f"Priority {row['priority']}; due {row['due_at'] or 'not set'}.",
        })

    exceptions = conn.execute(
        """SELECT ee.id,ee.exception_type,ee.details,j.job_name
           FROM execution_exceptions ee
           LEFT JOIN production_orders po ON po.id=ee.production_order_id
           LEFT JOIN jobs j ON j.id=po.job_id
           WHERE ee.status='open' ORDER BY ee.occurred_at,ee.id"""
    ).fetchall()
    for row in exceptions:
        triggers.append({
            "key": f"execution_exception:{row['id']}", "type": "execution_exception",
            "severity": "warning", "job_name": row["job_name"],
            "title": f"Execution exception: {row['exception_type']}",
            "detail": row["details"],
        })

    forecast = forecasting.snapshot(conn)
    if forecast.get("decision_ready") and forecast.get("latest"):
        for job in forecast["latest"]["result"].get("jobs", []):
            probability = job.get("late_probability")
            if probability is None or probability < 0.5:
                continue
            triggers.append({
                "key": f"forecast_late:{job.get('production_order_id') or job['job_name']}",
                "type": "forecast_late", "severity": "critical" if probability >= 0.8 else "warning",
                "job_name": job["job_name"],
                "title": f"{job['job_name']} has {round(probability * 100)}% simulated late risk",
                "detail": f"P80 completion {job['completion_at']['p80']}.",
                "late_probability": probability,
            })

    triggers.sort(key=lambda item: (-SEVERITY_ORDER[item["severity"]], item["key"]))
    signature_payload = [{key: item.get(key) for key in (
        "key", "severity", "delay_s", "late_probability"
    )} for item in triggers]
    trigger_signature = hashlib.sha256(
        json.dumps(signature_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "active_scenario_id": active["id"],
        "status": "triggered" if triggers else "stable",
        "triggers": triggers, "trigger_signature": trigger_signature,
    }


def _candidate_orders(conn: sqlite3.Connection, active: dict,
                      now: datetime) -> tuple[dict[str, list[str]], list[str], list[str]]:
    active_names = [
        item["job_name"] for item in active["items"]
        if item["status"] not in ("completed", "cancelled")
    ]
    scheduled = set(active_names)
    additions = [row["job_name"] for row in conn.execute(
        """SELECT j.job_name FROM production_orders po JOIN jobs j ON j.id=po.job_id
           WHERE po.status IN ('ready','released','in_progress')
           ORDER BY po.priority DESC,po.due_at,po.id"""
    ).fetchall() if row["job_name"] not in scheduled]
    baseline = [*active_names, *additions]
    if not baseline:
        return {}, [], []
    marks = ",".join("?" for _ in baseline)
    records = {row["job_name"]: dict(row) for row in conn.execute(
        f"""SELECT j.job_name,j.imported_at,po.due_at,po.priority,po.status
             FROM jobs j JOIN production_orders po ON po.job_id=j.id
             WHERE j.job_name IN ({marks})""", baseline
    ).fetchall()}
    jobs = digital_twin._load_jobs(conn, baseline)
    parts, _ = digital_twin._operation_plan(
        conn, jobs, remaining_only=True, simulated_at=now,
    )
    remaining_names = {part["job_name"] for part in parts}
    baseline = [name for name in baseline if name in remaining_names]
    if not baseline:
        return {}, [], []
    marks = ",".join("?" for _ in baseline)
    hard_frozen = {row["job_name"] for row in conn.execute(
        f"""SELECT DISTINCT j.job_name FROM execution_jobs ej
             JOIN production_orders po ON po.id=ej.production_order_id
             JOIN jobs j ON j.id=po.job_id
             WHERE j.job_name IN ({marks})
               AND (ej.state IN ('dispatched','acknowledged','running','held')
                    OR po.status='in_progress')""", baseline
    ).fetchall()}
    freeze_count = max(0, int(_settings()["freeze_horizon_jobs"]))
    frozen_set = hard_frozen | set(baseline[:freeze_count])
    frozen = [name for name in baseline if name in frozen_set]
    movable = [name for name in baseline if name not in frozen_set]

    processing = {
        name: digital_twin._job_processing_time(parts, name) for name in baseline
    }
    primary_material = {job["job_name"]: job["primary_material"] for job in jobs}

    def ordered(key):
        reordered = iter(sorted(movable, key=key))
        return [name if name in frozen_set else next(reordered) for name in baseline]

    def placed(sequence: list[str]) -> list[str]:
        reordered = iter(sequence)
        return [name if name in frozen_set else next(reordered) for name in baseline]

    movable_jobs = [job for job in jobs if job["job_name"] in set(movable)]
    setup_order = [job["job_name"] for job in digital_twin.setup_aware_order(
        conn, movable_jobs, [part for part in parts if part["job_name"] in set(movable)],
    )]

    candidates = {
        "current": baseline,
        "fifo": ordered(lambda name: (records[name]["imported_at"] or "", name)),
        "edd": ordered(lambda name: (
            records[name]["due_at"] or "9999-12-31", -int(records[name]["priority"] or 0), name
        )),
        "spt": ordered(lambda name: (processing.get(name, math.inf), name)),
        "material_batch": ordered(lambda name: (
            primary_material.get(name, "unknown"), records[name]["due_at"] or "", name
        )),
        "setup_aware": placed(setup_order),
    }
    return candidates, baseline, frozen


def _annotate_stability(scenarios: list[dict], baseline: list[str],
                        frozen: list[str]) -> None:
    baseline_position = {name: index for index, name in enumerate(baseline)}
    maximum_shift = max(1, sum(abs(index - (len(baseline) - index - 1))
                               for index in range(len(baseline))))
    frozen_positions = {name: baseline_position[name] for name in frozen}
    for scenario in scenarios:
        order = scenario["job_order"]
        shifts = {
            name: abs(index - baseline_position[name])
            for index, name in enumerate(order) if name in baseline_position
        }
        moved = [name for name, shift in shifts.items() if shift]
        total_shift = sum(shifts.values())
        scenario["stability"] = {
            "score": round(max(0.0, 1 - total_shift / maximum_shift), 4),
            "moved_jobs": len(moved), "moved_job_names": moved,
            "moved_job_share": round(len(moved) / len(baseline), 4) if baseline else 0.0,
            "total_position_shift": total_shift,
            "maximum_position_shift": max(shifts.values(), default=0),
            "frozen_jobs": frozen,
            "frozen_positions": frozen_positions,
            "frozen_positions_preserved": all(
                order[position] == name for name, position in frozen_positions.items()
            ),
        }


def _recommend(scenarios: list[dict], settings: dict) -> dict | None:
    if not scenarios:
        return None
    baseline = next((item for item in scenarios if item["policy"] == "current"), scenarios[0])
    baseline_tardiness = float(baseline["total_tardiness_s"])
    baseline_late = int(baseline["late_jobs"])
    candidates = []
    for item in scenarios:
        item["recovery"] = {
            "late_job_reduction": baseline_late - int(item["late_jobs"]),
            "tardiness_reduction_s": round(
                baseline_tardiness - float(item["total_tardiness_s"]), 1
            ),
            "makespan_reduction_s": round(
                float(baseline["makespan_s"]) - float(item["makespan_s"]), 1
            ),
            "actionable": False,
        }
        if item["policy"] == "current" or not item["feasible"]:
            continue
        stability = item["stability"]
        if not stability["frozen_positions_preserved"]:
            continue
        material = (
            item["recovery"]["late_job_reduction"] > 0
            or item["recovery"]["tardiness_reduction_s"] >= float(
                settings["minimum_tardiness_recovery_s"]
            )
            or (baseline_late == 0 and item["recovery"]["makespan_reduction_s"] >= float(
                settings["minimum_makespan_recovery_s"]
            ))
        )
        stable_enough = (
            stability["moved_job_share"] <= float(settings["maximum_moved_job_share"])
            or item["recovery"]["late_job_reduction"] > 0
        )
        if material and stable_enough:
            item["recovery"]["actionable"] = True
            candidates.append(item)
    if not candidates:
        return {
            "policy": "current", "actionable": False,
            "basis": "No candidate clears the declared recovery and stability thresholds.",
            "late_job_reduction": 0, "tardiness_reduction_s": 0,
        }
    penalty = float(settings["stability_penalty_s_per_position"])
    chosen = min(candidates, key=lambda item: (
        item["late_jobs"],
        float(item["total_tardiness_s"]) + item["stability"]["total_position_shift"] * penalty,
        item["makespan_s"], item["setup_time_s"], item["policy"],
    ))
    return {
        "policy": chosen["policy"], "actionable": True,
        "basis": (
            "Lowest late-job and stability-penalized tardiness result that preserves "
            "the frozen execution horizon."
        ),
        **chosen["recovery"], "stability": chosen["stability"],
    }


def _expire_prior_drafts(conn: sqlite3.Connection) -> None:
    for row in conn.execute(
        "SELECT id,request_json FROM planning_scenarios WHERE status='draft'"
    ).fetchall():
        request = json.loads(row["request_json"])
        if request.get("recovery"):
            conn.execute(
                "UPDATE planning_scenarios SET status='expired' WHERE id=?", (row["id"],)
            )


def analyze(conn: sqlite3.Connection, *, actor: str = "system", force: bool = False,
            now: Optional[datetime] = None) -> dict:
    now = now or _now()
    now = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
    current = detect(conn, now)
    active = _active_schedule(conn)
    if not active:
        return snapshot(conn, now)
    if not current["triggers"] and not force:
        return snapshot(conn, now)
    candidates, baseline, frozen = _candidate_orders(conn, active, now)
    if not candidates:
        return snapshot(conn, now)
    comparison = digital_twin.compare_orders(
        conn, candidates, job_names=baseline, simulated_at=now,
    )
    _annotate_stability(comparison["scenarios"], baseline, frozen)
    settings = _settings()
    recommendation = _recommend(comparison["scenarios"], settings)
    comparison["recommendation"] = recommendation
    comparison["recovery"] = {
        "active_scenario_id": active["id"], "baseline_order": baseline,
        "frozen_jobs": frozen, "triggers": current["triggers"],
        "settings": settings,
    }
    input_signature = planning.factory_signature(conn, baseline)
    status = "commissioning"
    planning_scenario_id = None
    if comparison["scenarios"]:
        status = "review" if (
            recommendation and recommendation["actionable"]
            and comparison["readiness"].get("operational_recommendation")
        ) else "monitor"
    if status == "review":
        _expire_prior_drafts(conn)
        request = {
            "job_names": baseline, "policies": list(candidates),
            "stochastic": False, "seed": 1,
            "recovery": {
                "active_scenario_id": active["id"],
                "trigger_signature": current["trigger_signature"],
                "frozen_jobs": frozen,
            },
        }
        cursor = conn.execute(
            """INSERT INTO planning_scenarios
               (name,created_by,request_json,result_json,readiness_json,
                input_signature,status,created_at)
               VALUES (?,?,?,?,?,?,'draft',?)""",
            (f"Recovery from schedule {active['id']}", actor,
             json.dumps(request, sort_keys=True), json.dumps(comparison, sort_keys=True),
             json.dumps(comparison["readiness"], sort_keys=True), input_signature, _iso(now)),
        )
        planning_scenario_id = cursor.lastrowid
    cursor = conn.execute(
        """INSERT INTO schedule_recovery_assessments
           (active_scenario_id,planning_scenario_id,input_signature,trigger_signature,
            status,triggers_json,result_json,created_by,created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (active["id"], planning_scenario_id, input_signature,
         current["trigger_signature"], status,
         json.dumps(current["triggers"], sort_keys=True),
         json.dumps(comparison, sort_keys=True), actor, _iso(now)),
    )
    conn.commit()
    response = snapshot(conn, now)
    response["created_assessment_id"] = cursor.lastrowid
    return response


def _row(row: sqlite3.Row | dict) -> dict:
    item = dict(row)
    item["triggers"] = json.loads(item.pop("triggers_json"))
    item["result"] = json.loads(item.pop("result_json"))
    if item.get("planning_scenario_id"):
        scenario = item.get("scenario_status")
        item["planning_scenario_status"] = scenario
    item.pop("scenario_status", None)
    return item


def history(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        """SELECT sra.*,ps.status scenario_status
           FROM schedule_recovery_assessments sra
           LEFT JOIN planning_scenarios ps ON ps.id=sra.planning_scenario_id
           ORDER BY sra.created_at DESC,sra.id DESC LIMIT ?""",
        (max(1, min(int(limit), 100)),),
    ).fetchall()
    return [_row(row) for row in rows]


def snapshot(conn: sqlite3.Connection, now: Optional[datetime] = None) -> dict:
    current = detect(conn, now)
    records = history(conn, 1)
    latest = records[0] if records else None
    stale = False
    if latest:
        job_names = latest["result"].get("recovery", {}).get("baseline_order")
        stale = latest["input_signature"] != planning.factory_signature(conn, job_names)
    action_required = bool(
        latest and latest["status"] == "review" and not stale
        and latest.get("planning_scenario_status") == "draft"
    )
    return {
        "status": "review" if action_required else current["status"],
        "current": current, "latest": latest, "stale": stale,
        "action_required": action_required,
        "guardrail": (
            "Factory inputs changed; regenerate recovery evidence before approval."
            if stale else
            "A named planner must approve any recovery sequence before dispatch changes."
            if action_required else
            "The active schedule is within configured recovery thresholds."
            if current["status"] == "stable" else
            "Approve a production schedule before HIVE can monitor recovery."
            if current["status"] == "waiting_for_schedule" else
            "A deviation is present; analyze it before changing dispatch."
        ),
    }


def refresh_if_needed(conn: sqlite3.Connection) -> dict:
    now = _now()
    current = detect(conn, now)
    if not current["triggers"]:
        return snapshot(conn, now)
    records = history(conn, 1)
    if records:
        latest = records[0]
        age = (now - _parse(latest["created_at"])).total_seconds()
        if (latest["trigger_signature"] == current["trigger_signature"]
                and age < float(_settings()["automatic_refresh_interval_s"])):
            return snapshot(conn, now)
    return analyze(conn, actor="system", now=now)


def decide(conn: sqlite3.Connection, assessment_id: int, decision: str,
           actor: str, selected_policy: str | None = None,
           notes: str | None = None) -> dict:
    row = conn.execute(
        "SELECT * FROM schedule_recovery_assessments WHERE id=?", (assessment_id,)
    ).fetchone()
    if not row:
        raise KeyError(f"Recovery assessment {assessment_id} not found")
    assessment = _row(row)
    if assessment["decision"]:
        raise ValueError(f"Recovery assessment is already {assessment['decision']}")
    scenario_id = assessment.get("planning_scenario_id")
    if not scenario_id:
        raise ValueError("This assessment has no actionable recovery scenario")
    job_names = assessment["result"].get("recovery", {}).get("baseline_order")
    if assessment["input_signature"] != planning.factory_signature(conn, job_names):
        conn.execute(
            "UPDATE schedule_recovery_assessments SET status='expired' WHERE id=?",
            (assessment_id,),
        )
        conn.execute(
            "UPDATE planning_scenarios SET status='expired' WHERE id=? AND status='draft'",
            (scenario_id,),
        )
        conn.commit()
        raise ValueError("Factory inputs changed; analyze a fresh recovery")
    if decision not in ("approve", "reject"):
        raise ValueError("decision must be approve or reject")
    recommendation = assessment["result"].get("recommendation") or {}
    policy = selected_policy or recommendation.get("policy")
    if decision == "approve":
        selected = next((item for item in assessment["result"].get("scenarios", [])
                         if item["policy"] == policy), None)
        if not selected or not selected.get("recovery", {}).get("actionable"):
            raise ValueError("Select a recovery policy that clears benefit and stability thresholds")
    scenario = planning.decide(
        conn, scenario_id, decision, actor,
        policy if decision == "approve" else None, notes,
    )
    if decision == "approve":
        import execution
        execution.sync(conn, commit=False)
    decided_at = _iso(_now())
    conn.execute(
        """UPDATE schedule_recovery_assessments
           SET status=?,decision=?,selected_policy=?,decided_by=?,decided_at=?,notes=?
           WHERE id=?""",
        ("approved" if decision == "approve" else "rejected", decision,
         policy if decision == "approve" else None, actor, decided_at, notes,
         assessment_id),
    )
    conn.commit()
    result = snapshot(conn)
    result["decided_scenario"] = scenario
    return result
