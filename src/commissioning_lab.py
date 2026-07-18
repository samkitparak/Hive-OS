"""Assumption-isolated virtual factory commissioning and sensitivity analysis."""

from __future__ import annotations

import hashlib
import json
import math
import random
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import simpy
import yaml


ROOT = Path(__file__).parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config" / "virtual_factory.yaml"
FORMAT = "hive-virtual-factory-priors"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _percentile(values: list[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower))


def _band(values: list[float], digits: int = 3) -> dict:
    return {
        key: round(_percentile(values, probability), digits)
        for key, probability in (("p10", 0.10), ("p50", 0.50), ("p90", 0.90))
    }


def _triple(value: object, label: str, *, upper: float | None = None) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must define min, mode, and max")
    try:
        minimum, mode, maximum = (float(value[key]) for key in ("min", "mode", "max"))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"{label} must contain numeric min, mode, and max") from error
    if minimum <= 0 or not minimum <= mode <= maximum or (upper is not None and maximum > upper):
        raise ValueError(f"{label} has an invalid triangular range")
    return {"min": minimum, "mode": mode, "max": maximum}


def _validate(cfg: dict) -> None:
    if cfg.get("format") != FORMAT or cfg.get("format_version") != 1:
        raise ValueError("Unsupported virtual factory assumptions format")
    if cfg.get("status") != "assumption_only":
        raise ValueError("Virtual factory assumptions must remain assumption_only")
    if not 1 <= float(cfg.get("shift_hours", 0)) <= 24:
        raise ValueError("shift_hours must be between 1 and 24")
    if not 1 <= int(cfg.get("reference_units_per_shift", 0)) <= 2000:
        raise ValueError("reference_units_per_shift must be between 1 and 2000")
    machines, families = cfg.get("machines"), cfg.get("families")
    if not isinstance(machines, dict) or not machines or not isinstance(families, dict) or not families:
        raise ValueError("Virtual factory requires machines and product families")
    for key, machine in machines.items():
        if not isinstance(machine, dict) or not str(machine.get("label") or "").strip():
            raise ValueError(f"Machine {key} requires a label")
        if not 1 <= int(machine.get("capacity", 0)) <= 20:
            raise ValueError(f"Machine {key} capacity must be 1-20")
        _triple(machine.get("cycle_s"), f"{key}.cycle_s")
        _triple(machine.get("availability"), f"{key}.availability", upper=1.0)
        if not str(machine.get("measure") or "").strip():
            raise ValueError(f"Machine {key} requires an on-site measurement instruction")
    share = 0.0
    for key, family in families.items():
        try:
            family_share = float(family["share"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"Family {key} requires a numeric share") from error
        if family_share <= 0:
            raise ValueError(f"Family {key} share must be positive")
        share += family_share
        route = family.get("route")
        if not isinstance(route, list) or not route:
            raise ValueError(f"Family {key} requires a route")
        for step in route:
            if not isinstance(step, dict) or step.get("machine") not in machines:
                raise ValueError(f"Family {key} references an unknown machine")
            if not 0.05 <= float(step.get("duration_scale", 1)) <= 10:
                raise ValueError(f"Family {key} has an invalid duration scale")
    if abs(share - 1.0) > 1e-9:
        raise ValueError("Product family shares must sum to 1.0")
    intervention_keys: set[str] = set()
    for item in cfg.get("interventions", []):
        key = str(item.get("key") or "")
        if not key or key in intervention_keys or item.get("machine") not in machines:
            raise ValueError("Interventions require unique keys and known machines")
        intervention_keys.add(key)
        if not 0.05 <= float(item.get("duration_scale", 0)) < 1:
            raise ValueError(f"Intervention {key} must reduce duration without reaching zero")


def _load(path: Path | None = None) -> tuple[dict, str, Path]:
    config_path = Path(path or DEFAULT_CONFIG_PATH)
    raw = config_path.read_bytes()
    cfg = yaml.safe_load(raw) or {}
    _validate(cfg)
    return cfg, hashlib.sha256(raw).hexdigest(), config_path


def assumptions(path: Path | None = None) -> dict:
    cfg, digest, config_path = _load(path)
    return {
        "format": cfg["format"], "format_version": cfg["format_version"],
        "version": str(cfg.get("version")), "status": cfg["status"],
        "site": cfg.get("site"), "sha256": digest, "path": str(config_path),
        "machine_count": len(cfg["machines"]), "family_count": len(cfg["families"]),
        "reference_units_per_shift": int(cfg["reference_units_per_shift"]),
        "shift_hours": float(cfg["shift_hours"]), "sources": cfg.get("sources", []),
        "production_eligible": False,
        "guardrail": "Engineering priors cannot activate cycle models, routes, forecasts, schedules, or machine control.",
    }


def _family_counts(cfg: dict) -> dict[str, int]:
    total = int(cfg["reference_units_per_shift"])
    raw = {key: total * float(item["share"]) for key, item in cfg["families"].items()}
    counts = {key: math.floor(value) for key, value in raw.items()}
    remaining = total - sum(counts.values())
    order = sorted(raw, key=lambda key: (raw[key] - counts[key], key), reverse=True)
    for key in order[:remaining]:
        counts[key] += 1
    return counts


def _simulate_once(cfg: dict, seed: int, scales: dict[str, float] | None = None) -> dict:
    scales = scales or {}
    rng, env = random.Random(seed), simpy.Environment()
    machines = cfg["machines"]
    resources = {
        key: simpy.Resource(env, capacity=int(item["capacity"]))
        for key, item in machines.items()
    }
    availability = {
        key: rng.triangular(
            float(item["availability"]["min"]), float(item["availability"]["max"]),
            float(item["availability"]["mode"]),
        ) for key, item in machines.items()
    }
    busy, queue_wait = defaultdict(float), defaultdict(float)
    completions: list[float] = []

    def process_unit(family_key: str, sampled_durations: list[float]):
        for step, duration in zip(cfg["families"][family_key]["route"], sampled_durations):
            machine_key = step["machine"]
            queued_at = env.now
            with resources[machine_key].request() as request:
                yield request
                queue_wait[machine_key] += env.now - queued_at
                yield env.timeout(duration)
                busy[machine_key] += duration
        completions.append(float(env.now))

    counts = _family_counts(cfg)
    for family_key, count in counts.items():
        for _ in range(count):
            sampled_durations = []
            for step in cfg["families"][family_key]["route"]:
                machine_key = step["machine"]
                prior = machines[machine_key]["cycle_s"]
                duration = rng.triangular(
                    float(prior["min"]), float(prior["max"]), float(prior["mode"]),
                )
                duration *= float(step.get("duration_scale", 1))
                duration *= float(scales.get(machine_key, 1))
                sampled_durations.append(duration / availability[machine_key])
            env.process(process_unit(family_key, sampled_durations))
    env.run()
    makespan = float(env.now)
    shift_s = float(cfg["shift_hours"]) * 3600
    utilization = {
        key: busy[key] / (makespan * resources[key].capacity) if makespan else 0.0
        for key in machines
    }
    used = [key for key in machines if busy[key] > 0]
    bottleneck = max(used, key=lambda key: (utilization[key], queue_wait[key], key)) if used else None
    return {
        "makespan_s": makespan,
        "throughput_parts_per_hour": len(completions) / (makespan / 3600) if makespan else 0.0,
        "completed_within_shift": sum(value <= shift_s for value in completions),
        "bottleneck": bottleneck, "machine_utilization": utilization,
        "machine_wait_s": dict(queue_wait),
    }


def _runs(cfg: dict, samples: int, seed: int,
          scales: dict[str, float] | None = None) -> list[dict]:
    return [_simulate_once(cfg, seed + index * 1009, scales) for index in range(samples)]


def _summary(cfg: dict, runs: list[dict]) -> dict:
    constraints = Counter(run["bottleneck"] for run in runs if run["bottleneck"])
    ranked = [{
        "machine_key": key, "machine_name": item["label"],
        "bottleneck_probability": round(constraints[key] / len(runs), 4),
        "utilization": _band([run["machine_utilization"].get(key, 0) for run in runs], 4),
        "queue_wait_s": _band([run["machine_wait_s"].get(key, 0) for run in runs], 1),
    } for key, item in cfg["machines"].items()]
    ranked.sort(key=lambda item: (
        item["bottleneck_probability"], item["utilization"]["p50"], item["queue_wait_s"]["p50"]
    ), reverse=True)
    return {
        "throughput_parts_per_hour": _band([run["throughput_parts_per_hour"] for run in runs]),
        "makespan_s": _band([run["makespan_s"] for run in runs], 1),
        "completed_within_shift": _band([run["completed_within_shift"] for run in runs], 1),
        "constraints": ranked,
    }


def analyze(*, samples: int = 20, seed: int = 1,
            config_path: Path | None = None) -> dict:
    if not 10 <= samples <= 100:
        raise ValueError("Virtual lab samples must be between 10 and 100")
    if not 0 <= seed <= 2_147_483_647:
        raise ValueError("Virtual lab seed must be between 0 and 2147483647")
    cfg, digest, path = _load(config_path)
    baseline = _summary(cfg, _runs(cfg, samples, seed))
    baseline_throughput = baseline["throughput_parts_per_hour"]["p50"]
    sensitivity_samples = min(samples, 30)
    priorities = []
    for machine_key, machine in cfg["machines"].items():
        faster = _summary(cfg, _runs(cfg, sensitivity_samples, seed, {machine_key: 0.8}))
        slower = _summary(cfg, _runs(cfg, sensitivity_samples, seed, {machine_key: 1.2}))
        fast_value = faster["throughput_parts_per_hour"]["p50"]
        slow_value = slower["throughput_parts_per_hour"]["p50"]
        impact_span = ((fast_value - slow_value) / baseline_throughput * 100) if baseline_throughput else 0
        cycle = machine["cycle_s"]
        uncertainty = (float(cycle["max"]) - float(cycle["min"])) / (2 * float(cycle["mode"]))
        priorities.append({
            "machine_key": machine_key, "machine_name": machine["label"],
            "impact_span_pct": round(impact_span, 2),
            "local_elasticity": round(impact_span / 40, 3),
            "prior_relative_uncertainty": round(uncertainty, 3),
            "priority_score": round(max(0, impact_span) * uncertainty, 3),
            "faster_p50_throughput": fast_value, "slower_p50_throughput": slow_value,
            "measure_on_site": machine["measure"], "basis": machine.get("basis"),
        })
    priorities.sort(key=lambda item: (item["priority_score"], item["impact_span_pct"]), reverse=True)

    interventions = []
    for item in cfg.get("interventions", []):
        result = _summary(cfg, _runs(
            cfg, sensitivity_samples, seed, {item["machine"]: float(item["duration_scale"])}
        ))
        throughput, makespan = (
            result["throughput_parts_per_hour"]["p50"], result["makespan_s"]["p50"]
        )
        interventions.append({
            "key": item["key"], "label": item["label"], "machine_key": item["machine"],
            "assumed_duration_reduction_pct": round((1 - float(item["duration_scale"])) * 100, 1),
            "p50_throughput": throughput,
            "modeled_throughput_uplift_pct": round(
                ((throughput / baseline_throughput) - 1) * 100 if baseline_throughput else 0, 2
            ),
            "modeled_makespan_reduction_pct": round(
                (1 - makespan / baseline["makespan_s"]["p50"]) * 100
                if baseline["makespan_s"]["p50"] else 0, 2
            ),
            "production_eligible": False,
        })
    interventions.sort(key=lambda item: (
        item["modeled_throughput_uplift_pct"], item["modeled_makespan_reduction_pct"]
    ), reverse=True)
    return {
        "generated_at": _now(), "status": "assumption_only", "production_eligible": False,
        "samples": samples, "seed": seed, "assumptions_sha256": digest,
        "assumptions_version": str(cfg.get("version")), "config_path": str(path),
        "reference_workload": {
            "units": int(cfg["reference_units_per_shift"]),
            "shift_hours": float(cfg["shift_hours"]), "family_units": _family_counts(cfg),
        },
        "baseline": baseline, "measurement_priorities": priorities,
        "interventions": interventions, "sources": cfg.get("sources", []),
        "guardrails": [
            "Results are screening evidence from broad engineering priors, not production forecasts.",
            "No lab run writes machine events, routes, cycle models, resource truth, forecasts, or schedules.",
            "Modeled intervention uplift is conditional on the reference mix and must be re-run with factory evidence.",
        ],
    }


def run(conn: sqlite3.Connection, *, samples: int = 20, seed: int = 1,
        actor: str = "operator", config_path: Path | None = None) -> dict:
    result = analyze(samples=samples, seed=seed, config_path=config_path)
    cursor = conn.execute(
        """INSERT INTO virtual_factory_runs
           (assumptions_sha256,assumptions_version,sample_count,seed,actor,result_json,created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (result["assumptions_sha256"], result["assumptions_version"], samples, seed,
         actor, json.dumps(result, separators=(",", ":")), result["generated_at"]),
    )
    conn.commit()
    return {"run_id": cursor.lastrowid, **result}


def history(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        """SELECT id,assumptions_sha256,assumptions_version,sample_count,seed,actor,
                  result_json,created_at FROM virtual_factory_runs ORDER BY id DESC LIMIT ?""",
        (max(1, min(int(limit), 100)),),
    ).fetchall()
    return [{
        "id": row["id"], "assumptions_sha256": row["assumptions_sha256"],
        "assumptions_version": row["assumptions_version"], "sample_count": row["sample_count"],
        "seed": row["seed"], "actor": row["actor"], "created_at": row["created_at"],
        "result": json.loads(row["result_json"]),
    } for row in rows]


def snapshot(conn: sqlite3.Connection, config_path: Path | None = None) -> dict:
    current, runs = assumptions(config_path), history(conn)
    latest = runs[0] if runs else None
    return {
        "generated_at": _now(), "assumptions": current, "latest": latest,
        "history": [{key: item[key] for key in (
            "id", "assumptions_sha256", "assumptions_version", "sample_count",
            "seed", "actor", "created_at",
        )} for item in runs],
        "stale": bool(latest and latest["assumptions_sha256"] != current["sha256"]),
    }
