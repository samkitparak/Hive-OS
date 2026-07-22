"""Evidence-gated workload control for production-order release.

The approved schedule is the pre-shop pool. This module periodically reviews
ready orders, but never releases one without a named approval. Numerical
previews remain commissioning-only until policy, station norms, resource state,
due dates, routes, and processing-time evidence are all verified.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yaml

import cycle_time
import execution
import planning
import production_control
import production_loss
import resources as factory_resources


METHOD_VERSION = "corrected-workload-release-v1"
DEFAULT_NORM_MINUTES = 240.0
CONFIG_PATH = Path(__file__).parent.parent / "config" / "cycle_times.yaml"


class VersionConflict(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _dt(value: str | None) -> Optional[datetime]:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _bucket(now: datetime, interval_seconds: int) -> str:
    epoch = int(now.timestamp())
    return _iso(datetime.fromtimestamp(
        epoch - epoch % interval_seconds, tz=timezone.utc
    ))


def sync_defaults(conn: sqlite3.Connection, commit: bool = True) -> None:
    now = _iso(_now())
    keys = tuple(production_loss.PRODUCTION_MACHINE_KEYS)
    marks = ",".join("?" for _ in keys)
    conn.execute(
        f"""INSERT OR IGNORE INTO release_control_norms
            (machine_id,workload_norm_minutes,source,verified,updated_by,updated_at)
            SELECT id,?,'engineering_assumption',0,'schema',?
            FROM machines WHERE active=1 AND machine_key IN ({marks})""",
        (DEFAULT_NORM_MINUTES, now, *keys),
    )
    if commit:
        conn.commit()


def settings(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT * FROM release_control_settings WHERE id=1").fetchone()
    if not row:
        raise RuntimeError("Release-control settings are missing")
    result = dict(row)
    for key in ("auto_review", "allow_starvation_override", "verified"):
        result[key] = bool(result[key])
    return result


def norms(conn: sqlite3.Connection) -> list[dict]:
    sync_defaults(conn)
    return [dict(row) | {"verified": bool(row["verified"])} for row in conn.execute(
        """SELECT rcn.*,m.machine_key,m.name machine_name
           FROM release_control_norms rcn JOIN machines m ON m.id=rcn.machine_id
           ORDER BY m.name"""
    ).fetchall()]


def update_settings(conn: sqlite3.Connection, payload: dict) -> dict:
    current = settings(conn)
    expected = payload.get("expected_version")
    if expected is not None and int(expected) != current["version"]:
        raise VersionConflict(
            f"Release policy changed from version {expected} to {current['version']}"
        )
    updates = {}
    for key in (
        "auto_review", "overload_threshold_ratio", "work_ahead_hours",
        "queue_allowance_hours", "expedite_after_hours", "max_releases_per_review",
        "allow_starvation_override", "verified",
    ):
        if key in payload and payload[key] is not None:
            updates[key] = int(payload[key]) if key in {
                "auto_review", "max_releases_per_review", "allow_starvation_override", "verified"
            } else float(payload[key])
    if payload.get("interval_seconds") is not None:
        interval = int(payload["interval_seconds"])
        if interval < 60 or interval > 86400:
            raise ValueError("Review interval must be between 60 seconds and one day")
        updates["interval_seconds"] = interval
    if not 0 < float(updates.get("overload_threshold_ratio", current["overload_threshold_ratio"])) <= 2:
        raise ValueError("Overload threshold ratio must be greater than zero and at most two")
    if any(float(updates.get(key, current[key])) < 0 for key in (
        "work_ahead_hours", "queue_allowance_hours", "expedite_after_hours"
    )):
        raise ValueError("Release-control hour settings cannot be negative")
    maximum = int(updates.get("max_releases_per_review", current["max_releases_per_review"]))
    if maximum < 1 or maximum > 20:
        raise ValueError("Maximum releases per review must be between 1 and 20")
    if not updates:
        return current
    updates.update({
        "source": "manual", "version": current["version"] + 1,
        "updated_by": payload.get("actor", "operator"), "updated_at": _iso(_now()),
    })
    columns = ",".join(f"{key}=?" for key in updates)
    cursor = conn.execute(
        f"UPDATE release_control_settings SET {columns} WHERE id=1 AND version=?",
        (*updates.values(), current["version"]),
    )
    if cursor.rowcount != 1:
        conn.rollback()
        raise VersionConflict("Release policy was changed by another operator")
    conn.commit()
    return settings(conn)


def update_norm(conn: sqlite3.Connection, machine_key: str, payload: dict) -> dict:
    sync_defaults(conn)
    current = conn.execute(
        """SELECT rcn.* FROM release_control_norms rcn JOIN machines m ON m.id=rcn.machine_id
           WHERE m.machine_key=?""", (machine_key,),
    ).fetchone()
    if not current:
        raise KeyError(f"Release norm for machine '{machine_key}' not found")
    expected = payload.get("expected_version")
    if expected is not None and int(expected) != current["version"]:
        raise VersionConflict(
            f"Release norm changed from version {expected} to {current['version']}"
        )
    workload = float(payload["workload_norm_minutes"])
    standard = payload.get("standard_operation_seconds")
    standard = float(standard) if standard is not None else None
    if workload <= 0:
        raise ValueError("Workload norm must be positive")
    if standard is not None and standard <= 0:
        raise ValueError("Standard operation seconds must be positive")
    cursor = conn.execute(
        """UPDATE release_control_norms
           SET workload_norm_minutes=?,standard_operation_seconds=?,source='manual',
               verified=?,version=version+1,updated_by=?,updated_at=?
           WHERE machine_id=? AND version=?""",
        (workload, standard, int(bool(payload.get("verified", False))),
         payload.get("actor", "operator"), _iso(_now()), current["machine_id"], current["version"]),
    )
    if cursor.rowcount != 1:
        conn.rollback()
        raise VersionConflict("Release norm was changed by another operator")
    conn.commit()
    return next(item for item in norms(conn) if item["machine_key"] == machine_key)


def input_signature(conn: sqlite3.Connection) -> str:
    sync_defaults(conn, commit=False)
    policy = settings(conn)
    policy_fields = {key: policy[key] for key in (
        "interval_seconds", "overload_threshold_ratio", "work_ahead_hours",
        "queue_allowance_hours", "expedite_after_hours", "max_releases_per_review",
        "allow_starvation_override", "verified", "version",
    )}
    norm_rows = [dict(row) for row in conn.execute(
        """SELECT machine_id,workload_norm_minutes,standard_operation_seconds,
                  source,verified,version FROM release_control_norms ORDER BY machine_id"""
    ).fetchall()]
    schedule = conn.execute(
        "SELECT id FROM planning_scenarios WHERE status='approved' ORDER BY approved_at DESC LIMIT 1"
    ).fetchone()
    context = conn.execute(
        """SELECT
             (SELECT COALESCE(MAX(id),0) FROM flow_samples) flow_sample_id,
             (SELECT COALESCE(MAX(id),0) FROM constraint_snapshots) constraint_snapshot_id,
             (SELECT COALESCE(MAX(id),0) FROM downtime_events) downtime_id"""
    ).fetchone()
    payload = {
        "factory": planning.factory_signature(conn),
        "scenario_id": schedule["id"] if schedule else None,
        "policy": policy_fields, "norms": norm_rows, "context": dict(context),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _operation_rows(conn: sqlite3.Connection, order_id: int) -> list[dict]:
    return [dict(row) for row in conn.execute(
        """SELECT prs.id route_step_id,prs.step_index,prs.required_qty,prs.confirmed_qty,
                  prs.status route_status,p.*,m.id machine_id,m.machine_key,m.name machine_name,
                  ej.id execution_job_id,ej.state execution_state,
                  ej.required_qty execution_required_qty,ej.completed_qty execution_completed_qty
           FROM production_orders po JOIN parts p ON p.job_id=po.job_id
           JOIN part_route_steps prs ON prs.part_id=p.id AND prs.required=1
           JOIN machines m ON m.id=prs.machine_id
           LEFT JOIN execution_jobs ej ON ej.route_step_id=prs.id
           WHERE po.id=? ORDER BY p.id,prs.step_index""", (order_id,)
    ).fetchall()]


def _estimate(row: dict, learned: dict, config: dict, norm_by_machine: dict) -> dict:
    prediction = cycle_time.estimate_for_part(
        None, row, row["machine_key"], learned_models=learned, config=config
    )
    if prediction["seconds"] is not None and prediction["seconds"] > 0:
        return prediction
    fallback = norm_by_machine.get(row["machine_key"], {}).get("standard_operation_seconds")
    if fallback:
        return {
            "seconds": float(fallback), "source": "release_norm",
            "confidence": "manual", "model_version": None,
        }
    return {"seconds": None, "source": "unavailable", "confidence": "none", "model_version": None}


def _order_load(conn: sqlite3.Connection, order: dict, learned: dict, config: dict,
                norm_by_machine: dict, *, current: bool) -> dict:
    operations = _operation_rows(conn, order["id"])
    loads = defaultdict(float)
    direct = defaultdict(float)
    missing = []
    total_s = 0.0
    max_step = 0
    for row in operations:
        required = int(row["execution_required_qty"] or row["required_qty"] or row["qty"] or 1)
        completed = int(row["execution_completed_qty"] if row["execution_job_id"] else row["confirmed_qty"] or 0)
        remaining = max(0, required - completed) if current else required
        if not remaining:
            continue
        max_step = max(max_step, int(row["step_index"]))
        prediction = _estimate(row, learned, config, norm_by_machine)
        if prediction["seconds"] is None:
            missing.append({
                "machine_key": row["machine_key"], "machine_name": row["machine_name"],
                "part_id": row["id"], "part_name": row["part_name"],
            })
            continue
        operation_s = float(prediction["seconds"]) * remaining
        loads[row["machine_key"]] += operation_s / max(1, int(row["step_index"]))
        total_s += operation_s
        if row["execution_state"] in {"available", "dispatched", "acknowledged", "running", "held"}:
            direct[row["machine_key"]] += operation_s
    return {
        "loads_s": dict(loads), "direct_s": dict(direct), "missing": missing,
        "operation_count": len(operations), "estimated_operations": len(operations) - len(missing),
        "total_processing_s": round(total_s, 3), "routing_length": max_step,
    }


def _schedule(conn: sqlite3.Connection) -> tuple[dict | None, list[dict]]:
    scenario = conn.execute(
        """SELECT id,name,approved_at,approved_by,selected_policy
           FROM planning_scenarios WHERE status='approved'
           ORDER BY approved_at DESC LIMIT 1"""
    ).fetchone()
    if not scenario:
        return None, []
    rows = [dict(row) for row in conn.execute(
        """SELECT po.id,po.job_id,po.status,po.version,po.due_at,po.priority,
                  po.released_at,po.release_sequence,j.job_name,j.total_parts,psi.position
           FROM production_schedule_items psi
           JOIN production_orders po ON po.id=psi.production_order_id
           JOIN jobs j ON j.id=po.job_id WHERE psi.scenario_id=?
           ORDER BY psi.position""", (scenario["id"],)
    ).fetchall()]
    return dict(scenario), rows


def _score(order: dict, planned_release: Optional[datetime], projected_ratio: float,
           now: datetime) -> float:
    urgency_hours = ((now - planned_release).total_seconds() / 3600) if planned_release else -24
    value = 35 + min(35, max(-20, urgency_hours * 2))
    value += float(order["priority"] or 50) * 0.25
    value += max(0, 12 - int(order["position"] or 1))
    value -= min(25, projected_ratio * 10)
    return round(max(0, min(100, value)), 2)


def _build_review(conn: sqlite3.Connection, now: datetime, actor: str) -> dict:
    sync_defaults(conn, commit=False)
    execution.sync(conn, commit=False)
    policy = settings(conn)
    norm_rows = norms(conn)
    norm_by_machine = {item["machine_key"]: item for item in norm_rows}
    scenario, schedule_rows = _schedule(conn)
    if not scenario:
        return {
            "method_version": METHOD_VERSION, "generated_at": _iso(now),
            "status": "waiting_for_schedule", "scenario": None,
            "summary": {"pre_shop_orders": 0, "release": 0, "expedite": 0, "hold": 0,
                        "actionable": 0, "current_corrected_load_minutes": 0},
            "current_loads": [], "recommendations": [],
            "evidence_gaps": ["No approved production schedule exists."],
            "method_note": "Ready orders remain in the pre-shop pool until an approved release decision.",
        }

    learned = cycle_time.active_models(conn)
    with open(CONFIG_PATH, encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file) or {}
    current_loads = defaultdict(float)
    direct_loads = defaultdict(float)
    current_missing = defaultdict(int)
    on_floor = [row for row in schedule_rows if row["released_at"] and row["status"] not in {"completed", "cancelled"}]
    for order in on_floor:
        load = _order_load(conn, order, learned, config, norm_by_machine, current=True)
        for key, value in load["loads_s"].items():
            current_loads[key] += value
        for key, value in load["direct_s"].items():
            direct_loads[key] += value
        for item in load["missing"]:
            current_missing[item["machine_key"]] += 1

    station_state = []
    ratios = []
    for key, norm in norm_by_machine.items():
        norm_s = float(norm["workload_norm_minutes"]) * 60
        load_s = current_loads.get(key, 0.0)
        ratio = load_s / norm_s if norm_s else 0.0
        ratios.append(ratio)
        station_state.append({
            "machine_key": key, "machine_name": norm["machine_name"],
            "corrected_load_minutes": round(load_s / 60, 2),
            "direct_load_minutes": round(direct_loads.get(key, 0) / 60, 2),
            "workload_norm_minutes": norm["workload_norm_minutes"],
            "load_ratio": round(ratio, 4), "verified": bool(norm["verified"]),
            "unknown_operations": current_missing.get(key, 0),
        })
    shop_load_ratio = max(ratios, default=0.0)
    overloaded = shop_load_ratio >= float(policy["overload_threshold_ratio"])
    provisional = dict(current_loads)
    pool = [row for row in schedule_rows if row["status"] == "ready"]
    candidates = []
    for order in pool:
        load = _order_load(conn, order, learned, config, norm_by_machine, current=False)
        due = _dt(order["due_at"])
        planned_release = None
        if due and not load["missing"]:
            allowance_s = load["routing_length"] * float(policy["queue_allowance_hours"]) * 3600
            planned_release = due - timedelta(seconds=load["total_processing_s"] + allowance_s)
        projected = []
        for key, value in load["loads_s"].items():
            norm = norm_by_machine.get(key)
            if norm:
                projected.append((provisional.get(key, 0) + value) / (float(norm["workload_norm_minutes"]) * 60))
        candidates.append({
            "order": order, "load": load, "due": due, "planned_release": planned_release,
            "projected_max_ratio": max(projected, default=0.0),
        })
    candidates.sort(key=lambda item: (
        0 if item["planned_release"] and item["planned_release"] <= now else 1,
        item["projected_max_ratio"] if item["planned_release"] and item["planned_release"] <= now else 0,
        item["planned_release"] or item["due"] or datetime.max.replace(tzinfo=timezone.utc),
        item["order"]["position"],
    ))

    recommendations = []
    selected = 0
    global_gaps = []
    if not policy["verified"]:
        global_gaps.append("Release policy is an unverified engineering assumption.")
    if current_missing:
        global_gaps.append("Current released workload contains operations without processing-time evidence.")
    for rank, candidate in enumerate(candidates, start=1):
        order, load = candidate["order"], candidate["load"]
        used_keys = set(load["loads_s"])
        missing_norms = sorted({
            item["machine_key"] for item in load["missing"]
        } | {key for key in used_keys if key not in norm_by_machine})
        unverified_norms = sorted(
            key for key in used_keys if not norm_by_machine.get(key, {}).get("verified")
        )
        resource_state = factory_resources.snapshot(conn, [order["job_name"]], sync=False)
        evidence_ready = bool(
            policy["verified"] and candidate["due"] and not load["missing"]
            and not missing_norms and not unverified_norms and resource_state["resource_ready"]
            and not any(current_missing.get(key) for key in used_keys)
        )
        projected_by_station = []
        exceeded = []
        for key, contribution in load["loads_s"].items():
            norm = norm_by_machine.get(key)
            if not norm:
                continue
            projected_s = provisional.get(key, 0) + contribution
            norm_s = float(norm["workload_norm_minutes"]) * 60
            ratio = projected_s / norm_s
            projected_by_station.append({
                "machine_key": key, "machine_name": norm["machine_name"],
                "current_minutes": round(provisional.get(key, 0) / 60, 2),
                "order_contribution_minutes": round(contribution / 60, 2),
                "projected_minutes": round(projected_s / 60, 2),
                "norm_minutes": norm["workload_norm_minutes"],
                "projected_ratio": round(ratio, 4), "norm_verified": bool(norm["verified"]),
            })
            if ratio > 1:
                exceeded.append(key)
        first_operation = min(_operation_rows(conn, order["id"]), key=lambda row: row["step_index"], default=None)
        starvation = bool(first_operation and direct_loads.get(first_operation["machine_key"], 0) <= 0)
        too_early = bool(
            overloaded and candidate["planned_release"]
            and candidate["planned_release"] > now + timedelta(hours=float(policy["work_ahead_hours"]))
        )
        requires_override = False
        if load["missing"] or missing_norms or candidate["due"] is None:
            recommendation, reason = "hold", "missing_release_evidence"
        elif not resource_state["resource_ready"]:
            recommendation, reason = "hold", "resources_changed"
        elif too_early:
            recommendation, reason = "hold", "outside_adaptive_work_ahead"
        elif exceeded:
            if starvation and policy["allow_starvation_override"]:
                recommendation, reason = "expedite", "starvation_override"
                requires_override = True
            else:
                recommendation, reason = "hold", "workload_norm_exceeded"
        elif selected >= int(policy["max_releases_per_review"]):
            recommendation, reason = "hold", "review_release_limit"
        else:
            overdue_s = (now - candidate["planned_release"]).total_seconds() if candidate["planned_release"] else 0
            recommendation = "expedite" if overdue_s >= float(policy["expedite_after_hours"]) * 3600 else "release"
            reason = "planned_release_overdue" if recommendation == "expedite" else "fits_workload_norms"

        if recommendation in {"release", "expedite"}:
            selected += 1
            for key, contribution in load["loads_s"].items():
                provisional[key] = provisional.get(key, 0) + contribution
        if recommendation in {"release", "expedite"} and not evidence_ready:
            reason = "commissioning_only_preview"
        urgency_s = ((now - candidate["planned_release"]).total_seconds()
                     if candidate["planned_release"] else None)
        recommendations.append({
            "production_order_id": order["id"], "order_version": order["version"],
            "job_name": order["job_name"], "schedule_position": order["position"],
            "rank": rank, "recommendation": recommendation, "reason_code": reason,
            "evidence_ready": evidence_ready, "requires_override": requires_override,
            "due_at": order["due_at"],
            "planned_release_at": _iso(candidate["planned_release"]) if candidate["planned_release"] else None,
            "urgency_seconds": round(urgency_s, 1) if urgency_s is not None else None,
            "score": _score(order, candidate["planned_release"], candidate["projected_max_ratio"], now),
            "workload": {
                "total_processing_minutes": round(load["total_processing_s"] / 60, 2),
                "model_coverage": round(load["estimated_operations"] / load["operation_count"], 4) if load["operation_count"] else 0,
                "projected_stations": projected_by_station,
                "missing_operations": load["missing"], "unverified_norms": unverified_norms,
                "resource_gaps": [check["label"] for check in resource_state["checks"] if not check["passed"]],
                "first_station_starved": starvation,
            },
        })
    actionable = sum(item["evidence_ready"] and item["recommendation"] in {"release", "expedite"}
                     for item in recommendations)
    status = "actionable" if actionable else ("commissioning" if pool else "stable")
    return {
        "method_version": METHOD_VERSION, "generated_at": _iso(now), "status": status,
        "scenario": scenario,
        "policy_state": {"verified": policy["verified"], "overloaded": overloaded,
                         "shop_load_ratio": round(shop_load_ratio, 4)},
        "summary": {
            "pre_shop_orders": len(pool), "on_floor_orders": len(on_floor),
            "release": sum(item["recommendation"] == "release" for item in recommendations),
            "expedite": sum(item["recommendation"] == "expedite" for item in recommendations),
            "hold": sum(item["recommendation"] == "hold" for item in recommendations),
            "actionable": actionable,
            "current_corrected_load_minutes": round(sum(current_loads.values()) / 60, 2),
        },
        "current_loads": station_state, "recommendations": recommendations,
        "evidence_gaps": global_gaps,
        "method_note": (
            "Corrected load divides each remaining operation's processing time by its route position. "
            "Urgent orders are load-balanced; every release still requires named approval."
        ),
    }


def _hydrate_review(conn: sqlite3.Connection, row: sqlite3.Row | dict) -> dict:
    item = dict(row)
    result = json.loads(item.pop("result_json"))
    recommendations = []
    for record in conn.execute(
        """SELECT rcr.*,po.due_at,j.job_name,psi.position schedule_position
           FROM release_control_recommendations rcr
           JOIN production_orders po ON po.id=rcr.production_order_id
           JOIN jobs j ON j.id=po.job_id
           LEFT JOIN production_schedule_items psi
             ON psi.production_order_id=po.id AND psi.scenario_id=?
           WHERE rcr.review_id=? ORDER BY rcr.rank""",
        (item["planning_scenario_id"], item["id"]),
    ).fetchall():
        recommendation = dict(record)
        recommendation["evidence_ready"] = bool(recommendation["evidence_ready"])
        recommendation["requires_override"] = bool(recommendation["requires_override"])
        recommendation["workload"] = json.loads(recommendation.pop("workload_json"))
        recommendations.append(recommendation)
    return {**item, **result, "recommendations": recommendations}


def create_review(conn: sqlite3.Connection, actor: str = "hive-release-worker",
                  now: Optional[datetime] = None) -> dict:
    now = now or _now()
    result = _build_review(conn, now, actor)
    signature = input_signature(conn)
    policy = settings(conn)
    bucket = _bucket(now, int(policy["interval_seconds"]))
    existing = conn.execute(
        "SELECT * FROM release_control_reviews WHERE review_bucket=? AND input_signature=?",
        (bucket, signature),
    ).fetchone()
    if existing:
        conn.commit()
        return _hydrate_review(conn, existing)
    recommendations = result.pop("recommendations")
    cursor = conn.execute(
        """INSERT OR IGNORE INTO release_control_reviews
           (review_bucket,input_signature,method_version,planning_scenario_id,status,
            result_json,created_by,created_at) VALUES (?,?,?,?,?,?,?,?)""",
        (bucket, signature, METHOD_VERSION,
         result["scenario"]["id"] if result.get("scenario") else None,
         result["status"], json.dumps(result, sort_keys=True), actor, _iso(now)),
    )
    if cursor.rowcount != 1:
        raced = conn.execute(
            "SELECT * FROM release_control_reviews WHERE review_bucket=? AND input_signature=?",
            (bucket, signature),
        ).fetchone()
        conn.commit()
        return _hydrate_review(conn, raced)
    review_id = cursor.lastrowid
    conn.execute(
        """UPDATE release_control_recommendations SET status='stale'
           WHERE status='open' AND review_id!=?""", (review_id,)
    )
    for item in recommendations:
        conn.execute(
            """INSERT INTO release_control_recommendations
               (review_id,production_order_id,order_version,rank,recommendation,
                reason_code,evidence_ready,requires_override,planned_release_at,
                urgency_seconds,score,workload_json,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (review_id, item["production_order_id"], item["order_version"], item["rank"],
             item["recommendation"], item["reason_code"], int(item["evidence_ready"]),
             int(item["requires_override"]), item["planned_release_at"],
             item["urgency_seconds"], item["score"], json.dumps(item["workload"], sort_keys=True),
             _iso(now)),
        )
    conn.execute(
        """UPDATE release_control_settings SET last_review_at=?,consecutive_failures=0,
              last_error=NULL WHERE id=1""", (_iso(now),)
    )
    conn.commit()
    row = conn.execute("SELECT * FROM release_control_reviews WHERE id=?", (review_id,)).fetchone()
    return _hydrate_review(conn, row)


