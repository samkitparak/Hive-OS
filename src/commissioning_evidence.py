"""Guided, assumption-isolated factory measurement studies."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import random
import sqlite3
import statistics
import uuid
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Iterable

import commissioning_lab


METHOD_VERSION = "hive-guided-time-study-v1"
PACK_FORMAT = "hive-commissioning-evidence-pack"
MEASUREMENT_METHODS = {
    "stopwatch", "video_review", "machine_log", "controller_counter", "operator_scan",
}
SEGMENTS = (
    "queue_s", "setup_s", "load_s", "process_s", "blocked_s", "starved_s",
    "unload_s", "quality_s", "rework_s",
)
OCCUPANCY_SEGMENTS = (
    "setup_s", "load_s", "process_s", "blocked_s", "unload_s", "quality_s", "rework_s",
)
CSV_FIELDS = (
    "source_record_id", "measured_at", "shift_key", "measurement_method", "observer",
    "product_family", "program_key", "unit_count", "operator_count", *SEGMENTS,
    "total_s", "good_units", "reject_units", "notes",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sha(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _percentile(values: list[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower))


def _stats(values: list[float]) -> dict:
    if not values:
        return {key: 0.0 for key in ("min", "p10", "median", "mean", "p90", "max", "mad")}
    median = statistics.median(values)
    return {
        "min": round(min(values), 3), "p10": round(_percentile(values, 0.10), 3),
        "median": round(median, 3), "mean": round(statistics.fmean(values), 3),
        "p90": round(_percentile(values, 0.90), 3), "max": round(max(values), 3),
        "mad": round(statistics.median(abs(value - median) for value in values), 3),
    }


def _bootstrap_median(values: list[float], seed: int, samples: int = 500) -> dict:
    if not values:
        return {"samples": samples, "confidence": 0.90, "lower": 0.0, "upper": 0.0}
    rng = random.Random(seed)
    medians = [
        statistics.median(rng.choices(values, k=len(values)))
        for _ in range(samples)
    ]
    return {
        "samples": samples, "confidence": 0.90,
        "lower": round(_percentile(medians, 0.05), 3),
        "upper": round(_percentile(medians, 0.95), 3),
    }


def _iso(value: object, label: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.isoformat()


def _number(value: object, label: str, *, minimum: float = 0,
            maximum: float = 604800) -> float:
    try:
        result = float(0 if value in (None, "") else value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be numeric") from error
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"{label} must be between {minimum:g} and {maximum:g}")
    return result


def _integer(value: object, label: str, *, minimum: int = 0, maximum: int = 10000,
             optional: bool = False) -> int | None:
    if optional and value in (None, ""):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be an integer") from error
    if not minimum <= result <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return result


def _study_row(conn: sqlite3.Connection, study_id: int) -> dict:
    row = conn.execute(
        """SELECT s.*,m.machine_key,m.name machine_name
           FROM commissioning_evidence_studies s
           JOIN machines m ON m.id=s.machine_id WHERE s.id=?""",
        (study_id,),
    ).fetchone()
    if not row:
        raise KeyError(f"Commissioning evidence study {study_id} not found")
    return dict(row)


def _event(conn: sqlite3.Connection, study_id: int, event_type: str, actor: str,
           *, from_status: str | None = None, to_status: str | None = None,
           details: dict | None = None) -> None:
    conn.execute(
        """INSERT INTO commissioning_evidence_events
           (study_id,event_type,actor,from_status,to_status,details_json,ts)
           VALUES (?,?,?,?,?,?,?)""",
        (study_id, event_type, actor, from_status, to_status, _json(details or {}), _now()),
    )


def protocols(conn: sqlite3.Connection) -> list[dict]:
    catalog = commissioning_lab.catalog()
    lab = commissioning_lab.snapshot(conn)
    latest = lab.get("latest") if not lab.get("stale") else None
    priorities = latest["result"].get("measurement_priorities", []) if latest else []
    priority_by_key = {item["machine_key"]: item for item in priorities}
    rank_by_key = {item["machine_key"]: index + 1 for index, item in enumerate(priorities)}
    studies = Counter(
        row["machine_key"] for row in conn.execute(
            """SELECT m.machine_key FROM commissioning_evidence_studies s
               JOIN machines m ON m.id=s.machine_id WHERE s.status!='archived'"""
        ).fetchall()
    )
    result = []
    for machine_key, machine in catalog["machines"].items():
        cycle = machine["cycle_s"]
        uncertainty = (float(cycle["max"]) - float(cycle["min"])) / (2 * float(cycle["mode"]))
        priority = priority_by_key.get(machine_key, {})
        result.append({
            "machine_key": machine_key, "machine_name": machine["label"],
            "priority_rank": rank_by_key.get(machine_key),
            "priority_score": priority.get("priority_score", round(uncertainty, 3)),
            "impact_span_pct": priority.get("impact_span_pct"),
            "prior_cycle_s": cycle, "prior_availability": machine["availability"],
            "prior_basis": machine.get("basis"), "measurement_instruction": machine["measure"],
            "recommended_samples": 20, "recommended_strata": 2,
            "active_studies": studies[machine_key],
            "assumptions_sha256": catalog["sha256"], "production_eligible": False,
        })
    result.sort(key=lambda item: (
        item["priority_rank"] is None,
        item["priority_rank"] if item["priority_rank"] is not None else 999,
        -float(item["priority_score"] or 0),
    ))
    return result


def create_study(conn: sqlite3.Connection, payload: dict) -> dict:
    machine_key = str(payload.get("machine_key") or "").strip()
    protocol = next((item for item in protocols(conn) if item["machine_key"] == machine_key), None)
    if not protocol:
        raise KeyError(f"Virtual factory machine '{machine_key}' not found")
    machine = conn.execute(
        "SELECT id FROM machines WHERE machine_key=? AND active=1", (machine_key,),
    ).fetchone()
    if not machine:
        raise KeyError(f"Active machine '{machine_key}' not found")
    target_samples = _integer(payload.get("target_samples", 20), "target_samples", minimum=5, maximum=5000)
    target_strata = _integer(payload.get("target_strata", 2), "target_strata", minimum=1, maximum=50)
    actor = str(payload.get("actor") or "commissioning").strip()
    title = str(payload.get("title") or f"{protocol['machine_name']} baseline study").strip()
    goal = str(payload.get("goal") or protocol["measurement_instruction"]).strip()
    if not actor or not title or not goal:
        raise ValueError("Study actor, title, and goal are required")
    now = _now()
    study_key = f"CE-{machine_key.upper().replace('_', '-')}-{uuid.uuid4().hex[:10].upper()}"
    cursor = conn.execute(
        """INSERT INTO commissioning_evidence_studies
           (study_key,machine_id,title,goal,method_version,assumptions_sha256,status,
            target_samples,target_strata,created_by,created_at)
           VALUES (?,?,?,?,?,?,'draft',?,?,?,?)""",
        (study_key, machine["id"], title, goal, METHOD_VERSION,
         protocol["assumptions_sha256"], target_samples, target_strata, actor, now),
    )
    _event(conn, cursor.lastrowid, "created", actor, to_status="draft", details={
        "target_samples": target_samples, "target_strata": target_strata,
        "assumptions_sha256": protocol["assumptions_sha256"],
    })
    conn.commit()
    return study_detail(conn, cursor.lastrowid)


def _normalize_observation(payload: dict, actor: str) -> dict:
    method = str(payload.get("measurement_method") or "").strip()
    if method not in MEASUREMENT_METHODS:
        raise ValueError(f"measurement_method must be one of {', '.join(sorted(MEASUREMENT_METHODS))}")
    observer = str(payload.get("observer") or actor).strip()
    product_family = str(payload.get("product_family") or "").strip()
    if not observer or not product_family:
        raise ValueError("observer and product_family are required")
    unit_count = _integer(payload.get("unit_count", 1), "unit_count", minimum=1, maximum=1000)
    operator_count = _integer(payload.get("operator_count", 1), "operator_count", minimum=1, maximum=100)
    values = {field: _number(payload.get(field), field) for field in SEGMENTS}
    if values["process_s"] <= 0:
        raise ValueError("process_s must be greater than zero")
    component_total = sum(values.values())
    raw_total = payload.get("total_s")
    total_s = component_total if raw_total in (None, "") else _number(
        raw_total, "total_s", minimum=0.001
    )
    if total_s + 1e-6 < component_total:
        raise ValueError("total_s cannot be less than the sum of exclusive time segments")
    good_units = _integer(payload.get("good_units"), "good_units", minimum=0,
                          maximum=unit_count, optional=True)
    reject_units = _integer(payload.get("reject_units", 0), "reject_units", minimum=0,
                            maximum=unit_count)
    if good_units is not None and good_units + reject_units > unit_count:
        raise ValueError("good_units plus reject_units cannot exceed unit_count")
    normalized = {
        "measured_at": _iso(payload.get("measured_at"), "measured_at"),
        "shift_key": str(payload.get("shift_key") or "").strip() or None,
        "measurement_method": method, "observer": observer,
        "product_family": product_family,
        "program_key": str(payload.get("program_key") or "").strip() or None,
        "unit_count": unit_count, "operator_count": operator_count,
        **values, "total_s": total_s, "good_units": good_units,
        "reject_units": reject_units,
        "notes": str(payload.get("notes") or "").strip() or None,
    }
    digest = _sha(normalized)
    normalized["source_record_id"] = str(
        payload.get("source_record_id") or f"manual-{digest[:16]}"
    ).strip()
    if not normalized["source_record_id"] or len(normalized["source_record_id"]) > 200:
        raise ValueError("source_record_id must contain 1-200 characters")
    normalized["source_sha256"] = digest
    return normalized


def _duplicate_status(conn: sqlite3.Connection, study_id: int, item: dict) -> str | None:
    row = conn.execute(
        """SELECT source_sha256 FROM commissioning_evidence_observations
           WHERE study_id=? AND source_record_id=?""",
        (study_id, item["source_record_id"]),
    ).fetchone()
    if not row:
        return None
    if row["source_sha256"] == item["source_sha256"]:
        return "duplicate"
    raise ValueError(f"source_record_id '{item['source_record_id']}' conflicts with existing evidence")


def _insert_observation(conn: sqlite3.Connection, study_id: int, item: dict, actor: str) -> tuple[str, int | None]:
    duplicate = _duplicate_status(conn, study_id, item)
    if duplicate:
        return duplicate, None
    columns = [
        "study_id", "source_record_id", "source_sha256", "measured_at", "shift_key",
        "measurement_method", "observer", "product_family", "program_key", "unit_count",
        "operator_count", *SEGMENTS, "total_s", "good_units", "reject_units", "notes",
        "raw_payload_json", "created_by", "created_at",
    ]
    values = [
        study_id, item["source_record_id"], item["source_sha256"], item["measured_at"],
        item["shift_key"], item["measurement_method"], item["observer"],
        item["product_family"], item["program_key"], item["unit_count"],
        item["operator_count"], *(item[field] for field in SEGMENTS), item["total_s"],
        item["good_units"], item["reject_units"], item["notes"], _json(item), actor, _now(),
    ]
    cursor = conn.execute(
        f"INSERT INTO commissioning_evidence_observations ({','.join(columns)}) "
        f"VALUES ({','.join('?' for _ in columns)})",
        values,
    )
    return "accepted", cursor.lastrowid


def _touch_collection(conn: sqlite3.Connection, study: dict, actor: str,
                      accepted: int, duplicates: int) -> None:
    if not accepted:
        return
    target_status = "collecting"
    started_at = study["started_at"] or _now()
    conn.execute(
        """UPDATE commissioning_evidence_studies
           SET status=?,started_at=?,submitted_at=NULL,version=version+1 WHERE id=?""",
        (target_status, started_at, study["id"]),
    )
    _event(conn, study["id"], "observations_added", actor,
           from_status=study["status"], to_status=target_status,
           details={"accepted": accepted, "duplicates": duplicates})


def add_observation(conn: sqlite3.Connection, study_id: int, payload: dict) -> dict:
    study = _study_row(conn, study_id)
    if study["status"] in {"proposal_approved", "proposal_rejected", "archived"}:
        raise ValueError(f"Study in {study['status']} status cannot accept observations")
    actor = str(payload.get("actor") or "commissioning").strip()
    item = _normalize_observation(payload, actor)
    status, observation_id = _insert_observation(conn, study_id, item, actor)
    _touch_collection(conn, study, actor, int(status == "accepted"), int(status == "duplicate"))
    conn.commit()
    return {
        "status": status, "observation_id": observation_id,
        "source_record_id": item["source_record_id"], "source_sha256": item["source_sha256"],
        "study": study_detail(conn, study_id),
    }


def _csv_rows(csv_text: str) -> Iterable[tuple[int, dict]]:
    if len(csv_text.encode("utf-8")) > 10_000_000:
        raise ValueError("Commissioning CSV must be 10 MB or smaller")
    reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))
    if not reader.fieldnames:
        raise ValueError("Commissioning CSV requires a header row")
    unknown = set(reader.fieldnames) - set(CSV_FIELDS)
    if unknown:
        raise ValueError(f"Commissioning CSV contains unknown columns: {', '.join(sorted(unknown))}")
    missing = {"measured_at", "measurement_method", "product_family", "process_s"} - set(reader.fieldnames)
    if missing:
        raise ValueError(f"Commissioning CSV is missing required columns: {', '.join(sorted(missing))}")
    for row_number, row in enumerate(reader, start=2):
        if any(str(value or "").strip() for value in row.values()):
            yield row_number, row


def import_csv(conn: sqlite3.Connection, study_id: int, csv_text: str, *, apply: bool,
               actor: str) -> dict:
    study = _study_row(conn, study_id)
    if study["status"] in {"proposal_approved", "proposal_rejected", "archived"}:
        raise ValueError(f"Study in {study['status']} status cannot accept observations")
    normalized, issues, seen = [], [], {}
    for row_number, row in _csv_rows(csv_text):
        try:
            item = _normalize_observation(row, actor)
            previous = seen.get(item["source_record_id"])
            if previous and previous != item["source_sha256"]:
                raise ValueError(
                    f"source_record_id '{item['source_record_id']}' conflicts within this CSV"
                )
            status = "duplicate_file" if previous else _duplicate_status(conn, study_id, item)
            seen[item["source_record_id"]] = item["source_sha256"]
            normalized.append((row_number, item, status))
        except ValueError as error:
            issues.append({"row": row_number, "detail": str(error)})
    summary = {
        "rows_seen": len(normalized) + len(issues),
        "rows_valid": len(normalized), "rows_invalid": len(issues),
        "duplicates": sum(status in {"duplicate", "duplicate_file"} for _, _, status in normalized),
        "issues": issues[:100], "ready_to_apply": bool(normalized) and not issues,
        "applied": False, "accepted": 0,
    }
    if not apply:
        return summary
    if issues or not normalized:
        raise ValueError("CSV import must pass validation before it can be applied")
    accepted = duplicates = 0
    try:
        for _, item, _ in normalized:
            status, _ = _insert_observation(conn, study_id, item, actor)
            accepted += int(status == "accepted")
            duplicates += int(status == "duplicate")
        _touch_collection(conn, study, actor, accepted, duplicates)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    summary.update({"applied": True, "accepted": accepted, "duplicates": duplicates})
    return summary


def _observation_values(rows: list[dict]) -> tuple[list[float], list[float]]:
    occupancy = [sum(float(row[field]) for field in OCCUPANCY_SEGMENTS) / row["unit_count"] for row in rows]
    flow = [float(row["total_s"]) / row["unit_count"] for row in rows]
    return occupancy, flow


def _group_stats(rows: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[row["product_family"]].append(row)
    result = []
    for key, items in groups.items():
        occupancy, flow = _observation_values(items)
        result.append({
            "product_family": key, "sample_count": len(items),
            "unit_count": sum(item["unit_count"] for item in items),
            "occupancy_s_per_unit": _stats(occupancy), "flow_s_per_unit": _stats(flow),
        })
    return sorted(result, key=lambda item: (-item["sample_count"], item["product_family"]))


def analyze(conn: sqlite3.Connection, study_id: int) -> dict:
    study = _study_row(conn, study_id)
    catalog = commissioning_lab.catalog()
    machine = catalog["machines"].get(study["machine_key"])
    if not machine:
        raise ValueError("Study machine no longer exists in the assumption catalog")
    rows = [dict(row) for row in conn.execute(
        """SELECT * FROM commissioning_evidence_observations
           WHERE study_id=? AND validity='accepted' ORDER BY measured_at,id""",
        (study_id,),
    ).fetchall()]
    signature = _sha({
        "study_id": study_id, "method_version": study["method_version"],
        "assumptions_sha256": catalog["sha256"],
        "observations": [(row["id"], row["source_sha256"]) for row in rows],
    })
    occupancy, flow = _observation_values(rows)
    occupancy_stats, flow_stats = _stats(occupancy), _stats(flow)
    bootstrap = _bootstrap_median(occupancy, int(signature[:16], 16))
    median = occupancy_stats["median"]
    ci_relative_width = (
        (bootstrap["upper"] - bootstrap["lower"]) / median if median else None
    )
    strata = {
        (row["product_family"], row["program_key"] or "unspecified") for row in rows
    }
    dates = {row["measured_at"][:10] for row in rows}
    shifts = {row["shift_key"] for row in rows if row["shift_key"]}
    observers = {row["observer"] for row in rows}
    methods = Counter(row["measurement_method"] for row in rows)
    automated = sum(methods[key] for key in ("machine_log", "controller_counter"))
    automated_fraction = automated / len(rows) if rows else 0
    split_drift = None
    if len(occupancy) >= 6:
        midpoint = len(occupancy) // 2
        first, second = statistics.median(occupancy[:midpoint]), statistics.median(occupancy[midpoint:])
        split_drift = abs(second - first) / first if first else None
    checks = [
        {"key": "sample_count", "label": "Representative sample count",
         "passed": len(rows) >= study["target_samples"],
         "detail": f"{len(rows)}/{study['target_samples']} observations"},
        {"key": "strata", "label": "Product/program coverage",
         "passed": len(strata) >= study["target_strata"],
         "detail": f"{len(strata)}/{study['target_strata']} distinct strata"},
        {"key": "days", "label": "Across-day repeatability",
         "passed": len(dates) >= 2, "detail": f"{len(dates)}/2 measurement dates"},
        {"key": "reproducibility", "label": "Observer or automated reproducibility",
         "passed": len(observers) >= 2 or automated_fraction >= 0.8,
         "detail": f"{len(observers)} observers; {automated_fraction:.0%} automated evidence"},
        {"key": "median_uncertainty", "label": "Median uncertainty",
         "passed": len(rows) >= 5 and ci_relative_width is not None and ci_relative_width <= 0.30,
         "detail": "At least 5 samples required" if len(rows) < 5 else
         ("No samples" if ci_relative_width is None else f"90% bootstrap width {ci_relative_width:.1%} of median")},
        {"key": "split_stability", "label": "First/second-half stability",
         "passed": split_drift is not None and split_drift <= 0.20,
         "detail": "At least 6 samples required" if split_drift is None else f"Median drift {split_drift:.1%}"},
    ]
    review_ready = bool(rows) and all(check["passed"] for check in checks)
    prior = machine["cycle_s"]
    proposal = None
    if len(rows) >= 5:
        proposal = {
            "status": "review_ready" if review_ready else "provisional",
            "cycle_s": {
                "min": round(min(occupancy_stats["p10"], bootstrap["lower"]), 3),
                "mode": occupancy_stats["median"],
                "max": round(max(occupancy_stats["p90"], bootstrap["upper"]), 3),
            },
            "availability": None,
            "availability_reason": "Cycle observations do not measure planned uptime, failures, or repair duration.",
            "production_eligible": False,
            "application": "Manual review for config/virtual_factory.yaml only",
        }
    mad = occupancy_stats["mad"]
    outliers = []
    if mad > 0:
        for row, value in zip(rows, occupancy):
            modified_z = 0.6745 * (value - median) / mad
            if abs(modified_z) > 3.5:
                outliers.append({
                    "observation_id": row["id"], "source_record_id": row["source_record_id"],
                    "occupancy_s_per_unit": round(value, 3), "modified_z": round(modified_z, 3),
                })
    quality_units = sum(row["good_units"] + row["reject_units"] for row in rows if row["good_units"] is not None)
    good_units = sum(row["good_units"] for row in rows if row["good_units"] is not None)
    return {
        "generated_at": _now(), "study_id": study_id, "study_key": study["study_key"],
        "machine_key": study["machine_key"], "machine_name": study["machine_name"],
        "status": "review_ready" if review_ready else ("collecting" if rows else "empty"),
        "production_eligible": False, "method_version": study["method_version"],
        "input_signature": signature, "assumptions_sha256": catalog["sha256"],
        "assumptions_stale": study["assumptions_sha256"] != catalog["sha256"],
        "sample_count": len(rows), "unit_count": sum(row["unit_count"] for row in rows),
        "coverage": {
            "strata_count": len(strata), "dates": sorted(dates), "shift_count": len(shifts),
            "observer_count": len(observers), "methods": dict(sorted(methods.items())),
        },
        "occupancy_s_per_unit": occupancy_stats, "flow_s_per_unit": flow_stats,
        "bootstrap_median_90": bootstrap,
        "segment_median_s_per_unit": {
            field: round(statistics.median(float(row[field]) / row["unit_count"] for row in rows), 3)
            if rows else 0.0 for field in SEGMENTS
        },
        "groups": _group_stats(rows), "outliers": outliers,
        "quality": {
            "assessed_units": quality_units, "good_units": good_units,
            "first_pass_yield": round(good_units / quality_units, 4) if quality_units else None,
        },
        "stability": {"first_second_median_drift": round(split_drift, 4) if split_drift is not None else None},
        "prior_comparison": {
            "prior_cycle_s": prior,
            "observed_median_vs_prior_mode_pct": round((median / float(prior["mode"]) - 1) * 100, 2)
            if median else None,
            "observations_inside_prior_range_pct": round(
                sum(float(prior["min"]) <= value <= float(prior["max"]) for value in occupancy)
                / len(occupancy) * 100, 2,
            ) if occupancy else None,
        },
        "checks": checks, "review_ready": review_ready, "proposal": proposal,
        "guardrails": [
            "Commissioning observations never enter production cycle_observations or cycle_models.",
            "An approved proposal remains non-production and can only inform manual assumption review.",
            "Availability requires a separate planned-time, failure, and repair observation window.",
            "Outliers are flagged but never silently removed.",
        ],
    }


def persist_analysis(conn: sqlite3.Connection, study_id: int, actor: str) -> dict:
    result = analyze(conn, study_id)
    conn.execute(
        """INSERT OR IGNORE INTO commissioning_evidence_analyses
           (study_id,input_signature,assumptions_sha256,sample_count,result_json,created_by,created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (study_id, result["input_signature"], result["assumptions_sha256"],
         result["sample_count"], _json(result), actor, result["generated_at"]),
    )
    row = conn.execute(
        """SELECT id,created_at FROM commissioning_evidence_analyses
           WHERE study_id=? AND input_signature=?""",
        (study_id, result["input_signature"]),
    ).fetchone()
    _event(conn, study_id, "analysis_saved", actor, details={
        "analysis_id": row["id"], "input_signature": result["input_signature"],
        "review_ready": result["review_ready"],
    })
    conn.commit()
    return {"analysis_id": row["id"], "created_at": row["created_at"], **result}


