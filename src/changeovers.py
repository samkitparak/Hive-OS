"""Evidence-gated sequence-dependent setup and changeover intelligence."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable


METHOD_VERSION = "hive-changeover-intelligence-v1"
MAX_SETUP_S = 14_400.0
OBSERVATION_SOURCES = {
    "manual_time_study", "machine_log", "controller_event", "downtime_event",
}

# Broad priors keep commissioning simulations executable. They remain
# production-ineligible until a named operator verifies the machine standard.
SETUP_CAPABILITIES = {
    "gabbiani_pt80": {
        "basis": "board material code", "assumed_default_s": 600.0,
    },
    "nova_si400": {
        "basis": "board material code", "assumed_default_s": 300.0,
    },
    "morbidelli_cx100": {
        "basis": "face and groove program class", "assumed_default_s": 180.0,
    },
    "morbidelli_n100": {
        "basis": "face and groove program class", "assumed_default_s": 300.0,
    },
    "stefani_kd": {
        "basis": "edge-band material set", "assumed_default_s": 600.0,
    },
    "sergiani_gs120": {
        "basis": "material and thickness recipe proxy", "assumed_default_s": 600.0,
    },
    "varie_osama": {
        "basis": "material and thickness recipe proxy", "assumed_default_s": 300.0,
    },
    "dmc60_rcs135": {
        "basis": "panel thickness setting", "assumed_default_s": 300.0,
    },
    "dmc90_xrt135": {
        "basis": "panel thickness setting", "assumed_default_s": 300.0,
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sha(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _normalize(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip()).upper()
    text = re.sub(r"[^A-Z0-9._:+|= /-]", "", text)
    if not text:
        raise ValueError("Setup keys cannot be empty")
    return text[:240]


def _iso(value: object, label: str = "observed_at") -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower))


def _machine(conn: sqlite3.Connection, machine_key: str) -> dict:
    row = conn.execute(
        "SELECT id,machine_key,name FROM machines WHERE machine_key=? AND active=1",
        (machine_key,),
    ).fetchone()
    if not row:
        raise KeyError(f"Active machine '{machine_key}' not found")
    if machine_key not in SETUP_CAPABILITIES:
        raise ValueError(f"Machine '{machine_key}' has no commissioned setup-family contract")
    return dict(row)


def _event(conn: sqlite3.Connection, machine_id: int, event_type: str, actor: str,
           object_type: str, object_id: int | None, details: dict) -> None:
    conn.execute(
        """INSERT INTO changeover_events
           (machine_id,event_type,actor,object_type,object_id,details_json,ts)
           VALUES (?,?,?,?,?,?,?)""",
        (machine_id, event_type, actor, object_type, object_id, _json(details), _now()),
    )


def setup_family(machine_key: str, part: dict) -> dict | None:
    """Return a coarse, explainable setup family from fields HIVE actually owns."""
    if machine_key not in SETUP_CAPABILITIES:
        return None
    material = re.sub(r"\s+", " ", str(part.get("material") or "").strip()).upper()
    thickness = part.get("thickness_mm")
    if machine_key in {"gabbiani_pt80", "nova_si400"}:
        if not material:
            return None
        return {"key": _normalize(f"MATERIAL|{material}"), "label": material,
                "basis": "board material code"}
    if machine_key in {"morbidelli_cx100", "morbidelli_n100"}:
        back = str(part.get("cnc_file_back") or "")
        front = str(part.get("cnc_file_front") or "")
        if not (back or front or part.get("has_cnc")):
            return None
        faces = 2 if back and front else 1
        groove = bool(re.search(r"r\d+[bf]g\d+", f"{back} {front}", re.IGNORECASE))
        key = f"CNC|FACES={faces}|GROOVE={int(groove)}"
        return {"key": key, "label": f"{faces} face, {'groove' if groove else 'no groove'}",
                "basis": "face and groove program class"}
    if machine_key == "stefani_kd":
        bands = sorted({_normalize(part.get(field)) for field in ("eb1", "eb2", "eb3", "eb4")
                        if str(part.get(field) or "").strip()})
        if not bands:
            return None
        label = " + ".join(bands)
        return {"key": _normalize(f"EDGE|{label}"), "label": label,
                "basis": "edge-band material set"}
    if machine_key in {"dmc60_rcs135", "dmc90_xrt135"}:
        if thickness is None:
            return None
        label = f"{float(thickness):g} mm"
        return {"key": _normalize(f"THICKNESS|{label}"), "label": label,
                "basis": "panel thickness setting"}
    if machine_key in {"sergiani_gs120", "varie_osama"}:
        if not material and thickness is None:
            return None
        label = f"{material or 'UNSPECIFIED'} | {float(thickness):g} mm" if thickness is not None else material
        return {"key": _normalize(f"RECIPE_PROXY|{label}"), "label": label,
                "basis": "material and thickness recipe proxy"}
    return None


def sync_defaults(conn: sqlite3.Connection, commit: bool = True) -> int:
    now = _now()
    rows = {row["machine_key"]: row["id"] for row in conn.execute(
        "SELECT id,machine_key FROM machines WHERE active=1"
    ).fetchall()}
    written = 0
    for machine_key, capability in SETUP_CAPABILITIES.items():
        machine_id = rows.get(machine_key)
        if not machine_id:
            continue
        cursor = conn.execute(
            """INSERT OR IGNORE INTO changeover_machine_standards
               (machine_id,default_setup_s,source,verified,version,updated_by,created_at,updated_at)
               VALUES (?,?,'engineering_assumption',0,1,'hive',?,?)""",
            (machine_id, capability["assumed_default_s"], now, now),
        )
        written += cursor.rowcount
    if commit:
        conn.commit()
    return written


def update_standard(conn: sqlite3.Connection, machine_key: str, payload: dict) -> dict:
    sync_defaults(conn, commit=False)
    machine = _machine(conn, machine_key)
    row = conn.execute(
        "SELECT * FROM changeover_machine_standards WHERE machine_id=?", (machine["id"],)
    ).fetchone()
    expected = payload.get("expected_version")
    if expected is not None and int(expected) != int(row["version"]):
        raise ValueError("Changeover standard changed; refresh before saving")
    try:
        seconds = float(payload.get("default_setup_s", row["default_setup_s"]))
    except (TypeError, ValueError) as error:
        raise ValueError("default_setup_s must be numeric") from error
    if not math.isfinite(seconds) or not 0 <= seconds <= MAX_SETUP_S:
        raise ValueError("default_setup_s must be between 0 and 14400")
    actor = str(payload.get("actor") or "planner").strip()
    if not actor:
        raise ValueError("actor is required")
    verified = int(bool(payload.get("verified", row["verified"])))
    source = str(payload.get("source") or ("site_standard" if verified else row["source"])).strip()
    notes = str(payload.get("notes") or "").strip() or None
    now = _now()
    conn.execute(
        """UPDATE changeover_machine_standards
           SET default_setup_s=?,source=?,verified=?,version=version+1,notes=?,
               updated_by=?,updated_at=? WHERE machine_id=?""",
        (seconds, source, verified, notes, actor, now, machine["id"]),
    )
    updated = dict(conn.execute(
        "SELECT * FROM changeover_machine_standards WHERE machine_id=?", (machine["id"],)
    ).fetchone())
    _event(conn, machine["id"], "standard_updated", actor, "standard", machine["id"], {
        "default_setup_s": seconds, "source": source, "verified": bool(verified),
        "version": updated["version"],
    })
    conn.commit()
    return {**updated, "machine_key": machine_key, "machine_name": machine["name"],
            "verified": bool(updated["verified"])}


def _scope_rows(conn: sqlite3.Connection, job_names: list[str] | None = None) -> list[dict]:
    params: list[object] = []
    if job_names:
        where = f"j.job_name IN ({','.join('?' for _ in job_names)})"
        params.extend(job_names)
    elif conn.execute("SELECT COUNT(*) count FROM production_orders").fetchone()["count"]:
        where = "po.status IN ('ready','released','in_progress')"
    else:
        where = "1=1"
    return [dict(row) for row in conn.execute(
        f"""SELECT p.*,j.job_name,m.machine_key,m.name machine_name
            FROM parts p JOIN jobs j ON j.id=p.job_id
            LEFT JOIN production_orders po ON po.job_id=j.id
            JOIN part_route_steps prs ON prs.part_id=p.id AND prs.required=1
            JOIN machines m ON m.id=prs.machine_id
            WHERE {where} ORDER BY j.id,p.id,prs.step_index""",
        params,
    ).fetchall()]


def scope_families(conn: sqlite3.Connection, job_names: list[str] | None = None) -> dict[str, list[dict]]:
    grouped: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in _scope_rows(conn, job_names):
        family = setup_family(row["machine_key"], row)
        if family:
            grouped[row["machine_key"]][family["key"]] = family
    return {key: sorted(values.values(), key=lambda item: item["key"])
            for key, values in grouped.items()}


def _active_models(conn: sqlite3.Connection) -> list[dict]:
    return [dict(row) for row in conn.execute(
        """SELECT cm.*,m.machine_key,m.name machine_name
           FROM changeover_models cm JOIN machines m ON m.id=cm.machine_id
           WHERE cm.status='active' ORDER BY m.name,cm.from_setup_key,cm.to_setup_key"""
    ).fetchall()]


def readiness_for_parts(conn: sqlite3.Connection, parts: Iterable[dict]) -> dict:
    keys: dict[str, set[str]] = defaultdict(set)
    for part in parts:
        for operation in part.get("operations", []):
            key = operation.get("setup_key")
            if key:
                keys[operation["machine_key"]].add(key)
    standards = {row["machine_key"]: dict(row) for row in conn.execute(
        """SELECT cms.*,m.machine_key,m.name machine_name
           FROM changeover_machine_standards cms JOIN machines m ON m.id=cms.machine_id"""
    ).fetchall()}
    model_pairs: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for row in _active_models(conn):
        model_pairs[row["machine_key"]].add((row["from_setup_key"], row["to_setup_key"]))
    checks = []
    for machine_key, family_keys in sorted(keys.items()):
        if len(family_keys) < 2:
            continue
        standard = standards.get(machine_key)
        required_pairs = {(source, target) for source in family_keys for target in family_keys
                          if source != target}
        learned = model_pairs.get(machine_key, set())
        verified_default = bool(standard and standard["verified"])
        covered = required_pairs.issubset(learned)
        checks.append({
            "machine_key": machine_key,
            "machine_name": standard["machine_name"] if standard else machine_key,
            "family_count": len(family_keys), "transition_count": len(required_pairs),
            "learned_transition_count": len(required_pairs & learned),
            "verified_default": verified_default,
            "passed": verified_default or covered,
            "detail": (
                "verified machine fallback" if verified_default else
                f"{len(required_pairs & learned)} of {len(required_pairs)} directional transitions learned"
            ),
        })
    ready = all(check["passed"] for check in checks)
    return {
        "applicable": bool(checks), "ready": ready, "checks": checks,
        "sensitive_machines": len(checks),
        "guardrail": (
            "Sequence-dependent setups use verified fallbacks or repeated directional evidence."
            if ready else
            "Verify a fallback standard or learn every required directional transition before schedule approval."
        ),
    }


def estimate(conn: sqlite3.Connection, machine_key: str,
             from_setup_key: str | None, to_setup_key: str | None) -> dict:
    if not from_setup_key or not to_setup_key or from_setup_key == to_setup_key:
        return {"seconds": 0.0, "source": "same_family", "confidence": "high",
                "production_eligible": True, "model_version": None}
    source_key, target_key = _normalize(from_setup_key), _normalize(to_setup_key)
    row = conn.execute(
        """SELECT cm.* FROM changeover_models cm JOIN machines m ON m.id=cm.machine_id
           WHERE m.machine_key=? AND cm.from_setup_key=? AND cm.to_setup_key=?
             AND cm.status='active' ORDER BY cm.version DESC LIMIT 1""",
        (machine_key, source_key, target_key),
    ).fetchone()
    if row:
        return {"seconds": round(float(row["p90_s"]), 1), "source": "learned_p90",
                "confidence": row["confidence"], "production_eligible": True,
                "model_version": row["version"], "sample_count": row["sample_count"]}
    standard = conn.execute(
        """SELECT cms.* FROM changeover_machine_standards cms
           JOIN machines m ON m.id=cms.machine_id WHERE m.machine_key=?""", (machine_key,)
    ).fetchone()
    if not standard:
        return {"seconds": 0.0, "source": "unavailable", "confidence": "none",
                "production_eligible": False, "model_version": None}
    verified = bool(standard["verified"])
    return {"seconds": round(float(standard["default_setup_s"]), 1),
            "source": "verified_standard" if verified else "engineering_assumption",
            "confidence": "manual" if verified else "low",
            "production_eligible": verified, "model_version": None,
            "standard_version": standard["version"]}


def _model_reason(sample_count: int, date_count: int, quality_count: int,
                  median_s: float, mad_s: float) -> tuple[str, str]:
    quality_share = quality_count / sample_count
    relative_mad = mad_s / median_s if median_s else math.inf
    if sample_count >= 15 and date_count >= 3 and quality_share >= 0.8 and relative_mad <= 0.3:
        return "high", "15+ samples across 3+ dates with first-good-piece evidence and stable variation"
    if sample_count >= 5 and date_count >= 2 and relative_mad <= 0.5:
        return "medium", "5+ samples across 2+ dates with bounded variation"
    return "low", "needs 5 samples across 2 dates and stable variation"


def sync_models(conn: sqlite3.Connection, actor: str = "hive-learning",
                commit: bool = True) -> dict:
    rows = [dict(row) for row in conn.execute(
        """SELECT * FROM changeover_observations WHERE validity='accepted'
           ORDER BY machine_id,from_setup_key,to_setup_key,observed_at,id"""
    ).fetchall()]
    grouped: dict[tuple[int, str, str], list[dict]] = defaultdict(list)
    for row in rows:
        grouped[(row["machine_id"], row["from_setup_key"], row["to_setup_key"])].append(row)
    existing_pairs = {(row["machine_id"], row["from_setup_key"], row["to_setup_key"])
                      for row in conn.execute(
                          "SELECT machine_id,from_setup_key,to_setup_key FROM changeover_models WHERE status IN ('active','candidate')"
                      ).fetchall()}
    cleared = existing_pairs - set(grouped)
    for machine_id, source_key, target_key in cleared:
        conn.execute(
            """UPDATE changeover_models SET status='superseded'
               WHERE machine_id=? AND from_setup_key=? AND to_setup_key=?
                 AND status IN ('active','candidate')""",
            (machine_id, source_key, target_key),
        )
    created = active = candidates = 0
    for (machine_id, source_key, target_key), observations in grouped.items():
        signature = _sha([{"id": item["id"], "fingerprint": item["fingerprint"]}
                          for item in observations])
        current = conn.execute(
            """SELECT id,status,confidence FROM changeover_models
               WHERE training_signature=?""", (signature,)
        ).fetchone()
        if current:
            desired = "active" if current["confidence"] in {"medium", "high"} else "candidate"
            conn.execute(
                """UPDATE changeover_models SET status='superseded'
                   WHERE machine_id=? AND from_setup_key=? AND to_setup_key=?
                     AND status IN ('active','candidate')""",
                (machine_id, source_key, target_key),
            )
            conn.execute(
                "UPDATE changeover_models SET status=? WHERE id=?",
                (desired, current["id"]),
            )
            continue
        values = [float(item["duration_s"]) for item in observations]
        median_s = statistics.median(values)
        mad_s = statistics.median(abs(value - median_s) for value in values)
        date_count = len({item["observed_at"][:10] for item in observations})
        quality_count = sum(bool(item["quality_confirmed"]) for item in observations)
        confidence, reason = _model_reason(
            len(values), date_count, quality_count, median_s, mad_s,
        )
        status = "active" if confidence in {"medium", "high"} else "candidate"
        if status == "active":
            conn.execute(
                """UPDATE changeover_models SET status='superseded'
                   WHERE machine_id=? AND from_setup_key=? AND to_setup_key=? AND status='active'""",
                (machine_id, source_key, target_key),
            )
        else:
            conn.execute(
                """UPDATE changeover_models SET status='superseded'
                   WHERE machine_id=? AND from_setup_key=? AND to_setup_key=?
                     AND status IN ('active','candidate')""",
                (machine_id, source_key, target_key),
            )
        version = conn.execute(
            """SELECT COALESCE(MAX(version),0)+1 version FROM changeover_models
               WHERE machine_id=? AND from_setup_key=? AND to_setup_key=?""",
            (machine_id, source_key, target_key),
        ).fetchone()["version"]
        cursor = conn.execute(
            """INSERT INTO changeover_models
               (machine_id,from_setup_key,to_setup_key,version,training_signature,
                sample_count,date_count,quality_confirmed_count,median_s,p90_s,mad_s,
                confidence,status,reason,trained_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (machine_id, source_key, target_key, version, signature, len(values), date_count,
             quality_count, round(median_s, 3), round(_percentile(values, 0.9), 3),
             round(mad_s, 3), confidence, status, reason, _now()),
        )
        _event(conn, machine_id, "model_trained", actor, "model", cursor.lastrowid, {
            "from_setup_key": source_key, "to_setup_key": target_key,
            "version": version, "sample_count": len(values), "confidence": confidence,
            "status": status,
        })
        created += 1
        active += status == "active"
        candidates += status == "candidate"
    if commit:
        conn.commit()
    return {"created": created, "active": active, "candidates": candidates,
            "cleared": len(cleared), "method_version": METHOD_VERSION}


