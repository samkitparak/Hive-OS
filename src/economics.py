"""Evidence-gated production economics and continuous value assurance.

The module deliberately keeps three ledgers separate:

* direct cost exposure is cash-like waste supported by physical evidence;
* constraint opportunity is recoverable system capacity, never generic machine time;
* measured benefit comes from completed improvement experiments and is only
  called sustained after independent follow-up windows and named adjustments.

Unverified rates can produce previews, but they never enter decision-ready or
measured totals.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

import bottleneck
import energy_intelligence
import improvement
import production_loss


METHOD_VERSION = "production-economics-mv-v1"
MAX_PERSISTENCE_WINDOWS = 12
RECOVERABLE_CONSTRAINT_LOSSES = {
    "breakdown", "setup_adjustment", "material_starvation", "tooling_stop",
    "staffing_loss", "quality_stop", "minor_stop", "speed_loss", "quality_loss",
}
RATE_CATALOG = {
    "throughput_contribution_per_unit": {
        "name": "Throughput contribution per good unit",
        "unit": "currency/good_unit",
        "category": "throughput",
        "note": "Selling price less truly variable cost for one additional good unit.",
    },
    "constraint_minute_value": {
        "name": "Constraint minute value",
        "unit": "currency/constraint_minute",
        "category": "throughput",
        "note": "Contribution protected by one productive minute at a confirmed constraint.",
    },
    "internal_failure_cost_per_unit": {
        "name": "Internal failure cost per unit",
        "unit": "currency/failed_unit",
        "category": "quality",
        "note": "Material and processing cost for one internally rejected unit.",
    },
    "rework_cost_per_unit": {
        "name": "Rework cost per unit",
        "unit": "currency/reworked_unit",
        "category": "quality",
        "note": "Incremental labor, material, and utility cost for one reworked unit.",
    },
}


class VersionConflict(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
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


def _json(value, fallback):
    if value is None:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _named(value: str | None, label: str = "Actor") -> str:
    result = " ".join((value or "").split())
    if len(result) < 2 or len(result) > 120:
        raise ValueError(f"{label} must be 2-120 characters")
    return result


def _claim_key(*parts: object) -> str:
    return hashlib.sha256("|".join(str(part) for part in parts).encode()).hexdigest()[:24]


def settings(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT * FROM economics_settings WHERE id=1").fetchone()
    if not row:
        raise RuntimeError("Economics settings are missing")
    result = dict(row)
    for key in ("auto_review", "verified"):
        result[key] = bool(result[key])
    return result


def update_settings(conn: sqlite3.Connection, payload: dict) -> dict:
    current = settings(conn)
    expected = payload.get("expected_version")
    if expected is not None and int(expected) != int(current["version"]):
        raise VersionConflict(
            f"Economics policy changed from version {expected} to {current['version']}"
        )
    updates = {}
    for key in (
        "auto_review", "interval_seconds", "window_hours", "persistence_window_days",
        "minimum_persistence_reviews", "verified",
    ):
        if payload.get(key) is not None:
            updates[key] = int(payload[key])
    if payload.get("currency") is not None:
        currency = str(payload["currency"]).strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError("Currency must be a three-letter ISO-style code")
        updates["currency"] = currency
    interval = int(updates.get("interval_seconds", current["interval_seconds"]))
    if not 60 <= interval <= 86400:
        raise ValueError("Review interval must be between 60 seconds and one day")
    window = int(updates.get("window_hours", current["window_hours"]))
    if not 1 <= window <= 24 * 30:
        raise ValueError("Economics window must be between 1 and 720 hours")
    persistence = int(updates.get(
        "persistence_window_days", current["persistence_window_days"]
    ))
    if not 1 <= persistence <= 365:
        raise ValueError("Persistence window must be between 1 and 365 days")
    reviews = int(updates.get(
        "minimum_persistence_reviews", current["minimum_persistence_reviews"]
    ))
    if not 1 <= reviews <= MAX_PERSISTENCE_WINDOWS:
        raise ValueError(
            f"Minimum persistence reviews must be between 1 and {MAX_PERSISTENCE_WINDOWS}"
        )
    if not updates:
        return current
    updates.update({
        "source": "manual", "version": int(current["version"]) + 1,
        "updated_by": _named(payload.get("actor")), "updated_at": _iso(_now()),
    })
    columns = ",".join(f"{key}=?" for key in updates)
    cursor = conn.execute(
        f"UPDATE economics_settings SET {columns} WHERE id=1 AND version=?",
        (*updates.values(), current["version"]),
    )
    if cursor.rowcount != 1:
        conn.rollback()
        raise VersionConflict("Economics policy was changed by another operator")
    conn.commit()
    return settings(conn)


def rates(conn: sqlite3.Connection, *, active_only: bool = True) -> list[dict]:
    where = "WHERE er.active=1" if active_only else ""
    rows = conn.execute(
        f"""SELECT er.*,m.name machine_name FROM economics_rates er
            LEFT JOIN machines m ON er.scope_type='machine' AND m.machine_key=er.scope_key
            {where}
            ORDER BY er.rate_key,er.scope_type,er.scope_key,er.version DESC"""
    ).fetchall()
    return [dict(row) | {
        "verified": bool(row["verified"]), "active": bool(row["active"]),
    } for row in rows]


def update_rate(conn: sqlite3.Connection, rate_key: str, payload: dict) -> dict:
    if rate_key not in RATE_CATALOG:
        raise KeyError(f"Unknown economics rate '{rate_key}'")
    policy = settings(conn)
    scope_type = payload.get("scope_type", "factory")
    scope_key = payload.get("scope_key", "factory")
    if scope_type not in {"factory", "machine"}:
        raise ValueError("Economics rate scope must be factory or machine")
    if scope_type == "factory":
        scope_key = "factory"
    elif not conn.execute(
        "SELECT 1 FROM machines WHERE machine_key=?", (scope_key,)
    ).fetchone():
        raise KeyError(f"Machine '{scope_key}' not found")
    amount = float(payload["amount"])
    if amount < 0 or amount > 1_000_000_000:
        raise ValueError("Economics rate amount must be between zero and one billion")
    actor = _named(payload.get("actor"))
    current = conn.execute(
        """SELECT * FROM economics_rates WHERE rate_key=? AND scope_type=?
             AND scope_key=? AND active=1 ORDER BY version DESC LIMIT 1""",
        (rate_key, scope_type, scope_key),
    ).fetchone()
    expected = payload.get("expected_version")
    if expected is not None and int(expected) != int(current["version"] if current else 0):
        raise VersionConflict(
            f"Economics rate changed from version {expected} to "
            f"{current['version'] if current else 0}"
        )
    now = _iso(_now())
    version = int(current["version"]) + 1 if current else 1
    if current:
        cursor = conn.execute(
            "UPDATE economics_rates SET active=0 WHERE id=? AND active=1",
            (current["id"],),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            raise VersionConflict("Economics rate was changed by another operator")
    verified = bool(payload.get("verified", False))
    meta = RATE_CATALOG[rate_key]
    cursor = conn.execute(
        """INSERT INTO economics_rates
           (rate_key,scope_type,scope_key,name,amount,unit,currency,source,verified,
            active,version,approved_by,approved_at,created_at)
               VALUES (?,?,?,?,?,?,?,'manual',?,1,?,?,?,?)""",
        (rate_key, scope_type, scope_key, meta["name"], amount, meta["unit"],
         policy["currency"], int(verified), version, actor if verified else None,
         now if verified else None, now),
    )
    rate_id = cursor.lastrowid
    conn.execute(
        """INSERT INTO economics_rate_events
           (rate_id,event_type,actor,details_json,ts) VALUES (?,?,?,?,?)""",
        (rate_id, "commissioned" if verified else "drafted", actor,
         json.dumps({"replaces_rate_id": current["id"] if current else None}, sort_keys=True),
         now),
    )
    conn.commit()
    return next(item for item in rates(conn) if item["id"] == rate_id)


def _resolve_rate(conn: sqlite3.Connection, rate_key: str,
                  target_type: str, target_key: str) -> Optional[dict]:
    scopes = []
    if target_type == "machine":
        scopes.append(("machine", target_key))
    scopes.append(("factory", "factory"))
    for scope_type, scope_key in scopes:
        row = conn.execute(
            """SELECT * FROM economics_rates WHERE rate_key=? AND scope_type=?
                 AND scope_key=? AND active=1 ORDER BY version DESC LIMIT 1""",
            (rate_key, scope_type, scope_key),
        ).fetchone()
        if row:
            return dict(row) | {
                "verified": bool(row["verified"]), "active": bool(row["active"]),
            }
    return None


def record_adjustment(conn: sqlite3.Connection, experiment_id: int, payload: dict) -> dict:
    experiment = conn.execute(
        "SELECT id FROM improvement_experiments WHERE id=?", (experiment_id,)
    ).fetchone()
    if not experiment:
        raise KeyError(f"Improvement experiment {experiment_id} not found")
    start, end = _dt(payload["window_start"]), _dt(payload["window_end"])
    if not start or not end or end <= start:
        raise ValueError("Adjustment window end must be after its start")
    actor = _named(payload.get("actor"))
    reason = " ".join(str(payload.get("reason") or "").split())
    if len(reason) < 3 or len(reason) > 500:
        raise ValueError("Adjustment reason must be 3-500 characters")
    current = conn.execute(
        """SELECT * FROM economics_adjustments WHERE experiment_id=?
             AND window_start=? AND window_end=? AND active=1
             ORDER BY version DESC LIMIT 1""",
        (experiment_id, _iso(start), _iso(end)),
    ).fetchone()
    expected = payload.get("expected_version")
    if expected is not None and int(expected) != int(current["version"] if current else 0):
        raise VersionConflict("Economics adjustment changed; refresh before saving")
    if current:
        conn.execute("UPDATE economics_adjustments SET active=0 WHERE id=?", (current["id"],))
    version = int(current["version"]) + 1 if current else 1
    cursor = conn.execute(
        """INSERT INTO economics_adjustments
           (experiment_id,window_start,window_end,adjustment_amount,reason,actor,
            verified,active,version,created_at) VALUES (?,?,?,?,?,?,?,1,?,?)""",
        (experiment_id, _iso(start), _iso(end), float(payload.get("adjustment_amount", 0)),
         reason, actor, int(bool(payload.get("verified", False))), version, _iso(_now())),
    )
    conn.commit()
    return dict(conn.execute(
        "SELECT * FROM economics_adjustments WHERE id=?", (cursor.lastrowid,)
    ).fetchone())


def input_signature(conn: sqlite3.Connection) -> str:
    policy = settings(conn)
    inputs = dict(conn.execute(
        """SELECT
             (SELECT COALESCE(MAX(id),0) FROM machine_events) machine_event_id,
             (SELECT COALESCE(MAX(id),0) FROM downtime_events) downtime_id,
             (SELECT COALESCE(MAX(id),0) FROM quality_checks) quality_id,
             (SELECT COALESCE(MAX(id),0) FROM telemetry_samples) telemetry_id,
             (SELECT COALESCE(MAX(id),0) FROM constraint_snapshots) constraint_snapshot_id,
             (SELECT COALESCE(MAX(id),0) FROM improvement_events) improvement_event_id,
             (SELECT COALESCE(MAX(id),0) FROM economics_adjustments) adjustment_id"""
    ).fetchone())
    mutable_inputs = {
        "industrial_profiles": [dict(row) for row in conn.execute(
            """SELECT profile_key,version,verified,active_contract_id,settings_json
                 FROM industrial_profiles ORDER BY profile_key"""
        ).fetchall()],
        "production_orders": [dict(row) for row in conn.execute(
            """SELECT id,status,version,updated_at FROM production_orders
                 ORDER BY id"""
        ).fetchall()],
        "constraint_episodes": [dict(row) for row in conn.execute(
            """SELECT id,status,last_snapshot_id,updated_at FROM constraint_episodes
                 ORDER BY id"""
        ).fetchall()],
        "improvement_experiments": [dict(row) for row in conn.execute(
            """SELECT id,status,outcome,version,updated_at FROM improvement_experiments
                 ORDER BY id"""
        ).fetchall()],
    }
    payload = {
        "policy": {key: policy[key] for key in (
            "interval_seconds", "window_hours", "persistence_window_days",
            "minimum_persistence_reviews", "currency", "verified", "version",
        )},
        "rates": [{key: row[key] for key in (
            "id", "rate_key", "scope_type", "scope_key", "amount", "currency",
            "verified", "version",
        )} for row in rates(conn)],
        "inputs": inputs, "mutable_inputs": mutable_inputs,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _claim(*, claim_type: str, category: str, target_type: str, target_key: str,
           source_type: str, source_key: str, quantity: Optional[float],
           quantity_unit: Optional[str], rate: Optional[dict], amount: Optional[float],
           currency: str, status: str, confidence: str, evidence: list[str],
           blocked_by: list[str]) -> dict:
    return {
        "claim_key": _claim_key(claim_type, category, target_type, target_key,
                                source_type, source_key),
        "claim_type": claim_type, "category": category,
        "target_type": target_type, "target_key": target_key,
        "source_type": source_type, "source_key": str(source_key),
        "quantity": round(quantity, 6) if quantity is not None else None,
        "quantity_unit": quantity_unit, "rate_id": rate["id"] if rate else None,
        "rate": ({key: rate[key] for key in (
            "id", "rate_key", "name", "amount", "unit", "scope_type", "scope_key",
            "currency", "verified", "version",
        )} if rate else None),
        "amount": round(amount, 2) if amount is not None else None,
        "currency": currency, "status": status, "confidence": confidence,
        "evidence": evidence, "blocked_by": list(dict.fromkeys(blocked_by)),
    }


def _energy_claims(conn: sqlite3.Connection, policy: dict, now: datetime) -> list[dict]:
    report = energy_intelligence.build(conn, hours=int(policy["window_hours"]))
    profile_state = {
        row["profile_key"]: dict(row) for row in conn.execute(
            "SELECT profile_key,verified,active_contract_id FROM industrial_profiles"
        ).fetchall()
    }
    claims = []
    for item in report["profiles"]:
        quantity = float(item["idle_energy_kwh"] or 0)
        if quantity <= 0:
            continue
        tariff = item.get("tariff_per_kwh")
        state = profile_state.get(item["profile_key"], {})
        blocked = []
        if not policy["verified"]:
            blocked.append("Economics policy is not site-verified.")
        if not state.get("verified") or not state.get("active_contract_id"):
            blocked.append("Industrial profile and signal contract are not approved.")
        if not tariff:
            blocked.append("Electricity tariff is not commissioned on the profile.")
        if float(item["coverage"] or 0) < 0.8:
            blocked.append("Contiguous power evidence is below 80%.")
        amount = quantity * float(tariff) if tariff else None
        claims.append(_claim(
            claim_type="direct_cost_exposure", category="idle_energy",
            target_type="machine" if item.get("machine_key") else "profile",
            target_key=item.get("machine_key") or item["profile_key"],
            source_type="industrial_profile", source_key=item["profile_key"],
            quantity=quantity, quantity_unit="kWh", rate=None, amount=amount,
            currency=policy["currency"],
            status="decision_ready" if not blocked else "preview_only",
            confidence=item["confidence"],
            evidence=[
                f"{item['sample_count']} good power samples",
                f"{round(float(item['coverage'] or 0) * 100)}% contiguous coverage",
                f"{quantity:.3f} kWh in the approved idle band",
                f"Window ended {_iso(now)}",
            ], blocked_by=blocked,
        ))
    return claims


def _quality_claims(conn: sqlite3.Connection, policy: dict,
                    start: datetime, end: datetime) -> list[dict]:
    rows = conn.execute(
        """SELECT qc.result,COALESCE(m.machine_key,'factory') target_key,
                  COALESCE(m.name,'Factory') target_name,COUNT(*) units,
                  SUM(CASE WHEN qc.part_id IS NOT NULL THEN 1 ELSE 0 END) linked_units,
                  SUM(CASE WHEN TRIM(COALESCE(qc.inspector,''))!='' THEN 1 ELSE 0 END) attributed_units
           FROM quality_checks qc LEFT JOIN machines m ON m.id=qc.machine_id
           WHERE qc.result IN ('fail','rework') AND qc.ts>=? AND qc.ts<?
           GROUP BY qc.result,qc.machine_id ORDER BY qc.result,target_key""",
        (_iso(start), _iso(end)),
    ).fetchall()
    claims = []
    for row in rows:
        target_type = "machine" if row["target_key"] != "factory" else "factory"
        rate_key = (
            "rework_cost_per_unit" if row["result"] == "rework"
            else "internal_failure_cost_per_unit"
        )
        rate = _resolve_rate(conn, rate_key, target_type, row["target_key"])
        blocked = []
        if not policy["verified"]:
            blocked.append("Economics policy is not site-verified.")
        if not rate:
            blocked.append(f"{RATE_CATALOG[rate_key]['name']} is not commissioned.")
        elif not rate["verified"]:
            blocked.append("The selected quality cost rate is not verified.")
        elif rate["currency"] != policy["currency"]:
            blocked.append("The selected rate currency differs from the economics policy.")
        if int(row["linked_units"] or 0) != int(row["units"]):
            blocked.append("Every failed disposition must resolve to a physical part identity.")
        if int(row["attributed_units"] or 0) != int(row["units"]):
            blocked.append("Every failed disposition requires named inspection attribution.")
        amount = float(row["units"]) * float(rate["amount"]) if rate else None
        claims.append(_claim(
            claim_type="direct_cost_exposure",
            category="rework" if row["result"] == "rework" else "internal_failure",
            target_type=target_type, target_key=row["target_key"],
            source_type="quality_checks", source_key=f"{row['result']}:{row['target_key']}",
            quantity=float(row["units"]), quantity_unit="dispositions",
            rate=rate, amount=amount, currency=policy["currency"],
            status="decision_ready" if not blocked else "preview_only",
            confidence="high" if not blocked else "low",
            evidence=[
                f"{row['units']} {row['result']} dispositions in the review window",
                f"{row['linked_units']} linked to physical parts",
                f"{row['attributed_units']} attributed to named inspectors",
            ], blocked_by=blocked,
        ))
    return claims


def _constraint_claim(conn: sqlite3.Connection, policy: dict,
                      now: datetime) -> Optional[dict]:
    report = bottleneck.detect(conn, int(policy["window_hours"]), now)
    current, episode = report.current, report.episode
    if not current:
        return None
    loss = production_loss.build(conn, now=now, machine_key=current.machine_key)
    machine = loss["machines"][0] if loss["machines"] else None
    if not machine:
        return None
    relevant = [item for item in machine["losses"]
                if item["category"] in RECOVERABLE_CONSTRAINT_LOSSES]
    quantity = sum(float(item["seconds"]) for item in relevant) / 60
    if quantity <= 0:
        return None
    rate = _resolve_rate(conn, "constraint_minute_value", "machine", current.machine_key)
    derived = False
    effective_rate = float(rate["amount"]) if rate else None
    if rate is None:
        contribution = _resolve_rate(
            conn, "throughput_contribution_per_unit", "machine", current.machine_key
        )
        if contribution and current.throughput_per_hour > 0:
            rate = contribution
            effective_rate = float(contribution["amount"]) * current.throughput_per_hour / 60
            derived = True
    confirmed = bool(
        episode and episode.get("status") == "open"
        and episode.get("machine_key") == current.machine_key
        and episode.get("constraint_state") == current.state
    )
    blocked = []
    if not policy["verified"]:
        blocked.append("Economics policy is not site-verified.")
    if not confirmed or current.confidence not in {"medium", "high"}:
        blocked.append("A repeated medium/high-confidence constraint episode is required.")
    if current.demand_qty <= 0:
        blocked.append("Released demand is absent at the candidate constraint.")
    if not machine["decision_ready"]:
        blocked.append("The constraint machine loss waterfall is not decision-ready.")
    if not rate or effective_rate is None:
        blocked.append("Constraint minute value or a derivable throughput contribution is missing.")
    elif not rate["verified"]:
        blocked.append("The selected throughput rate is not verified.")
    elif rate["currency"] != policy["currency"]:
        blocked.append("The selected rate currency differs from the economics policy.")
    amount = quantity * effective_rate if effective_rate is not None else None
    evidence = [
        f"{current.machine_name} is classified {current.state}",
        f"{quantity:.1f} recoverable constraint minutes in the reconciled waterfall",
        *[f"{item['label']}: {item['machine_minutes']:.1f} min" for item in relevant[:4]],
    ]
    if derived:
        evidence.append(
            f"Derived {effective_rate:.2f}/min from contribution and observed constraint throughput"
        )
    return _claim(
        claim_type="constraint_capacity_opportunity", category="throughput_capacity",
        target_type="machine", target_key=current.machine_key,
        source_type="constraint_episode",
        source_key=str(episode.get("id") if episode else "unconfirmed"),
        quantity=quantity, quantity_unit="constraint_minutes", rate=rate,
        amount=amount, currency=policy["currency"],
        status="decision_ready" if not blocked else "preview_only",
        confidence=current.confidence, evidence=evidence, blocked_by=blocked,
    )


def _historical_constraint(conn: sqlite3.Connection, target_type: str, target_key: str,
                           start: datetime, end: datetime) -> bool:
    if target_type != "machine":
        return False
    row = conn.execute(
        """SELECT 1 FROM constraint_episodes ce JOIN machines m ON m.id=ce.machine_id
           WHERE m.machine_key=? AND ce.confirmed_at IS NOT NULL
             AND ce.confirmed_at<? AND COALESCE(ce.ended_at,ce.last_seen_at)>=?
             AND ce.confidence IN ('medium','high') LIMIT 1""",
        (target_key, _iso(end), _iso(start)),
    ).fetchone()
    return bool(row)


def _operational_quantity(metric: str, baseline: float, evaluation: float,
                          sample_count: int, duration_hours: float) -> tuple[float, str, str]:
    if metric == "throughput_per_hour":
        return max(0.0, evaluation - baseline) * duration_hours, "good_units", \
            "throughput_contribution_per_unit"
    if metric == "downtime_minutes_per_hour":
        return max(0.0, baseline - evaluation) * duration_hours, "constraint_minutes", \
            "constraint_minute_value"
    if metric == "defect_rate":
        return max(0.0, baseline - evaluation) * sample_count, "avoided_failures", \
            "internal_failure_cost_per_unit"
    if metric == "median_cycle_time_s":
        return max(0.0, baseline - evaluation) * sample_count / 60, \
            "constraint_minutes", "constraint_minute_value"
    raise ValueError(f"Unsupported economics metric '{metric}'")


def _effect_pct(metric: str, baseline: float, evaluation: float) -> Optional[float]:
    if abs(baseline) < 1e-9:
        return None
    if improvement.METRICS[metric]["direction"] == "increase":
        return (evaluation - baseline) / abs(baseline) * 100
    return (baseline - evaluation) / abs(baseline) * 100


def _persistence_windows(conn: sqlite3.Connection, recommendation: dict,
                         experiment: dict, policy: dict, now: datetime,
                         rate: Optional[dict]) -> list[dict]:
    evaluation_end = _dt(experiment.get("evaluation_due_at"))
    baseline_start = _dt(experiment.get("baseline_start"))
    baseline_end = _dt(experiment.get("baseline_end"))
    baseline = _json(experiment.get("baseline_json"), {})
    if not evaluation_end or not baseline_start or not baseline_end or baseline.get("value") is None:
        return []
    span = timedelta(days=int(policy["persistence_window_days"]))
    windows = []
    start = evaluation_end
    while start + span <= now and len(windows) < MAX_PERSISTENCE_WINDOWS:
        end = start + span
        samples = improvement.metric_samples(
            conn, experiment["primary_metric"], recommendation["target_type"],
            recommendation["target_key"], start, end,
        )
        statistic = improvement.METRICS[experiment["primary_metric"]]["statistic"]
        if samples:
            ordered = sorted(samples)
            value = (sum(samples) / len(samples) if statistic == "mean" else
                     ordered[len(ordered) // 2] if len(ordered) % 2 else
                     (ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) / 2)
        else:
            value = None
        effect = _effect_pct(experiment["primary_metric"], float(baseline["value"]), value) \
            if value is not None else None
        quantity = raw_amount = None
        if value is not None:
            quantity, _, _ = _operational_quantity(
                experiment["primary_metric"], float(baseline["value"]), value,
                len(samples), (end - start).total_seconds() / 3600,
            )
            raw_amount = quantity * float(rate["amount"]) if rate else None
        adjustment = conn.execute(
            """SELECT * FROM economics_adjustments WHERE experiment_id=?
                 AND window_start=? AND window_end=? AND active=1
                 ORDER BY version DESC LIMIT 1""",
            (experiment["id"], _iso(start), _iso(end)),
        ).fetchone()
        adjustment_item = dict(adjustment) if adjustment else None
        guardrails = improvement._guardrails(
            conn, recommendation, experiment, baseline_start, baseline_end, start, end
        ) if samples else []
        enough = len(samples) >= int(experiment["min_samples"])
        guardrail_pass = not any(item["status"] == "fail" for item in guardrails)
        adjustment_ready = bool(adjustment_item and adjustment_item["verified"])
        passed = bool(
            enough and effect is not None
            and effect >= float(experiment["target_delta_pct"])
            and guardrail_pass and adjustment_ready and raw_amount is not None
        )
        adjusted_amount = (
            raw_amount + float(adjustment_item["adjustment_amount"])
            if raw_amount is not None and adjustment_ready else None
        )
        windows.append({
            "window_start": _iso(start), "window_end": _iso(end),
            "sample_count": len(samples), "value": round(value, 6) if value is not None else None,
            "effect_pct": round(effect, 3) if effect is not None else None,
            "quantity": round(quantity, 6) if quantity is not None else None,
            "raw_amount": round(raw_amount, 2) if raw_amount is not None else None,
            "adjustment": adjustment_item,
            "adjusted_amount": round(adjusted_amount, 2) if adjusted_amount is not None else None,
            "guardrails": guardrails, "status": "pass" if passed else (
                "adjustment_required" if not adjustment_ready else "fail"
            ),
        })
        start = end
    return windows


def _improvement_claims(conn: sqlite3.Connection, policy: dict,
                        now: datetime) -> list[dict]:
    rows = conn.execute(
        """SELECT ie.*,ir.target_type,ir.target_key,ir.title recommendation_title,
                  ir.recommendation_key
           FROM improvement_experiments ie
           JOIN improvement_recommendations ir ON ir.id=ie.recommendation_id
           WHERE ie.baseline_json IS NOT NULL AND ie.evaluation_json IS NOT NULL
             AND ie.outcome IS NOT NULL ORDER BY ie.id"""
    ).fetchall()
    claims = []
    for raw in rows:
        experiment = dict(raw)
        baseline = _json(experiment["baseline_json"], {})
        evaluation = _json(experiment["evaluation_json"], {})
        metric = experiment["primary_metric"]
        if metric not in improvement.METRICS or baseline.get("value") is None \
                or evaluation.get("value") is None:
            continue
        duration_h = max(0.0, (
            _dt(evaluation["window_end"]) - _dt(evaluation["window_start"])
        ).total_seconds() / 3600)
        quantity, unit, rate_key = _operational_quantity(
            metric, float(baseline["value"]), float(evaluation["value"]),
            int(evaluation.get("sample_count") or 0), duration_h,
        )
        rate = _resolve_rate(
            conn, rate_key, experiment["target_type"], experiment["target_key"]
        )
        amount = quantity * float(rate["amount"]) if rate else None
        blocked = []
        if not policy["verified"]:
            blocked.append("Economics policy is not site-verified.")
        if experiment["outcome"] != "validated" or experiment["ci_lower_pct"] is None \
                or float(experiment["ci_lower_pct"]) <= 0:
            blocked.append("Only a validated experiment with a positive lower confidence bound is measured.")
        if not rate:
            blocked.append(f"{RATE_CATALOG[rate_key]['name']} is not commissioned.")
        elif not rate["verified"]:
            blocked.append("The selected benefit rate is not verified.")
        elif rate["currency"] != policy["currency"]:
            blocked.append("The selected rate currency differs from the economics policy.")
        if metric in {"downtime_minutes_per_hour", "median_cycle_time_s"} and not \
                _historical_constraint(
                    conn, experiment["target_type"], experiment["target_key"],
                    _dt(evaluation["window_start"]), _dt(evaluation["window_end"]),
                ):
            blocked.append("The capacity benefit lacks an overlapping confirmed constraint episode.")
        if metric == "defect_rate":
            params = [_iso(_dt(evaluation["window_start"])), _iso(_dt(evaluation["window_end"]))]
            target_sql = ""
            if experiment["target_type"] == "machine":
                target_sql = " AND m.machine_key=?"
                params.append(experiment["target_key"])
            identity = conn.execute(
                f"""SELECT COUNT(*) total,
                           SUM(CASE WHEN qc.part_id IS NOT NULL THEN 1 ELSE 0 END) linked
                    FROM quality_checks qc LEFT JOIN machines m ON m.id=qc.machine_id
                    WHERE qc.ts>=? AND qc.ts<?{target_sql}""", params,
            ).fetchone()
            if int(identity["total"] or 0) != int(identity["linked"] or 0):
                blocked.append("Evaluation quality dispositions are not fully linked to physical parts.")
        recommendation = {
            "target_type": experiment["target_type"],
            "target_key": experiment["target_key"],
        }
        persistence = _persistence_windows(
            conn, recommendation, experiment, policy, now, rate
        ) if not blocked else []
        qualified = [item for item in persistence if item["status"] == "pass"]
        required = int(policy["minimum_persistence_reviews"])
        latest_reviewed = next((item for item in reversed(persistence)
                                if item["status"] != "adjustment_required"), None)
        sustained = bool(
            len(qualified) >= required and latest_reviewed
            and latest_reviewed["status"] == "pass"
        )
        if persistence and any(item["status"] == "adjustment_required" for item in persistence):
            blocked.append("Named routine/non-routine adjustment review is pending for persistence windows.")
        measured = not [item for item in blocked if not item.startswith("Named routine")]
        if measured:
            status = "sustained" if sustained else "measured"
            amount += sum(float(item["adjusted_amount"] or 0) for item in qualified)
        elif experiment["outcome"] in {"ineffective", "inconclusive"}:
            status = "no_measured_value"
            amount = 0.0
        else:
            status = "preview_only"
        evidence = [
            f"Experiment #{experiment['id']}: {experiment['recommendation_title']}",
            f"{metric} improved {experiment['effect_pct']}%",
            f"90% bootstrap interval {experiment['ci_lower_pct']}% to {experiment['ci_upper_pct']}%",
            f"{quantity:.3f} {unit} measured during evaluation",
        ]
        if persistence:
            evidence.append(
                f"{len(qualified)}/{len(persistence)} completed persistence windows verified"
            )
        claim = _claim(
            claim_type="measured_improvement_benefit", category=metric,
            target_type=experiment["target_type"], target_key=experiment["target_key"],
            source_type="improvement_experiment", source_key=str(experiment["id"]),
            quantity=quantity, quantity_unit=unit, rate=rate, amount=amount,
            currency=policy["currency"], status=status,
            confidence="high" if status in {"measured", "sustained"} else "low",
            evidence=evidence, blocked_by=blocked,
        )
        claim["persistence"] = persistence
        claims.append(claim)
    return claims


def _build_review(conn: sqlite3.Connection, now: datetime, actor: str) -> dict:
    policy = settings(conn)
    start = now - timedelta(hours=int(policy["window_hours"]))
    claims = []
    claims.extend(_energy_claims(conn, policy, now))
    claims.extend(_quality_claims(conn, policy, start, now))
    constraint = _constraint_claim(conn, policy, now)
    if constraint:
        claims.append(constraint)
    claims.extend(_improvement_claims(conn, policy, now))
    ready = [item for item in claims if item["status"] == "decision_ready"]
    measured = [item for item in claims if item["status"] in {"measured", "sustained"}]
    sustained = [item for item in claims if item["status"] == "sustained"]
    blocked = [item for item in claims if item["blocked_by"]]
    amountable = [item for item in claims if item["amount"] is not None]
    direct = sum(float(item["amount"] or 0) for item in ready
                 if item["claim_type"] == "direct_cost_exposure")
    opportunity = sum(float(item["amount"] or 0) for item in ready
                      if item["claim_type"] == "constraint_capacity_opportunity")
    benefit = sum(float(item["amount"] or 0) for item in measured)
    sustained_value = sum(float(item["amount"] or 0) for item in sustained)
    evidence_gaps = []
    if not policy["verified"]:
        evidence_gaps.append("Economics policy and reporting currency require site verification.")
    active_rates = rates(conn)
    verified_rates = [item for item in active_rates if item["verified"]
                      and item["currency"] == policy["currency"]]
    if not verified_rates:
        evidence_gaps.append("No finance rate is verified in the reporting currency.")
    if not claims:
        evidence_gaps.append("No cost exposure or completed experiment exists in the review window.")
    if measured:
        status = "verified_value"
    elif ready:
        status = "measuring"
    elif claims:
        status = "commissioning_required"
    else:
        status = "waiting_for_evidence"
    return {
        "method_version": METHOD_VERSION, "generated_at": _iso(now),
        "window_start": _iso(start), "window_end": _iso(now), "status": status,
        "currency": policy["currency"],
        "summary": {
            "direct_cost_exposure": round(direct, 2),
            "constraint_capacity_opportunity": round(opportunity, 2),
            "measured_improvement_benefit": round(benefit, 2),
            "sustained_improvement_benefit": round(sustained_value, 2),
            "claims": len(claims), "decision_ready_claims": len(ready),
            "measured_claims": len(measured), "sustained_claims": len(sustained),
            "blocked_claims": len(blocked), "active_rates": len(active_rates),
            "verified_rates": len(verified_rates),
            "value_coverage": round((len(ready) + len(measured)) / len(amountable), 4)
            if amountable else 0.0,
        },
        "claims": claims, "evidence_gaps": evidence_gaps,
        "guardrails": [
            "Direct cost, constraint opportunity, and measured benefit are never added into one total.",
            "Only confirmed constraint minutes can carry system throughput opportunity.",
            "Measured benefit requires a validated experiment, positive lower confidence bound, and verified rate.",
            "Sustained benefit requires independent follow-up windows, guardrails, and named baseline adjustments.",
        ],
    }


def _hydrate_review(conn: sqlite3.Connection, row: sqlite3.Row | dict) -> dict:
    item = dict(row)
    result = _json(item.pop("result_json"), {})
    claims = []
    for raw in conn.execute(
        "SELECT * FROM economics_claims WHERE review_id=? ORDER BY id", (item["id"],)
    ).fetchall():
        claim = dict(raw)
        claim["evidence"] = _json(claim.pop("evidence_json"), [])
        claim["blocked_by"] = _json(claim.pop("blocked_by_json"), [])
        rate = conn.execute(
            "SELECT * FROM economics_rates WHERE id=?", (claim["rate_id"],)
        ).fetchone() if claim["rate_id"] else None
        claim["rate"] = dict(rate) | {"verified": bool(rate["verified"])} if rate else None
        claim["persistence"] = result.get("persistence", {}).get(claim["claim_key"], [])
        claims.append(claim)
    result.pop("persistence", None)
    return {**item, **result, "claims": claims}


def create_review(conn: sqlite3.Connection, actor: str = "hive-economics-worker",
                  now: Optional[datetime] = None) -> dict:
    now = now or _now()
    result = _build_review(conn, now, actor)
    signature = input_signature(conn)
    policy = settings(conn)
    bucket = _bucket(now, int(policy["interval_seconds"]))
    existing = conn.execute(
        "SELECT * FROM economics_reviews WHERE review_bucket=? AND input_signature=?",
        (bucket, signature),
    ).fetchone()
    if existing:
        return _hydrate_review(conn, existing)
    claims = result.pop("claims")
    persistence = {item["claim_key"]: item.pop("persistence", [])
                   for item in claims if "persistence" in item}
    if persistence:
        result["persistence"] = persistence
    cursor = conn.execute(
        """INSERT OR IGNORE INTO economics_reviews
           (review_bucket,input_signature,method_version,status,window_start,window_end,
            result_json,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
        (bucket, signature, METHOD_VERSION, result["status"], result["window_start"],
         result["window_end"], json.dumps(result, sort_keys=True), actor, _iso(now)),
    )
    if cursor.rowcount != 1:
        raced = conn.execute(
            "SELECT * FROM economics_reviews WHERE review_bucket=? AND input_signature=?",
            (bucket, signature),
        ).fetchone()
        conn.commit()
        return _hydrate_review(conn, raced)
    review_id = cursor.lastrowid
    for item in claims:
        conn.execute(
            """INSERT INTO economics_claims
               (review_id,claim_key,claim_type,category,target_type,target_key,
                source_type,source_key,quantity,quantity_unit,rate_id,amount,currency,
                status,confidence,evidence_json,blocked_by_json,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (review_id, item["claim_key"], item["claim_type"], item["category"],
             item["target_type"], item["target_key"], item["source_type"],
             item["source_key"], item["quantity"], item["quantity_unit"],
             item["rate_id"], item["amount"], item["currency"], item["status"],
             item["confidence"], json.dumps(item["evidence"], sort_keys=True),
             json.dumps(item["blocked_by"], sort_keys=True), _iso(now)),
        )
    conn.execute(
        """UPDATE economics_settings SET last_review_at=?,consecutive_failures=0,
              last_error=NULL WHERE id=1""", (_iso(now),)
    )
    conn.commit()
    row = conn.execute("SELECT * FROM economics_reviews WHERE id=?", (review_id,)).fetchone()
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
        "interval_seconds": policy["interval_seconds"],
        "last_review_at": policy["last_review_at"],
        "age_seconds": round(age, 1) if age is not None else None,
        "consecutive_failures": policy["consecutive_failures"],
        "last_error": policy["last_error"],
    }


def snapshot(conn: sqlite3.Connection, limit: int = 20) -> dict:
    latest = conn.execute(
        "SELECT * FROM economics_reviews ORDER BY created_at DESC,id DESC LIMIT 1"
    ).fetchone()
    history = [dict(row) for row in conn.execute(
        """SELECT id,review_bucket,status,window_start,window_end,created_by,created_at
           FROM economics_reviews ORDER BY created_at DESC,id DESC LIMIT ?""", (limit,)
    ).fetchall()]
    return {
        "method_version": METHOD_VERSION, "settings": settings(conn),
        "rate_catalog": RATE_CATALOG, "rates": rates(conn), "runtime": runtime(conn),
        "current": _hydrate_review(conn, latest) if latest else None,
        "history": history,
    }


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
            """UPDATE economics_settings SET consecutive_failures=consecutive_failures+1,
                  last_error=?,last_review_at=? WHERE id=1""",
            (str(error)[:1000], _iso(now)),
        )
        conn.commit()
        raise