def exclude_observation(conn: sqlite3.Connection, study_id: int, observation_id: int,
                        reason: str, actor: str) -> dict:
    study = _study_row(conn, study_id)
    if study["status"] in {"proposal_approved", "proposal_rejected", "archived"}:
        raise ValueError(f"Study in {study['status']} status cannot change evidence validity")
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("An exclusion reason is required")
    row = conn.execute(
        """SELECT id,validity FROM commissioning_evidence_observations
           WHERE id=? AND study_id=?""", (observation_id, study_id),
    ).fetchone()
    if not row:
        raise KeyError(f"Observation {observation_id} not found in study {study_id}")
    if row["validity"] == "excluded":
        return study_detail(conn, study_id)
    conn.execute(
        """UPDATE commissioning_evidence_observations
           SET validity='excluded',exclusion_reason=? WHERE id=?""",
        (reason, observation_id),
    )
    conn.execute(
        """UPDATE commissioning_evidence_studies
           SET status='collecting',submitted_at=NULL,version=version+1 WHERE id=?""",
        (study_id,),
    )
    _event(conn, study_id, "observation_excluded", actor,
           from_status=study["status"], to_status="collecting",
           details={"observation_id": observation_id, "reason": reason})
    conn.commit()
    return study_detail(conn, study_id)


