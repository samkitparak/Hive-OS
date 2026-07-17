"""Uncertainty-aware production, constraint, and delivery-risk forecasting."""

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import digital_twin
import planning
import resources as factory_resources


DEFAULT_SAMPLES = 50
MIN_SAMPLES = 20
MAX_SAMPLES = 200


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _percentile(values: list[float], probability: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _rounded_percentiles(values: list[float]) -> dict:
    return {
        "p10": round(_percentile(values, 0.10) or 0.0, 1),
        "p50": round(_percentile(values, 0.50) or 0.0, 1),
        "p80": round(_percentile(values, 0.80) or 0.0, 1),
        "p95": round(_percentile(values, 0.95) or 0.0, 1),
    }


def _model_evidence(conn: sqlite3.Connection, machine_keys: set[str]) -> dict:
    if not machine_keys:
        return {"machines": [], "minimum_confidence": "low", "mean_residual_cv": 0.0}
    placeholders = ",".join("?" for _ in machine_keys)
    rows = [dict(row) for row in conn.execute(
        f"""SELECT m.machine_key,m.name,cm.confidence,cm.sample_count,
                   COALESCE(cm.residual_cv,0) residual_cv,cm.version
            FROM cycle_models cm JOIN machines m ON m.id=cm.machine_id
            WHERE cm.status='active' AND m.machine_key IN ({placeholders})
            ORDER BY m.machine_key""", sorted(machine_keys)
    ).fetchall()]
    tiers = {"low": 0, "medium": 1, "high": 2}
    minimum = min((row["confidence"] for row in rows),
                  key=lambda value: tiers.get(value, 0), default="low")
    cvs = [float(row["residual_cv"] or 0) for row in rows]
    return {
        "machines": rows,
        "minimum_confidence": minimum,
        "mean_residual_cv": round(sum(cvs) / len(cvs), 4) if cvs else 0.0,
        "stochastic_machines": sum(value > 0 for value in cvs),
    }


def generate(conn: sqlite3.Connection, *, job_names: list[str] | None = None,
             policy: str = "current", samples: int = DEFAULT_SAMPLES, seed: int = 1,
             now: Optional[datetime] = None) -> dict:
    """Run a reproducible Monte Carlo ensemble from the current factory twin."""
    if policy not in digital_twin.POLICIES:
        raise ValueError(f"unknown forecast policy: {policy}")
    if samples < MIN_SAMPLES or samples > MAX_SAMPLES:
        raise ValueError(f"samples must be between {MIN_SAMPLES} and {MAX_SAMPLES}")
    if seed < 0 or seed > 2_147_483_647 - samples:
        raise ValueError("seed is outside the supported range")

    generated = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    jobs = digital_twin._load_jobs(conn, job_names)
    selected_names = [job["job_name"] for job in jobs]
    resource_status = factory_resources.snapshot(conn, selected_names or job_names)
    parts, readiness = digital_twin._operation_plan(conn, jobs, resource_status)
    if not jobs or readiness["model_coverage"] < 1:
        return {
            "generated_at": _iso(generated), "policy": policy, "sample_count": 0,
            "status": "commissioning", "decision_ready": False,
            "readiness": readiness, "feasible_probability": 0.0,
            "constraints": [], "jobs": [], "kpis": {},
            "guardrail": readiness["guardrail"],
        }

    cfg = digital_twin._config()
    resource_context = factory_resources.simulation_context(conn, jobs, generated)
    runs = [digital_twin._single_run(
        conn, jobs, parts, policy, True, seed + index, cfg, generated, resource_context
    ) for index in range(samples)]
    feasible = [run for run in runs if run["feasible"]]
    feasible_probability = len(feasible) / samples
    machine_keys = {
        operation["machine_key"] for part in parts for operation in part["operations"]
    }
    models = _model_evidence(conn, machine_keys)

    winners = Counter()
    relative_severity = defaultdict(list)
    utilization = defaultdict(list)
    waits = defaultdict(list)
    for run in feasible:
        values = run["machine_utilization"]
        if not values:
            continue
        peak = max(values.values())
        tied = [
            key for key, value in values.items()
            if math.isclose(value, peak, rel_tol=1e-9, abs_tol=1e-12)
        ]
        credit = 1 / len(tied)
        for key in tied:
            winners[key] += credit
        for key in machine_keys:
            value = float(values.get(key, 0))
            utilization[key].append(value)
            relative_severity[key].append(value / peak if peak > 0 else 0.0)
            waits[key].append(float(run.get("machine_wait_s", {}).get(key, 0)))
    machine_names = {row["machine_key"]: row["name"] for row in conn.execute(
        "SELECT machine_key,name FROM machines"
    ).fetchall()}
    constraints = []
    denominator = max(len(feasible), 1)
    for key in machine_keys:
        frequency = winners[key] / denominator
        severity_values = relative_severity[key]
        util_values = utilization[key]
        constraints.append({
            "machine_key": key, "machine_name": machine_names.get(key, key),
            "bottleneck_probability": round(frequency, 4),
            "relative_severity": round(
                sum(severity_values) / len(severity_values), 4
            ) if severity_values else 0.0,
            "mean_utilization": round(sum(util_values) / len(util_values), 4)
            if util_values else 0.0,
            "p90_utilization": round(_percentile(util_values, 0.90) or 0.0, 4),
            "p80_wait_s": round(_percentile(waits[key], 0.80) or 0.0, 1),
        })
    constraints.sort(key=lambda item: (
        item["bottleneck_probability"], item["relative_severity"],
        item["mean_utilization"]
    ), reverse=True)

    job_results = []
    for job in jobs:
        name = job["job_name"]
        completion = [float(run["job_completion_s"][name]) for run in feasible
                      if name in run["job_completion_s"]]
        tardiness = [float(run.get("job_tardiness_s", {}).get(name, 0))
                     for run in feasible]
        completion_p = _rounded_percentiles(completion)
        late_probability = (sum(value > 0 for value in tardiness) / len(tardiness)
                            if tardiness and job.get("due_at") else None)
        risk = ("high" if late_probability is not None and late_probability >= 0.5
                else "watch" if late_probability is not None and late_probability >= 0.2
                else "low" if late_probability is not None else "unscored")
        job_results.append({
            "production_order_id": job.get("production_order_id"),
            "job_name": name, "due_at": job.get("due_at"),
            "completion_s": completion_p,
            "completion_at": {
                key: _iso(generated + timedelta(seconds=value))
                for key, value in completion_p.items()
            },
            "late_probability": round(late_probability, 4)
            if late_probability is not None else None,
            "risk": risk,
            "expected_tardiness_s": round(sum(tardiness) / len(tardiness), 1)
            if tardiness else 0.0,
            "p80_tardiness_s": round(_percentile(tardiness, 0.80) or 0.0, 1),
        })
    job_results.sort(key=lambda item: (
        item["late_probability"] if item["late_probability"] is not None else -1,
        item["p80_tardiness_s"], item["job_name"]
    ), reverse=True)

    decision_ready = bool(
        readiness["operational_recommendation"] and feasible_probability >= 0.95
        and len(feasible) >= MIN_SAMPLES
    )
    kpis = {
        "makespan_s": _rounded_percentiles([run["makespan_s"] for run in feasible]),
        "throughput_parts_per_hour": _rounded_percentiles([
            run["throughput_parts_per_hour"] for run in feasible
        ]),
        "late_jobs": _rounded_percentiles([run["late_jobs"] for run in feasible]),
        "total_tardiness_s": _rounded_percentiles([
            run["total_tardiness_s"] for run in feasible
        ]),
    }
    uncertainty = {
        "method": "Monte Carlo with active cycle-model residual variation",
        "seed_start": seed, "sample_count": samples,
        "feasible_runs": len(feasible), "model_evidence": models,
        "assumptions": {
            "operation_durations": "independent Gaussian residuals, clipped to positive durations",
            "residual_cv_cap": 0.5,
            "unmodeled_disturbances": "breakdowns and absenteeism require commissioned calendar or availability inputs",
        },
    }
    return {
        "generated_at": _iso(generated), "policy": policy,
        "sample_count": samples, "status": "ready" if decision_ready else "learning",
        "decision_ready": decision_ready, "readiness": readiness,
        "feasible_probability": round(feasible_probability, 4),
        "constraints": constraints, "jobs": job_results, "kpis": kpis,
        "uncertainty": uncertainty,
        "guardrail": (
            "Forecast is decision-ready but remains advisory until its factory calibration is credible."
            if decision_ready else
            "Forecast is commissioning evidence only; resolve readiness or feasibility gaps before acting."
        ),
    }


def _row_result(row: sqlite3.Row | dict) -> dict:
    item = dict(row)
    item["request"] = json.loads(item.pop("request_json"))
    item["result"] = json.loads(item.pop("result_json"))
    return item


def history(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM production_forecasts ORDER BY generated_at DESC,id DESC LIMIT ?",
        (max(1, min(int(limit), 100)),),
    ).fetchall()
    return [_row_result(row) for row in rows]


def calibration(conn: sqlite3.Connection) -> dict:
    """Compare the latest pre-completion forecast for each order with reality."""
    forecasts = history(conn, 100)
    completions = [dict(row) for row in conn.execute(
        """SELECT po.id production_order_id,j.job_name,po.due_at,MAX(poe.ts) completed_at
             FROM production_orders po JOIN jobs j ON j.id=po.job_id
             JOIN production_order_events poe ON poe.production_order_id=po.id
            WHERE poe.to_status='completed' GROUP BY po.id"""
    ).fetchall()]
    outcomes = []
    for completion in completions:
        completed_at = _parse(completion["completed_at"])
        candidates = []
        for forecast in forecasts:
            generated_at = _parse(forecast["generated_at"])
            if generated_at >= completed_at:
                continue
            job = next((item for item in forecast["result"].get("jobs", [])
                        if item.get("production_order_id") == completion["production_order_id"]), None)
            if job:
                candidates.append((generated_at, forecast, job))
        if not candidates:
            continue
        generated_at, forecast, job = max(candidates, key=lambda item: item[0])
        actual_s = max(0.0, (completed_at - generated_at).total_seconds())
        p50 = float(job["completion_s"]["p50"])
        p80 = float(job["completion_s"]["p80"])
        p95 = float(job["completion_s"]["p95"])
        due_at = _parse(completion["due_at"]) if completion.get("due_at") else None
        actual_late = bool(due_at and completed_at > due_at)
        late_probability = job.get("late_probability")
        outcomes.append({
            "forecast_id": forecast["id"],
            "production_order_id": completion["production_order_id"],
            "job_name": completion["job_name"],
            "generated_at": _iso(generated_at), "completed_at": _iso(completed_at),
            "actual_completion_s": round(actual_s, 1),
            "p50_error_s": round(actual_s - p50, 1),
            "inside_p80": actual_s <= p80, "inside_p95": actual_s <= p95,
            "predicted_late_probability": late_probability,
            "actual_late": actual_late,
        })
    count = len(outcomes)
    p80_coverage = sum(item["inside_p80"] for item in outcomes) / count if count else None
    p95_coverage = sum(item["inside_p95"] for item in outcomes) / count if count else None
    mae = (sum(abs(item["p50_error_s"]) for item in outcomes) / count if count else None)
    scored = [item for item in outcomes if item["predicted_late_probability"] is not None]
    brier = (sum((float(item["predicted_late_probability"]) - int(item["actual_late"])) ** 2
                 for item in scored) / len(scored) if scored else None)
    if count < 5:
        status = "collecting"
    elif (p80_coverage or 0) < 0.5 or (brier is not None and brier > 0.35):
        status = "drift"
    elif (p80_coverage or 0) >= 0.65 and (brier is None or brier <= 0.25):
        status = "credible"
    else:
        status = "monitor"
    return {
        "status": status, "outcome_count": count,
        "p80_coverage": round(p80_coverage, 4) if p80_coverage is not None else None,
        "p95_coverage": round(p95_coverage, 4) if p95_coverage is not None else None,
        "p50_mean_absolute_error_s": round(mae, 1) if mae is not None else None,
        "late_risk_brier_score": round(brier, 4) if brier is not None else None,
        "outcomes": outcomes[:20],
        "guardrail": (
            "Collect at least five completed-order outcomes before trusting forecast calibration."
            if status == "collecting" else
            "Forecast drift detected; review cycle models, routes, calendars, and resource assumptions."
            if status == "drift" else
            "Forecast calibration is within the provisional operating tolerance."
            if status == "credible" else
            "Forecast calibration needs more evidence or tighter uncertainty bounds."
        ),
    }


def refresh(conn: sqlite3.Connection, *, job_names: list[str] | None = None,
            policy: str = "current", samples: int = DEFAULT_SAMPLES, seed: int = 1,
            force: bool = False, now: Optional[datetime] = None) -> dict:
    signature = planning.factory_signature(conn, job_names)
    request = {"job_names": job_names, "policy": policy, "samples": samples, "seed": seed}
    latest = conn.execute(
        "SELECT * FROM production_forecasts ORDER BY generated_at DESC,id DESC LIMIT 1"
    ).fetchone()
    if latest and not force and latest["input_signature"] == signature:
        previous = _row_result(latest)
        if previous["request"] == request:
            result = snapshot(conn)
            result["reused"] = True
            return result
    result = generate(
        conn, job_names=job_names, policy=policy, samples=samples, seed=seed, now=now
    )
    generated_at = result["generated_at"]
    cursor = conn.execute(
        """INSERT INTO production_forecasts
           (input_signature,policy,sample_count,seed,status,request_json,result_json,generated_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (signature, policy, result["sample_count"], seed, result["status"],
         json.dumps(request, sort_keys=True), json.dumps(result, sort_keys=True), generated_at),
    )
    conn.commit()
    response = snapshot(conn)
    response["created_forecast_id"] = cursor.lastrowid
    response["reused"] = False
    return response


def refresh_if_needed(conn: sqlite3.Connection) -> dict:
    readiness = digital_twin.readiness(conn)
    if not readiness["operational_recommendation"]:
        return snapshot(conn)
    return refresh(conn)


def snapshot(conn: sqlite3.Connection) -> dict:
    latest = conn.execute(
        "SELECT * FROM production_forecasts ORDER BY generated_at DESC,id DESC LIMIT 1"
    ).fetchone()
    calibration_result = calibration(conn)
    if not latest:
        return {
            "latest": None, "stale": False, "calibration": calibration_result,
            "guardrail": "A forecast will appear after routes, models, and resources are commissioned.",
        }
    item = _row_result(latest)
    stale = item["input_signature"] != planning.factory_signature(
        conn, item["request"].get("job_names")
    )
    effective_ready = bool(
        item["result"].get("decision_ready") and not stale
        and calibration_result["status"] != "drift"
    )
    return {
        "latest": item, "stale": stale, "decision_ready": effective_ready,
        "calibration": calibration_result,
        "guardrail": (
            "Factory inputs changed; refresh before using this forecast."
            if stale else calibration_result["guardrail"]
            if calibration_result["status"] == "drift" else item["result"]["guardrail"]
        ),
    }
