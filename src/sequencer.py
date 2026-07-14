"""
Job sequencer — automatic production order optimisation.

Algorithm: Weighted Shortest Processing Time (WSPT) with material batching.

Scoring per job:
    score = (urgency_weight * due_date_factor) / bottleneck_machine_time
    + material_batch_bonus (if adjacent job uses same primary material)

Due date factor:
    > 2 days away  → 1.0  (normal)
    1–2 days       → 2.0  (elevated)
    today/overdue  → 4.0  (urgent)
    no date set    → 1.0

Material batch bonus: jobs using the same primary sheet material score
higher when placed adjacent — reduces beam saw sheet changeovers.

Output: ordered list of jobs with estimated start/end times per machine,
critical path, and a plain-English reason for each job's position.

All times are in seconds. Returns estimated times as None when cycle time
coefficients are uncalibrated (system still produces a valid sequence,
just without time estimates).
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Optional

import cycle_time as ct

CONFIG_PATH = Path(__file__).parent.parent / "config" / "cycle_times.yaml"


@dataclass
class SequencedJob:
    position:         int            # 1-based position in the sequence
    job_name:         str
    client_name:      str
    total_parts:      int
    primary_material: str            # most common material in the job
    due_date:         Optional[str]  # ISO date if known
    urgency:          str            # "overdue" | "urgent" | "normal" | "unknown"
    score:            float          # higher = earlier in sequence
    score_reason:     str            # plain English explanation

    # Cycle time estimates (None if uncalibrated)
    bottleneck_machine:    Optional[str]
    bottleneck_time_s:     Optional[float]
    estimated_total_s:     Optional[float]  # wall-clock on critical path


@dataclass
class SequencePlan:
    generated_at:   str
    total_jobs:     int
    jobs:           list[SequencedJob] = field(default_factory=list)
    uncalibrated:   bool = False      # True if any estimates are missing
    shift_hours:    int  = 9


def _primary_material(conn: sqlite3.Connection, job_id: int) -> str:
    """Most common material in the job by part count."""
    row = conn.execute(
        """SELECT material, COUNT(*) as cnt
           FROM parts WHERE job_id=? AND material IS NOT NULL
           GROUP BY material ORDER BY cnt DESC LIMIT 1""",
        (job_id,)
    ).fetchone()
    return row["material"] if row else "unknown"


def _due_date_factor(due_date: Optional[str]) -> tuple[float, str]:
    """Returns (multiplier, urgency_label)."""
    if not due_date:
        return 1.0, "unknown"
    try:
        due = datetime.fromisoformat(due_date.replace("Z", "+00:00")).date()
        today = datetime.now(timezone.utc).date()
        days_left = (due - today).days
        if days_left < 0:
            return 4.0, "overdue"
        elif days_left == 0:
            return 4.0, "urgent"
        elif days_left <= 2:
            return 2.0, "urgent"
        else:
            return 1.0, "normal"
    except ValueError:
        return 1.0, "unknown"


def _material_group(material: str) -> str:
    """Normalise material name to a grouping key for batch detection."""
    if not material:
        return "unknown"
    m = material.upper()
    # Group by thickness primarily — that's what determines beam saw setup
    import re
    thick = re.search(r'(\d+)MM', m)
    if thick:
        return thick.group(1) + "mm"
    return m[:20]  # fallback: first 20 chars


def sequence(conn: sqlite3.Connection,
             job_names: Optional[list[str]] = None,
             cfg_path: Path = CONFIG_PATH) -> SequencePlan:
    """
    Compute optimal job sequence.

    job_names: if provided, sequence only these jobs.
                if None, sequence all jobs not yet started today.
    """
    import yaml
    with open(cfg_path) as f:
        raw_cfg = yaml.safe_load(f)
    shift_hours = raw_cfg.get("shift_hours", 9)

    # Load jobs
    controlled = conn.execute("SELECT COUNT(*) count FROM production_orders").fetchone()["count"]
    if job_names:
        placeholders = ",".join("?" * len(job_names))
        rows = conn.execute(
            f"""SELECT j.id, j.job_name, j.job_date, j.total_parts,
                       c.name as client_name, po.due_at, po.priority,
                       po.status order_status, po.release_sequence
                FROM jobs j LEFT JOIN clients c ON j.client_id=c.id
                LEFT JOIN production_orders po ON po.job_id=j.id
                WHERE j.job_name IN ({placeholders})""",
            job_names
        ).fetchall()
    else:
        # All jobs not fully completed
        rows = conn.execute(
            """SELECT j.id, j.job_name, j.job_date, j.total_parts,
                      c.name as client_name, po.due_at, po.priority,
                      po.status order_status, po.release_sequence
               FROM jobs j LEFT JOIN clients c ON j.client_id=c.id
               LEFT JOIN production_orders po ON po.job_id=j.id
               WHERE ?=0 OR po.status IN ('ready','released','in_progress')
               ORDER BY COALESCE(po.release_sequence, 999999), po.due_at, j.id""",
            (controlled,),
        ).fetchall()

    if not rows:
        return SequencePlan(
            generated_at = datetime.now(timezone.utc).isoformat(),
            total_jobs   = 0,
            shift_hours  = shift_hours,
        )

    # Score each job
    scored: list[tuple[float, dict]] = []
    any_uncalibrated = False

    for row in rows:
        job_id      = row["id"]
        job_name    = row["job_name"]
        total_parts = row["total_parts"] or 0
        due_date    = row["due_at"] if controlled else row["job_date"]
        priority    = row["priority"] or 50
        client_name = row["client_name"] or ""

        primary_mat = _primary_material(conn, job_id)
        due_factor, urgency = _due_date_factor(due_date)

        # Cycle time estimate for this job
        ct_result = ct.estimate_job(conn, job_name, cfg_path)
        bottleneck_machine = ct_result.get("critical_machine")
        bottleneck_time_s  = ct_result.get("critical_path_s")

        # Check if any machine was uncalibrated
        for mk_data in ct_result.get("machines", {}).values():
            if mk_data.get("uncalibrated"):
                any_uncalibrated = True

        # WSPT score: higher = run earlier
        # When uncalibrated: use total_parts as proxy for job size
        if bottleneck_time_s and bottleneck_time_s > 0:
            base_score = due_factor * (priority / 50) / bottleneck_time_s * 10000
        else:
            # No cycle time data — use due date only, tie-break by part count
            base_score = due_factor * (priority / 50) / max(total_parts, 1) * 100

        scored.append((base_score, {
            "job_name":         job_name,
            "client_name":      client_name,
            "total_parts":      total_parts,
            "primary_material": primary_mat,
            "due_date":         due_date,
            "urgency":          urgency,
            "score":            base_score,
            "bottleneck_machine": bottleneck_machine,
            "bottleneck_time_s":  bottleneck_time_s,
            "estimated_total_s":  bottleneck_time_s,
            "mat_group":        _material_group(primary_mat),
        }))

    # Sort by score descending
    scored.sort(key=lambda x: x[0], reverse=True)

    # Material batching pass: if two adjacent jobs differ in material group
    # but there's a same-group job within 3 positions, swap it forward
    # (only if urgency difference is small — don't demote urgent jobs)
    for i in range(len(scored) - 1):
        cur_group = scored[i][1]["mat_group"]
        for j in range(i + 1, min(i + 4, len(scored))):
            if scored[j][1]["mat_group"] == cur_group:
                # Only swap if urgency is same or lower
                if scored[j][1]["urgency"] in ("normal", "unknown") and \
                   scored[i + 1][1]["urgency"] in ("normal", "unknown"):
                    # Move j to i+1
                    item = scored.pop(j)
                    scored.insert(i + 1, item)
                break

    # Build output
    result_jobs = []
    for pos, (score, d) in enumerate(scored, start=1):
        # Build reason string
        reasons = []
        if d["urgency"] == "overdue":
            reasons.append("overdue")
        elif d["urgency"] == "urgent":
            reasons.append(f"due {d['due_date']}")
        if d["bottleneck_time_s"]:
            h = int(d["bottleneck_time_s"] // 3600)
            m = int((d["bottleneck_time_s"] % 3600) // 60)
            reasons.append(f"~{h}h{m:02d}m on {d['bottleneck_machine'] or 'unknown'}")
        if not reasons:
            reasons.append("no due date, ordered by job date")

        result_jobs.append(SequencedJob(
            position          = pos,
            job_name          = d["job_name"],
            client_name       = d["client_name"],
            total_parts       = d["total_parts"],
            primary_material  = d["primary_material"],
            due_date          = d["due_date"],
            urgency           = d["urgency"],
            score             = round(score, 4),
            score_reason      = ", ".join(reasons),
            bottleneck_machine= d["bottleneck_machine"],
            bottleneck_time_s = d["bottleneck_time_s"],
            estimated_total_s = d["estimated_total_s"],
        ))

    return SequencePlan(
        generated_at = datetime.now(timezone.utc).isoformat(),
        total_jobs   = len(result_jobs),
        jobs         = result_jobs,
        uncalibrated = any_uncalibrated,
        shift_hours  = shift_hours,
    )