def action(conn: sqlite3.Connection, study_id: int, payload: dict) -> dict:
    study = _study_row(conn, study_id)
    expected = payload.get("expected_version")
    if expected is not None and int(expected) != study["version"]:
        raise ValueError(f"Study changed; expected version {expected}, current version {study['version']}")
    actor = str(payload.get("actor") or "commissioning").strip()
    requested = str(payload.get("action") or "").strip()
    transitions = {
        "start": ({"draft"}, "collecting"),
        "submit_review": ({"collecting"}, "review_ready"),
        "approve_proposal": ({"review_ready"}, "proposal_approved"),
        "reject_proposal": ({"review_ready"}, "proposal_rejected"),
        "archive": ({"draft", "collecting", "review_ready", "proposal_approved", "proposal_rejected"}, "archived"),
    }
    if requested not in transitions:
        raise ValueError("Unsupported commissioning evidence action")
    allowed, target = transitions[requested]
    if study["status"] not in allowed:
        raise ValueError(f"Cannot {requested} a study in {study['status']} status")
    analysis_result = None
    if requested in {"submit_review", "approve_proposal"}:
        candidate = analyze(conn, study_id)
        if not candidate["review_ready"] or candidate["assumptions_stale"]:
            raise ValueError("Study must pass every credibility check against current assumptions")
        analysis_result = persist_analysis(conn, study_id, actor)
        fresh = _study_row(conn, study_id)
        if fresh["version"] != study["version"]:
            raise ValueError("Study changed while its evidence was being analyzed")
        if analyze(conn, study_id)["input_signature"] != candidate["input_signature"]:
            raise ValueError("Study evidence changed while it was being analyzed")
    now = _now()
    submitted_at = now if requested == "submit_review" else study["submitted_at"]
    decided_by = actor if requested in {"approve_proposal", "reject_proposal"} else study["decided_by"]
    decided_at = now if requested in {"approve_proposal", "reject_proposal"} else study["decided_at"]
    notes = str(payload.get("notes") or "").strip() or study["decision_notes"]
    cursor = conn.execute(
        """UPDATE commissioning_evidence_studies
           SET status=?,version=version+1,started_at=COALESCE(started_at,?),submitted_at=?,
               decided_by=?,decided_at=?,decision_notes=? WHERE id=? AND version=?""",
        (target, now, submitted_at, decided_by, decided_at, notes, study_id, study["version"]),
    )
    if cursor.rowcount != 1:
        conn.rollback()
        raise ValueError("Study changed before the transition could be recorded")
    _event(conn, study_id, requested, actor, from_status=study["status"], to_status=target,
           details={"notes": notes, "analysis_id": analysis_result.get("analysis_id") if analysis_result else None})
    conn.commit()
    return study_detail(conn, study_id)