def runtime(conn: sqlite3.Connection, now: Optional[datetime] = None) -> dict:
    now = now or _now()
    policy = settings(conn)
    last = _dt(policy.get("last_review_at"))
    age = (now - last).total_seconds() if last else None
    if not policy["auto_review"]:
        status = "disabled"
    elif policy["last_error"]:
        status = "error"
    elif age is None:
        status = "starting"
    elif age > max(120, int(policy["interval_seconds"]) * 2):
        status = "stale"
    else:
        status = "healthy"
    return {
        "status": status, "auto_review": policy["auto_review"],
        "interval_seconds": policy["interval_seconds"], "last_review_at": policy["last_review_at"],
        "age_seconds": round(age, 1) if age is not None else None,
        "consecutive_failures": policy["consecutive_failures"], "last_error": policy["last_error"],
    }


def snapshot(conn: sqlite3.Connection, limit: int = 20) -> dict:
    sync_defaults(conn)
    latest = conn.execute(
        "SELECT * FROM release_control_reviews ORDER BY created_at DESC,id DESC LIMIT 1"
    ).fetchone()
    history = [dict(row) for row in conn.execute(
        """SELECT id,review_bucket,status,planning_scenario_id,created_by,created_at
           FROM release_control_reviews ORDER BY created_at DESC,id DESC LIMIT ?""", (limit,)
    ).fetchall()]
    return {
        "method_version": METHOD_VERSION, "settings": settings(conn), "norms": norms(conn),
        "runtime": runtime(conn), "current": _hydrate_review(conn, latest) if latest else None,
        "history": history,
    }


