"""Physical unit identity, scan resolution, and print-ready labels."""

from __future__ import annotations

import base64
import html
import io
import secrets
import sqlite3
import unicodedata
from datetime import datetime, timezone

import segno


CONTROLLED_ORDER_STATES = ("ready", "released", "in_progress", "hold")
TERMINAL_UNIT_STATES = ("dispatched", "void")
LABEL_TEMPLATE = "part_100x50"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_unit_key(conn: sqlite3.Connection) -> str:
    while True:
        token = base64.b32encode(secrets.token_bytes(10)).decode("ascii").rstrip("=")
        key = f"HU-{token}"
        if not conn.execute("SELECT 1 FROM trace_units WHERE unit_key=?", (key,)).fetchone():
            return key


def _initial_status(order_status: str) -> str:
    if order_status in ("released", "in_progress", "hold"):
        return "released"
    if order_status == "cancelled":
        return "void"
    return "planned"


def _unit_row(conn: sqlite3.Connection, *, unit_id: int | None = None,
              unit_key: str | None = None) -> dict:
    where = "tu.id=?" if unit_id is not None else "tu.unit_key=?"
    value = unit_id if unit_id is not None else unit_key
    row = conn.execute(
        f"""SELECT tu.*, po.status order_status, po.external_order_id, po.job_id,
                   j.job_name, p.part_name, p.qty part_qty, p.material,
                   p.length_mm, p.width_mm, p.thickness_mm, p.part_cv_id,
                   a.assembly_name, m.machine_key current_machine_key,
                   m.name current_machine_name,
                   COALESCE((SELECT SUM(lpi.printed_count)
                             FROM label_print_items lpi WHERE lpi.unit_id=tu.id),0) label_print_count
            FROM trace_units tu
            JOIN production_orders po ON po.id=tu.production_order_id
            JOIN jobs j ON j.id=po.job_id
            JOIN parts p ON p.id=tu.part_id
            LEFT JOIN assemblies a ON a.id=p.assembly_id
            LEFT JOIN machines m ON m.id=tu.current_machine_id
            WHERE {where}""", (value,),
    ).fetchone()
    if not row:
        raise KeyError(f"Unit {unit_key or unit_id} not found")
    return dict(row)


