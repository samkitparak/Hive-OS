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
import resources as factory_resources
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


def _operation_plan(conn: sqlite3.Connection, jobs: list[dict],
                    resource_status: dict | None = None, *,
                    remaining_only: bool = False,
                    simulated_at: datetime | None = None) -> tuple[list[dict], dict]:
    parts = []
    operation_count = modeled_count = observed_routes = 0
    missing_models: set[str] = set()
    learned_models = cycle_time.active_models(conn)
    with open(cycle_time.CONFIG_PATH) as config_file:
        cycle_config = yaml.safe_load(config_file) or {}
    simulated_at = simulated_at or datetime.now(timezone.utc)
    simulated_at = (simulated_at.replace(tzinfo=timezone.utc) if simulated_at.tzinfo is None
                    else simulated_at.astimezone(timezone.utc))
    for job in jobs:
        for part in job["parts"]:
            route_info = routing.part_route(conn, part)
            quantity = max(int(part.get("qty") or 1), 1)
            progress = [dict(row) for row in conn.execute(
                """SELECT prs.step_index,m.machine_key,
                          COALESCE(ej.completed_qty,0) completed_qty,
                          COALESCE(ej.in_process_qty,0) in_process_qty,
                          ej.started_at
                   FROM part_route_steps prs JOIN machines m ON m.id=prs.machine_id
                   LEFT JOIN execution_jobs ej ON ej.route_step_id=prs.id
                   WHERE prs.part_id=? AND prs.required=1 ORDER BY prs.step_index""",
                (part["id"],),
            ).fetchall()] if remaining_only else []
            operation_templates = []
            for step_index, machine_key in enumerate(route_info["machines"], start=1):
                prediction = cycle_time.estimate_for_part(
                    conn, part, machine_key, learned_models=learned_models,
                    config=cycle_config,
                )
                operation_templates.append({
                    "machine_key": machine_key, "step_index": step_index, **prediction,
                })
            for unit in range(quantity):
                operations = []
                for operation in operation_templates:
                    item = dict(operation)
                    step_progress = next((row for row in progress
                                          if row["step_index"] == item["step_index"]), None)
                    if remaining_only and step_progress:
                        completed = int(step_progress["completed_qty"] or 0)
                        in_process = int(step_progress["in_process_qty"] or 0)
                        if unit < completed:
                            continue
                        if unit < completed + in_process:
                            item["frozen_in_process"] = True
                            if item["seconds"] and step_progress.get("started_at"):
                                started = datetime.fromisoformat(
                                    step_progress["started_at"].replace("Z", "+00:00")
                                )
                                if started.tzinfo is None:
                                    started = started.replace(tzinfo=timezone.utc)
                                elapsed_per_unit = max(
                                    0.0, (simulated_at - started.astimezone(timezone.utc)).total_seconds()
                                ) / max(1, in_process)
                                item["seconds"] = max(1.0, float(item["seconds"]) - elapsed_per_unit)
                    operation_count += 1
                    if item["seconds"] is None or item["seconds"] <= 0:
                        missing_models.add(item["machine_key"])
                    else:
                        modeled_count += 1
                    operations.append(item)
                if not operations:
                    continue
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
    controlled = any(job.get("production_order_id") for job in jobs)
    control_ready = (not controlled or (bool(jobs) and all(
        job.get("order_status") in ("ready", "released", "in_progress") and job.get("due_at")
        for job in jobs
    )))
    resources_ready = not controlled or bool(resource_status and resource_status["resource_ready"])
    ready = (bool(parts) and model_coverage == 1 and route_coverage >= 0.8 and
             control_ready and resources_ready)
    blockers = []
    if controlled and not control_ready:
        blockers.append("all selected orders must be ready or released with due times")
    if controlled and not resources_ready:
        blockers.append("materials, labor, tooling, calendars, WIP, and availability must be verified")
    if model_coverage < 1:
        blockers.append("every operation needs an active cycle model")
    if route_coverage < 0.8:
        blockers.append("at least 80% of routes need observed or operator-confirmed evidence")
    readiness = {
        "status": "ready" if ready else "learning",
        "job_count": len(jobs), "part_count": part_count,
        "operation_count": operation_count,
        "modeled_operations": modeled_count,
        "model_coverage": round(model_coverage, 4),
        "observed_route_coverage": round(route_coverage, 4),
        "missing_models": sorted(missing_models),
        "control_ready": control_ready,
        "resource_ready": resources_ready,
        "resource_checks": resource_status["checks"] if resource_status else [],
        "operational_recommendation": ready,
        "guardrail": ("Schedule recommendations are enabled."
                      if ready else
                      "Commissioning only: " + "; ".join(blockers) + "."),
    }
    return parts, readiness


