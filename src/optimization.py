"""Explainable factory optimization priorities built from trusted HIVE evidence."""

import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional

import bottleneck
import data_quality


def _downtime_evidence(conn: sqlite3.Connection, start: str, end: str) -> Optional[dict]:
    row = conn.execute(
        """SELECT dr.code,dr.label,dr.category,
                  COUNT(*) occurrences,
                  SUM(MAX(0,(julianday(COALESCE(de.ended_at,?))-julianday(de.started_at))*86400)) seconds
           FROM downtime_events de
           LEFT JOIN downtime_reasons dr ON dr.id=de.reason_id
           WHERE de.started_at>=? AND de.started_at<=?
           GROUP BY dr.id ORDER BY seconds DESC LIMIT 1""",
        (end, start, end),
    ).fetchone()
    if not row or not row["seconds"]:
        return None
    return dict(row)


def _quality_evidence(conn: sqlite3.Connection, start: str, end: str) -> Optional[dict]:
    row = conn.execute(
        """SELECT COALESCE(dt.label,'Unclassified') defect,COUNT(*) count
           FROM quality_checks qc LEFT JOIN defect_types dt ON dt.id=qc.defect_type_id
           WHERE qc.result IN ('fail','rework') AND qc.ts>=? AND qc.ts<=?
           GROUP BY dt.id ORDER BY count DESC LIMIT 1""",
        (start, end),
    ).fetchone()
    return dict(row) if row else None


def build(conn: sqlite3.Connection, window_hours: int = 8,
          now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    start = (now - timedelta(hours=window_hours)).isoformat()
    end = now.isoformat()
    quality = data_quality.build(conn, window_hours, now)
    constraint = bottleneck.detect(conn, window_hours, now)
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
        })

    downtime = _downtime_evidence(conn, start, end)
    if downtime:
        recommendations.append({
            "priority": len(recommendations) + 1,
            "category": "downtime",
            "title": f"Reduce {downtime['label'].lower()}",
            "action": "Run a focused cause review on the largest recorded downtime category.",
            "confidence": "medium",
            "estimated_gain": None,
            "evidence": [
                f"{downtime['occurrences']} events",
                f"{round(downtime['seconds'] / 60)} recorded minutes",
            ],
        })

    defects = _quality_evidence(conn, start, end)
    if defects:
        recommendations.append({
            "priority": len(recommendations) + 1,
            "category": "quality",
            "title": f"Contain {defects['defect'].lower()} defects",
            "action": "Trace the affected parts to machine, material, and program before the next batch.",
            "confidence": "medium",
            "estimated_gain": None,
            "evidence": [f"{defects['count']} failures or rework records"],
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
        "recommendations": recommendations,
        "guardrail": (
            "Estimated gains remain hidden until real cycle times and stable telemetry are available."
        ),
    }