def materialize_order(conn: sqlite3.Connection, order_id: int,
                      actor: str = "system", commit: bool = True) -> dict:
    order = conn.execute(
        """SELECT po.id, po.status, po.job_id, j.job_name
           FROM production_orders po JOIN jobs j ON j.id=po.job_id WHERE po.id=?""",
        (order_id,),
    ).fetchone()
    if not order:
        raise KeyError(f"Production order {order_id} not found")
    parts = conn.execute(
        "SELECT id, qty FROM parts WHERE job_id=? ORDER BY id", (order["job_id"],)
    ).fetchall()
    now = _now()
    created = voided = status_changed = 0
    target_status = _initial_status(order["status"])
    for part in parts:
        quantity = max(1, int(part["qty"] or 1))
        existing = {row["ordinal"]: dict(row) for row in conn.execute(
            "SELECT id, ordinal, status FROM trace_units WHERE part_id=?", (part["id"],)
        ).fetchall()}
        for ordinal in range(1, quantity + 1):
            current = existing.get(ordinal)
            if current:
                if target_status == "void":
                    progressed = conn.execute(
                        "SELECT 1 FROM unit_route_progress WHERE unit_id=? LIMIT 1",
                        (current["id"],),
                    ).fetchone()
                    if current["status"] in ("planned", "released") and not progressed:
                        conn.execute(
                            """UPDATE trace_units SET status='void', version=version+1,
                                      updated_at=? WHERE id=?""", (now, current["id"]),
                        )
                        voided += 1
                    continue
                if current["status"] == "void":
                    conn.execute(
                        """UPDATE trace_units SET status=?, version=version+1,
                                  updated_at=? WHERE id=?""",
                        (target_status, now, current["id"]),
                    )
                    status_changed += 1
                elif target_status == "released" and current["status"] == "planned":
                    conn.execute(
                        """UPDATE trace_units SET status='released', version=version+1,
                                  updated_at=? WHERE id=?""", (now, current["id"]),
                    )
                    status_changed += 1
                continue
            if target_status == "void":
                continue
            unit_key = _new_unit_key(conn)
            qr_payload = f"HIVE:U:{unit_key}"
            cursor = conn.execute(
                """INSERT INTO trace_units
                   (unit_key, qr_payload, production_order_id, part_id, ordinal,
                    status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)""",
                (unit_key, qr_payload, order_id, part["id"], ordinal,
                 target_status, now, now),
            )
            for scheme, value in (("hive_unit", unit_key), ("hive_qr", qr_payload)):
                conn.execute(
                    """INSERT INTO unit_identifier_aliases
                       (unit_id, scheme, value, source, created_by, created_at)
                       VALUES (?,?,?,'hive',?,?)""",
                    (cursor.lastrowid, scheme, value, actor, now),
                )
            created += 1
        for ordinal, current in existing.items():
            if ordinal <= quantity or current["status"] in TERMINAL_UNIT_STATES:
                continue
            progressed = conn.execute(
                "SELECT 1 FROM unit_route_progress WHERE unit_id=? LIMIT 1", (current["id"],)
            ).fetchone()
            if not progressed:
                conn.execute(
                    """UPDATE trace_units SET status='void', version=version+1,
                              updated_at=? WHERE id=?""", (now, current["id"]),
                )
                voided += 1
    if commit:
        conn.commit()
    total = conn.execute(
        "SELECT COUNT(*) count FROM trace_units WHERE production_order_id=? AND status!='void'",
        (order_id,),
    ).fetchone()["count"]
    return {"order_id": order_id, "job_name": order["job_name"], "created": created,
            "voided": voided, "status_changed": status_changed, "unit_count": total}


def sync_controlled_orders(conn: sqlite3.Connection, commit: bool = True) -> dict:
    rows = conn.execute(
        "SELECT id FROM production_orders WHERE status IN ('ready','released','in_progress','hold')"
    ).fetchall()
    created = changed = 0
    for row in rows:
        result = materialize_order(conn, row["id"], commit=False)
        created += result["created"]
        changed += result["status_changed"]
    if commit:
        conn.commit()
    return {"orders": len(rows), "created": created, "status_changed": changed}


def list_order_units(conn: sqlite3.Connection, order_id: int,
                     include_void: bool = False) -> list[dict]:
    clause = "" if include_void else "AND status!='void'"
    ids = conn.execute(
        f"""SELECT id FROM trace_units WHERE production_order_id=? {clause}
            ORDER BY part_id, ordinal""", (order_id,),
    ).fetchall()
    return [_unit_row(conn, unit_id=row["id"]) for row in ids]


def get_unit(conn: sqlite3.Connection, unit_key: str) -> dict:
    unit = _unit_row(conn, unit_key=unit_key.upper())
    unit["aliases"] = [dict(row) for row in conn.execute(
        """SELECT scheme, value, active, source, created_by, created_at
           FROM unit_identifier_aliases WHERE unit_id=? ORDER BY id""", (unit["id"],)
    ).fetchall()]
    unit["route_progress"] = [dict(row) for row in conn.execute(
        """SELECT urp.*, prs.step_index, m.machine_key, m.name machine_name
           FROM unit_route_progress urp
           JOIN part_route_steps prs ON prs.id=urp.route_step_id
           JOIN machines m ON m.id=prs.machine_id
           WHERE urp.unit_id=? ORDER BY prs.step_index""", (unit["id"],)
    ).fetchall()]
    unit["traceability"] = [dict(row) for row in conn.execute(
        """SELECT * FROM traceability_events WHERE object_key=?
           ORDER BY event_time DESC, id DESC LIMIT 100""", (unit["unit_key"],)
    ).fetchall()]
    return unit