def readiness(conn: sqlite3.Connection, job_names: list[str] | None = None) -> dict:
    jobs = _load_jobs(conn, job_names)
    resource_status = factory_resources.snapshot(
        conn, [job["job_name"] for job in jobs] if jobs else job_names
    )
    _, result = _operation_plan(conn, jobs, resource_status)
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
                simulated_at: datetime, resource_context: dict,
                ordered_job_names: list[str] | None = None) -> dict:
    randomizer = random.Random(seed)
    env = simpy.Environment()
    machine_keys = sorted({operation["machine_key"] for part in parts for operation in part["operations"]})
    profiles = resource_context["profiles"]
    machine_resources = {
        key: simpy.Resource(env, capacity=max(1, int(profiles.get(key, {}).get("machine_capacity", 1))))
        for key in machine_keys
    }
    labor_resources = {
        key: simpy.Resource(env, capacity=capacity)
        for key, capacity in resource_context["labor"].items() if capacity > 0
    }
    tool_resources = {
        key: simpy.Resource(env, capacity=capacity)
        for key, capacity in resource_context["tooling"].items() if capacity > 0
    }
    buffers = {
        key: simpy.Container(env, capacity=max(1, values["capacity"]),
                             init=min(values["current"], max(1, values["capacity"])))
        for key, values in resource_context["buffers"].items()
    }
    busy = defaultdict(float)
    job_completion = defaultdict(float)
    flow_times = []
    setup_count = 0
    setup_time = 0.0
    calendar_wait = 0.0
    capacity_wait = 0.0
    machine_wait = defaultdict(float)
    labor_busy = defaultdict(float)
    tooling_busy = defaultdict(float)
    blocked_units: list[str] = []
    saw_material = {"gabbiani_pt80": None}
    transfer_s = float(cfg.get("transfer_time_s", 30))
    changeover_s = float(cfg.get("material_changeover_s", 600))

    if ordered_job_names is None:
        ordered_jobs = _order_jobs(conn, jobs, parts, policy)
    else:
        by_name = {job["job_name"]: job for job in jobs}
        ordered_jobs = [by_name[name] for name in ordered_job_names if name in by_name]
        ordered_jobs.extend(job for job in jobs if job["job_name"] not in ordered_job_names)
    rank = {job["job_name"]: index for index, job in enumerate(ordered_jobs)}
    ordered_parts = sorted(parts, key=lambda part: (
        rank[part["job_name"]], part["id"], part["unit"]
    ))

    def run_part(part: dict):
        nonlocal setup_count, setup_time, calendar_wait, capacity_wait
        started = env.now
        pending_buffer = None
        for index, operation in enumerate(part["operations"]):
            seconds = operation["seconds"]
            if seconds is None or seconds <= 0:
                blocked_units.append(f"{part['id']}:{part['unit']}:missing-model")
                return
            machine_key = operation["machine_key"]
            profile = profiles.get(machine_key, {})
            role_key = profile.get("role_key")
            pool_key = profile.get("pool_key")
            if role_key and role_key not in labor_resources:
                blocked_units.append(f"{part['id']}:{part['unit']}:no-labor:{role_key}")
                return
            if pool_key and pool_key not in tool_resources:
                blocked_units.append(f"{part['id']}:{part['unit']}:no-tooling:{pool_key}")
                return
            duration = float(seconds)
            if stochastic:
                model = cycle_time.active_model(conn, machine_key)
                cv = float(model["residual_cv"] or 0) if model else 0
                duration = max(1, randomizer.gauss(duration, duration * min(cv, 0.5)))
            while True:
                acquired = []
                wait_started = env.now
                resource_sequence = []
                if role_key:
                    resource_sequence.append(labor_resources[role_key])
                if pool_key:
                    resource_sequence.append(tool_resources[pool_key])
                resource_sequence.append(machine_resources[machine_key])
                for resource in resource_sequence:
                    request = resource.request()
                    yield request
                    acquired.append((resource, request))
                waited = env.now - wait_started
                capacity_wait += waited
                machine_wait[machine_key] += waited
                needs_setup = (machine_key == "gabbiani_pt80" and
                               saw_material[machine_key] not in (None, part["material"]))
                total_duration = duration + (changeover_s if needs_setup else 0)
                delay = factory_resources.next_available_delay(
                    resource_context, machine_key, role_key, pool_key, env.now, total_duration
                )
                if delay is None:
                    for resource, request in reversed(acquired):
                        resource.release(request)
                    blocked_units.append(f"{part['id']}:{part['unit']}:calendar:{machine_key}")
                    return
                if delay > 0:
                    for resource, request in reversed(acquired):
                        resource.release(request)
                    calendar_wait += delay
                    yield env.timeout(delay)
                    continue
                if pending_buffer is not None:
                    yield buffers[pending_buffer].get(1)
                    pending_buffer = None
                try:
                    if needs_setup:
                        yield env.timeout(changeover_s)
                        busy[machine_key] += changeover_s
                        setup_count += 1
                        setup_time += changeover_s
                    if machine_key == "gabbiani_pt80":
                        saw_material[machine_key] = part["material"]
                    yield env.timeout(duration)
                    busy[machine_key] += duration
                    if role_key:
                        labor_busy[role_key] += total_duration
                    if pool_key:
                        tooling_busy[pool_key] += total_duration
                finally:
                    for resource, request in reversed(acquired):
                        resource.release(request)
                break
            if index < len(part["operations"]) - 1:
                yield env.timeout(transfer_s)
                next_machine = part["operations"][index + 1]["machine_key"]
                if next_machine in buffers:
                    yield buffers[next_machine].put(1)
                    pending_buffer = next_machine
        job_completion[part["job_name"]] = max(job_completion[part["job_name"]], env.now)
        flow_times.append(env.now - started)

    for part in ordered_parts:
        env.process(run_part(part))
    env.run()
    makespan = float(env.now)
    incomplete_units = max(0, len(parts) - len(flow_times))
    due_offsets = {}
    for job in jobs:
        if not job.get("due_at"):
            continue
        due = datetime.fromisoformat(job["due_at"].replace("Z", "+00:00"))
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        due_offsets[job["job_name"]] = (due.astimezone(timezone.utc) - simulated_at).total_seconds()
    tardiness = {
        name: max(0.0, job_completion.get(
            name, float(resource_context["horizon_s"]) if incomplete_units else makespan
        ) - offset)
        for name, offset in due_offsets.items()
    }
    utilization = {
        key: round(value / (makespan * machine_resources[key].capacity), 4) if makespan else 0
        for key, value in busy.items()
    }
    labor_utilization = {
        key: round(value / (makespan * labor_resources[key].capacity), 4) if makespan else 0
        for key, value in labor_busy.items()
    }
    tool_utilization = {
        key: round(value / (makespan * tool_resources[key].capacity), 4) if makespan else 0
        for key, value in tooling_busy.items()
    }
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
        "feasible": incomplete_units == 0,
        "completed_parts": len(flow_times),
        "blocked_parts": incomplete_units,
        "blocked_reasons": sorted(set(blocked_units))[:50],
        "calendar_wait_s": round(calendar_wait, 1),
        "capacity_wait_s": round(capacity_wait, 1),
        "machine_wait_s": {key: round(machine_wait.get(key, 0.0), 1)
                           for key in machine_keys},
        "machine_busy_s": {key: round(busy.get(key, 0.0), 1)
                           for key in machine_keys},
        "machine_utilization": utilization,
        "labor_utilization": labor_utilization,
        "tool_utilization": tool_utilization,
        "job_completion_s": {key: round(value, 1) for key, value in job_completion.items()},
        "job_tardiness_s": {key: round(value, 1) for key, value in tardiness.items()},
    }


