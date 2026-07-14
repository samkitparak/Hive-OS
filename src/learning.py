"""Automatic, conservative cycle-time observation and model learning."""

from __future__ import annotations

import hashlib
import itertools
import json
import sqlite3
from datetime import datetime, timezone

import numpy as np

import cycle_time

MAX_CYCLE_SECONDS = 4 * 60 * 60
MIN_CYCLE_SECONDS = 1


def _parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _payload_duration(raw_payload: str | None) -> float | None:
    if not raw_payload:
        return None
    try:
        value = json.loads(raw_payload)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    duration = value.get("duration_s") or value.get("cycle_time_s")
    try:
        return float(duration) if duration is not None else None
    except (TypeError, ValueError):
        return None


def refresh_cycle_observations(conn: sqlite3.Connection) -> dict:
    """Pair previously unseen cycle ends with the nearest unused cycle start."""
    ends = conn.execute(
        """SELECT me.* FROM machine_events me
           LEFT JOIN cycle_observations co ON co.end_event_id=me.id
           WHERE me.event_type='cycle_end' AND co.id IS NULL
           ORDER BY me.ts, me.id"""
    ).fetchall()
    created = valid = rejected = 0
    for end in ends:
        start = conn.execute(
            """SELECT me.* FROM machine_events me
               LEFT JOIN cycle_observations co ON co.start_event_id=me.id
               WHERE me.machine_id=? AND me.event_type='cycle_start'
                 AND me.ts<=? AND co.id IS NULL
               ORDER BY me.ts DESC, me.id DESC LIMIT 1""",
            (end["machine_id"], end["ts"]),
        ).fetchone()

        duration = None
        source = "event_pair"
        reason = None
        part_id = end["part_id"] or (start["part_id"] if start else None)
        if start:
            duration = (_parse_ts(end["ts"]) - _parse_ts(start["ts"])).total_seconds()
            if start["part_id"] and end["part_id"] and start["part_id"] != end["part_id"]:
                reason = "part_mismatch"
        else:
            duration = _payload_duration(end["raw_payload"])
            source = "payload"
            if duration is None:
                reason = "missing_cycle_start"

        if reason is None and not part_id:
            reason = "missing_part_link"
        if reason is None and (duration is None or duration < MIN_CYCLE_SECONDS):
            reason = "duration_too_short"
        if reason is None and duration > MAX_CYCLE_SECONDS:
            reason = "duration_too_long"

        features_json = None
        if part_id:
            part = conn.execute("SELECT * FROM parts WHERE id=?", (part_id,)).fetchone()
            if part:
                features_json = json.dumps(dict(part), sort_keys=True)
            elif reason is None:
                reason = "missing_part_record"

        validity = "rejected" if reason else "valid"
        conn.execute(
            """INSERT INTO cycle_observations
               (machine_id, part_id, start_event_id, end_event_id, started_at,
                ended_at, duration_s, duration_source, validity, rejection_reason,
                features_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (end["machine_id"], part_id, start["id"] if start else None, end["id"],
             start["ts"] if start else None, end["ts"], duration, source, validity,
             reason, features_json),
        )
        created += 1
        valid += validity == "valid"
        rejected += validity == "rejected"
    conn.commit()
    return {"created": created, "valid": valid, "rejected": rejected}


def _metrics(actual: np.ndarray, predicted: np.ndarray) -> dict:
    residuals = actual - predicted
    mae = float(np.mean(np.abs(residuals)))
    nonzero = np.maximum(np.abs(actual), 1.0)
    mape = float(np.mean(np.abs(residuals) / nonzero))
    denominator = float(np.sum((actual - np.mean(actual)) ** 2))
    r2 = 1 - float(np.sum(residuals ** 2)) / denominator if denominator > 0 else 0.0
    residual_cv = float(np.std(residuals) / max(float(np.mean(actual)), 1.0))
    return {"mae_s": mae, "mape": mape, "r2": r2, "residual_cv": residual_cv}


def _confidence(metrics: dict, validation_count: int) -> tuple[str, str]:
    if (validation_count >= 10 and metrics["mape"] <= 0.15
            and metrics["residual_cv"] <= 0.20 and metrics["r2"] >= 0.60):
        return "high", "stable validation error and strong explained variance"
    if (validation_count >= 3 and metrics["mape"] <= 0.30
            and metrics["residual_cv"] <= 0.35 and metrics["r2"] >= 0.20):
        return "medium", "validation thresholds passed"
    return "low", "validation error or feature coverage is not yet production-ready"


def _nonnegative_fit(matrix: np.ndarray, targets: np.ndarray) -> np.ndarray:
    """Solve tiny NNLS models by checking every possible active feature set."""
    feature_count = matrix.shape[1]
    best = np.zeros(feature_count)
    best_error = float(np.sum(targets ** 2))
    for size in range(1, feature_count + 1):
        for active in itertools.combinations(range(feature_count), size):
            candidate, *_ = np.linalg.lstsq(matrix[:, active], targets, rcond=None)
            if np.any(candidate < -1e-9):
                continue
            coefficients = np.zeros(feature_count)
            coefficients[list(active)] = np.maximum(candidate, 0)
            error = float(np.sum((targets - matrix @ coefficients) ** 2))
            if error < best_error:
                best = coefficients
                best_error = error
    return best


def train_machine(conn: sqlite3.Connection, machine_id: int, machine_key: str) -> dict:
    rows = conn.execute(
        """SELECT id, duration_s, features_json FROM cycle_observations
           WHERE machine_id=? AND validity='valid' AND features_json IS NOT NULL
           ORDER BY ended_at, id""",
        (machine_id,),
    ).fetchall()
    names = cycle_time.coefficient_names(machine_key)
    if not names:
        return {"machine_key": machine_key, "status": "unsupported", "samples": len(rows)}

    matrix = []
    targets = []
    observation_ids = []
    for row in rows:
        part = json.loads(row["features_json"])
        design = cycle_time.design_row(cycle_time.extract_features(part, machine_key))
        if design and row["duration_s"] is not None:
            matrix.append(design)
            targets.append(float(row["duration_s"]))
            observation_ids.append(row["id"])

    X = np.asarray(matrix, dtype=float)
    y = np.asarray(targets, dtype=float)
    sample_count = len(y)
    if sample_count == 0:
        return {"machine_key": machine_key, "status": "collecting", "samples": 0}

    signature_data = f"{machine_id}:" + ",".join(
        f"{oid}:{duration:.3f}" for oid, duration in zip(observation_ids, y)
    )
    signature = hashlib.sha256(signature_data.encode()).hexdigest()
    existing = conn.execute(
        "SELECT status, confidence FROM cycle_models WHERE training_signature=?",
        (signature,),
    ).fetchone()
    if existing:
        return {"machine_key": machine_key, "status": existing["status"],
                "confidence": existing["confidence"], "samples": sample_count,
                "unchanged": True}

    # Keep a constant-like column and only fit varying optional features. This
    # avoids publishing coefficients that the observed parts cannot identify.
    spreads = np.ptp(X, axis=0)
    identified_idx = [i for i, spread in enumerate(spreads) if spread > 1e-9]
    if not identified_idx:
        identified_idx = [0]
    elif 0 not in identified_idx and np.allclose(X[:, 0], X[0, 0]):
        identified_idx.insert(0, 0)
    minimum_samples = max(10, len(identified_idx) * 5)
    if sample_count < minimum_samples:
        return {"machine_key": machine_key, "status": "collecting",
                "samples": sample_count, "required_samples": minimum_samples}

    validation_count = max(3, int(round(sample_count * 0.2)))
    split = sample_count - validation_count
    train_X = X[:split, identified_idx]
    train_y = y[:split]
    validation_X = X[split:, identified_idx]
    validation_y = y[split:]

    preliminary, *_ = np.linalg.lstsq(train_X, train_y, rcond=None)
    residuals = train_y - train_X @ preliminary
    median = float(np.median(residuals))
    mad = float(np.median(np.abs(residuals - median)))
    if mad > 0:
        inliers = np.abs(residuals - median) <= 3.5 * 1.4826 * mad
    else:
        inliers = np.ones(len(train_y), dtype=bool)
    required_inliers = max(len(identified_idx) + 1, int(len(train_y) * 0.6))
    if int(np.sum(inliers)) >= required_inliers:
        fitted = _nonnegative_fit(train_X[inliers], train_y[inliers])
    else:
        inliers = np.ones(len(train_y), dtype=bool)
        fitted = _nonnegative_fit(train_X, train_y)

    metrics = _metrics(validation_y, validation_X @ fitted)
    confidence, reason = _confidence(metrics, validation_count)
    coefficients = {name: 0.0 for name in names}
    for index, value in zip(identified_idx, fitted):
        coefficients[names[index]] = round(float(value), 8)

    current = conn.execute(
        """SELECT * FROM cycle_models WHERE machine_id=? AND status='active'
           ORDER BY version DESC LIMIT 1""", (machine_id,)
    ).fetchone()
    ranks = {"low": 0, "medium": 1, "high": 2}
    should_activate = confidence != "low" and (
        not current
        or ranks[confidence] > ranks[current["confidence"]]
        or (ranks[confidence] == ranks[current["confidence"]]
            and metrics["mape"] <= float(current["mape"] or 1) * 0.95)
    )
    status = "active" if should_activate else "candidate"
    if should_activate and current:
        conn.execute("UPDATE cycle_models SET status='superseded' WHERE id=?", (current["id"],))
    version = conn.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 value FROM cycle_models WHERE machine_id=?",
        (machine_id,),
    ).fetchone()["value"]
    trained_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO cycle_models
           (machine_id, version, training_signature, sample_count, train_count,
            validation_count, inlier_count, coefficients_json,
            identified_features_json, mae_s, mape, r2, residual_cv,
            confidence, status, reason, trained_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (machine_id, version, signature, sample_count, split, validation_count,
         int(np.sum(inliers)), json.dumps(coefficients, sort_keys=True),
         json.dumps([names[i] for i in identified_idx]), metrics["mae_s"],
         metrics["mape"], metrics["r2"], metrics["residual_cv"], confidence,
         status, reason, trained_at),
    )
    if should_activate:
        conn.executemany(
            "UPDATE cycle_observations SET used_for_training=1 WHERE id=?",
            [(oid,) for oid in observation_ids],
        )
    conn.commit()
    return {"machine_key": machine_key, "status": status, "confidence": confidence,
            "samples": sample_count, "version": version,
            "metrics": {key: round(value, 4) for key, value in metrics.items()},
            "reason": reason}


def status(conn: sqlite3.Connection) -> dict:
    summary = conn.execute(
        """SELECT COUNT(*) total,
                  SUM(CASE WHEN validity='valid' THEN 1 ELSE 0 END) valid,
                  SUM(CASE WHEN validity='rejected' THEN 1 ELSE 0 END) rejected
           FROM cycle_observations"""
    ).fetchone()
    models = conn.execute(
        """SELECT m.machine_key, cm.version, cm.sample_count, cm.confidence,
                  cm.status, cm.mae_s, cm.mape, cm.r2, cm.residual_cv,
                  cm.reason, cm.trained_at
           FROM cycle_models cm JOIN machines m ON m.id=cm.machine_id
           WHERE cm.status IN ('active', 'candidate')
           ORDER BY m.machine_key, cm.version DESC"""
    ).fetchall()
    active_count = sum(row["status"] == "active" for row in models)
    return {
        "observations": {
            "total": summary["total"] or 0,
            "valid": summary["valid"] or 0,
            "rejected": summary["rejected"] or 0,
        },
        "active_models": active_count,
        "models": [dict(row) for row in models],
        "status": "ready" if active_count else ("learning" if summary["valid"] else "collecting"),
    }


def refresh_all(conn: sqlite3.Connection) -> dict:
    observations = refresh_cycle_observations(conn)
    machines = conn.execute(
        "SELECT id, machine_key FROM machines WHERE active=1 ORDER BY id"
    ).fetchall()
    trained = [train_machine(conn, row["id"], row["machine_key"]) for row in machines
               if row["machine_key"] in cycle_time.MACHINE_TYPE_MAP]
    return {"observations": observations, "training": trained, "status": status(conn)}