def _analysis_history(conn: sqlite3.Connection, study_id: int) -> list[dict]:
    return [{
        "id": row["id"], "input_signature": row["input_signature"],
        "assumptions_sha256": row["assumptions_sha256"], "sample_count": row["sample_count"],
        "created_by": row["created_by"], "created_at": row["created_at"],
    } for row in conn.execute(
        """SELECT id,input_signature,assumptions_sha256,sample_count,created_by,created_at
           FROM commissioning_evidence_analyses WHERE study_id=? ORDER BY id DESC LIMIT 20""",
        (study_id,),
    ).fetchall()]


def study_detail(conn: sqlite3.Connection, study_id: int) -> dict:
    study = _study_row(conn, study_id)
    observations = [dict(row) for row in conn.execute(
        """SELECT id,source_record_id,source_sha256,measured_at,shift_key,measurement_method,
                  observer,product_family,program_key,unit_count,operator_count,queue_s,setup_s,
                  load_s,process_s,blocked_s,starved_s,unload_s,quality_s,rework_s,total_s,
                  good_units,reject_units,notes,validity,exclusion_reason,created_by,created_at
           FROM commissioning_evidence_observations WHERE study_id=? ORDER BY measured_at,id""",
        (study_id,),
    ).fetchall()]
    events = [{**dict(row), "details": json.loads(row["details_json"])} for row in conn.execute(
        """SELECT id,event_type,actor,from_status,to_status,details_json,ts
           FROM commissioning_evidence_events WHERE study_id=? ORDER BY id DESC LIMIT 100""",
        (study_id,),
    ).fetchall()]
    return {
        **study, "production_eligible": False, "analysis": analyze(conn, study_id),
        "observations": observations, "analysis_history": _analysis_history(conn, study_id),
        "events": events,
    }


