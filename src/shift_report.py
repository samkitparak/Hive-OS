"""
Shift report generator.

Produces a self-contained HTML file summarising one shift's production.
Opens in any browser and prints to PDF with Ctrl+P.

Usage (standalone):
    PYTHONPATH=src python src/shift_report.py                  # today's shift
    PYTHONPATH=src python src/shift_report.py 2026-06-01       # specific date

Called by FastAPI:
    GET /report/shift?date=2026-06-01   → HTML response
    GET /report/shift                   → today
"""

import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yaml
from jinja2 import Template

import oee as oee_module
import score as score_module

CONFIG_PATH    = Path(__file__).parent.parent / "config" / "cycle_times.yaml"
UTILITY_KEYS   = {"elgi_1", "elgi_2", "aarco_1", "aarco_2"}


# ── Data collection ───────────────────────────────────────────────────────────

@dataclass
class MachineReport:
    machine_key:  str
    name:         str
    type:         str
    availability: float
    oee:          float
    parts_made:   int
    run_time_s:   int
    down_time_s:  int
    alarm_count:  int


@dataclass
class JobReport:
    job_name:     str
    client_name:  str
    total_parts:  int
    parts_done:   int
    pct_done:     float
    completed:    bool


@dataclass
class ShiftReport:
    date:           str
    shift_hours:    int
    factory_oee:    float
    factory_avail:  float
    score:          float
    rolling_avg:    float
    trend:          str
    streak:         int
    total_parts:    int
    alarms:         int
    machines:       list[MachineReport] = field(default_factory=list)
    jobs:           list[JobReport]     = field(default_factory=list)
    top_machine:    Optional[str]       = None   # highest OEE
    worst_machine:  Optional[str]       = None   # lowest OEE (among active)
    generated_at:   str                 = ""


