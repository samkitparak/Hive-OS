"""Auditable recommendation experiments and conservative outcome learning."""

import hashlib
import json
import math
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from statistics import mean, median
from typing import Optional

import optimization


TERMINAL = {"validated", "promising", "ineffective", "inconclusive", "completed", "rejected", "cancelled"}
METRICS = {
    "throughput_per_hour": {"label": "Throughput per hour", "unit": "cycles/h", "statistic": "mean", "direction": "increase"},
    "downtime_minutes_per_hour": {"label": "Downtime per hour", "unit": "min/h", "statistic": "mean", "direction": "decrease"},
    "defect_rate": {"label": "Defect rate", "unit": "ratio", "statistic": "mean", "direction": "decrease"},
    "median_cycle_time_s": {"label": "Median cycle time", "unit": "s", "statistic": "median", "direction": "decrease"},
}


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _json(value, fallback):
    if value is None:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _machine_id(conn: sqlite3.Connection, target_type: str, target_key: str) -> Optional[int]:
    if target_type != "machine":
        return None
    row = conn.execute("SELECT id FROM machines WHERE machine_key=?", (target_key,)).fetchone()
    if not row:
        raise KeyError(f"Machine '{target_key}' not found")
    return int(row["id"])


def _hour_buckets(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    count = max(1, math.ceil((end - start).total_seconds() / 3600))
    return [(start + timedelta(hours=i), min(end, start + timedelta(hours=i + 1))) for i in range(count)]


def metric_samples(conn: sqlite3.Connection, metric: str, target_type: str,
                   target_key: str, start: datetime, end: datetime) -> list[float]:
    if metric not in METRICS:
        raise ValueError(f"Unsupported improvement metric '{metric}'")
    if end <= start:
        raise ValueError("Metric window end must be after start")
    machine_id = _machine_id(conn, target_type, target_key)
    machine_sql = " AND machine_id=?" if machine_id is not None else ""
    params = [_iso(start), _iso(end)] + ([machine_id] if machine_id is not None else [])

    if metric == "throughput_per_hour":
        rows = conn.execute(
            f"SELECT ts FROM machine_events WHERE event_type='cycle_end' AND ts>=? AND ts<?{machine_sql}",
            params,
        ).fetchall()
        buckets = _hour_buckets(start, end)
        values = [0.0 for _ in buckets]
        for row in rows:
            index = min(len(values) - 1, int((_parse(row["ts"]) - start).total_seconds() // 3600))
            if index >= 0:
                duration_h = max((buckets[index][1] - buckets[index][0]).total_seconds() / 3600, 1e-9)
                values[index] += 1 / duration_h
        return values

    if metric == "downtime_minutes_per_hour":
        rows = conn.execute(
            f"""SELECT started_at,COALESCE(ended_at,?) ended_at FROM downtime_events
                WHERE started_at<? AND COALESCE(ended_at,?)>?{machine_sql}""",
            [_iso(end), _iso(end), _iso(end), _iso(start)] + ([machine_id] if machine_id is not None else []),
        ).fetchall()
        buckets = _hour_buckets(start, end)
        values = []
        for bucket_start, bucket_end in buckets:
            overlap_s = sum(max(0.0, (min(bucket_end, _parse(row["ended_at"])) -
                                      max(bucket_start, _parse(row["started_at"]))).total_seconds()) for row in rows)
            duration_h = max((bucket_end - bucket_start).total_seconds() / 3600, 1e-9)
            values.append(overlap_s / 60 / duration_h)
        return values

    if metric == "defect_rate":
        rows = conn.execute(
            f"SELECT result FROM quality_checks WHERE ts>=? AND ts<?{machine_sql}", params
        ).fetchall()
        return [0.0 if row["result"] == "pass" else 1.0 for row in rows]

    rows = conn.execute(
        f"""SELECT duration_s FROM cycle_observations
            WHERE validity='valid' AND duration_s IS NOT NULL AND ended_at>=? AND ended_at<?{machine_sql}""",
        params,
    ).fetchall()
    return [float(row["duration_s"]) for row in rows]


def _summary(metric: str, samples: list[float], start: datetime, end: datetime) -> dict:
    statistic = METRICS[metric]["statistic"]
    value = (median(samples) if statistic == "median" else mean(samples)) if samples else None
    return {
        "metric": metric, "label": METRICS[metric]["label"], "unit": METRICS[metric]["unit"],
        "statistic": statistic, "sample_count": len(samples),
        "value": round(value, 6) if value is not None else None,
        "window_start": _iso(start), "window_end": _iso(end),
    }


def _effect(metric: str, direction: str, baseline: list[float], evaluation: list[float],
            seed_key: str) -> tuple[Optional[float], Optional[float], Optional[float]]:
    statistic = median if METRICS[metric]["statistic"] == "median" else mean
    base = statistic(baseline) if baseline else None
    after = statistic(evaluation) if evaluation else None
    if base is None or after is None or abs(base) < 1e-9:
        return None, None, None
    sign = 1 if direction == "increase" else -1

    def calculate(before, after_values):
        return sign * (statistic(after_values) - statistic(before)) / abs(statistic(before)) * 100

    point = calculate(baseline, evaluation)
    rng = random.Random(int(hashlib.sha256(seed_key.encode()).hexdigest()[:16], 16))
    boot = []
    for _ in range(1000):
        before = [rng.choice(baseline) for _ in baseline]
        after_values = [rng.choice(evaluation) for _ in evaluation]
        if abs(statistic(before)) >= 1e-9:
            boot.append(calculate(before, after_values))
    if not boot:
        return round(point, 3), None, None
    boot.sort()
    return round(point, 3), round(boot[int(len(boot) * 0.05)], 3), round(boot[int(len(boot) * 0.95) - 1], 3)


def _event(conn, recommendation_id: int, experiment_id: Optional[int], event_type: str,
           from_status: Optional[str], to_status: str, actor: str, notes: Optional[str],
           payload: Optional[dict], now: datetime):
    conn.execute(
        """INSERT INTO improvement_events
           (recommendation_id,experiment_id,event_type,from_status,to_status,actor,notes,payload_json,ts)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (recommendation_id, experiment_id, event_type, from_status, to_status, actor,
         notes, json.dumps(payload or {}, sort_keys=True), _iso(now)),
    )


def sync(conn: sqlite3.Connection, actor: str = "operator", window_hours: int = 8,
         now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    report = optimization.build(conn, window_hours, now)
    created = refreshed = 0
    for item in report["recommendations"]:
        row = conn.execute(
            "SELECT id,status FROM improvement_recommendations WHERE recommendation_key=?",
            (item["recommendation_key"],),
        ).fetchone()
        values = (
            item["category"], item["title"], item["action"], item["target_type"], item["target_key"],
            item["cause_code"], item["confidence"], item["metric_hint"], item["target_direction"],
            json.dumps(item.get("evidence", [])), item["source_window_start"], item["source_window_end"],
            item["source_generated_at"], _iso(now),
        )
        if row:
            conn.execute(
                """UPDATE improvement_recommendations SET category=?,title=?,action=?,target_type=?,target_key=?,
                   cause_code=?,confidence=?,metric_hint=?,target_direction=?,evidence_json=?,source_window_start=?,
                   source_window_end=?,source_generated_at=?,updated_at=?,version=version+1 WHERE id=?""",
                (*values, row["id"]),
            )
            refreshed += 1
        else:
            cursor = conn.execute(
                """INSERT INTO improvement_recommendations
                   (recommendation_key,category,title,action,target_type,target_key,cause_code,confidence,
                    metric_hint,target_direction,evidence_json,source_window_start,source_window_end,
                    source_generated_at,status,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'proposed',?,?)""",
                (item["recommendation_key"], *values[:-1], _iso(now), _iso(now)),
            )
            _event(conn, cursor.lastrowid, None, "created", None, "proposed", actor, None,
                   {"source_generated_at": item["source_generated_at"]}, now)
            created += 1
    conn.commit()
    result = snapshot(conn, now)
    result["sync"] = {"created": created, "refreshed": refreshed, "source_count": len(report["recommendations"])}
    return result


def _row_dict(row) -> dict:
    result = dict(row)
    for key, fallback in (("evidence_json", []), ("confounders_json", []),
                          ("baseline_json", None), ("evaluation_json", None),
                          ("guardrails_json", []), ("payload_json", {})):
        if key in result:
            result[key.removesuffix("_json")] = _json(result.pop(key), fallback)
    return result


def _latest_experiment(conn: sqlite3.Connection, recommendation_id: int):
    return conn.execute(
        "SELECT * FROM improvement_experiments WHERE recommendation_id=? ORDER BY id DESC LIMIT 1",
        (recommendation_id,),
    ).fetchone()


def _guardrails(conn, recommendation: dict, experiment: dict,
                baseline_start: datetime, baseline_end: datetime,
                evaluation_start: datetime, evaluation_end: datetime) -> list[dict]:
    metric = experiment["primary_metric"]
    checks = []
    if metric == "throughput_per_hour":
        checks = [("defect_rate", 2.0, "absolute_points"), ("downtime_minutes_per_hour", 10.0, "relative")]
    elif metric in ("median_cycle_time_s",):
        checks = [("defect_rate", 2.0, "absolute_points")]
    elif metric in ("defect_rate", "downtime_minutes_per_hour"):
        checks = [("throughput_per_hour", 10.0, "relative")]
    results = []
    for guard_metric, tolerance, mode in checks:
        before = metric_samples(conn, guard_metric, recommendation["target_type"], recommendation["target_key"], baseline_start, baseline_end)
        after = metric_samples(conn, guard_metric, recommendation["target_type"], recommendation["target_key"], evaluation_start, evaluation_end)
        if len(before) < 3 or len(after) < 3:
            results.append({"metric": guard_metric, "status": "unavailable", "reason": "Fewer than 3 samples in one window"})
            continue
        before_value, after_value = mean(before), mean(after)
        if mode == "absolute_points":
            regression = max(0.0, (after_value - before_value) * 100)
        elif before_value > 1e-9:
            regression = max(0.0, (before_value - after_value) / before_value * 100)
        else:
            regression = 0.0
        results.append({
            "metric": guard_metric, "status": "pass" if regression <= tolerance else "fail",
            "baseline": round(before_value, 6), "evaluation": round(after_value, 6),
            "regression": round(regression, 3), "tolerance": tolerance,
            "mode": mode,
        })
    return results


def act(conn: sqlite3.Connection, recommendation_id: int, payload: dict,
        now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    recommendation_row = conn.execute(
        "SELECT * FROM improvement_recommendations WHERE id=?", (recommendation_id,)
    ).fetchone()
    if not recommendation_row:
        raise KeyError(f"Improvement recommendation {recommendation_id} not found")
    recommendation = _row_dict(recommendation_row)
    expected = payload.get("expected_version")
    if expected is not None and int(expected) != int(recommendation["version"]):
        raise ValueError("Recommendation changed; refresh before acting")
    action = payload["action"]
    actor = payload.get("actor", "operator")
    notes = payload.get("notes")
    current = recommendation["status"]
    experiment_row = _latest_experiment(conn, recommendation_id)

    if action == "accept":
        if current not in ({"proposed", "rejected", "completed", "cancelled"} | TERMINAL):
            raise ValueError(f"Cannot accept a recommendation in '{current}' status")
        metric = payload.get("primary_metric") or recommendation.get("metric_hint")
        experiment_id = None
        if metric:
            if metric not in METRICS:
                raise ValueError(f"Unsupported improvement metric '{metric}'")
            direction = payload.get("target_direction") or recommendation.get("target_direction") or METRICS[metric]["direction"]
            if direction not in ("increase", "decrease"):
                raise ValueError("Target direction must be increase or decrease")
            owner = payload.get("owner") or actor
            hypothesis = payload.get("hypothesis") or f"{recommendation['action']} will improve {METRICS[metric]['label'].lower()}."
            cursor = conn.execute(
                """INSERT INTO improvement_experiments
                   (recommendation_id,status,owner,hypothesis,primary_metric,target_direction,target_delta_pct,
                    baseline_hours,evaluation_hours,min_samples,design_type,confounders_json,notes,created_at,updated_at)
                   VALUES (?,'accepted',?,?,?,?,?,?,?,?,'before_after',?,?,?,?)""",
                (recommendation_id, owner, hypothesis, metric, direction,
                 float(payload.get("target_delta_pct", 5)), int(payload.get("baseline_hours", 8)),
                 int(payload.get("evaluation_hours", 8)), int(payload.get("min_samples", 4)),
                 json.dumps(payload.get("confounders", [])), notes, _iso(now), _iso(now)),
            )
            experiment_id = cursor.lastrowid
        conn.execute(
            """UPDATE improvement_recommendations SET status='accepted',owner=?,resolution_notes=?,
               version=version+1,updated_at=? WHERE id=?""",
            (payload.get("owner") or actor, notes, _iso(now), recommendation_id),
        )
        _event(conn, recommendation_id, experiment_id, "accepted", current, "accepted", actor, notes, payload, now)

    elif action == "reject":
        if current not in ("proposed", "accepted"):
            raise ValueError(f"Cannot reject a recommendation in '{current}' status")
        if experiment_row and experiment_row["status"] == "accepted":
            conn.execute("UPDATE improvement_experiments SET status='cancelled',version=version+1,updated_at=? WHERE id=?", (_iso(now), experiment_row["id"]))
        conn.execute("UPDATE improvement_recommendations SET status='rejected',resolution_notes=?,version=version+1,updated_at=? WHERE id=?", (notes, _iso(now), recommendation_id))
        _event(conn, recommendation_id, experiment_row["id"] if experiment_row else None, "rejected", current, "rejected", actor, notes, {}, now)

    elif action == "implement":
        if current != "accepted" or not experiment_row or experiment_row["status"] != "accepted":
            raise ValueError("Accept a measurable recommendation before implementation")
        experiment = _row_dict(experiment_row)
        baseline_end = now
        baseline_start = now - timedelta(hours=int(experiment["baseline_hours"]))
        samples = metric_samples(conn, experiment["primary_metric"], recommendation["target_type"], recommendation["target_key"], baseline_start, baseline_end)
        if len(samples) < int(experiment["min_samples"]):
            raise ValueError(f"Baseline has {len(samples)} samples; {experiment['min_samples']} required")
        baseline = _summary(experiment["primary_metric"], samples, baseline_start, baseline_end)
        due = now + timedelta(hours=int(experiment["evaluation_hours"]))
        conn.execute(
            """UPDATE improvement_experiments SET status='evaluating',baseline_start=?,baseline_end=?,
               implemented_at=?,evaluation_due_at=?,baseline_json=?,confounders_json=?,notes=COALESCE(?,notes),
               version=version+1,updated_at=? WHERE id=?""",
            (_iso(baseline_start), _iso(baseline_end), _iso(now), _iso(due), json.dumps(baseline),
             json.dumps(payload.get("confounders", experiment["confounders"])), notes, _iso(now), experiment["id"]),
        )
        conn.execute("UPDATE improvement_recommendations SET status='evaluating',version=version+1,updated_at=? WHERE id=?", (_iso(now), recommendation_id))
        _event(conn, recommendation_id, experiment["id"], "implemented", current, "evaluating", actor, notes, {"baseline": baseline, "evaluation_due_at": _iso(due)}, now)

    elif action == "evaluate":
        if current != "evaluating" or not experiment_row or experiment_row["status"] != "evaluating":
            raise ValueError("Only an implemented experiment can be evaluated")
        experiment = _row_dict(experiment_row)
        due = _parse(experiment["evaluation_due_at"])
        if now < due:
            raise ValueError(f"Evaluation window is still open until {experiment['evaluation_due_at']}")
        baseline_start, baseline_end = _parse(experiment["baseline_start"]), _parse(experiment["baseline_end"])
        evaluation_start, evaluation_end = _parse(experiment["implemented_at"]), due
        baseline_samples = metric_samples(conn, experiment["primary_metric"], recommendation["target_type"], recommendation["target_key"], baseline_start, baseline_end)
        evaluation_samples = metric_samples(conn, experiment["primary_metric"], recommendation["target_type"], recommendation["target_key"], evaluation_start, evaluation_end)
        evaluation = _summary(experiment["primary_metric"], evaluation_samples, evaluation_start, evaluation_end)
        effect, ci_lower, ci_upper = _effect(experiment["primary_metric"], experiment["target_direction"], baseline_samples, evaluation_samples, f"{recommendation_id}:{experiment['id']}")
        guardrails = _guardrails(conn, recommendation, experiment, baseline_start, baseline_end, evaluation_start, evaluation_end)
        enough = len(baseline_samples) >= int(experiment["min_samples"]) and len(evaluation_samples) >= int(experiment["min_samples"])
        guardrail_pass = not any(item["status"] == "fail" for item in guardrails)
        if not enough or effect is None:
            outcome = "inconclusive"
        elif effect >= float(experiment["target_delta_pct"]) and guardrail_pass:
            outcome = "validated" if ci_lower is not None and ci_lower > 0 else "promising"
        else:
            outcome = "ineffective"
        conn.execute(
            """UPDATE improvement_experiments SET status=?,outcome=?,evaluation_json=?,guardrails_json=?,
               effect_pct=?,ci_lower_pct=?,ci_upper_pct=?,notes=COALESCE(?,notes),version=version+1,updated_at=? WHERE id=?""",
            (outcome, outcome, json.dumps(evaluation), json.dumps(guardrails), effect, ci_lower, ci_upper,
             notes, _iso(now), experiment["id"]),
        )
        conn.execute("UPDATE improvement_recommendations SET status=?,resolution_notes=?,version=version+1,updated_at=? WHERE id=?", (outcome, notes, _iso(now), recommendation_id))
        _event(conn, recommendation_id, experiment["id"], "evaluated", current, outcome, actor, notes,
               {"effect_pct": effect, "ci_90_pct": [ci_lower, ci_upper], "guardrails": guardrails}, now)

    elif action == "complete":
        if current != "accepted" or (experiment_row and experiment_row["status"] == "accepted"):
            raise ValueError("Measured actions must be implemented and evaluated")
        conn.execute("UPDATE improvement_recommendations SET status='completed',resolution_notes=?,version=version+1,updated_at=? WHERE id=?", (notes, _iso(now), recommendation_id))
        _event(conn, recommendation_id, None, "completed", current, "completed", actor, notes, {}, now)

    elif action == "cancel":
        if current not in ("accepted", "evaluating"):
            raise ValueError(f"Cannot cancel a recommendation in '{current}' status")
        if experiment_row and experiment_row["status"] in ("accepted", "evaluating"):
            conn.execute("UPDATE improvement_experiments SET status='cancelled',notes=COALESCE(?,notes),version=version+1,updated_at=? WHERE id=?", (notes, _iso(now), experiment_row["id"]))
        conn.execute("UPDATE improvement_recommendations SET status='cancelled',resolution_notes=?,version=version+1,updated_at=? WHERE id=?", (notes, _iso(now), recommendation_id))
        _event(conn, recommendation_id, experiment_row["id"] if experiment_row else None, "cancelled", current, "cancelled", actor, notes, {}, now)
    else:
        raise ValueError(f"Unsupported improvement action '{action}'")
    conn.commit()
    return recommendation_detail(conn, recommendation_id, now)


def recommendation_detail(conn: sqlite3.Connection, recommendation_id: int,
                          now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    row = conn.execute("SELECT * FROM improvement_recommendations WHERE id=?", (recommendation_id,)).fetchone()
    if not row:
        raise KeyError(f"Improvement recommendation {recommendation_id} not found")
    result = _row_dict(row)
    experiments = [_row_dict(item) for item in conn.execute(
        "SELECT * FROM improvement_experiments WHERE recommendation_id=? ORDER BY id DESC", (recommendation_id,)
    ).fetchall()]
    for experiment in experiments:
        experiment["evaluation_ready"] = bool(
            experiment["status"] == "evaluating" and experiment.get("evaluation_due_at")
            and now >= _parse(experiment["evaluation_due_at"])
        )
    result["experiments"] = experiments
    result["latest_experiment"] = experiments[0] if experiments else None
    result["events"] = [_row_dict(item) for item in conn.execute(
        "SELECT * FROM improvement_events WHERE recommendation_id=? ORDER BY id DESC LIMIT 20", (recommendation_id,)
    ).fetchall()]
    result["experiment_eligible"] = bool(result.get("metric_hint"))
    return result


def _learned_patterns(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT ir.id,ir.recommendation_key,ir.title,ir.category,ir.cause_code,ir.target_type,ir.target_key,
                  ie.outcome,ie.effect_pct,ie.implemented_at
           FROM improvement_recommendations ir JOIN improvement_experiments ie ON ie.recommendation_id=ir.id
           WHERE ie.outcome IN ('validated','promising','ineffective') ORDER BY ie.id"""
    ).fetchall()
    grouped = {}
    for row in rows:
        item = grouped.setdefault(row["id"], {"recommendation_id": row["id"], "recommendation_key": row["recommendation_key"],
            "title": row["title"], "category": row["category"], "cause_code": row["cause_code"],
            "target_type": row["target_type"], "target_key": row["target_key"], "outcomes": [], "dates": set()})
        item["outcomes"].append(row["outcome"])
        if row["implemented_at"]:
            item["dates"].add(row["implemented_at"][:10])
    result = []
    for item in grouped.values():
        validated = item["outcomes"].count("validated")
        decisive = len(item["outcomes"])
        success_rate = validated / decisive if decisive else 0
        result.append({key: value for key, value in item.items() if key not in ("outcomes", "dates")} | {
            "experiment_count": decisive, "validated_count": validated,
            "success_rate": round(success_rate, 3), "distinct_dates": len(item["dates"]),
            "promoted": decisive >= 3 and success_rate >= 0.7 and len(item["dates"]) >= 2,
            "advisory_only": True,
        })
    return sorted(result, key=lambda item: (item["promoted"], item["validated_count"]), reverse=True)


def snapshot(conn: sqlite3.Connection, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    rows = conn.execute("SELECT id,status FROM improvement_recommendations ORDER BY updated_at DESC,id DESC").fetchall()
    recommendations = [recommendation_detail(conn, row["id"], now) for row in rows]
    counts = {status: sum(1 for item in recommendations if item["status"] == status)
              for status in {"proposed", "accepted", "evaluating", *TERMINAL}}
    return {
        "generated_at": _iso(now),
        "summary": {
            "total": len(recommendations), "proposed": counts["proposed"],
            "active": counts["accepted"] + counts["evaluating"],
            "evaluable": sum(
                1 for item in recommendations
                if item.get("latest_experiment")
                and item["latest_experiment"].get("evaluation_ready")
            ),
            "validated": counts["validated"], "completed": sum(counts[item] for item in TERMINAL),
        },
        "metrics": METRICS,
        "recommendations": recommendations,
        "learned_patterns": _learned_patterns(conn),
        "guardrail": "HIVE never changes a machine or schedule from an experiment. Promotion remains advisory and requires at least three decisive outcomes across two dates.",
    }