def add_alias(conn: sqlite3.Connection, unit_key: str, scheme: str, value: str,
              actor: str, source: str = "manual") -> dict:
    unit = _unit_row(conn, unit_key=unit_key.upper())
    scheme = scheme.strip().lower()
    value = value.strip()
    if not scheme or not value:
        raise ValueError("Alias scheme and value are required")
    collision = conn.execute(
        "SELECT unit_id FROM unit_identifier_aliases WHERE value=?", (value,)
    ).fetchone()
    if collision and collision["unit_id"] != unit["id"]:
        raise ValueError("Identifier is already assigned to another unit")
    conn.execute(
        """INSERT OR IGNORE INTO unit_identifier_aliases
           (unit_id, scheme, value, source, created_by, created_at)
           VALUES (?,?,?,?,?,?)""",
        (unit["id"], scheme, value, source, actor, _now()),
    )
    conn.commit()
    return resolve_identifier(conn, value)


def resolve_identifier(conn: sqlite3.Connection, value: str) -> dict:
    scanned = value.strip()
    normalized_key = scanned.upper()
    if normalized_key.startswith("HIVE:U:"):
        normalized_key = normalized_key[7:]
    row = conn.execute(
        """SELECT uia.scheme, tu.id unit_id FROM unit_identifier_aliases uia
           JOIN trace_units tu ON tu.id=uia.unit_id
           WHERE uia.value=? AND uia.active=1""", (scanned,),
    ).fetchone()
    if not row and normalized_key.startswith("HU-"):
        row = conn.execute(
            "SELECT 'hive_unit' scheme, id unit_id FROM trace_units WHERE unit_key=?",
            (normalized_key,),
        ).fetchone()
    if not row:
        return {"status": "unknown", "scanned_value": scanned, "unit": None,
                "identifier_scheme": None}
    return {"status": "resolved", "scanned_value": scanned,
            "identifier_scheme": row["scheme"],
            "unit": _unit_row(conn, unit_id=row["unit_id"])}


def record_barcode_resolution(conn: sqlite3.Connection, barcode_event_id: int,
                              resolution: dict, details: str | None = None) -> dict:
    unit = resolution.get("unit")
    conn.execute(
        """INSERT OR REPLACE INTO barcode_event_resolutions
           (barcode_event_id, unit_id, identifier_scheme, status, details, resolved_at)
           VALUES (?,?,?,?,?,?)""",
        (barcode_event_id, unit["id"] if unit else None,
         resolution.get("identifier_scheme"), resolution["status"], details, _now()),
    )
    return get_barcode_resolution(conn, barcode_event_id)


def get_barcode_resolution(conn: sqlite3.Connection, barcode_event_id: int) -> dict | None:
    row = conn.execute(
        """SELECT ber.*, tu.unit_key, tu.qr_payload, tu.part_id, tu.production_order_id
           FROM barcode_event_resolutions ber
           LEFT JOIN trace_units tu ON tu.id=ber.unit_id
           WHERE ber.barcode_event_id=?""", (barcode_event_id,),
    ).fetchone()
    return dict(row) if row else None


def mark_barcode_resolution(conn: sqlite3.Connection, barcode_event_id: int,
                            status: str, details: str | None = None) -> None:
    conn.execute(
        """UPDATE barcode_event_resolutions SET status=?, details=COALESCE(?,details),
                  resolved_at=? WHERE barcode_event_id=?""",
        (status, details, _now(), barcode_event_id),
    )


def route_scan_is_duplicate(conn: sqlite3.Connection, unit_id: int,
                            route_step_id: int, event_type: str) -> bool:
    row = conn.execute(
        "SELECT state FROM unit_route_progress WHERE unit_id=? AND route_step_id=?",
        (unit_id, route_step_id),
    ).fetchone()
    if not row:
        return False
    if event_type == "operation_start":
        return row["state"] in ("started", "completed")
    return row["state"] == "completed"