def _shift_window(date_str: str, shift_hours: int,
                  now: Optional[datetime] = None) -> tuple[str, str]:
    """
    Returns (window_start, window_end) for a shift.

    For today: window_end = now, window_start = now - shift_hours.
    For a past date: window covers the full shift_hours from midnight UTC.
    This ensures today's events (which arrive throughout the day) are always
    inside the window regardless of what time of day the report is generated.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    if date_str == today:
        # Rolling window ending now — captures all events so far today
        start = now - timedelta(hours=shift_hours)
        return start.isoformat(), now.isoformat()
    else:
        # Historical date — full shift from midnight UTC
        d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return d.isoformat(), (d + timedelta(hours=shift_hours)).isoformat()


def _alarm_count(conn: sqlite3.Connection, machine_id: int,
                 ws: str, we: str) -> int:
    row = conn.execute(
        """SELECT COUNT(*) FROM machine_events
           WHERE machine_id=? AND event_type='alarm' AND ts >= ? AND ts <= ?""",
        (machine_id, ws, we)
    ).fetchone()
    return row[0] if row else 0


def _jobs_for_shift(conn: sqlite3.Connection, ws: str, we: str) -> list[JobReport]:
    rows = conn.execute(
        """SELECT DISTINCT j.job_name, j.total_parts,
                  c.name as client_name
           FROM machine_events me
           JOIN parts p ON me.part_id = p.id
           JOIN jobs j ON p.job_id = j.id
           LEFT JOIN clients c ON j.client_id = c.id
           WHERE me.ts >= ? AND me.ts <= ?
           GROUP BY j.id""",
        (ws, we)
    ).fetchall()

    result = []
    for row in rows:
        done_row = conn.execute(
            """SELECT COUNT(DISTINCT me.part_id)
               FROM machine_events me
               JOIN parts p ON me.part_id = p.id
               JOIN jobs j ON p.job_id = j.id
               WHERE j.job_name = ? AND me.event_type = 'cycle_end'
                 AND me.ts >= ? AND me.ts <= ?""",
            (row["job_name"], ws, we)
        ).fetchone()
        done  = done_row[0] if done_row else 0
        total = row["total_parts"] or 0
        result.append(JobReport(
            job_name    = row["job_name"],
            client_name = row["client_name"] or "",
            total_parts = total,
            parts_done  = done,
            pct_done    = round(done / total, 4) if total > 0 else 0.0,
            completed   = total > 0 and done >= total,
        ))
    return sorted(result, key=lambda j: j.pct_done, reverse=True)


def build(conn: sqlite3.Connection, date_str: Optional[str] = None) -> ShiftReport:
    if date_str is None:
        date_str = datetime.now(timezone.utc).date().isoformat()

    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)
    shift_hours = cfg.get("shift_hours", 9)

    now_dt = datetime.now(timezone.utc)
    ws, we = _shift_window(date_str, shift_hours, now_dt)
    all_machines = conn.execute(
        "SELECT id, name, machine_key, type FROM machines WHERE active=1"
    ).fetchall()

    machine_reports = []
    for m in all_machines:
        if m["machine_key"] in UTILITY_KEYS:
            continue
        result = oee_module.calculate(conn, m["id"], shift_hours, now_dt)
        if result.run_time_s == 0 and result.idle_time_s == 0 and result.down_time_s == 0:
            continue  # machine had no activity this shift
        alarms = _alarm_count(conn, m["id"], ws, we)
        machine_reports.append(MachineReport(
            machine_key  = m["machine_key"],
            name         = m["name"],
            type         = m["type"] or "",
            availability = result.availability,
            oee          = result.oee,
            parts_made   = result.parts_made,
            run_time_s   = result.run_time_s,
            down_time_s  = result.down_time_s,
            alarm_count  = alarms,
        ))

    machine_reports.sort(key=lambda m: m.oee, reverse=True)

    # Factory-wide aggregates
    factory_oee   = (sum(m.oee          for m in machine_reports) / len(machine_reports)
                     if machine_reports else 0.0)
    factory_avail = (sum(m.availability for m in machine_reports) / len(machine_reports)
                     if machine_reports else 0.0)
    total_parts   = sum(m.parts_made for m in machine_reports)
    total_alarms  = sum(m.alarm_count for m in machine_reports)

    top     = machine_reports[0].name   if machine_reports else None
    worst   = machine_reports[-1].name  if len(machine_reports) > 1 else None

    # Score + streak
    daily = score_module.get_daily_score(conn)

    jobs = _jobs_for_shift(conn, ws, we)

    return ShiftReport(
        date          = date_str,
        shift_hours   = shift_hours,
        factory_oee   = round(factory_oee, 4),
        factory_avail = round(factory_avail, 4),
        score         = daily.score,
        rolling_avg   = daily.rolling_avg,
        trend         = daily.trend,
        streak        = daily.streak,
        total_parts   = total_parts,
        alarms        = total_alarms,
        machines      = machine_reports,
        jobs          = jobs,
        top_machine   = top,
        worst_machine = worst,
        generated_at  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )


# ── HTML rendering ────────────────────────────────────────────────────────────

_TEMPLATE = Template(r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>HIVE OS — Shift Report {{ report.date }}</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Inter', system-ui, sans-serif; background: #f9fafb;
         color: #111827; font-size: 13px; padding: 32px; }
  h1   { font-size: 20px; font-weight: 800; margin-bottom: 2px; }
  h2   { font-size: 11px; font-weight: 700; color: #6b7280; letter-spacing: 1px;
         text-transform: uppercase; margin: 24px 0 10px; }
  .sub { font-size: 12px; color: #6b7280; margin-bottom: 24px; }

  /* Summary cards */
  .cards { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin-bottom: 8px; }
  .card  { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px;
           padding: 14px 16px; }
  .card .label { font-size: 10px; color: #9ca3af; text-transform: uppercase;
                 letter-spacing: 0.8px; margin-bottom: 6px; }
  .card .value { font-size: 26px; font-weight: 800; line-height: 1; }
  .card .hint  { font-size: 10px; color: #9ca3af; margin-top: 4px; }
  .green  { color: #16a34a; }
  .amber  { color: #d97706; }
  .red    { color: #dc2626; }
  .blue   { color: #2563eb; }

  /* Machine table */
  table  { width: 100%; border-collapse: collapse; background: #fff;
           border: 1px solid #e5e7eb; border-radius: 8px; overflow: hidden; }
  th     { background: #f3f4f6; font-size: 10px; font-weight: 700; color: #6b7280;
           text-transform: uppercase; letter-spacing: 0.8px;
           padding: 8px 12px; text-align: left; }
  td     { padding: 8px 12px; border-top: 1px solid #f3f4f6; }
  tr:hover td { background: #f9fafb; }

  /* OEE bar in table */
  .bar-wrap { width: 80px; height: 6px; background: #e5e7eb; border-radius: 3px; display: inline-block; vertical-align: middle; }
  .bar-fill  { height: 100%; border-radius: 3px; }

  /* Job list */
  .jobs { display: flex; flex-direction: column; gap: 8px; }
  .job  { background: #fff; border: 1px solid #e5e7eb; border-radius: 6px;
          padding: 10px 14px; display: flex; align-items: center; gap: 16px; }
  .job-name   { font-weight: 700; font-size: 13px; min-width: 120px; }
  .job-client { color: #6b7280; font-size: 11px; min-width: 100px; }
  .job-prog   { flex: 1; }
  .prog-bar   { height: 6px; background: #e5e7eb; border-radius: 3px; margin-top: 4px; }
  .prog-fill  { height: 100%; border-radius: 3px; transition: width 0.4s; }
  .job-pct    { font-size: 12px; font-weight: 700; min-width: 40px; text-align: right; }
  .badge      { font-size: 9px; font-weight: 700; padding: 2px 7px; border-radius: 3px;
                letter-spacing: 0.5px; }
  .badge-done { background: #dcfce7; color: #16a34a; }
  .badge-wip  { background: #fef9c3; color: #ca8a04; }

  footer { margin-top: 32px; font-size: 10px; color: #9ca3af; text-align: right; }

  @media print {
    body { background: #fff; padding: 16px; }
    .card { break-inside: avoid; }
  }
</style>
</head>
<body>

<h1>HIVE OS — Shift Report</h1>
<div class="sub">{{ report.date }} &nbsp;·&nbsp; {{ report.shift_hours }}-hour shift &nbsp;·&nbsp; HAEEV Factory</div>

<h2>Summary</h2>
<div class="cards">

  <div class="card">
    <div class="label">Daily Score</div>
    <div class="value {% if report.score >= 70 %}green{% elif report.score >= 50 %}amber{% else %}red{% endif %}">
      {{ report.score | round | int }}
    </div>
    <div class="hint">
      {% if report.trend == "up" %}▲{% elif report.trend == "down" %}▼{% else %}●{% endif %}
      vs {{ report.rolling_avg }} avg
      {% if report.streak > 1 %}&nbsp;· {{ report.streak }}-day streak{% endif %}
    </div>
  </div>

  <div class="card">
    <div class="label">Factory OEE</div>
    <div class="value {% if report.factory_oee >= 0.75 %}green{% elif report.factory_oee >= 0.5 %}amber{% else %}red{% endif %}">
      {{ (report.factory_oee * 100) | round | int }}%
    </div>
    <div class="hint">Avail {{ (report.factory_avail * 100) | round | int }}%</div>
  </div>

  <div class="card">
    <div class="label">Parts Processed</div>
    <div class="value blue">{{ report.total_parts }}</div>
    <div class="hint">cycle_end events</div>
  </div>

  <div class="card">
    <div class="label">Jobs Active</div>
    <div class="value">{{ report.jobs | length }}</div>
    <div class="hint">{{ report.jobs | selectattr("completed") | list | length }} completed</div>
  </div>

  <div class="card">
    <div class="label">Alarms</div>
    <div class="value {% if report.alarms > 0 %}red{% else %}green{% endif %}">
      {{ report.alarms }}
    </div>
    <div class="hint">across all machines</div>
  </div>

</div>

{% if report.top_machine or report.worst_machine %}
<div style="font-size:11px; color:#6b7280; margin-bottom:20px;">
  {% if report.top_machine %}
  Best machine: <strong style="color:#16a34a;">{{ report.top_machine }}</strong>
  {% endif %}
  {% if report.worst_machine %}
  &nbsp;·&nbsp; Needs attention: <strong style="color:#dc2626;">{{ report.worst_machine }}</strong>
  {% endif %}
</div>
{% endif %}

{% if report.machines %}
<h2>Machine Breakdown</h2>
<table>
  <thead>
    <tr>
      <th>Machine</th>
      <th>Type</th>
      <th>OEE</th>
      <th>Availability</th>
      <th>Parts</th>
      <th>Run time</th>
      <th>Downtime</th>
      <th>Alarms</th>
    </tr>
  </thead>
  <tbody>
    {% for m in report.machines %}
    {% set oee_pct = (m.oee * 100) | round | int %}
    {% set avail_pct = (m.availability * 100) | round | int %}
    {% set oee_color = "#16a34a" if oee_pct >= 75 else ("#d97706" if oee_pct >= 50 else "#dc2626") %}
    <tr>
      <td><strong>{{ m.name }}</strong></td>
      <td style="color:#6b7280;">{{ m.type }}</td>
      <td>
        <span style="font-weight:700; color:{{ oee_color }};">{{ oee_pct }}%</span>
        &nbsp;
        <span class="bar-wrap">
          <span class="bar-fill" style="width:{{ oee_pct }}%; background:{{ oee_color }};"></span>
        </span>
      </td>
      <td>{{ avail_pct }}%</td>
      <td>{{ m.parts_made }}</td>
      <td>{{ (m.run_time_s // 3600) }}h {{ ((m.run_time_s % 3600) // 60) }}m</td>
      <td {% if m.down_time_s > 1800 %}style="color:#dc2626;"{% endif %}>
        {{ (m.down_time_s // 3600) }}h {{ ((m.down_time_s % 3600) // 60) }}m
      </td>
      <td {% if m.alarm_count > 0 %}style="color:#dc2626; font-weight:700;"{% endif %}>
        {{ m.alarm_count if m.alarm_count > 0 else "—" }}
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endif %}

{% if report.jobs %}
<h2>Jobs</h2>
<div class="jobs">
  {% for j in report.jobs %}
  {% set pct = (j.pct_done * 100) | round | int %}
  {% set bar_color = "#16a34a" if pct >= 75 else ("#2563eb" if pct >= 40 else "#d97706") %}
  <div class="job">
    <div class="job-name">{{ j.job_name }}</div>
    <div class="job-client">{{ j.client_name or "—" }}</div>
    <div class="job-prog">
      <div style="display:flex; justify-content:space-between; font-size:10px; color:#6b7280;">
        <span>{{ j.parts_done }} / {{ j.total_parts }} parts</span>
      </div>
      <div class="prog-bar">
        <div class="prog-fill" style="width:{{ pct }}%; background:{{ bar_color }};"></div>
      </div>
    </div>
    <div class="job-pct" style="color:{{ bar_color }};">{{ pct }}%</div>
    <span class="badge {% if j.completed %}badge-done{% else %}badge-wip{% endif %}">
      {{ "DONE" if j.completed else "IN PROGRESS" }}
    </span>
  </div>
  {% endfor %}
</div>
{% endif %}

<footer>Generated {{ report.generated_at }} by HIVE OS &nbsp;·&nbsp; Tier 1 — read-only observation</footer>
</body>
</html>
""")


def render_html(report: ShiftReport) -> str:
    return _TEMPLATE.render(report=report)


def save(conn: sqlite3.Connection, date_str: Optional[str] = None,
         out_dir: Path = Path(".")) -> Path:
    report   = build(conn, date_str)
    html     = render_html(report)
    out_path = out_dir / f"shift_report_{report.date}.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


if __name__ == "__main__":
    from db import init_db, DB_PATH
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    conn     = init_db(DB_PATH, check_same_thread=False)
    path     = save(conn, date_arg, out_dir=Path("."))
    print(f"Report saved: {path}")
