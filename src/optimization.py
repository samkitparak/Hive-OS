"""Explainable factory optimization priorities built from trusted HIVE evidence."""

import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional

import bottleneck
import data_quality
import forecasting
import procurement


def _downtime_evidence(conn: sqlite3.Connection, start: str, end: str) -> Optional[dict]:
    row = conn.execute(
        """SELECT dr.code,dr.label,dr.category,m.machine_key,m.name machine_name,
                  COUNT(*) occurrences,
                  SUM(MAX(0,(julianday(COALESCE(de.ended_at,?))-julianday(de.started_at))*86400)) seconds,
                  MAX(dc.actual_cause_code) confirmed_cause
           FROM downtime_events de
           LEFT JOIN downtime_reasons dr ON dr.id=de.reason_id
           LEFT JOIN machines m ON m.id=de.machine_id
           LEFT JOIN diagnostic_cases dc ON dc.source_type='downtime' AND dc.source_id=de.id
                                        AND dc.status='confirmed'
           WHERE de.started_at>=? AND de.started_at<=?
           GROUP BY dr.id,de.machine_id ORDER BY seconds DESC LIMIT 1""",
        (end, start, end),
    ).fetchone()
    if not row or not row["seconds"]:
        return None
    return dict(row)


def _quality_evidence(conn: sqlite3.Connection, start: str, end: str) -> Optional[dict]:
    row = conn.execute(
        """SELECT COALESCE(dt.label,'Unclassified') defect,
                  COALESCE(dt.code,'unclassified') defect_code,
                  m.machine_key,m.name machine_name,COUNT(*) count,
                  MAX(dc.actual_cause_code) confirmed_cause
           FROM quality_checks qc
           LEFT JOIN defect_types dt ON dt.id=qc.defect_type_id
           LEFT JOIN machines m ON m.id=qc.machine_id
           LEFT JOIN diagnostic_cases dc ON dc.source_type='quality_check' AND dc.source_id=qc.id
                                        AND dc.status='confirmed'
           WHERE qc.result IN ('fail','rework') AND qc.ts>=? AND qc.ts<=?
           GROUP BY dt.id,qc.machine_id ORDER BY count DESC LIMIT 1""",
        (start, end),
    ).fetchone()
    return dict(row) if row else None


def _finalize(recommendations: list[dict], start: str, end: str,
              generated_at: str) -> list[dict]:
    for item in recommendations:
        item.setdefault("target_type", "factory")
        item.setdefault("target_key", "factory")
        item.setdefault("cause_code", item["category"])
        item.setdefault("metric_hint", None)
        item.setdefault("target_direction", None)
        identity = {
            key: item.get(key) for key in (
                "category", "target_type", "target_key", "cause_code", "action"
            )
        }
        item["recommendation_key"] = hashlib.sha256(
            json.dumps(identity, sort_keys=True).encode("utf-8")
        ).hexdigest()[:24]
        item["source_window_start"] = start
        item["source_window_end"] = end
        item["source_generated_at"] = generated_at
        item["experiment_eligible"] = bool(item["metric_hint"])
    return recommendations