def record_route_scan(conn: sqlite3.Connection, unit_id: int, route_step_id: int,
                      barcode_event_id: int, event_type: str, ts: str,
                      machine_id: int) -> dict:
    completion = event_type == "operation_complete"
    now = _now()
    conn.execute(
        """INSERT INTO unit_route_progress
           (unit_id, route_step_id, state, started_barcode_id, completed_barcode_id,
            started_at, completed_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(unit_id,route_step_id) DO UPDATE SET
             state=excluded.state,
             started_barcode_id=COALESCE(unit_route_progress.started_barcode_id,
                                         excluded.started_barcode_id),
             completed_barcode_id=COALESCE(excluded.completed_barcode_id,
                                           unit_route_progress.completed_barcode_id),
             started_at=COALESCE(unit_route_progress.started_at,excluded.started_at),
             completed_at=COALESCE(excluded.completed_at,unit_route_progress.completed_at),
             updated_at=excluded.updated_at""",
        (unit_id, route_step_id, "completed" if completion else "started",
         barcode_event_id, barcode_event_id if completion else None,
         ts, ts if completion else None, now),
    )
    next_step = conn.execute(
        """SELECT 1 FROM part_route_steps current
           JOIN part_route_steps next ON next.part_id=current.part_id
             AND next.required=1 AND next.step_index>current.step_index
           WHERE current.id=? LIMIT 1""", (route_step_id,),
    ).fetchone()
    unit_status = "completed" if completion and not next_step else "in_process"
    conn.execute(
        """UPDATE trace_units SET status=?, current_machine_id=?, version=version+1,
                  updated_at=? WHERE id=?""", (unit_status, machine_id, now, unit_id),
    )
    mark_barcode_resolution(conn, barcode_event_id, "applied")
    return _unit_row(conn, unit_id=unit_id)


def record_disposition_scan(conn: sqlite3.Connection, barcode_event_id: int,
                            event_type: str) -> dict | None:
    resolution = get_barcode_resolution(conn, barcode_event_id)
    if not resolution or not resolution["unit_id"]:
        return None
    status = {"qc_pass": "completed", "qc_fail": "non_conforming",
              "packed": "packed", "dispatched": "dispatched"}.get(event_type)
    if not status:
        return None
    conn.execute(
        """UPDATE trace_units SET status=?, version=version+1, updated_at=?
           WHERE id=?""", (status, _now(), resolution["unit_id"]),
    )
    mark_barcode_resolution(conn, barcode_event_id, "applied")
    return _unit_row(conn, unit_id=resolution["unit_id"])


def create_print_job(conn: sqlite3.Connection, order_id: int,
                     requested_by: str, only_unprinted: bool = True,
                     part_ids: list[int] | None = None,
                     template_key: str = LABEL_TEMPLATE,
                     printer_key: str | None = None,
                     notes: str | None = None) -> dict:
    if template_key != LABEL_TEMPLATE:
        raise ValueError(f"Unknown label template '{template_key}'")
    materialize_order(conn, order_id, requested_by, commit=False)
    clauses = ["tu.production_order_id=?", "tu.status!='void'"]
    params: list = [order_id]
    if part_ids:
        placeholders = ",".join("?" for _ in part_ids)
        clauses.append(f"tu.part_id IN ({placeholders})")
        params.extend(part_ids)
    if only_unprinted:
        clauses.append(
            """NOT EXISTS (
                 SELECT 1 FROM label_print_items existing
                 JOIN label_print_jobs existing_job ON existing_job.id=existing.print_job_id
                 WHERE existing.unit_id=tu.id AND existing_job.status IN ('ready','printed')
               )"""
        )
    units = conn.execute(
        f"""SELECT tu.id FROM trace_units tu WHERE {' AND '.join(clauses)}
            ORDER BY tu.part_id, tu.ordinal""", params,
    ).fetchall()
    if not units:
        raise ValueError("No units are waiting for a label job")
    now = _now()
    cursor = conn.execute(
        """INSERT INTO label_print_jobs
           (production_order_id, template_key, printer_key, unit_count,
            requested_by, notes, created_at) VALUES (?,?,?,?,?,?,?)""",
        (order_id, template_key, printer_key, len(units), requested_by, notes, now),
    )
    conn.executemany(
        """INSERT INTO label_print_items (print_job_id, unit_id, position)
           VALUES (?,?,?)""",
        [(cursor.lastrowid, row["id"], position)
         for position, row in enumerate(units, start=1)],
    )
    conn.commit()
    return get_print_job(conn, cursor.lastrowid)