def record_observation(conn: sqlite3.Connection, payload: dict) -> dict:
    machine_key = str(payload.get("machine_key") or "").strip()
    machine = _machine(conn, machine_key)
    source_key = _normalize(payload.get("from_setup_key"))
    target_key = _normalize(payload.get("to_setup_key"))
    if source_key == target_key:
        raise ValueError("A changeover observation requires two different setup families")
    try:
        duration_s = float(payload.get("duration_s"))
    except (TypeError, ValueError) as error:
        raise ValueError("duration_s must be numeric") from error
    if not math.isfinite(duration_s) or not 0 < duration_s <= MAX_SETUP_S:
        raise ValueError("duration_s must be greater than 0 and at most 14400")
    source = str(payload.get("source") or "manual_time_study").strip()
    if source not in OBSERVATION_SOURCES:
        raise ValueError(f"source must be one of {', '.join(sorted(OBSERVATION_SOURCES))}")
    actor = str(payload.get("actor") or "operator").strip()
    if not actor:
        raise ValueError("actor is required")
    observed_at = _iso(payload.get("observed_at"))
    evidence_type = str(payload.get("evidence_type") or "").strip() or None
    evidence_id = payload.get("evidence_id")
    quality_confirmed = int(bool(payload.get("quality_confirmed", False)))
    notes = str(payload.get("notes") or "").strip() or None
    identity = {
        "machine_key": machine_key, "from_setup_key": source_key,
        "to_setup_key": target_key, "duration_s": round(duration_s, 6),
        "observed_at": observed_at, "source": source,
        "evidence_type": evidence_type, "evidence_id": evidence_id,
        "quality_confirmed": bool(quality_confirmed),
    }
    fingerprint = _sha(identity)
    duplicate = conn.execute(
        "SELECT id FROM changeover_observations WHERE fingerprint=?", (fingerprint,)
    ).fetchone()
    if duplicate:
        return {"status": "duplicate", "observation_id": duplicate["id"],
                "fingerprint": fingerprint, "models": sync_models(conn)}
    try:
        cursor = conn.execute(
            """INSERT INTO changeover_observations
               (machine_id,from_setup_key,to_setup_key,duration_s,observed_at,source,
               evidence_type,evidence_id,quality_confirmed,validity,actor,notes,
                fingerprint,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,'accepted',?,?,?,?)""",
            (machine["id"], source_key, target_key, duration_s, observed_at, source,
             evidence_type, evidence_id, quality_confirmed, actor, notes, fingerprint, _now()),
        )
    except sqlite3.IntegrityError as error:
        if evidence_type and evidence_id is not None:
            existing = conn.execute(
                "SELECT id,fingerprint FROM changeover_observations WHERE evidence_type=? AND evidence_id=?",
                (evidence_type, evidence_id),
            ).fetchone()
            if existing and existing["fingerprint"] == fingerprint:
                return {"status": "duplicate", "observation_id": existing["id"],
                        "fingerprint": fingerprint, "models": sync_models(conn)}
            raise ValueError("This evidence record is already linked to different setup data") from error
        raise
    _event(conn, machine["id"], "observation_recorded", actor, "observation", cursor.lastrowid,
           identity)
    models = sync_models(conn, actor=actor, commit=False)
    conn.commit()
    return {"status": "accepted", "observation_id": cursor.lastrowid,
            "fingerprint": fingerprint, "models": models}