def snapshot(conn: sqlite3.Connection) -> dict:
    catalog = commissioning_lab.catalog()
    studies = []
    for row in conn.execute(
        """SELECT s.id,s.study_key,s.title,s.status,s.target_samples,s.target_strata,s.version,
                  s.assumptions_sha256,s.created_by,s.created_at,s.started_at,s.submitted_at,
                  s.decided_by,s.decided_at,m.machine_key,m.name machine_name,
                  COUNT(o.id) observation_count,
                  SUM(CASE WHEN o.validity='accepted' THEN 1 ELSE 0 END) accepted_count
           FROM commissioning_evidence_studies s JOIN machines m ON m.id=s.machine_id
           LEFT JOIN commissioning_evidence_observations o ON o.study_id=s.id
           GROUP BY s.id ORDER BY s.id DESC"""
    ).fetchall():
        item = dict(row)
        item["observation_count"] = int(item["observation_count"] or 0)
        item["accepted_count"] = int(item["accepted_count"] or 0)
        item["production_eligible"] = False
        studies.append(item)
    statuses = Counter(item["status"] for item in studies)
    return {
        "generated_at": _now(), "status": "commissioning_evidence_only",
        "production_eligible": False, "method_version": METHOD_VERSION,
        "product_families": [
            {"key": key, "label": item.get("label", key)}
            for key, item in catalog["families"].items()
        ],
        "protocols": protocols(conn), "studies": studies,
        "summary": {
            "studies": len(studies), "collecting": statuses["collecting"],
            "review_ready": statuses["review_ready"],
            "approved_proposals": statuses["proposal_approved"],
            "observations": sum(item["accepted_count"] for item in studies),
        },
    }