def build(conn: sqlite3.Connection, window_hours: int = 8,
          now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    start = (now - timedelta(hours=window_hours)).isoformat()
    end = now.isoformat()
    quality = data_quality.build(conn, window_hours, now)
    constraint = bottleneck.detect(conn, window_hours, now)
    purchasing = procurement.snapshot(conn)
    forecast = forecasting.snapshot(conn)
    recommendations = []

    low_reporting = [
        machine for machine in quality["machines"]
        if machine["event_count"] and machine["confidence"] == "low"
    ]
    if not quality["summary"]["reporting_machines"]:
        recommendations.append({
            "priority": 1,
            "category": "commissioning",
            "title": "Connect the first production machine",
            "action": "Import a Maestro log sample, pass parser checks, then start its agent.",
            "confidence": "high",
            "estimated_gain": None,
            "evidence": ["No production telemetry exists in the selected window"],
            "cause_code": "missing_telemetry",
        })
    elif low_reporting:
        target = sorted(low_reporting, key=lambda item: item["score"])[0]
        recommendations.append({
            "priority": 1,
            "category": "data_quality",
            "title": f"Repair telemetry confidence for {target['machine_name']}",
            "action": target["issues"][0] if target["issues"] else "Capture a longer validated event window.",
            "confidence": "high",
            "estimated_gain": None,
            "evidence": [f"Telemetry score {round(target['score'] * 100)}%", *target["issues"][:2]],
            "target_type": "machine", "target_key": target["machine_key"],
            "cause_code": "telemetry_quality",
        })

    uncovered = [
        item for item in purchasing["recommendations"]
        if item["uncovered_qty"] > 1e-9
    ]
    unmapped = [
        item for item in uncovered
        if not item["mapping"] or not item["mapping"]["verified"]
        or not item["mapping"]["supplier_verified"]
    ]
    supply_risks = [item for item in uncovered if item["at_risk"]]
    if unmapped:
        recommendations.append({
            "priority": len(recommendations) + 1,
            "category": "supply",
            "title": f"Commission suppliers for {len(unmapped)} shortages",
            "action": (
                "Map supplier SKUs, purchase units, pack sizes, and lead times; "
                "verify the master data before approving purchase orders."
            ),
            "confidence": "high",
            "estimated_gain": None,
            "evidence": [
                f"{item['name']}: {item['uncovered_qty']:g} {item['internal_uom']} uncovered"
                for item in unmapped[:3]
            ],
            "cause_code": "uncommissioned_supplier_master",
        })
    elif supply_risks:
        recommendations.append({
            "priority": len(recommendations) + 1,
            "category": "supply",
            "title": f"Expedite {len(supply_risks)} supply-risk items",
            "action": (
                "Review need-by dates against projected supplier arrival and expedite, "
                "split, or reschedule the affected orders."
            ),
            "confidence": "medium",
            "estimated_gain": None,
            "evidence": [
                f"{item['name']}: projected {item['projected_arrival_at']}"
                for item in supply_risks[:3]
            ],
            "cause_code": "supplier_lead_time_risk",
        })

    current = constraint.current
    if current and current.confidence in ("medium", "high"):
        recommendations.append({
            "priority": len(recommendations) + 1,
            "category": "constraint",
            "title": f"Protect throughput at {current.machine_name}",
            "action": current.recommendation,
            "confidence": current.confidence,
            "estimated_gain": None,
            "evidence": current.evidence or [f"Constraint score {round(current.score * 100)}%"],
            "target_type": "machine", "target_key": current.machine_key,
            "cause_code": current.primary_cause,
            "metric_hint": "throughput_per_hour", "target_direction": "increase",
        })

    latest_forecast = forecast.get("latest")
    forecast_result = latest_forecast["result"] if latest_forecast else {}
    if forecast.get("decision_ready"):
        at_risk = sorted([
            item for item in forecast_result.get("jobs", [])
            if item.get("late_probability") is not None and item["late_probability"] >= 0.2
        ], key=lambda item: (
            item["late_probability"], item.get("expected_tardiness_s") or 0
        ), reverse=True)
        if at_risk:
            risk = at_risk[0]
            recommendations.append({
                "priority": len(recommendations) + 1,
                "category": "delivery_risk",
                "title": f"Recover the schedule for {risk['job_name']}",
                "action": "Review its release position, constraint queue, material readiness, and due-time commitment before the next dispatch.",
                "confidence": "medium",
                "estimated_gain": None,
                "evidence": [
                    f"{round(risk['late_probability'] * 100)}% simulated late risk",
                    f"P80 completion {risk['completion_at']['p80']}",
                    f"Forecast calibration: {forecast['calibration']['status']}",
                ],
                "target_type": "production_order",
                "target_key": str(risk.get("production_order_id") or risk["job_name"]),
                "cause_code": "forecast_delivery_risk",
            })
        forecast_constraints = forecast_result.get("constraints", [])
        if forecast_constraints:
            future = forecast_constraints[0]
            if (future["bottleneck_probability"] >= 0.5 and
                    (not current or future["machine_key"] != current.machine_key)):
                recommendations.append({
                    "priority": len(recommendations) + 1,
                    "category": "forecast_constraint",
                    "title": f"Prepare {future['machine_name']} for incoming load",
                    "action": "Verify staffing, tooling, maintenance clearance, and its input buffer before releasing the forecast queue.",
                    "confidence": "medium",
                    "estimated_gain": None,
                    "evidence": [
                        f"{round(future['bottleneck_probability'] * 100)}% forecast bottleneck frequency",
                        f"P90 utilization {round(future['p90_utilization'] * 100)}%",
                    ],
                    "target_type": "machine", "target_key": future["machine_key"],
                    "cause_code": "forecast_capacity_risk",
                })

    downtime = _downtime_evidence(conn, start, end)
    if downtime:
        confirmed_downtime = downtime.get("confirmed_cause")
        downtime_evidence = [
            f"{downtime['occurrences']} events",
            f"{round(downtime['seconds'] / 60)} recorded minutes",
        ]
        if confirmed_downtime:
            downtime_evidence.append(f"Operator-confirmed cause: {confirmed_downtime}")
        recommendations.append({
            "priority": len(recommendations) + 1,
            "category": "downtime",
            "title": f"Reduce {downtime['label'].lower()}",
            "action": "Run a focused cause review on the largest recorded downtime category.",
            "confidence": "medium",
            "estimated_gain": None,
            "evidence": downtime_evidence,
            "target_type": "machine" if downtime.get("machine_key") else "factory",
            "target_key": downtime.get("machine_key") or "factory",
            "cause_code": confirmed_downtime or {
                "maintenance": "reliability", "flow": "material_flow",
                "labor": "staffing", "quality": "quality_loss", "planned": "setup",
            }.get(downtime.get("category"), downtime.get("code") or "downtime"),
            "metric_hint": "downtime_minutes_per_hour", "target_direction": "decrease",
        })

    defects = _quality_evidence(conn, start, end)
    if defects:
        defect_evidence = [f"{defects['count']} failures or rework records"]
        if defects.get("confirmed_cause"):
            defect_evidence.append(f"Operator-confirmed cause: {defects['confirmed_cause']}")
        recommendations.append({
            "priority": len(recommendations) + 1,
            "category": "quality",
            "title": f"Contain {defects['defect'].lower()} defects",
            "action": "Trace the affected parts to machine, material, and program before the next batch.",
            "confidence": "medium",
            "estimated_gain": None,
            "evidence": defect_evidence,
            "target_type": "machine" if defects.get("machine_key") else "factory",
            "target_key": defects.get("machine_key") or "factory",
            "cause_code": defects.get("confirmed_cause") or f"defect:{defects['defect_code']}",
            "metric_hint": "defect_rate", "target_direction": "decrease",
        })

    history = []
    for offset in range(0, min(24, window_hours * 4), window_hours):
        point_now = now - timedelta(hours=offset)
        report = bottleneck.detect(conn, window_hours, point_now)
        history.append({
            "window_end": point_now.isoformat(),
            "machine_key": report.current.machine_key if report.current else None,
            "score": report.current.score if report.current else 0.0,
            "confidence": report.current.confidence if report.current else "low",
        })
    keys = [item["machine_key"] for item in history if item["machine_key"]]
    common = Counter(keys).most_common(1)
    persistence = common[0][1] / len(history) if common and history else 0.0

    if quality["intelligence_ready"] and current:
        status = "ready"
    elif quality["summary"]["reporting_machines"]:
        status = "learning"
    else:
        status = "commissioning"
    return {
        "generated_at": now.isoformat(),
        "window_hours": window_hours,
        "status": status,
        "telemetry_score": quality["overall_score"],
        "intelligence_ready": quality["intelligence_ready"],
        "current_constraint": vars(current) if current else None,
        "constraint_persistence": round(persistence, 3),
        "constraint_history": history,
        "supply": {
            "commissioned": purchasing["commissioned"],
            "uncovered_shortages": len(uncovered),
            "unmapped_shortages": len(unmapped),
            "supply_risks": len(supply_risks),
            "open_purchase_orders": purchasing["summary"]["open_purchase_orders"],
        },
        "forecast": {
            "available": latest_forecast is not None,
            "stale": forecast.get("stale", False),
            "decision_ready": forecast.get("decision_ready", False),
            "calibration_status": forecast["calibration"]["status"],
            "forecast_id": latest_forecast["id"] if latest_forecast else None,
        },
        "recommendations": _finalize(recommendations, start, end, now.isoformat()),
        "guardrail": (
            "Estimated gains remain hidden until real cycle times and stable telemetry are available."
        ),
    }