def compare(conn: sqlite3.Connection, job_names: list[str] | None = None,
            policies: list[str] | None = None, stochastic: bool = False,
            seed: int = 1) -> dict:
    selected = policies or list(POLICIES)
    unknown = sorted(set(selected) - set(POLICIES))
    if unknown:
        raise ValueError(f"unknown policies: {', '.join(unknown)}")
    jobs = _load_jobs(conn, job_names)
    selected_names = [job["job_name"] for job in jobs]
    resource_status = factory_resources.snapshot(conn, selected_names or job_names)
    parts, readiness_result = _operation_plan(conn, jobs, resource_status)
    if not jobs:
        return {"readiness": readiness_result, "scenarios": [], "recommendation": None}
    if readiness_result["model_coverage"] < 1:
        return {"readiness": readiness_result, "scenarios": [], "recommendation": None}

    cfg = _config()
    # Factory calendars are minute-granular; removing sub-minute wall-clock noise
    # keeps identical deterministic comparisons stable.
    simulated_at = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    resource_context = factory_resources.simulation_context(conn, jobs, simulated_at)
    scenarios = [_single_run(conn, jobs, parts, policy, stochastic, seed, cfg, simulated_at,
                             resource_context)
                 for policy in selected]
    ranked = sorted(scenarios, key=lambda result: (
        not result["feasible"], result["total_tardiness_s"], result["late_jobs"],
        result["makespan_s"], result["setup_time_s"]
    ))
    recommendation = None
    if readiness_result["operational_recommendation"] and ranked and ranked[0]["feasible"]:
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
            "resources": resource_status["assumptions"],
        },
        "scenarios": scenarios,
        "recommendation": recommendation,
    }


