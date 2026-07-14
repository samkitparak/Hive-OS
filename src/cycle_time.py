"""
Cycle time estimator.

Estimates how long a part will take on each machine it visits,
derived purely from Cabinet Vision data (no real timing needed until calibration).

Formula per machine type:
  beam_saw / panel_saw / sander / paint:
      t = base_s + length_mm * length_coeff + width_mm * width_coeff
            + area_m2 * area_coeff

  cnc (morbidelli):
      t = base_s + area_m2 * area_coeff
            + (1 if two_faces else 0) * face_coeff
            + (1 if has_groove   else 0) * groove_coeff

  edge_bander (stefani):
      t = (base_s + length_mm * length_coeff) * num_edges
            + (num_edges - 1) * edge_coeff     ← return passes

  press / glue (sergiani / osama):
      t = base_s + area_m2 * area_coeff

Returns None for any machine whose coefficients are all zero (uncalibrated).

Grooves are detected from CNC filename: SCM Maestro/CV uses a 'g' suffix on
the sequence number for groove programs — e.g. r86bg007 vs r86b0007.
Pattern: r{run}{face}g{seq}  where face ∈ {b, f} and seq is digits.
"""

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml


CONFIG_PATH = Path(__file__).parent.parent / "config" / "cycle_times.yaml"

# Maps machine_key → machine type for formula selection
MACHINE_TYPE_MAP = {
    "gabbiani_pt80":   "beam_saw",
    "nova_si400":      "panel_saw",
    "morbidelli_cx100":"cnc",
    "morbidelli_n100": "cnc",
    "stefani_kd":      "edge_bander",
    "sergiani_gs120":  "press",
    "varie_osama":     "glue",
    "dmc60_rcs135":    "sander",
    "dmc90_xrt135":    "sander",
    "superfici":       "paint",
}

# Groove detection: filename contains face letter followed by 'g' then digits
# e.g. r86bg007, r66bg012 — the 'g' after the face letter signals a groove program
_GROOVE_RE = re.compile(r'r\d+[bf]g\d+', re.IGNORECASE)


@dataclass
class PartFeatures:
    """Extracted features for one part, used by all machine estimators."""
    length_mm:   float
    width_mm:    float
    area_m2:     float
    num_edges:   int          # 0–4
    has_cnc:     bool
    two_faces:   bool         # back AND front CNC file present
    has_groove:  bool         # groove detected in either CNC filename
    machine_key: str          # which machine this estimate is for


def extract_features(part: dict, machine_key: str) -> PartFeatures:
    l = part.get("length_mm") or 0.0
    w = part.get("width_mm")  or 0.0
    area = (l * w) / 1_000_000  # mm² → m²

    eb_count = sum(1 for k in ("eb1","eb2","eb3","eb4") if part.get(k))

    cnc_back  = part.get("cnc_file_back")  or ""
    cnc_front = part.get("cnc_file_front") or ""
    has_cnc   = bool(cnc_back or cnc_front)
    two_faces = bool(cnc_back and cnc_front)
    has_groove = bool(_GROOVE_RE.search(cnc_back) or _GROOVE_RE.search(cnc_front))

    return PartFeatures(
        length_mm  = l,
        width_mm   = w,
        area_m2    = area,
        num_edges  = eb_count,
        has_cnc    = has_cnc,
        two_faces  = two_faces,
        has_groove = has_groove,
        machine_key= machine_key,
    )


def _is_calibrated(cfg: dict) -> bool:
    """True if any coefficient is non-zero."""
    return any(v != 0 for v in cfg.values() if isinstance(v, (int, float)))


def coefficient_names(machine_key: str) -> list[str]:
    """Return the coefficient contract for a machine's linear model."""
    mtype = MACHINE_TYPE_MAP.get(machine_key, "")
    if mtype in ("beam_saw", "panel_saw"):
        return ["base_s", "length_coeff", "width_coeff", "area_coeff"]
    if mtype == "cnc":
        return ["base_s", "area_coeff", "face_coeff", "groove_coeff"]
    if mtype == "edge_bander":
        return ["base_s", "length_coeff", "edge_coeff"]
    if mtype in ("press", "glue"):
        return ["base_s", "area_coeff"]
    if mtype in ("sander", "paint"):
        return ["base_s", "length_coeff"]
    return []


def design_row(features: PartFeatures) -> list[float]:
    """Convert part features to the row used for fitting and prediction."""
    mtype = MACHINE_TYPE_MAP.get(features.machine_key, "")
    if mtype in ("beam_saw", "panel_saw"):
        return [1, features.length_mm, features.width_mm, features.area_m2]
    if mtype == "cnc":
        return [1, features.area_m2, float(features.two_faces), float(features.has_groove)]
    if mtype == "edge_bander":
        return [features.num_edges, features.length_mm * features.num_edges,
                max(0, features.num_edges - 1)]
    if mtype in ("press", "glue"):
        return [1, features.area_m2]
    if mtype in ("sander", "paint"):
        return [1, features.length_mm]
    return []