def exclude_observation(conn: sqlite3.Connection, observation_id: int,
                        reason: str, actor: str) -> dict:
    row = conn.execute(
        "SELECT * FROM changeover_observations WHERE id=?", (observation_id,)
    ).fetchone()
    if not row:
        raise KeyError(f"Changeover observation {observation_id} not found")
    if row["validity"] == "excluded":
        return {"status": "excluded", "observation_id": observation_id}
    reason = str(reason or "").strip()
    actor = str(actor or "").strip()
    if not reason or not actor:
        raise ValueError("Exclusion reason and actor are required")
    conn.execute(
        """UPDATE changeover_observations SET validity='excluded',exclusion_reason=?
           WHERE id=?""", (reason, observation_id)
    )
    _event(conn, row["machine_id"], "observation_excluded", actor, "observation",
           observation_id, {"reason": reason})
    models = sync_models(conn, actor=actor, commit=False)
    conn.commit()
    return {"status": "excluded", "observation_id": observation_id, "models": models}


def sync_downtime_observations(conn: sqlite3.Connection, actor: str = "hive-learning") -> dict:
    rows = conn.execute(
        """SELECT de.id,de.machine_id,de.started_at,de.ended_at,m.machine_key
           FROM downtime_events de JOIN machines m ON m.id=de.machine_id
           JOIN downtime_reasons dr ON dr.id=de.reason_id
           WHERE de.status='closed' AND de.ended_at IS NOT NULL
             AND (dr.code='setup' OR dr.label LIKE '%changeover%')
           ORDER BY de.id"""
    ).fetchall()
    accepted = duplicates = skipped = 0
    for downtime in rows:
        if downtime["machine_key"] not in SETUP_CAPABILITIES:
            skipped += 1
            continue
        previous = conn.execute(
            """SELECT p.* FROM machine_events me JOIN parts p ON p.id=me.part_id
               WHERE me.machine_id=? AND me.part_id IS NOT NULL AND me.ts<=?
                 AND me.event_type='cycle_end' ORDER BY me.ts DESC,me.id DESC LIMIT 1""",
            (downtime["machine_id"], downtime["started_at"]),
        ).fetchone()
        following = conn.execute(
            """SELECT p.* FROM machine_events me JOIN parts p ON p.id=me.part_id
               WHERE me.machine_id=? AND me.part_id IS NOT NULL AND me.ts>=?
                 AND me.event_type IN ('cycle_start','cycle_end')
               ORDER BY me.ts,me.id LIMIT 1""",
            (downtime["machine_id"], downtime["ended_at"]),
        ).fetchone()
        if not previous or not following:
            skipped += 1
            continue
        source_family = setup_family(downtime["machine_key"], dict(previous))
        target_family = setup_family(downtime["machine_key"], dict(following))
        start = datetime.fromisoformat(downtime["started_at"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(downtime["ended_at"].replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        duration_s = (end - start).total_seconds()
        if (not source_family or not target_family or
                source_family["key"] == target_family["key"] or
                not 0 < duration_s <= MAX_SETUP_S):
            skipped += 1
            continue
        result = record_observation(conn, {
            "machine_key": downtime["machine_key"],
            "from_setup_key": source_family["key"],
            "to_setup_key": target_family["key"],
            "duration_s": duration_s, "observed_at": downtime["started_at"],
            "source": "downtime_event", "evidence_type": "downtime_event",
            "evidence_id": downtime["id"], "quality_confirmed": False,
            "actor": actor, "notes": "Derived from an explicitly classified setup interval",
        })
        accepted += result["status"] == "accepted"
        duplicates += result["status"] == "duplicate"
    return {"downtime_events": len(rows), "accepted": accepted,
            "duplicates": duplicates, "skipped": skipped,
            "models": sync_models(conn, actor=actor)}


def snapshot(conn: sqlite3.Connection, job_names: list[str] | None = None) -> dict:
    families = scope_families(conn, job_names)
    standards = [dict(row) for row in conn.execute(
        """SELECT cms.*,m.machine_key,m.name machine_name
           FROM changeover_machine_standards cms JOIN machines m ON m.id=cms.machine_id
           ORDER BY m.name"""
    ).fetchall()]
    models = _active_models(conn)
    models_by_machine: dict[str, list[dict]] = defaultdict(list)
    for model in models:
        models_by_machine[model["machine_key"]].append(model)
    observations = [dict(row) for row in conn.execute(
        """SELECT co.*,m.machine_key,m.name machine_name
           FROM changeover_observations co JOIN machines m ON m.id=co.machine_id
           ORDER BY co.observed_at DESC,co.id DESC LIMIT 100"""
    ).fetchall()]
    accepted_observations = conn.execute(
        "SELECT COUNT(*) count FROM changeover_observations WHERE validity='accepted'"
    ).fetchone()["count"]
    machines = []
    for standard in standards:
        machine_families = families.get(standard["machine_key"], [])
        machine_models = models_by_machine.get(standard["machine_key"], [])
        machines.append({
            **standard, "verified": bool(standard["verified"]),
            "basis": SETUP_CAPABILITIES[standard["machine_key"]]["basis"],
            "families": machine_families, "family_count": len(machine_families),
            "setup_sensitive": len(machine_families) > 1,
            "models": machine_models, "active_model_count": len(machine_models),
        })
    synthetic_parts = [{"operations": [{"machine_key": machine_key, "setup_key": item["key"]}
                                         for item in values]}
                       for machine_key, values in families.items()]
    readiness = readiness_for_parts(conn, synthetic_parts)
    return {
        "method_version": METHOD_VERSION, "generated_at": _now(),
        "readiness": readiness,
        "summary": {
            "machines": len(machines),
            "verified_standards": sum(item["verified"] for item in machines),
            "setup_sensitive_machines": sum(item["setup_sensitive"] for item in machines),
            "active_models": len(models),
            "accepted_observations": accepted_observations,
        },
        "machines": machines, "observations": observations,
        "guardrail": (
            "Unverified engineering defaults can run commissioning comparisons but cannot make a schedule approvable. "
            "Learned transition estimates use the observed P90 and require repeated evidence."
        ),
    }