def act(conn: sqlite3.Connection, recommendation_id: int, payload: dict) -> dict:
    row = conn.execute(
        """SELECT rcr.*,rr.input_signature,rr.planning_scenario_id,po.status order_status
           FROM release_control_recommendations rcr
           JOIN release_control_reviews rr ON rr.id=rcr.review_id
           JOIN production_orders po ON po.id=rcr.production_order_id
           WHERE rcr.id=?""", (recommendation_id,),
    ).fetchone()
    if not row:
        raise KeyError(f"Release recommendation {recommendation_id} not found")
    row = dict(row)
    if row["status"] != "open":
        raise ValueError(f"Release recommendation is already {row['status']}")
    action = payload["action"]
    actor = payload.get("actor", "operator")
    now = _iso(_now())
    if action == "dismiss":
        conn.execute(
            "UPDATE release_control_recommendations SET status='dismissed' WHERE id=?",
            (recommendation_id,),
        )
        conn.execute(
            """INSERT INTO release_control_actions
               (recommendation_id,action,actor,notes,override_confirmed,result_json,ts)
               VALUES (?,'dismiss',?,?,0,'{}',?)""",
            (recommendation_id, actor, payload.get("notes"), now),
        )
        conn.commit()
        return {"recommendation_id": recommendation_id, "status": "dismissed"}
    if action != "approve":
        raise ValueError("Release action must be approve or dismiss")
    execution.sync(conn, commit=False)
    if input_signature(conn) != row["input_signature"]:
        conn.execute("UPDATE release_control_recommendations SET status='stale' WHERE id=?", (recommendation_id,))
        conn.commit()
        raise ValueError("Factory inputs changed; generate a fresh release review")
    if row["recommendation"] in {"release", "expedite"}:
        if not row["evidence_ready"]:
            raise ValueError("Commissioning-only release previews cannot be approved")
        if row["requires_override"] and not payload.get("confirm_override"):
            raise ValueError("Confirm the starvation override before approving this release")
        if row["order_status"] != "ready":
            raise ValueError("Only a ready production order can be released")
        order = production_control.update_order(conn, row["production_order_id"], {
            "status": "released", "actor": actor, "expected_version": row["order_version"],
            "notes": payload.get("notes") or f"Approved from release review {row['review_id']}",
        }, commit=False)
        result = {"production_order_id": order["id"], "order_status": order["status"]}
    else:
        result = {"production_order_id": row["production_order_id"], "order_status": row["order_status"]}
    conn.execute("UPDATE release_control_recommendations SET status='applied' WHERE id=?", (recommendation_id,))
    conn.execute(
        """UPDATE release_control_recommendations SET status='stale'
           WHERE review_id=? AND id!=? AND status='open'""",
        (row["review_id"], recommendation_id),
    )
    conn.execute(
        """INSERT INTO release_control_actions
           (recommendation_id,action,actor,notes,override_confirmed,result_json,ts)
           VALUES (?,'approve',?,?,?,?,?)""",
        (recommendation_id, actor, payload.get("notes"), int(bool(payload.get("confirm_override"))),
         json.dumps(result, sort_keys=True), now),
    )
    conn.commit()
    return {"recommendation_id": recommendation_id, "status": "applied", **result}


def automatic_refresh(conn: sqlite3.Connection, now: Optional[datetime] = None) -> dict:
    now = now or _now()
    policy = settings(conn)
    if not policy["auto_review"]:
        return {"status": "disabled", "review": None}
    last = _dt(policy.get("last_review_at"))
    if last and (now - last).total_seconds() < int(policy["interval_seconds"]):
        return {"status": "not_due", "review": None}
    try:
        review = create_review(conn, now=now)
        return {"status": "reviewed", "review_id": review["id"]}
    except Exception as error:
        conn.rollback()
        conn.execute(
            """UPDATE release_control_settings SET consecutive_failures=consecutive_failures+1,
                  last_error=?,last_review_at=? WHERE id=1""",
            (str(error)[:1000], _iso(now)),
        )
        conn.commit()
        raise
