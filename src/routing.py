"""Evidence-backed production-route inference."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from statistics import median

MAX_TRANSFER_SECONDS = 4 * 60 * 60


def _seconds(start: str, end: str) -> float:
    def parse(value: str) -> datetime:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)

    return (parse(end) - parse(start)).total_seconds()


def refresh_observations(conn: sqlite3.Connection) -> dict:
    """Record same-part transitions without extrapolating missing process steps."""
    ends = conn.execute(
        """SELECT me.* FROM machine_events me
           WHERE me.event_type='cycle_end' AND me.part_id IS NOT NULL
           ORDER BY me.ts, me.id"""
    ).fetchall()
    created = 0
    for end in ends:
        next_start = conn.execute(
            """SELECT * FROM machine_events
               WHERE part_id=? AND event_type='cycle_start'
                 AND (ts>? OR (ts=? AND id>?))
               ORDER BY ts, id LIMIT 1""",
            (end["part_id"], end["ts"], end["ts"], end["id"]),
        ).fetchone()
        if not next_start or next_start["machine_id"] == end["machine_id"]:
            continue
        transfer = _seconds(end["ts"], next_start["ts"])
        if transfer < 0 or transfer > MAX_TRANSFER_SECONDS:
            continue
        before = conn.total_changes
        conn.execute(
            """INSERT OR IGNORE INTO route_observations
               (part_id, from_machine_id, to_machine_id, from_event_id,
                to_event_id, transfer_s, observed_at)
               VALUES (?,?,?,?,?,?,?)""",
            (end["part_id"], end["machine_id"], next_start["machine_id"],
             end["id"], next_start["id"], transfer, next_start["ts"]),
        )
        created += conn.total_changes > before
    conn.commit()
    return {"created": created}


def graph(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        """SELECT fm.machine_key from_machine, tm.machine_key to_machine,
                  ro.part_id, ro.transfer_s
           FROM route_observations ro
           JOIN machines fm ON fm.id=ro.from_machine_id
           JOIN machines tm ON tm.id=ro.to_machine_id
           ORDER BY from_machine, to_machine"""
    ).fetchall()
    grouped: dict[tuple[str, str], list] = {}
    outgoing: dict[str, int] = {}
    for row in rows:
        key = (row["from_machine"], row["to_machine"])
        grouped.setdefault(key, []).append(row)
        outgoing[row["from_machine"]] = outgoing.get(row["from_machine"], 0) + 1
    edges = []
    for (source, target), observations in grouped.items():
        support = len(observations)
        unique_parts = len({row["part_id"] for row in observations})
        confidence = "high" if unique_parts >= 20 else "medium" if unique_parts >= 5 else "low"
        edges.append({
            "from_machine": source,
            "to_machine": target,
            "support": support,
            "unique_parts": unique_parts,
            "median_transfer_s": round(median(row["transfer_s"] for row in observations), 1),
            "outgoing_probability": round(support / outgoing[source], 4),
            "confidence": confidence,
        })
    return {
        "status": "observed" if edges else "collecting",
        "observation_count": len(rows),
        "edge_count": len(edges),
        "edges": edges,
        "guardrail": "Edges describe observed transitions only; they do not imply a complete route.",
    }


def part_route(conn: sqlite3.Connection, part: dict) -> dict:
    """Use this part's observed route, otherwise a minimal CV-derived route."""
    planned = conn.execute(
        """SELECT m.machine_key, prs.source, prs.confidence
           FROM part_route_steps prs JOIN machines m ON m.id=prs.machine_id
           WHERE prs.part_id=? AND prs.required=1 ORDER BY prs.step_index""",
        (part.get("id"),),
    ).fetchall() if part.get("id") else []
    if planned:
        confidences = {row["confidence"] for row in planned}
        confidence = ("confirmed" if "confirmed" in confidences or all(
            row["source"] == "manual" for row in planned)
            else "high" if confidences == {"high"} else "low")
        return {
            "machines": [row["machine_key"] for row in planned],
            "source": "planned_route",
            "evidence": sorted({row["source"] for row in planned}),
            "confidence": confidence,
        }
    observed = conn.execute(
        """SELECT m.machine_key FROM machine_events me
           JOIN machines m ON m.id=me.machine_id
           WHERE me.part_id=? AND me.event_type IN ('cycle_start', 'cycle_end')
           ORDER BY me.ts, me.id""", (part.get("id"),)
    ).fetchall() if part.get("id") else []
    route = []
    for row in observed:
        if not route or route[-1] != row["machine_key"]:
            route.append(row["machine_key"])
    if len(route) >= 2:
        return {"machines": route, "source": "part_history", "confidence": "high"}

    observed_prefix = route[0] if route else None
    route = ["gabbiani_pt80"]
    if part.get("has_cnc"):
        route.append("morbidelli_cx100")
    if any(part.get(key) for key in ("eb1", "eb2", "eb3", "eb4")):
        route.append("stefani_kd")
    return {
        "machines": route,
        "source": "cv_feature_assumption",
        "confidence": "low",
        "observed_prefix": observed_prefix,
    }