def list_print_jobs(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    return [dict(row) for row in conn.execute(
        """SELECT lpj.*, j.job_name
           FROM label_print_jobs lpj
           JOIN production_orders po ON po.id=lpj.production_order_id
           JOIN jobs j ON j.id=po.job_id
           ORDER BY lpj.created_at DESC, lpj.id DESC LIMIT ?""", (limit,),
    ).fetchall()]


def get_print_job(conn: sqlite3.Connection, print_job_id: int) -> dict:
    job = conn.execute(
        """SELECT lpj.*, j.job_name
           FROM label_print_jobs lpj
           JOIN production_orders po ON po.id=lpj.production_order_id
           JOIN jobs j ON j.id=po.job_id WHERE lpj.id=?""", (print_job_id,),
    ).fetchone()
    if not job:
        raise KeyError(f"Label print job {print_job_id} not found")
    result = dict(job)
    unit_ids = conn.execute(
        """SELECT unit_id FROM label_print_items WHERE print_job_id=?
           ORDER BY position""", (print_job_id,),
    ).fetchall()
    result["units"] = [_unit_row(conn, unit_id=row["unit_id"]) for row in unit_ids]
    return result


def mark_printed(conn: sqlite3.Connection, print_job_id: int,
                 actor: str, notes: str | None = None) -> dict:
    job = get_print_job(conn, print_job_id)
    if job["status"] == "cancelled":
        raise ValueError("Cancelled label jobs cannot be marked printed")
    if job["status"] == "printed":
        return job
    now = _now()
    conn.execute(
        """UPDATE label_print_jobs SET status='printed', printed_at=?, printed_by=?,
                  notes=COALESCE(?,notes) WHERE id=?""",
        (now, actor, notes, print_job_id),
    )
    conn.execute(
        """UPDATE label_print_items SET printed_count=printed_count+1,
                  last_printed_at=? WHERE print_job_id=?""", (now, print_job_id),
    )
    conn.commit()
    return get_print_job(conn, print_job_id)


def _display(value: object, limit: int = 42) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else f"{text[:limit - 3]}..."


def _dimensions(unit: dict) -> str:
    values = [unit.get("length_mm"), unit.get("width_mm"), unit.get("thickness_mm")]
    if not all(value is not None for value in values):
        return "Dimensions pending"
    return " x ".join(f"{float(value):g}" for value in values) + " mm"


def _qr_data_uri(payload: str) -> str:
    qr = segno.make_qr(payload, error="m")
    stream = io.BytesIO()
    qr.save(stream, kind="svg", scale=5, border=4, xmldecl=False)
    return "data:image/svg+xml;base64," + base64.b64encode(stream.getvalue()).decode("ascii")


def unit_label_svg(conn: sqlite3.Connection, unit_key: str) -> str:
    unit = _unit_row(conn, unit_key=unit_key.upper())
    qr_uri = _qr_data_uri(unit["qr_payload"])
    esc = lambda value, limit=42: html.escape(_display(value, limit))
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="50mm" viewBox="0 0 1000 500" role="img" aria-label="HIVE unit {esc(unit['unit_key'])}">
  <rect width="1000" height="500" fill="white"/>
  <rect x="12" y="12" width="976" height="476" fill="none" stroke="#111827" stroke-width="4"/>
  <text x="42" y="72" font-family="Arial,sans-serif" font-size="38" font-weight="700" fill="#111827">HIVE OS - HAEEV</text>
  <text x="42" y="130" font-family="Arial,sans-serif" font-size="31" font-weight="700" fill="#111827">{esc(unit['job_name'], 30)}</text>
  <text x="42" y="178" font-family="Arial,sans-serif" font-size="27" fill="#111827">{esc(unit['part_name'], 34)}</text>
  <text x="42" y="222" font-family="Arial,sans-serif" font-size="23" fill="#374151">{esc(unit.get('assembly_name') or 'Unassigned assembly', 40)}</text>
  <text x="42" y="270" font-family="Arial,sans-serif" font-size="23" fill="#111827">{esc(_dimensions(unit), 40)}</text>
  <text x="42" y="312" font-family="Arial,sans-serif" font-size="21" fill="#374151">{esc(unit.get('material') or 'Material pending', 45)}</text>
  <text x="42" y="378" font-family="Arial,sans-serif" font-size="28" font-weight="700" fill="#111827">UNIT {unit['ordinal']} / {unit['part_qty']}</text>
  <text x="42" y="432" font-family="Arial,sans-serif" font-size="23" font-weight="700" fill="#111827">{esc(unit['unit_key'])}</text>
  <image x="700" y="38" width="255" height="255" href="{qr_uri}"/>
  <text x="713" y="330" font-family="Arial,sans-serif" font-size="18" font-weight="700" fill="#111827">SCAN AT EACH STATION</text>
  <text x="713" y="365" font-family="Arial,sans-serif" font-size="17" fill="#374151">CV PART {esc(unit.get('part_cv_id') or unit['part_id'], 16)}</text>
</svg>"""


def print_job_html(conn: sqlite3.Connection, print_job_id: int) -> str:
    job = get_print_job(conn, print_job_id)
    pages = []
    for unit in job["units"]:
        svg = unit_label_svg(conn, unit["unit_key"]).encode("utf-8")
        uri = "data:image/svg+xml;base64," + base64.b64encode(svg).decode("ascii")
        pages.append(f'<section class="label"><img src="{uri}" alt="{html.escape(unit["unit_key"])}"></section>')
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>HIVE labels - {html.escape(job['job_name'])}</title>
<style>
@page {{ size: 100mm 50mm; margin: 0; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; font-family: Arial, sans-serif; background: #e5e7eb; }}
.toolbar {{ position: sticky; top: 0; z-index: 2; display: flex; gap: 12px; align-items: center; padding: 10px 14px; background: #111827; color: white; }}
.toolbar button {{ border: 1px solid #4b5563; background: #1d4ed8; color: white; padding: 8px 12px; border-radius: 5px; font-weight: 700; cursor: pointer; }}
.label {{ width: 100mm; height: 50mm; margin: 10px auto; background: white; break-after: page; page-break-after: always; }}
.label img {{ display: block; width: 100%; height: 100%; }}
@media print {{ body {{ background: white; }} .toolbar {{ display: none; }} .label {{ margin: 0; }} }}
</style></head><body><div class="toolbar"><button onclick="window.print()">Print</button><span>{job['unit_count']} labels - {html.escape(job['job_name'])}</span></div>{''.join(pages)}</body></html>"""


def _zpl_text(value: object, limit: int = 40) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    safe = " ".join(ascii_text.replace("^", " ").replace("~", " ").split())
    return safe[:limit]


def _unit_zpl(unit: dict) -> str:
    return "\n".join([
        "^XA", "^CI28", "^PW812", "^LL406", "^LH0,0",
        "^FO24,20^A0N,30,30^FDHIVE OS - HAEEV^FS",
        f"^FO24,62^A0N,28,28^FD{_zpl_text(unit['job_name'], 30)}^FS",
        f"^FO24,102^A0N,25,25^FD{_zpl_text(unit['part_name'], 34)}^FS",
        f"^FO24,140^A0N,21,21^FD{_zpl_text(_dimensions(unit), 38)}^FS",
        f"^FO24,174^A0N,20,20^FD{_zpl_text(unit.get('material') or 'Material pending', 42)}^FS",
        f"^FO24,230^A0N,27,27^FDUNIT {unit['ordinal']} / {unit['part_qty']}^FS",
        f"^FO24,274^A0N,23,23^FD{unit['unit_key']}^FS",
        "^FO560,24^BQN,2,5",
        f"^FDQA,{unit['qr_payload']}^FS",
        "^FO555,286^A0N,18,18^FDSCAN AT EACH STATION^FS",
        "^XZ",
    ])


def print_job_zpl(conn: sqlite3.Connection, print_job_id: int) -> str:
    job = get_print_job(conn, print_job_id)
    return "\n".join(_unit_zpl(unit) for unit in job["units"]) + "\n"


def snapshot(conn: sqlite3.Connection) -> dict:
    sync_result = sync_controlled_orders(conn)
    counts = dict(conn.execute(
        """SELECT COUNT(*) unitized,
                  SUM(CASE WHEN status='planned' THEN 1 ELSE 0 END) planned,
                  SUM(CASE WHEN status='released' THEN 1 ELSE 0 END) released,
                  SUM(CASE WHEN status='in_process' THEN 1 ELSE 0 END) in_process,
                  SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) completed,
                  SUM(CASE WHEN status='packed' THEN 1 ELSE 0 END) packed,
                  SUM(CASE WHEN status='dispatched' THEN 1 ELSE 0 END) dispatched
           FROM trace_units WHERE status!='void'"""
    ).fetchone())
    counts = {key: value or 0 for key, value in counts.items()}
    expected = conn.execute("SELECT COALESCE(SUM(qty),0) count FROM parts").fetchone()["count"]
    printed = conn.execute(
        "SELECT COUNT(DISTINCT unit_id) count FROM label_print_items WHERE printed_count>0"
    ).fetchone()["count"]
    pending = conn.execute(
        """SELECT COUNT(DISTINCT lpi.unit_id) count FROM label_print_items lpi
           JOIN label_print_jobs lpj ON lpj.id=lpi.print_job_id WHERE lpj.status='ready'"""
    ).fetchone()["count"]
    resolution_counts = {row["status"]: row["count"] for row in conn.execute(
        "SELECT status, COUNT(*) count FROM barcode_event_resolutions GROUP BY status"
    ).fetchall()}
    orders = [dict(row) for row in conn.execute(
        """SELECT po.id order_id, po.status, j.job_name, COALESCE(SUM(p.qty),0) expected_units,
                  (SELECT COUNT(*) FROM trace_units tu
                   WHERE tu.production_order_id=po.id AND tu.status!='void') unitized_units,
                  (SELECT COUNT(DISTINCT lpi.unit_id) FROM label_print_items lpi
                   JOIN trace_units tu ON tu.id=lpi.unit_id
                   WHERE tu.production_order_id=po.id AND lpi.printed_count>0) printed_units
           FROM production_orders po JOIN jobs j ON j.id=po.job_id
           LEFT JOIN parts p ON p.job_id=po.job_id
           GROUP BY po.id ORDER BY po.priority DESC, j.job_name"""
    ).fetchall()]
    return {
        "status": "ready" if counts.get("unitized", 0) else "awaiting_label_job",
        "summary": {**counts, "expected_units": expected, "printed_units": printed,
                    "pending_print_units": pending,
                    "unknown_scans": resolution_counts.get("unknown", 0),
                    "duplicate_scans": resolution_counts.get("duplicate", 0)},
        "orders": orders,
        "print_jobs": list_print_jobs(conn, 20),
        "sync": sync_result,
        "identity_policy": {
            "current_scheme": "hive_unit",
            "qr_payload": "HIVE:U:<unit-key>",
            "gs1": "Attach licensed GS1 aliases after HAEEV obtains a GS1 Company Prefix",
        },
    }