def _csv_template() -> str:
    output = io.StringIO(newline="")
    csv.writer(output).writerow(CSV_FIELDS)
    return output.getvalue()


def build_pack(conn: sqlite3.Connection) -> tuple[bytes, dict]:
    catalog = commissioning_lab.catalog()
    protocol_rows = protocols(conn)
    generated_at = _now()
    files: dict[str, bytes] = {}
    guide = """# HIVE Commissioning Evidence Pack

1. Open machine-protocols.csv and work in priority order.
2. Use one CSV row per exclusive timed observation. All durations are seconds.
3. measured_at must include a timezone, for example 2026-08-03T10:15:00+05:30.
4. Keep queue, setup, load, process, blocked, starved, unload, quality, and rework separate.
5. source_record_id must be stable and unique within one study so imports are repeat-safe.
6. Capture at least two product/program strata, two dates, and two observers unless evidence is automated.
7. Do not edit production cycle models from this pack. HIVE only creates a review proposal.
"""
    files["README.md"] = guide.encode("utf-8")
    protocol_output = io.StringIO(newline="")
    writer = csv.DictWriter(protocol_output, fieldnames=(
        "priority_rank", "machine_key", "machine_name", "recommended_samples",
        "recommended_strata", "prior_min_s", "prior_mode_s", "prior_max_s",
        "measurement_instruction", "prior_basis",
    ))
    writer.writeheader()
    for item in protocol_rows:
        writer.writerow({
            "priority_rank": item["priority_rank"] or "", "machine_key": item["machine_key"],
            "machine_name": item["machine_name"], "recommended_samples": item["recommended_samples"],
            "recommended_strata": item["recommended_strata"],
            "prior_min_s": item["prior_cycle_s"]["min"],
            "prior_mode_s": item["prior_cycle_s"]["mode"],
            "prior_max_s": item["prior_cycle_s"]["max"],
            "measurement_instruction": item["measurement_instruction"],
            "prior_basis": item["prior_basis"],
        })
        files[f"templates/{item['machine_key']}.csv"] = _csv_template().encode("utf-8")
        files[f"protocols/{item['machine_key']}.json"] = json.dumps(item, indent=2).encode("utf-8")
    files["machine-protocols.csv"] = protocol_output.getvalue().encode("utf-8")
    manifest = {
        "format": PACK_FORMAT, "format_version": 1, "generated_at": generated_at,
        "method_version": METHOD_VERSION, "assumptions_sha256": catalog["sha256"],
        "assumptions_version": catalog["version"], "production_eligible": False,
        "files": [{
            "path": path, "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        } for path, content in sorted(files.items())],
    }
    files["manifest.json"] = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    sums = "".join(
        f"{hashlib.sha256(content).hexdigest()}  {path}\n"
        for path, content in sorted(files.items())
    )
    files["SHA256SUMS"] = sums.encode("ascii")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in sorted(files.items()):
            archive.writestr(path, content)
    bundle = output.getvalue()
    return bundle, {
        **manifest, "bundle_sha256": hashlib.sha256(bundle).hexdigest(),
        "bundle_size": len(bundle), "file_count": len(files),
        "filename": f"hive-commissioning-evidence-{generated_at[:10]}.zip",
    }
