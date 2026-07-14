"""Discrete-event production twin for evidence-gated schedule comparison."""

from __future__ import annotations

import random
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import simpy
import yaml

import cycle_time
import routing
import sequencer

CONFIG_PATH = Path(__file__).parent.parent / "config" / "simulation.yaml"
POLICIES = ("current", "fifo", "edd", "spt", "material_batch")


def _config(path: Path = CONFIG_PATH) -> dict:
    with open(path) as config_file:
        return yaml.safe_load(config_file) or {}


def _load_jobs(conn: sqlite3.Connection, job_names: list[str] | None = None) -> list[dict]:
    params: list = []
    where = ""
    if job_names:
        where = f"WHERE j.job_name IN ({','.join('?' for _ in job_names)})"
        params = job_names
    else:
        controlled = conn.execute("SELECT COUNT(*) count FROM production_orders").fetchone()["count"]
        if controlled:
            where = "WHERE po.status IN ('ready','released','in_progress')"
    jobs = [dict(row) for row in conn.execute(
        f"""SELECT j.id, j.job_name, j.job_date, j.imported_at, j.total_parts,
                   po.id production_order_id, po.status order_status,
                   po.due_at, po.priority, po.release_sequence
            FROM jobs j LEFT JOIN production_orders po ON po.job_id=j.id
            {where} ORDER BY COALESCE(po.release_sequence, 999999), j.id""", params
    ).fetchall()]
    for job in jobs:
        job["parts"] = [dict(row) for row in conn.execute(
            "SELECT * FROM parts WHERE job_id=? ORDER BY COALESCE(beamsaw_seq, id), id",
            (job["id"],),
        ).fetchall()]
        materials = [part.get("material") or "unknown" for part in job["parts"]]
        job["primary_material"] = max(set(materials), key=materials.count) if materials else "unknown"
    return jobs


def _operation_plan(conn: sqlite3.Connection, jobs: list[dict]) -> tuple[list[dict], dict]:
    parts = []
    operation_count = modeled_count = observed_routes = 0
    missing_models: set[str] = set()
    learned_models = cycle_time.active_models(conn)
    with open(cycle_time.CONFIG_PATH) as config_file:
        cycle_config = yaml.safe_load(config_file) or {}
    for job in jobs:
        for part in job["parts"]:
            route_info = routing.part_route(conn, part)
            quantity = max(int(part.get("qty") or 1), 1)
            operations = []
            for machine_key in route_info["machines"]:
                prediction = cycle_time.estimate_for_part(
                    conn, part, machine_key, learned_models=learned_models,
                    config=cycle_config,
                )
                operation_count += quantity
                if prediction["seconds"] is None or prediction["seconds"] <= 0:
                    missing_models.add(machine_key)
                else:
                    modeled_count += quantity
                operations.append({"machine_key": machine_key, **prediction})
            for unit in range(quantity):
                observed_routes += route_info["confidence"] in ("high", "confirmed")
                parts.append({
                    "id": part["id"], "unit": unit + 1,
                    "job_name": job["job_name"],
                    "material": part.get("material") or "unknown",
                    "route": route_info, "operations": operations,
                })
    part_count = len(parts)
    model_coverage = modeled_count / operation_count if operation_count else 0
    route_coverage = observed_routes / part_count if part_count else 0
    ready = bool(parts) and model_coverage == 1 and route_coverage >= 0.8
    readiness = {
        "status": "ready" if ready else "learning",
        "job_count": len(jobs), "part_count": part_count,
        "operation_count": operation_count,
        "modeled_operations": modeled_count,
        "model_coverage": round(model_coverage, 4),
        "observed_route_coverage": round(route_coverage, 4),
        "missing_models": sorted(missing_models),
        "operational_recommendation": ready,
        "guardrail": ("Schedule recommendations are enabled."
                      if ready else
                      "Results are commissioning what-if scenarios until all operations have cycle models and 80% of routes are observed."),
    }
    return parts, readiness


def readiness(conn: sqlite3.Connection, job_names: list[str] | None = None) -> dict:
    jobs = _load_jobs(conn, job_names)
    _, result = _operation_plan(conn, jobs)
    return result


def _job_processing_time(parts: list[dict], job_name: str) -> float:
    return sum(operation["seconds"] or 0 for part in parts if part["job_name"] == job_name
               for operation in part["operations"])


def _order_jobs(conn: sqlite3.Connection, jobs: list[dict], parts: list[dict], policy: str) -> list[dict]:
    if policy == "current":
        names = [job["job_name"] for job in jobs]
        planned = sequencer.sequence(conn, names)
        position = {job.job_name: job.position for job in planned.jobs}
        return sorted(jobs, key=lambda job: position.get(job["job_name"], 10**9))
    if policy == "fifo":
        return sorted(jobs, key=lambda job: (job["imported_at"] or "", job["id"]))
    if policy == "edd":
        return sorted(jobs, key=lambda job: (job["due_at"] or "9999-12-31", job["id"]))
    if policy == "spt":
        return sorted(jobs, key=lambda job: (_job_processing_time(parts, job["job_name"]), job["id"]))
    if policy == "material_batch":
        return sorted(jobs, key=lambda job: (job["primary_material"], job["due_at"] or "", job["id"]))
    raise ValueError(f"unknown simulation policy: {policy}")