def compare_orders(conn: sqlite3.Connection, orders: dict[str, list[str]], *,
                   job_names: list[str] | None = None, stochastic: bool = False,
                   seed: int = 1, simulated_at: datetime | None = None) -> dict:
    """Compare explicit recovery orders against only unfinished shop-floor work."""
    if not orders:
        raise ValueError("at least one recovery order is required")
    jobs = _load_jobs(conn, job_names)
    selected_names = [job["job_name"] for job in jobs]
    expected = set(selected_names)
    for strategy, ordered_names in orders.items():
        if len(ordered_names) != len(set(ordered_names)):
            raise ValueError(f"recovery order '{strategy}' contains duplicate jobs")
        if set(ordered_names) != expected:
            raise ValueError(f"recovery order '{strategy}' must contain every selected job")
    simulated_at = simulated_at or datetime.now(timezone.utc)
    simulated_at = (simulated_at.replace(tzinfo=timezone.utc) if simulated_at.tzinfo is None
                    else simulated_at.astimezone(timezone.utc)).replace(microsecond=0)
    resource_status = factory_resources.snapshot(conn, selected_names or job_names)
    parts, readiness_result = _operation_plan(
        conn, jobs, resource_status, remaining_only=True, simulated_at=simulated_at,
    )
    active_names = {part["job_name"] for part in parts}
    jobs = [job for job in jobs if job["job_name"] in active_names]
    normalized_orders = {
        strategy: [name for name in ordered_names if name in active_names]
        for strategy, ordered_names in orders.items()
    }
    if not jobs:
        return {
            "readiness": readiness_result, "mode": "residual",
            "simulated_at": simulated_at.isoformat(), "seed": seed,
            "scenarios": [], "recommendation": None,
            "assumptions": {"execution_progress": "No unfinished operations remain."},
        }
    if readiness_result["model_coverage"] < 1:
        return {
            "readiness": readiness_result, "mode": "residual",
            "simulated_at": simulated_at.isoformat(), "seed": seed,
            "scenarios": [], "recommendation": None,
            "assumptions": {"execution_progress": "Only unfinished operations are modeled."},
        }
    cfg = _config()
    resource_context = factory_resources.simulation_context(conn, jobs, simulated_at)
    scenarios = [
        _single_run(
            conn, jobs, parts, strategy, stochastic, seed, cfg, simulated_at,
            resource_context, ordered_job_names=ordered_names,
        )
        for strategy, ordered_names in normalized_orders.items()
    ]
    return {
        "readiness": readiness_result,
        "mode": "stochastic_residual" if stochastic else "deterministic_residual",
        "simulated_at": simulated_at.isoformat(), "seed": seed,
        "assumptions": {
            "execution_progress": "Completed operations are removed from the model.",
            "in_process_work": (
                "Started work is frozen; elapsed time is spread conservatively across its "
                "in-process quantity and subtracted from modeled duration."
            ),
            "transfer_time_s": cfg.get("transfer_time_s", 30),
            "material_changeover_s": cfg.get("material_changeover_s", 600),
            "resources": resource_status["assumptions"],
        },
        "scenarios": scenarios, "recommendation": None,
    }
