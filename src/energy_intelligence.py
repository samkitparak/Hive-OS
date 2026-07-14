"""Evidence-labeled utility energy and waste analysis."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone


def _settings(value: str | None) -> dict:
    try:
        return json.loads(value) if value else {}
    except json.JSONDecodeError:
        return {}


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _round(value: float | None, digits: int = 3) -> float | None:
    return round(value, digits) if value is not None else None


def _profile_metrics(conn: sqlite3.Connection, profile: sqlite3.Row,
                     cutoff: str, now: datetime) -> dict:
    settings = _settings(profile["settings_json"])
    samples = conn.execute(
        """SELECT value_num,source_ts FROM telemetry_samples
           WHERE profile_key=? AND signal_key='power_w' AND quality='good'
             AND source_ts>=? ORDER BY source_ts,id""",
        (profile["profile_key"], cutoff),
    ).fetchall()
    idle_threshold = float(settings.get("idle_threshold_w", 300))
    on_threshold = float(settings.get("on_threshold_w", 2000))
    poll_interval = float(profile["poll_interval_s"] or 15)
    maximum_gap = max(60.0, poll_interval * 3)
    total_kwh = loaded_kwh = idle_kwh = standby_kwh = 0.0
    covered_s = loaded_s = idle_s = standby_s = 0.0
    max_power = 0.0
    weighted_power = 0.0
    gaps = 0
    for current, following in zip(samples, samples[1:]):
        power = max(0.0, float(current["value_num"] or 0))
        max_power = max(max_power, power)
        duration = (_dt(following["source_ts"]) - _dt(current["source_ts"])).total_seconds()
        if duration <= 0 or duration > maximum_gap:
            gaps += 1
            continue
        energy = power * duration / 3_600_000
        total_kwh += energy
        covered_s += duration
        weighted_power += power * duration
        if power >= on_threshold:
            loaded_kwh += energy
            loaded_s += duration
        elif power >= idle_threshold:
            idle_kwh += energy
            idle_s += duration
        else:
            standby_kwh += energy
            standby_s += duration
    if samples:
        max_power = max(max_power, max(float(sample["value_num"] or 0) for sample in samples))
        span_s = max(0.0, (min(now, _dt(samples[-1]["source_ts"])) - _dt(samples[0]["source_ts"])).total_seconds())
    else:
        span_s = 0.0
    coverage = min(1.0, covered_s / span_s) if span_s > 0 else 0.0
    average_power = weighted_power / covered_s if covered_s else None

    energy_rows = conn.execute(
        """SELECT value_num,source_ts FROM telemetry_samples
           WHERE profile_key=? AND signal_key='energy_kwh' AND quality='good'
             AND source_ts>=? ORDER BY source_ts,id""",
        (profile["profile_key"], cutoff),
    ).fetchall()
    meter_delta = None
    if len(energy_rows) >= 2:
        delta = float(energy_rows[-1]["value_num"]) - float(energy_rows[0]["value_num"])
        if delta >= 0:
            meter_delta = delta
    energy_used = meter_delta if meter_delta is not None else total_kwh
    energy_source = "cumulative_meter" if meter_delta is not None else "power_integration"

    pf = conn.execute(
        """SELECT AVG(value_num) average,MIN(value_num) minimum
           FROM telemetry_samples WHERE profile_key=? AND signal_key='power_factor'
             AND quality='good' AND source_ts>=?""",
        (profile["profile_key"], cutoff),
    ).fetchone()
    average_pf = float(pf["average"]) if pf and pf["average"] is not None else None
    confidence = "high" if coverage >= 0.9 and len(samples) >= 20 else (
        "medium" if coverage >= 0.6 and len(samples) >= 5 else "low"
    )
    idle_share = idle_kwh / total_kwh if total_kwh > 0 else 0.0
    load_factor = average_power / max_power if average_power is not None and max_power > 0 else None
    tariff = float(settings.get("tariff_per_kwh", 0) or 0)
    alerts = []
    if samples and coverage < 0.8:
        alerts.append({
            "code": "telemetry_gaps", "severity": "warning",
            "detail": f"Only {coverage:.0%} of the observed span has contiguous power evidence.",
            "action": "Check device reachability, poll interval, and network stability.",
        })
    if total_kwh >= 0.1 and idle_share >= 0.2:
        alerts.append({
            "code": "idle_energy", "severity": "opportunity",
            "detail": f"{idle_share:.0%} of integrated energy occurred in the idle band.",
            "action": "Review unload, auto-stop, extraction interlock, and shift shutdown settings.",
        })
    if average_pf is not None and average_pf < 0.85:
        alerts.append({
            "code": "low_power_factor", "severity": "warning",
            "detail": f"Average power factor is {average_pf:.2f}.",
            "action": "Have a qualified electrician verify load condition and correction equipment.",
        })
    return {
        "profile_key": profile["profile_key"],
        "name": profile["name"],
        "machine_key": profile["machine_key"],
        "sample_count": len(samples),
        "coverage": _round(coverage),
        "confidence": confidence,
        "energy_kwh": _round(energy_used),
        "energy_source": energy_source,
        "integrated_energy_kwh": _round(total_kwh),
        "loaded_energy_kwh": _round(loaded_kwh),
        "idle_energy_kwh": _round(idle_kwh),
        "standby_energy_kwh": _round(standby_kwh),
        "loaded_hours": _round(loaded_s / 3600),
        "idle_hours": _round(idle_s / 3600),
        "standby_hours": _round(standby_s / 3600),
        "idle_energy_share": _round(idle_share),
        "average_power_w": _round(average_power, 1),
        "max_power_w": _round(max_power, 1),
        "load_factor": _round(load_factor),
        "average_power_factor": _round(average_pf),
        "tariff_per_kwh": tariff or None,
        "estimated_cost": _round(energy_used * tariff, 2) if tariff else None,
        "gap_count": gaps,
        "alerts": alerts,
    }


def build(conn: sqlite3.Connection, *, hours: int = 24) -> dict:
    hours = max(1, min(int(hours), 24 * 30))
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=hours)).isoformat()
    profiles = conn.execute(
        """SELECT ip.*,m.machine_key FROM industrial_profiles ip
           LEFT JOIN machines m ON m.id=ip.machine_id
           WHERE EXISTS (
             SELECT 1 FROM telemetry_samples ts
             WHERE ts.profile_key=ip.profile_key AND ts.signal_key='power_w'
           ) ORDER BY ip.profile_key"""
    ).fetchall()
    metrics = [_profile_metrics(conn, profile, cutoff, now) for profile in profiles]
    alerts = [
        {"profile_key": profile["profile_key"], "name": profile["name"], **alert}
        for profile in metrics for alert in profile["alerts"]
    ]
    energy_total = sum(profile["energy_kwh"] or 0 for profile in metrics)
    cost_values = [profile["estimated_cost"] for profile in metrics
                   if profile["estimated_cost"] is not None]
    return {
        "generated_at": now.isoformat(),
        "window_hours": hours,
        "summary": {
            "profiles_with_power": len(metrics),
            "energy_kwh": _round(energy_total),
            "estimated_cost": _round(sum(cost_values), 2) if cost_values else None,
            "idle_energy_kwh": _round(sum(profile["idle_energy_kwh"] or 0 for profile in metrics)),
            "alerts": len(alerts),
        },
        "profiles": metrics,
        "alerts": alerts,
        "assumptions": [
            "Power integration excludes intervals longer than three polls (minimum 60 seconds).",
            "Cumulative meter delta is preferred when monotonic energy_kwh evidence exists.",
            "Idle and loaded bands use the approved profile thresholds.",
            "Cost remains unavailable until tariff_per_kwh is commissioned.",
        ],
    }