def _single_run(conn: sqlite3.Connection, jobs: list[dict], parts: list[dict],
                policy: str, stochastic: bool, seed: int, cfg: dict,
                simulated_at: datetime) -> dict:
    randomizer = random.Random(seed)
    env = simpy.Environment()
    machine_keys = sorted({operation["machine_key"] for part in parts for operation in part["operations"]})
    resources = {key: simpy.Resource(env, capacity=1) for key in machine_keys}
    busy = defaultdict(float)
    job_completion = defaultdict(float)
    flow_times = []
    setup_count = 0
    setup_time = 0.0
    saw_material = {"gabbiani_pt80": None}
    transfer_s = float(cfg.get("transfer_time_s", 30))
    changeover_s = float(cfg.get("material_changeover_s", 600))

    ordered_jobs = _order_jobs(conn, jobs, parts, policy)
    rank = {job["job_name"]: index for index, job in enumerate(ordered_jobs)}
    ordered_parts = sorted(parts, key=lambda part: (
        rank[part["job_name"]], part["id"], part["unit"]
    ))

    def run_part(part: dict):
        nonlocal setup_count, setup_time
        started = env.now
        for index, operation in enumerate(part["operations"]):
            seconds = operation["seconds"]
            if seconds is None or seconds <= 0:
                return
            machine_key = operation["machine_key"]
            with resources[machine_key].request() as request:
                yield request
                if machine_key == "gabbiani_pt80" and saw_material[machine_key] not in (None, part["material"]):
                    yield env.timeout(changeover_s)
                    busy[machine_key] += changeover_s
                    setup_count += 1
                    setup_time += changeover_s
                if machine_key == "gabbiani_pt80":
                    saw_material[machine_key] = part["material"]
                duration = float(seconds)
                if stochastic:
                    model = cycle_time.active_model(conn, machine_key)
                    cv = float(model["residual_cv"] or 0) if model else 0
                    duration = max(1, randomizer.gauss(duration, duration * min(cv, 0.5)))
                yield env.timeout(duration)
                busy[machine_key] += duration
            if index < len(part["operations"]) - 1:
                yield env.timeout(transfer_s)
        job_completion[part["job_name"]] = max(job_completion[part["job_name"]], env.now)
        flow_times.append(env.now - started)

    for part in ordered_parts:
        env.process(run_part(part))
    env.run()
    makespan = float(env.now)
    due_offsets = {}
    for job in jobs:
        if not job.get("due_at"):
            continue
        due = datetime.fromisoformat(job["due_at"].replace("Z", "+00:00"))
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        due_offsets[job["job_name"]] = (due.astimezone(timezone.utc) - simulated_at).total_seconds()
    tardiness = {
        name: max(0.0, job_completion.get(name, makespan) - offset)
        for name, offset in due_offsets.items()
    }
    utilization = {key: round(value / makespan, 4) if makespan else 0
                   for key, value in busy.items()}
    return {
        "policy": policy,
        "job_order": [job["job_name"] for job in ordered_jobs],
        "makespan_s": round(makespan, 1),
        "throughput_parts_per_hour": round(len(flow_times) / (makespan / 3600), 3) if makespan else 0,
        "average_flow_time_s": round(sum(flow_times) / len(flow_times), 1) if flow_times else None,
        "setup_count": setup_count,
        "setup_time_s": round(setup_time, 1),
        "jobs_with_due_dates": len(due_offsets),
        "late_jobs": sum(value > 0 for value in tardiness.values()),
        "total_tardiness_s": round(sum(tardiness.values()), 1),
        "maximum_tardiness_s": round(max(tardiness.values()), 1) if tardiness else 0,
        "machine_utilization": utilization,
        "job_completion_s": {key: round(value, 1) for key, value in job_completion.items()},
    }


def compare(conn: sqlite3.Connection, job_names: list[str] | None = None,
            policies: list[str] | None = None, stochastic: bool = False,
            seed: int = 1) -> dict:
    selected = policies or list(POLICIES)
    unknown = sorted(set(selected) - set(POLICIES))
    if unknown:
        raise ValueError(f"unknown policies: {', '.join(unknown)}")
    jobs = _load_jobs(conn, job_names)
    parts, readiness_result = _operation_plan(conn, jobs)
    if not jobs:
        return {"readiness": readiness_result, "scenarios": [], "recommendation": None}
    if readiness_result["model_coverage"] < 1:
        return {"readiness": readiness_result, "scenarios": [], "recommendation": None}

    cfg = _config()
    simulated_at = datetime.now(timezone.utc)
    scenarios = [_single_run(conn, jobs, parts, policy, stochastic, seed, cfg, simulated_at)
                 for policy in selected]
    ranked = sorted(scenarios, key=lambda result: (
        result["total_tardiness_s"], result["late_jobs"],
        result["makespan_s"], result["setup_time_s"]
    ))
    recommendation = None
    if readiness_result["operational_recommendation"] and ranked:
        recommendation = {
            "policy": ranked[0]["policy"],
            "basis": "lowest total tardiness, late-job count, makespan, then setup time",
        }
    return {
        "readiness": readiness_result,
        "mode": "stochastic" if stochastic else "deterministic",
        "simulated_at": simulated_at.isoformat(),
        "seed": seed,
        "assumptions": {
            "transfer_time_s": cfg.get("transfer_time_s", 30),
            "material_changeover_s": cfg.get("material_changeover_s", 600),
            "due_dates": "Only production_order.due_at is treated as contractual",
        },
        "scenarios": scenarios,
        "recommendation": recommendation,
    }