def estimate_from_coefficients(features: PartFeatures,
                               coefficients: dict) -> Optional[float]:
    """Estimate from an already-loaded coefficient mapping."""
    if features.machine_key not in MACHINE_TYPE_MAP or not coefficients:
        return None
    if MACHINE_TYPE_MAP[features.machine_key] == "edge_bander" and features.num_edges == 0:
        return None
    names = coefficient_names(features.machine_key)
    row = design_row(features)
    if not names or len(names) != len(row):
        return None
    value = sum(float(coefficients.get(name, 0)) * term for name, term in zip(names, row))
    return max(0.0, round(value, 1))


def active_model(conn: sqlite3.Connection, machine_key: str) -> Optional[dict]:
    row = conn.execute(
        """SELECT cm.* FROM cycle_models cm
           JOIN machines m ON m.id=cm.machine_id
           WHERE m.machine_key=? AND cm.status='active'
           ORDER BY cm.version DESC LIMIT 1""",
        (machine_key,),
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["coefficients"] = json.loads(result.pop("coefficients_json"))
    result["identified_features"] = json.loads(result.pop("identified_features_json"))
    return result


def active_models(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute(
        """SELECT cm.*, m.machine_key FROM cycle_models cm
           JOIN machines m ON m.id=cm.machine_id WHERE cm.status='active'
           ORDER BY cm.version"""
    ).fetchall()
    result = {}
    for row in rows:
        model = dict(row)
        model["coefficients"] = json.loads(model.pop("coefficients_json"))
        model["identified_features"] = json.loads(model.pop("identified_features_json"))
        result[model["machine_key"]] = model
    return result


def estimate_for_part(conn: sqlite3.Connection, part: dict, machine_key: str,
                      cfg_path: Path = CONFIG_PATH,
                      learned_models: Optional[dict[str, dict]] = None,
                      config: Optional[dict] = None) -> dict:
    """Choose a learned model first, then the manually calibrated fallback."""
    features = extract_features(part, machine_key)
    learned = (learned_models.get(machine_key) if learned_models is not None
               else active_model(conn, machine_key))
    if learned:
        return {
            "seconds": estimate_from_coefficients(features, learned["coefficients"]),
            "source": "learned",
            "confidence": learned["confidence"],
            "model_version": learned["version"],
        }
    if config is None:
        with open(cfg_path) as config_file:
            config = yaml.safe_load(config_file) or {}
    coefficients = config.get("machines", {}).get(machine_key, {})
    seconds = estimate_from_coefficients(features, coefficients) if _is_calibrated(coefficients) else None
    return {
        "seconds": seconds,
        "source": "manual" if seconds is not None else "unavailable",
        "confidence": "manual" if seconds is not None else "none",
        "model_version": None,
    }


def estimate(features: PartFeatures,
             cfg_path: Path = CONFIG_PATH) -> Optional[float]:
    """
    Returns estimated cycle time in seconds, or None if uncalibrated.
    """
    with open(cfg_path) as f:
        raw = yaml.safe_load(f)

    machines = raw.get("machines", {})
    cfg      = machines.get(features.machine_key, {})
    if not cfg or not _is_calibrated(cfg):
        return None

    return estimate_from_coefficients(features, cfg)


def estimate_job(conn: sqlite3.Connection, job_name: str,
                 cfg_path: Path = CONFIG_PATH) -> dict:
    """
    Estimate total and per-machine cycle times for all parts in a job.

    Returns:
    {
        "job_name": str,
        "total_parts": int,
        "machines": {
            machine_key: {
                "parts": int,               # parts visiting this machine
                "estimated_total_s": float | None,
                "estimated_avg_s":   float | None,
                "uncalibrated":      bool,
            }
        },
        "critical_path_s": float | None,   # longest single-machine total (bottleneck)
        "critical_machine": str | None,
    }
    """
    job = conn.execute(
        "SELECT id, total_parts FROM jobs WHERE job_name=?", (job_name,)
    ).fetchone()
    if not job:
        return {}

    parts = conn.execute(
        """SELECT id, length_mm, width_mm, qty, eb1, eb2, eb3, eb4,
                  cnc_file_back, cnc_file_front, has_cnc
           FROM parts WHERE job_id=?""",
        (job["id"],)
    ).fetchall()

    machine_totals: dict[str, list[float]] = {k: [] for k in MACHINE_TYPE_MAP}
    machine_uncalibrated: dict[str, bool]  = {k: False for k in MACHINE_TYPE_MAP}
    learned_models = active_models(conn)
    with open(cfg_path) as config_file:
        config = yaml.safe_load(config_file) or {}

    for part in parts:
        p = dict(part)
        observed = {
            row["machine_key"] for row in conn.execute(
                """SELECT DISTINCT m.machine_key FROM machine_events me
                   JOIN machines m ON m.id=me.machine_id WHERE me.part_id=?""",
                (p["id"],),
            ).fetchall()
        }
        applicable = {"gabbiani_pt80"} | observed
        if p.get("has_cnc"):
            applicable.add("morbidelli_cx100")
        if any(p.get(key) for key in ("eb1", "eb2", "eb3", "eb4")):
            applicable.add("stefani_kd")
        for mk in MACHINE_TYPE_MAP:
            if mk not in applicable:
                continue

            feats = extract_features(p, mk)
            prediction = estimate_for_part(
                conn, p, mk, cfg_path, learned_models=learned_models, config=config
            )
            t = prediction["seconds"]
            if t is None:
                machine_uncalibrated[mk] = True
            else:
                machine_totals[mk].extend([t] * max(int(p.get("qty") or 1), 1))

    result_machines = {}
    critical_path_s = None
    critical_machine = None

    for mk, times in machine_totals.items():
        if not times and not machine_uncalibrated[mk]:
            continue  # no parts for this machine
        total = sum(times) if times else None
        avg   = (total / len(times)) if (total and times) else None
        result_machines[mk] = {
            "parts":             len(times),
            "estimated_total_s": round(total, 1) if total else None,
            "estimated_avg_s":   round(avg,   1) if avg   else None,
            "uncalibrated":      machine_uncalibrated[mk],
            "source":            "learned" if mk in learned_models else (
                "manual" if times else "unavailable"
            ),
        }
        if total and (critical_path_s is None or total > critical_path_s):
            critical_path_s  = total
            critical_machine = mk

    return {
        "job_name":        job_name,
        "total_parts":     job["total_parts"],
        "machines":        result_machines,
        "critical_path_s": round(critical_path_s, 1) if critical_path_s else None,
        "critical_machine": critical_machine,
    }


def calibrate(timing_records: list[dict],
              machine_key: str,
              cfg_path: Path = CONFIG_PATH) -> dict:
    """
    Fit coefficients from real timing data.

    timing_records: list of dicts with keys:
        length_mm, width_mm, eb1, eb2, eb3, eb4,
        cnc_file_back, cnc_file_front, has_cnc,
        actual_seconds

    Returns fitted coefficients dict. Does NOT write to file — caller decides.

    Uses ordinary least squares. Requires numpy (falls back to returning
    zeroed coefficients with a message if numpy not available).
    """
    try:
        import numpy as np
    except ImportError:
        return {"error": "numpy required for calibration: pip install numpy"}

    mtype = MACHINE_TYPE_MAP.get(machine_key, "")
    if not mtype:
        return {"error": f"unknown machine_key: {machine_key}"}

    rows_X = []
    rows_y = []

    for r in timing_records:
        p     = r.copy()
        feats = extract_features(p, machine_key)
        y     = r["actual_seconds"]

        if mtype in ("beam_saw", "panel_saw"):
            rows_X.append([1, feats.length_mm, feats.width_mm, feats.area_m2])
        elif mtype == "cnc":
            rows_X.append([1, feats.area_m2,
                           1 if feats.two_faces  else 0,
                           1 if feats.has_groove else 0])
        elif mtype == "edge_bander":
            per_pass = feats.length_mm
            rows_X.append([feats.num_edges, per_pass * feats.num_edges,
                           max(0, feats.num_edges - 1)])
        elif mtype in ("press", "glue"):
            rows_X.append([1, feats.area_m2])
        elif mtype in ("sander", "paint"):
            rows_X.append([1, feats.length_mm])
        else:
            continue
        rows_y.append(y)

    if len(rows_y) < 3:
        return {"error": "need at least 3 timing records to calibrate"}

    X = np.array(rows_X, dtype=float)
    y = np.array(rows_y, dtype=float)

    # Non-negative least squares — coefficients can't be negative (time can't decrease)
    from numpy.linalg import lstsq
    coeffs, _, _, _ = lstsq(X, y, rcond=None)
    coeffs = np.maximum(coeffs, 0)  # clip negatives to 0

    if mtype in ("beam_saw", "panel_saw"):
        return {"base_s": round(float(coeffs[0]), 2),
                "length_coeff": round(float(coeffs[1]), 6),
                "width_coeff":  round(float(coeffs[2]), 6),
                "area_coeff":   round(float(coeffs[3]), 4)}
    elif mtype == "cnc":
        return {"base_s":        round(float(coeffs[0]), 2),
                "area_coeff":    round(float(coeffs[1]), 4),
                "face_coeff":    round(float(coeffs[2]), 2),
                "groove_coeff":  round(float(coeffs[3]), 2)}
    elif mtype == "edge_bander":
        return {"base_s":        round(float(coeffs[0]), 2),
                "length_coeff":  round(float(coeffs[1]), 6),
                "edge_coeff":    round(float(coeffs[2]), 2)}
    elif mtype in ("press", "glue"):
        return {"base_s":      round(float(coeffs[0]), 2),
                "area_coeff":  round(float(coeffs[1]), 4)}
    elif mtype in ("sander", "paint"):
        return {"base_s":        round(float(coeffs[0]), 2),
                "length_coeff":  round(float(coeffs[1]), 6)}
    return {}
