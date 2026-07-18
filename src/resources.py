"""Factory resource capability, availability, inventory, and reservation model."""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import inventory
import tooling
import changeovers


DEFAULT_SHEET_LENGTH_MM = 2440.0
DEFAULT_SHEET_WIDTH_MM = 1220.0
DEFAULT_YIELD_FACTOR = 0.82
HORIZON_DAYS = 90

ROLE_DEFAULTS = {
    "cutting_operator": ("Cutting operator", 1),
    "cnc_operator": ("CNC operator", 1),
    "edge_operator": ("Edge-bander operator", 1),
    "press_operator": ("Press and glue operator", 1),
    "finishing_operator": ("Sanding and finishing operator", 2),
    "packing_operator": ("Packing operator", 1),
}

TOOL_DEFAULTS = {
    "cutting_tooling": "Cutting blades and saw tooling",
    "cnc_tooling": "CNC drill and router toolset",
    "edge_tooling": "Edge-banding tooling and glue system",
    "press_tooling": "Press, glue, and fixture tooling",
    "sanding_tooling": "Sanding belts and calibration tooling",
    "paint_tooling": "Paint-line guns and finishing tooling",
    "packing_tooling": "Boxing and packing tooling",
}

MACHINE_DEFAULTS = {
    "gabbiani_pt80": ("cutting_operator", "cutting_tooling"),
    "nova_si400": ("cutting_operator", "cutting_tooling"),
    "morbidelli_cx100": ("cnc_operator", "cnc_tooling"),
    "morbidelli_n100": ("cnc_operator", "cnc_tooling"),
    "stefani_kd": ("edge_operator", "edge_tooling"),
    "sergiani_gs120": ("press_operator", "press_tooling"),
    "varie_osama": ("press_operator", "press_tooling"),
    "dmc60_rcs135": ("finishing_operator", "sanding_tooling"),
    "dmc90_xrt135": ("finishing_operator", "sanding_tooling"),
    "superfici": ("finishing_operator", "paint_tooling"),
    "action_e": ("packing_operator", "packing_tooling"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _material_key(value: str | None) -> str:
    if not value or not value.strip():
        return "UNSPECIFIED"
    return re.sub(r"\s+", " ", value.strip()).upper()


def _audit(conn: sqlite3.Connection, resource_type: str, resource_key: str,
           event_type: str, actor: str, payload: dict) -> None:
    conn.execute(
        """INSERT INTO resource_change_events
           (resource_type, resource_key, event_type, actor, payload_json, ts)
           VALUES (?,?,?,?,?,?)""",
        (resource_type, resource_key, event_type, actor,
         json.dumps(payload, sort_keys=True), _now()),
    )


def sync_defaults(conn: sqlite3.Connection, commit: bool = True) -> dict:
    now = _now()
    for role_key, (name, headcount) in ROLE_DEFAULTS.items():
        conn.execute(
            """INSERT OR IGNORE INTO labor_roles
               (role_key, name, headcount, source, verified, updated_at)
               VALUES (?,?,?,'engineering_assumption',0,?)""",
            (role_key, name, headcount, now),
        )
    for pool_key, name in TOOL_DEFAULTS.items():
        conn.execute(
            """INSERT OR IGNORE INTO tool_pools
               (pool_key, name, total_qty, available_qty, source, verified, updated_at)
               VALUES (?,?,1,1,'engineering_assumption',0,?)""",
            (pool_key, name, now),
        )
    role_ids = {row["role_key"]: row["id"] for row in conn.execute(
        "SELECT id, role_key FROM labor_roles"
    ).fetchall()}
    pool_ids = {row["pool_key"]: row["id"] for row in conn.execute(
        "SELECT id, pool_key FROM tool_pools"
    ).fetchall()}
    machines = {row["machine_key"]: row["id"] for row in conn.execute(
        "SELECT id, machine_key FROM machines"
    ).fetchall()}
    for machine_key, (role_key, pool_key) in MACHINE_DEFAULTS.items():
        machine_id = machines.get(machine_key)
        if not machine_id:
            continue
        conn.execute(
            """INSERT OR IGNORE INTO machine_resource_profiles
               (machine_id, labor_role_id, labor_qty, tool_pool_id, tool_qty,
                machine_capacity, source, verified, updated_at)
               VALUES (?,?,1,?,1,1,'engineering_assumption',0,?)""",
            (machine_id, role_ids[role_key], pool_ids[pool_key], now),
        )
        conn.execute(
            """INSERT OR IGNORE INTO wip_buffers
               (machine_id, capacity_qty, current_qty, source, verified, updated_at)
               VALUES (?,50,0,'engineering_assumption',0,?)""",
            (machine_id, now),
        )
    if not conn.execute(
        "SELECT 1 FROM work_calendar_windows WHERE resource_type='factory' AND resource_key='factory'"
    ).fetchone():
        for weekday in range(6):
            conn.execute(
                """INSERT INTO work_calendar_windows
                   (resource_type, resource_key, weekday, start_time, end_time,
                    capacity, timezone, source, verified, active, updated_at)
                   VALUES ('factory','factory',?,'09:00','18:00',1,'Asia/Kolkata',
                           'engineering_assumption',0,1,?)""",
                (weekday, now),
            )
    material_count = sync_material_requirements(conn, commit=False)
    inventory_status = inventory.sync_requirements(conn, commit=False)
    changeover_defaults = changeovers.sync_defaults(conn, commit=False)
    if commit:
        conn.commit()
    return {
        "labor_roles": len(ROLE_DEFAULTS),
        "tool_pools": len(TOOL_DEFAULTS),
        "machine_profiles": len(MACHINE_DEFAULTS),
        "material_requirements": material_count,
        "component_requirements": inventory_status["requirements"],
        "changeover_defaults": changeover_defaults,
    }


def sync_material_requirements(conn: sqlite3.Connection, commit: bool = True) -> int:
    now = _now()
    orders = conn.execute(
        "SELECT id, job_id FROM production_orders"
    ).fetchall()
    written = 0
    for order in orders:
        grouped: dict[str, dict] = {}
        rows = conn.execute(
            """SELECT material, length_mm, width_mm, qty
               FROM parts WHERE job_id=?""", (order["job_id"],)
        ).fetchall()
        for row in rows:
            key = _material_key(row["material"])
            item = grouped.setdefault(key, {
                "name": row["material"] or "Unspecified material",
                "area": 0.0, "unknown": 0,
            })
            quantity = max(int(row["qty"] or 1), 1)
            if row["length_mm"] and row["width_mm"]:
                item["area"] += float(row["length_mm"]) * float(row["width_mm"]) * quantity / 1_000_000
            else:
                item["unknown"] += quantity
        keep_ids: list[int] = []
        for key, item in grouped.items():
            conn.execute(
                """INSERT OR IGNORE INTO material_definitions
                   (material_key, name, sheet_length_mm, sheet_width_mm, yield_factor,
                    source, verified, created_at, updated_at)
                   VALUES (?,?,?,?,?,'engineering_assumption',0,?,?)""",
                (key, item["name"], DEFAULT_SHEET_LENGTH_MM, DEFAULT_SHEET_WIDTH_MM,
                 DEFAULT_YIELD_FACTOR, now, now),
            )
            definition = conn.execute(
                """SELECT id, sheet_length_mm, sheet_width_mm, yield_factor
                   FROM material_definitions WHERE material_key=?""", (key,)
            ).fetchone()
            usable_area = (float(definition["sheet_length_mm"]) *
                           float(definition["sheet_width_mm"]) / 1_000_000 *
                           float(definition["yield_factor"]))
            required_sheets = None if item["unknown"] else math.ceil(item["area"] / usable_area)
            conn.execute(
                """INSERT INTO material_requirements
                   (production_order_id, material_id, required_area_m2, required_sheets,
                    unknown_part_count, source, confidence, updated_at)
                   VALUES (?,?,?,?,?,'cv_dimensions','estimated',?)
                   ON CONFLICT(production_order_id, material_id) DO UPDATE SET
                     required_area_m2=excluded.required_area_m2,
                     required_sheets=excluded.required_sheets,
                     unknown_part_count=excluded.unknown_part_count,
                     updated_at=excluded.updated_at
                   WHERE material_requirements.required_area_m2 IS NOT excluded.required_area_m2
                      OR material_requirements.required_sheets IS NOT excluded.required_sheets
                      OR material_requirements.unknown_part_count IS NOT excluded.unknown_part_count""",
                (order["id"], definition["id"], round(item["area"], 4),
                 required_sheets, item["unknown"], now),
            )
            keep_ids.append(definition["id"])
            written += 1
        if keep_ids:
            placeholders = ",".join("?" for _ in keep_ids)
            conn.execute(
                f"DELETE FROM material_requirements WHERE production_order_id=? AND material_id NOT IN ({placeholders})",
                (order["id"], *keep_ids),
            )
        else:
            conn.execute(
                "DELETE FROM material_requirements WHERE production_order_id=?", (order["id"],)
            )
    if commit:
        conn.commit()
    return written


def _orders(conn: sqlite3.Connection, job_names: list[str] | None,
            display: bool = False) -> list[dict]:
    if job_names:
        placeholders = ",".join("?" for _ in job_names)
        where = f"j.job_name IN ({placeholders})"
        params: tuple = tuple(job_names)
    elif display:
        where = "po.status NOT IN ('completed','cancelled')"
        params = ()
    else:
        where = "po.status IN ('ready','released','in_progress')"
        params = ()
    return [dict(row) for row in conn.execute(
        f"""SELECT po.id, po.job_id, po.status, j.job_name
            FROM production_orders po JOIN jobs j ON j.id=po.job_id
            WHERE {where} ORDER BY po.id""", params
    ).fetchall()]


def _material_rows(conn: sqlite3.Connection, display_order_ids: list[int],
                   scope_order_ids: list[int]) -> list[dict]:
    if not display_order_ids:
        return []
    display_marks = ",".join("?" for _ in display_order_ids)
    scope_marks = ",".join("?" for _ in scope_order_ids) if scope_order_ids else "NULL"
    params = (*display_order_ids, *scope_order_ids)
    rows = conn.execute(
        f"""SELECT md.id, md.material_key, md.name, md.sheet_length_mm,
                   md.sheet_width_mm, md.yield_factor, md.source, md.verified, md.updated_at,
                   SUM(mr.required_area_m2) open_required_area_m2,
                   SUM(COALESCE(mr.required_sheets, 0)) open_required_sheets,
                   SUM(mr.unknown_part_count) open_unknown_parts,
                   SUM(CASE WHEN mr.production_order_id IN ({scope_marks})
                            THEN mr.required_area_m2 ELSE 0 END) required_area_m2,
                   SUM(CASE WHEN mr.production_order_id IN ({scope_marks})
                            THEN COALESCE(mr.required_sheets, 0) ELSE 0 END) required_sheets,
                   SUM(CASE WHEN mr.production_order_id IN ({scope_marks})
                            THEN mr.unknown_part_count ELSE 0 END) unknown_part_count
            FROM material_definitions md
            JOIN material_requirements mr ON mr.material_id=md.id
            WHERE mr.production_order_id IN ({display_marks})
            GROUP BY md.id ORDER BY md.name""",
        (*scope_order_ids, *scope_order_ids, *scope_order_ids, *display_order_ids),
    ).fetchall()
    scope_remnants = inventory.plan_remnants(conn, scope_order_ids)
    display_remnants = inventory.plan_remnants(conn, display_order_ids)
    result = []
    for row in rows:
        item = dict(row)
        gross_required = float(item["required_sheets"] or 0)
        gross_open_required = float(item["open_required_sheets"] or 0)
        usable_sheet_area = (float(item["sheet_length_mm"]) * float(item["sheet_width_mm"]) /
                             1_000_000 * float(item["yield_factor"]))

        def net_sheets(order_ids: list[int], credits: dict) -> tuple[int, float]:
            if not order_ids:
                return 0, 0.0
            marks = ",".join("?" for _ in order_ids)
            requirements = conn.execute(
                f"""SELECT production_order_id,required_area_m2,unknown_part_count
                    FROM material_requirements WHERE material_id=?
                      AND production_order_id IN ({marks})""",
                (item["id"], *order_ids),
            ).fetchall()
            total = 0
            credited = 0.0
            for requirement in requirements:
                credit = min(float(requirement["required_area_m2"]),
                             float(credits.get((requirement["production_order_id"], item["id"]), 0)))
                credited += credit
                if not requirement["unknown_part_count"]:
                    total += math.ceil(max(0.0, float(requirement["required_area_m2"]) - credit) /
                                       usable_sheet_area - 1e-12)
            return total, credited

        net_required, remnant_credit = net_sheets(scope_order_ids, scope_remnants["credits"])
        net_open_required, open_remnant_credit = net_sheets(display_order_ids, display_remnants["credits"])
        item["gross_required_sheets"] = gross_required
        item["gross_open_required_sheets"] = gross_open_required
        item["required_sheets"] = net_required
        item["open_required_sheets"] = net_open_required
        item["remnant_credit_area_m2"] = round(remnant_credit, 4)
        item["open_remnant_credit_area_m2"] = round(open_remnant_credit, 4)
        lots = [dict(lot) for lot in conn.execute(
            """SELECT id, lot_code, location, status, on_hand_sheets,
                      reserved_sheets, source, verified, updated_at
               FROM material_lots WHERE material_id=? ORDER BY status, lot_code""",
            (item["id"],),
        ).fetchall()]
        on_hand = sum(float(lot["on_hand_sheets"]) for lot in lots if lot["status"] == "available")
        reserved = sum(float(lot["reserved_sheets"]) for lot in lots if lot["status"] == "available")
        held_by_scope = 0.0
        if scope_order_ids:
            marks = ",".join("?" for _ in scope_order_ids)
            held_by_scope = conn.execute(
                f"""SELECT COALESCE(SUM(mres.quantity_sheets),0) quantity
                    FROM material_reservations mres JOIN material_lots ml ON ml.id=mres.material_lot_id
                    WHERE ml.material_id=? AND mres.status='committed'
                      AND mres.production_order_id IN ({marks})""",
                (item["id"], *scope_order_ids),
            ).fetchone()["quantity"]
        effective_available = on_hand - reserved + float(held_by_scope)
        required = float(item["required_sheets"] or 0)
        open_required = float(item["open_required_sheets"] or 0)
        unknown = int(item["unknown_part_count"] or 0)
        stock_verified = bool(lots) and all(bool(lot["verified"]) for lot in lots if lot["status"] == "available")
        item.update({
            "lots": lots,
            "on_hand_sheets": round(on_hand, 3),
            "reserved_sheets": round(reserved, 3),
            "effective_available_sheets": round(effective_available, 3),
            "held_for_scope_sheets": round(float(held_by_scope), 3),
            "shortage_sheets": round(max(0.0, required - effective_available), 3),
            "open_shortage_sheets": round(max(0.0, open_required - (on_hand - reserved)), 3),
            "stock_verified": stock_verified,
            "feasible": bool(item["verified"]) and stock_verified and unknown == 0 and effective_available >= required,
        })
        result.append(item)
    return result


def snapshot(conn: sqlite3.Connection, job_names: list[str] | None = None,
             sync: bool = True) -> dict:
    if sync:
        sync_defaults(conn)
    scope_orders = _orders(conn, job_names)
    display_orders = _orders(conn, job_names, display=True)
    scope_ids = [order["id"] for order in scope_orders]
    display_ids = [order["id"] for order in display_orders]
    materials = _material_rows(conn, display_ids, scope_ids)
    warehouse = inventory.snapshot(conn, job_names, sync=False)
    changeover_status = changeovers.snapshot(conn, job_names)

    labor_roles = [dict(row) for row in conn.execute(
        "SELECT role_key, name, headcount, source, verified, updated_at FROM labor_roles ORDER BY name"
    ).fetchall()]
    tool_pools = [dict(row) for row in conn.execute(
        "SELECT pool_key, name, total_qty, available_qty, source, verified, updated_at FROM tool_pools ORDER BY name"
    ).fetchall()]
    tooling_status = tooling.snapshot(conn)
    lifecycle_pools = {item["pool_key"]: item for item in tooling_status["pools"]}
    for pool in tool_pools:
        pool.update(lifecycle_pools[pool["pool_key"]])
    profiles = [dict(row) for row in conn.execute(
        """SELECT m.machine_key, m.name machine_name, lr.role_key, lr.name labor_role,
                  mrp.labor_qty, tp.pool_key, tp.name tool_pool, mrp.tool_qty,
                  mrp.machine_capacity, mrp.source, mrp.verified, mrp.updated_at
           FROM machine_resource_profiles mrp JOIN machines m ON m.id=mrp.machine_id
           LEFT JOIN labor_roles lr ON lr.id=mrp.labor_role_id
           LEFT JOIN tool_pools tp ON tp.id=mrp.tool_pool_id ORDER BY m.name"""
    ).fetchall()]
    buffers = [dict(row) for row in conn.execute(
        """SELECT m.machine_key, m.name machine_name, wb.capacity_qty, wb.current_qty,
                  wb.source, wb.verified, wb.updated_at
           FROM wip_buffers wb JOIN machines m ON m.id=wb.machine_id ORDER BY m.name"""
    ).fetchall()]
    calendar = [dict(row) for row in conn.execute(
        """SELECT id, resource_type, resource_key, weekday, start_time, end_time,
                  capacity, timezone, source, verified, active, updated_at
           FROM work_calendar_windows WHERE active=1
           ORDER BY resource_type, resource_key, weekday, start_time"""
    ).fetchall()]
    unavailability = [dict(row) for row in conn.execute(
        """SELECT id, resource_type, resource_key, starts_at, ends_at, reason,
                  source, work_order_id, created_by, created_at
           FROM resource_unavailability WHERE ends_at>=? ORDER BY starts_at""", (_now(),)
    ).fetchall()]

    used_machine_keys: set[str] = set()
    downstream_keys: set[str] = set()
    if scope_ids:
        marks = ",".join("?" for _ in scope_ids)
        route_rows = conn.execute(
            f"""SELECT DISTINCT m.machine_key, prs.step_index
                FROM material_requirements mr
                JOIN production_orders po ON po.id=mr.production_order_id
                JOIN parts p ON p.job_id=po.job_id
                JOIN part_route_steps prs ON prs.part_id=p.id AND prs.required=1
                JOIN machines m ON m.id=prs.machine_id
                WHERE po.id IN ({marks})""", tuple(scope_ids)
        ).fetchall()
        used_machine_keys = {row["machine_key"] for row in route_rows}
        downstream_keys = {row["machine_key"] for row in route_rows if row["step_index"] > 1}

    profile_by_machine = {item["machine_key"]: item for item in profiles}
    role_by_key = {item["role_key"]: item for item in labor_roles}
    pool_by_key = {item["pool_key"]: item for item in tool_pools}
    buffer_by_machine = {item["machine_key"]: item for item in buffers}
    used_profiles = [profile_by_machine.get(key) for key in used_machine_keys]
    profile_ok = bool(used_machine_keys) and all(item and item["verified"] for item in used_profiles)
    used_roles = {item["role_key"] for item in used_profiles if item and item["role_key"]}
    labor_ok = bool(used_roles) and all(
        role_by_key[key]["verified"] and role_by_key[key]["headcount"] > 0 for key in used_roles
    )
    used_pools = {item["pool_key"] for item in used_profiles if item and item["pool_key"]}
    tooling_ok = bool(used_pools) and all(
        pool_by_key[key]["verified"] and pool_by_key[key]["effective_available_qty"] > 0 for key in used_pools
    )
    factory_calendar = [item for item in calendar
                        if item["resource_type"] == "factory" and item["resource_key"] == "factory"]
    calendar_ok = bool(factory_calendar) and all(item["verified"] for item in factory_calendar)
    wip_ok = bool(scope_ids) and all(
        key in buffer_by_machine and buffer_by_machine[key]["verified"] and
        buffer_by_machine[key]["current_qty"] < buffer_by_machine[key]["capacity_qty"]
        for key in downstream_keys
    )
    material_scope = [item for item in materials if float(item["required_sheets"] or 0) > 0 or item["unknown_part_count"]]
    materials_ok = bool(scope_ids) and bool(material_scope) and all(item["feasible"] for item in material_scope)
    component_requirement_key = "required_qty" if scope_ids else "open_required_qty"
    component_shortage_key = "shortage_qty" if scope_ids else "open_shortage_qty"
    component_feasibility_key = "feasible" if scope_ids else "open_feasible"
    component_scope = [
        item for item in warehouse["components"]
        if float(item[component_requirement_key] or 0) > 0
    ]
    components_ok = bool(scope_ids) and (
        not component_scope or all(item[component_feasibility_key] for item in component_scope)
    )
    open_downtime = 0
    if used_machine_keys:
        marks = ",".join("?" for _ in used_machine_keys)
        open_downtime = conn.execute(
            f"""SELECT COUNT(*) count FROM downtime_events de JOIN machines m ON m.id=de.machine_id
                WHERE de.status='open' AND m.machine_key IN ({marks})""", tuple(used_machine_keys)
        ).fetchone()["count"]
    availability_ok = bool(used_machine_keys) and open_downtime == 0
    changeover_ready = changeover_status["readiness"]["ready"]
    applicable = bool(scope_ids)
    checks = [
        {"key": "materials", "label": "Material stock", "passed": materials_ok,
         "detail": f"{sum(item['shortage_sheets'] for item in material_scope):g} sheet shortage across {len(material_scope)} materials"},
        {"key": "components", "label": "Edge and hardware stock", "passed": components_ok,
         "detail": f"{sum(item[component_shortage_key] for item in component_scope):g} unit shortage across {len(component_scope)} required items"},
        {"key": "profiles", "label": "Machine profiles", "passed": profile_ok,
         "detail": f"{sum(bool(item and item['verified']) for item in used_profiles)} of {len(used_machine_keys)} route machines verified"},
        {"key": "labor", "label": "Labor capacity", "passed": labor_ok,
         "detail": f"{sum(bool(role_by_key[key]['verified']) for key in used_roles)} of {len(used_roles)} roles verified"},
        {"key": "tooling", "label": "Tooling capacity", "passed": tooling_ok,
         "detail": f"{sum(bool(pool_by_key[key]['verified']) for key in used_pools)} of {len(used_pools)} pools verified; "
                   f"{sum(pool_by_key[key]['effective_available_qty'] for key in used_pools)} usable tools"},
        {"key": "calendar", "label": "Work calendar", "passed": calendar_ok,
         "detail": f"{len(factory_calendar)} weekly windows; {'verified' if calendar_ok else 'confirmation required'}"},
        {"key": "wip", "label": "WIP buffers", "passed": wip_ok,
         "detail": f"{sum(bool(buffer_by_machine.get(key, {}).get('verified')) for key in downstream_keys)} of {len(downstream_keys)} downstream buffers verified"},
        {"key": "availability", "label": "Machine availability", "passed": availability_ok,
         "detail": f"{open_downtime} route machines currently down"},
        {"key": "changeovers", "label": "Setup standards", "passed": changeover_ready,
         "detail": (
             f"{sum(item['verified'] for item in changeover_status['machines'])} verified fallbacks; "
             f"{changeover_status['summary']['active_models']} learned transitions"
         )},
    ]
    resource_ready = applicable and all(check["passed"] for check in checks)
    return {
        "status": "ready" if resource_ready else "commissioning",
        "applicable": applicable,
        "resource_ready": resource_ready,
        "scope_orders": scope_orders,
        "checks": checks,
        "materials": materials,
        "labor_roles": labor_roles,
        "tool_pools": tool_pools,
        "tooling": tooling_status,
        "machine_profiles": profiles,
        "calendar": calendar,
        "wip_buffers": buffers,
        "unavailability": unavailability,
        "warehouse": warehouse,
        "changeovers": changeover_status,
        "assumptions": {
            "default_sheet_mm": [DEFAULT_SHEET_LENGTH_MM, DEFAULT_SHEET_WIDTH_MM],
            "default_nesting_yield": DEFAULT_YIELD_FACTOR,
            "default_factory_calendar": "Monday-Saturday 09:00-18:00 Asia/Kolkata",
            "default_wip_capacity": 50,
            "changeover_defaults": "Unverified machine-specific engineering priors",
        },
    }


def set_material_stock(conn: sqlite3.Connection, material_key: str, payload: dict) -> dict:
    sync_defaults(conn)
    definition = conn.execute(
        "SELECT * FROM material_definitions WHERE material_key=?", (_material_key(material_key),)
    ).fetchone()
    if not definition:
        raise KeyError(f"Material '{material_key}' not found")
    length = float(payload.get("sheet_length_mm", definition["sheet_length_mm"]))
    width = float(payload.get("sheet_width_mm", definition["sheet_width_mm"]))
    yield_factor = float(payload.get("yield_factor", definition["yield_factor"]))
    if length <= 0 or width <= 0 or not 0 < yield_factor <= 1:
        raise ValueError("Sheet dimensions must be positive and yield_factor must be between 0 and 1")
    now = _now()
    verified = int(bool(payload.get("verified", False)))
    lot_code = payload.get("lot_code") or "MANUAL-BALANCE"
    current = conn.execute(
        "SELECT id,on_hand_sheets,reserved_sheets FROM material_lots WHERE material_id=? AND lot_code=?",
        (definition["id"], lot_code),
    ).fetchone()
    on_hand = float(payload["on_hand_sheets"])
    if current and on_hand < float(current["reserved_sheets"]):
        raise ValueError("On-hand stock cannot be lower than committed reservations")
    conn.execute(
        """UPDATE material_definitions SET sheet_length_mm=?, sheet_width_mm=?,
              yield_factor=?, source='manual', verified=?, updated_at=? WHERE id=?""",
        (length, width, yield_factor, verified, now, definition["id"]),
    )
    conn.execute(
        """INSERT INTO material_lots
           (material_id, lot_code, location, status, on_hand_sheets, reserved_sheets,
            source, verified, updated_at) VALUES (?,?,?,'available',?,0,'manual',?,?)
           ON CONFLICT(material_id, lot_code) DO UPDATE SET
             location=excluded.location, status='available', on_hand_sheets=excluded.on_hand_sheets,
             source='manual', verified=excluded.verified, updated_at=excluded.updated_at""",
        (definition["id"], lot_code, payload.get("location"), on_hand, verified, now),
    )
    lot = conn.execute(
        "SELECT id,on_hand_sheets FROM material_lots WHERE material_id=? AND lot_code=?",
        (definition["id"], lot_code),
    ).fetchone()
    inventory.record_movement(
        conn, object_type="sheet_lot", object_key=f"{definition['material_key']}:{lot_code}",
        movement_type="adjustment", quantity=on_hand - float(current["on_hand_sheets"] if current else 0),
        uom="sheet", balance_after=on_hand, actor=payload.get("actor", "operator"),
        source="manual", notes=payload.get("notes"),
    )
    sync_material_requirements(conn, commit=False)
    _audit(conn, "material", definition["material_key"], "stock_updated",
           payload.get("actor", "operator"), payload)
    conn.commit()
    return next(item for item in snapshot(conn)["materials"]
                if item["material_key"] == definition["material_key"])


def update_labor_role(conn: sqlite3.Connection, role_key: str, payload: dict) -> dict:
    row = conn.execute("SELECT * FROM labor_roles WHERE role_key=?", (role_key,)).fetchone()
    if not row:
        raise KeyError(f"Labor role '{role_key}' not found")
    conn.execute(
        """UPDATE labor_roles SET headcount=?, source='manual', verified=?, updated_at=?
           WHERE role_key=?""",
        (int(payload["headcount"]), int(bool(payload.get("verified", False))), _now(), role_key),
    )
    _audit(conn, "labor_role", role_key, "capacity_updated", payload.get("actor", "operator"), payload)
    conn.commit()
    return dict(conn.execute("SELECT * FROM labor_roles WHERE role_key=?", (role_key,)).fetchone())


def update_tool_pool(conn: sqlite3.Connection, pool_key: str, payload: dict) -> dict:
    row = conn.execute("SELECT * FROM tool_pools WHERE pool_key=?", (pool_key,)).fetchone()
    if not row:
        raise KeyError(f"Tool pool '{pool_key}' not found")
    total = int(payload["total_qty"])
    available = int(payload["available_qty"])
    if available > total:
        raise ValueError("Available tooling cannot exceed total tooling")
    conn.execute(
        """UPDATE tool_pools SET total_qty=?, available_qty=?, source='manual',
              verified=?, updated_at=? WHERE pool_key=?""",
        (total, available, int(bool(payload.get("verified", False))), _now(), pool_key),
    )
    _audit(conn, "tool_pool", pool_key, "capacity_updated", payload.get("actor", "operator"), payload)
    conn.commit()
    return dict(conn.execute("SELECT * FROM tool_pools WHERE pool_key=?", (pool_key,)).fetchone())


def update_machine_profile(conn: sqlite3.Connection, machine_key: str, payload: dict) -> dict:
    machine = conn.execute("SELECT id FROM machines WHERE machine_key=?", (machine_key,)).fetchone()
    role = conn.execute("SELECT id FROM labor_roles WHERE role_key=?", (payload["role_key"],)).fetchone()
    pool = conn.execute("SELECT id FROM tool_pools WHERE pool_key=?", (payload["pool_key"],)).fetchone()
    if not machine:
        raise KeyError(f"Machine '{machine_key}' not found")
    if not role or not pool:
        raise ValueError("Unknown labor role or tool pool")
    conn.execute(
        """UPDATE machine_resource_profiles SET labor_role_id=?, labor_qty=?, tool_pool_id=?,
              tool_qty=?, machine_capacity=?, source='manual', verified=?, updated_at=?
           WHERE machine_id=?""",
        (role["id"], int(payload["labor_qty"]), pool["id"], int(payload["tool_qty"]),
         int(payload["machine_capacity"]), int(bool(payload.get("verified", False))),
         _now(), machine["id"]),
    )
    _audit(conn, "machine_profile", machine_key, "profile_updated",
           payload.get("actor", "operator"), payload)
    conn.commit()
    return next(item for item in snapshot(conn)["machine_profiles"] if item["machine_key"] == machine_key)


def update_factory_calendar(conn: sqlite3.Connection, payload: dict) -> list[dict]:
    weekdays = sorted(set(int(day) for day in payload["weekdays"]))
    if not weekdays or any(day < 0 or day > 6 for day in weekdays):
        raise ValueError("Select at least one weekday from 0 through 6")
    start = time.fromisoformat(payload["start_time"])
    end = time.fromisoformat(payload["end_time"])
    if start == end:
        raise ValueError("Calendar start and end times must differ")
    try:
        ZoneInfo(payload.get("timezone", "Asia/Kolkata"))
    except Exception as error:
        raise ValueError("Unknown calendar timezone") from error
    now = _now()
    conn.execute("DELETE FROM work_calendar_windows WHERE resource_type='factory' AND resource_key='factory'")
    for weekday in weekdays:
        conn.execute(
            """INSERT INTO work_calendar_windows
               (resource_type, resource_key, weekday, start_time, end_time, capacity,
                timezone, source, verified, active, updated_at)
               VALUES ('factory','factory',?,?,?,?,?,'manual',?,1,?)""",
            (weekday, payload["start_time"], payload["end_time"], int(payload.get("capacity", 1)),
             payload.get("timezone", "Asia/Kolkata"), int(bool(payload.get("verified", False))), now),
        )
    _audit(conn, "calendar", "factory", "calendar_replaced", payload.get("actor", "operator"), payload)
    conn.commit()
    return snapshot(conn)["calendar"]


def update_wip_buffer(conn: sqlite3.Connection, machine_key: str, payload: dict) -> dict:
    machine = conn.execute("SELECT id FROM machines WHERE machine_key=?", (machine_key,)).fetchone()
    if not machine:
        raise KeyError(f"Machine '{machine_key}' not found")
    capacity = int(payload["capacity_qty"])
    current = int(payload["current_qty"])
    if current > capacity:
        raise ValueError("Current WIP cannot exceed buffer capacity")
    conn.execute(
        """UPDATE wip_buffers SET capacity_qty=?, current_qty=?, source='manual',
              verified=?, updated_at=? WHERE machine_id=?""",
        (capacity, current, int(bool(payload.get("verified", False))), _now(), machine["id"]),
    )
    _audit(conn, "wip_buffer", machine_key, "buffer_updated", payload.get("actor", "operator"), payload)
    conn.commit()
    return next(item for item in snapshot(conn)["wip_buffers"] if item["machine_key"] == machine_key)


def create_unavailability(conn: sqlite3.Connection, payload: dict) -> dict:
    start = datetime.fromisoformat(payload["starts_at"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(payload["ends_at"].replace("Z", "+00:00"))
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("Unavailability timestamps must include a timezone")
    if end <= start:
        raise ValueError("Unavailability must end after it starts")
    resource_type = payload.get("resource_type", "machine")
    resource_key = payload["resource_key"]
    resource_queries = {
        "machine": ("machines", "machine_key"),
        "labor_role": ("labor_roles", "role_key"),
        "tool_pool": ("tool_pools", "pool_key"),
    }
    if resource_type == "factory":
        if resource_key != "factory":
            raise KeyError(f"Factory resource '{resource_key}' not found")
    else:
        table, column = resource_queries[resource_type]
        if not conn.execute(
            f"SELECT 1 FROM {table} WHERE {column}=?", (resource_key,)
        ).fetchone():
            raise KeyError(f"{resource_type.replace('_', ' ').title()} '{resource_key}' not found")
    if payload.get("work_order_id") and not conn.execute(
        "SELECT 1 FROM maintenance_work_orders WHERE id=?", (payload["work_order_id"],)
    ).fetchone():
        raise KeyError(f"Maintenance work order {payload['work_order_id']} not found")
    cursor = conn.execute(
        """INSERT INTO resource_unavailability
           (resource_type, resource_key, starts_at, ends_at, reason, source,
            work_order_id, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
        (resource_type, resource_key, start.isoformat(), end.isoformat(), payload["reason"],
         payload.get("source", "manual"), payload.get("work_order_id"),
         payload.get("actor", "operator"), _now()),
    )
    _audit(conn, resource_type, resource_key, "unavailability_created",
           payload.get("actor", "operator"), payload)
    conn.commit()
    return dict(conn.execute("SELECT * FROM resource_unavailability WHERE id=?", (cursor.lastrowid,)).fetchone())


def delete_unavailability(conn: sqlite3.Connection, unavailability_id: int, actor: str) -> dict:
    row = conn.execute("SELECT * FROM resource_unavailability WHERE id=?", (unavailability_id,)).fetchone()
    if not row:
        raise KeyError(f"Unavailability {unavailability_id} not found")
    conn.execute("DELETE FROM resource_unavailability WHERE id=?", (unavailability_id,))
    _audit(conn, row["resource_type"], row["resource_key"], "unavailability_deleted", actor, {"id": unavailability_id})
    conn.commit()
    return {"id": unavailability_id, "deleted": True}


def release_committed_reservations(conn: sqlite3.Connection) -> None:
    inventory.release_committed(conn)
    rows = conn.execute(
        """SELECT mres.id,mres.scenario_id,mres.production_order_id,mres.material_lot_id,
                  mres.quantity_sheets,ml.lot_code,ml.on_hand_sheets,md.material_key
           FROM material_reservations mres JOIN material_lots ml ON ml.id=mres.material_lot_id
           JOIN material_definitions md ON md.id=ml.material_id
           WHERE mres.status='committed'"""
    ).fetchall()
    now = _now()
    for row in rows:
        conn.execute(
            """UPDATE material_lots SET reserved_sheets=MAX(0, reserved_sheets-?), updated_at=?
               WHERE id=?""", (row["quantity_sheets"], now, row["material_lot_id"])
        )
        conn.execute(
            "UPDATE material_reservations SET status='released', updated_at=? WHERE id=?",
            (now, row["id"]),
        )
        inventory.record_movement(
            conn, object_type="sheet_lot", object_key=f"{row['material_key']}:{row['lot_code']}",
            movement_type="release", quantity=float(row["quantity_sheets"]), uom="sheet",
            balance_after=float(row["on_hand_sheets"]), actor="planner", source="planning",
            production_order_id=row["production_order_id"], scenario_id=row["scenario_id"],
        )


def settle_order_reservations(conn: sqlite3.Connection, production_order_id: int,
                              completed: bool, actor: str) -> None:
    rows = conn.execute(
        """SELECT mres.id,mres.scenario_id,mres.material_lot_id,mres.quantity_sheets,
                  md.material_key,ml.lot_code,ml.on_hand_sheets
           FROM material_reservations mres
           JOIN material_lots ml ON ml.id=mres.material_lot_id
           JOIN material_definitions md ON md.id=ml.material_id
           WHERE mres.production_order_id=? AND mres.status='committed'""",
        (production_order_id,),
    ).fetchall()
    now = _now()
    for row in rows:
        if completed:
            conn.execute(
                """UPDATE material_lots SET
                     on_hand_sheets=MAX(0, on_hand_sheets-?),
                     reserved_sheets=MAX(0, reserved_sheets-?), updated_at=? WHERE id=?""",
                (row["quantity_sheets"], row["quantity_sheets"], now, row["material_lot_id"]),
            )
            status = "consumed"
        else:
            conn.execute(
                """UPDATE material_lots SET reserved_sheets=MAX(0, reserved_sheets-?),
                     updated_at=? WHERE id=?""",
                (row["quantity_sheets"], now, row["material_lot_id"]),
            )
            status = "released"
        conn.execute(
            "UPDATE material_reservations SET status=?, updated_at=? WHERE id=?",
            (status, now, row["id"]),
        )
        _audit(conn, "material", row["material_key"],
               "reservation_consumed" if completed else "reservation_released",
               actor, {"production_order_id": production_order_id,
                       "quantity_sheets": row["quantity_sheets"]})
        inventory.record_movement(
            conn, object_type="sheet_lot", object_key=f"{row['material_key']}:{row['lot_code']}",
            movement_type="issue" if completed else "release",
            quantity=-float(row["quantity_sheets"]) if completed else float(row["quantity_sheets"]),
            uom="sheet", balance_after=max(0.0, float(row["on_hand_sheets"]) -
                                            (float(row["quantity_sheets"]) if completed else 0)),
            actor=actor, source="production", production_order_id=production_order_id,
            scenario_id=row["scenario_id"],
        )
    inventory.settle_order(conn, production_order_id, completed, actor)


def reserve_materials(conn: sqlite3.Connection, scenario_id: int, job_names: list[str]) -> None:
    status = snapshot(conn, job_names, sync=False)
    if not status["resource_ready"]:
        raise ValueError("Factory resources changed or are no longer feasible; generate a fresh scenario")
    release_committed_reservations(conn)
    orders = _orders(conn, job_names)
    order_ids = [order["id"] for order in orders]
    remnant_credits = inventory.reserve_remnants(conn, scenario_id, order_ids)
    now = _now()
    for order in orders:
        requirements = conn.execute(
            """SELECT mr.material_id,mr.required_area_m2,mr.unknown_part_count,
                      md.material_key,md.sheet_length_mm,md.sheet_width_mm,md.yield_factor
               FROM material_requirements mr JOIN material_definitions md ON md.id=mr.material_id
               WHERE mr.production_order_id=?""", (order["id"],)
        ).fetchall()
        for requirement in requirements:
            credit = float(remnant_credits.get((order["id"], requirement["material_id"]), 0))
            usable_area = (float(requirement["sheet_length_mm"]) * float(requirement["sheet_width_mm"]) /
                           1_000_000 * float(requirement["yield_factor"]))
            remaining = float(math.ceil(max(0.0, float(requirement["required_area_m2"]) - credit) /
                                        usable_area - 1e-12))
            lots = conn.execute(
                """SELECT id,lot_code,on_hand_sheets,reserved_sheets FROM material_lots
                   WHERE material_id=? AND status='available' AND verified=1
                   ORDER BY updated_at, id""", (requirement["material_id"],)
            ).fetchall()
            for lot in lots:
                available = float(lot["on_hand_sheets"]) - float(lot["reserved_sheets"])
                quantity = min(remaining, max(0.0, available))
                if quantity <= 0:
                    continue
                conn.execute(
                    """INSERT INTO material_reservations
                       (scenario_id, production_order_id, material_lot_id, quantity_sheets,
                        status, created_at, updated_at) VALUES (?,?,?,?,'committed',?,?)""",
                    (scenario_id, order["id"], lot["id"], quantity, now, now),
                )
                conn.execute(
                    "UPDATE material_lots SET reserved_sheets=reserved_sheets+?, updated_at=? WHERE id=?",
                    (quantity, now, lot["id"]),
                )
                inventory.record_movement(
                    conn, object_type="sheet_lot",
                    object_key=f"{requirement['material_key']}:{lot['lot_code']}",
                    movement_type="reservation", quantity=quantity, uom="sheet",
                    balance_after=float(lot["on_hand_sheets"]), actor="planner", source="planning",
                    production_order_id=order["id"], scenario_id=scenario_id,
                )
                remaining -= quantity
                if remaining <= 1e-9:
                    break
            if remaining > 1e-9:
                raise ValueError("Material stock changed during approval; generate a fresh scenario")
    inventory.reserve_components(conn, scenario_id, order_ids)


def simulation_context(conn: sqlite3.Connection, jobs: list[dict], simulated_at: datetime,
                       sync: bool = True) -> dict:
    job_names = [job["job_name"] for job in jobs]
    status = snapshot(conn, job_names, sync=sync)
    profiles = {row["machine_key"]: dict(row) for row in conn.execute(
        """SELECT m.machine_key, lr.role_key, mrp.labor_qty, tp.pool_key,
                  mrp.tool_qty, mrp.machine_capacity
           FROM machine_resource_profiles mrp JOIN machines m ON m.id=mrp.machine_id
           LEFT JOIN labor_roles lr ON lr.id=mrp.labor_role_id
           LEFT JOIN tool_pools tp ON tp.id=mrp.tool_pool_id"""
    ).fetchall()}
    labor = {row["role_key"]: int(row["headcount"]) for row in conn.execute(
        "SELECT role_key, headcount FROM labor_roles"
    ).fetchall()}
    tooling = {row["pool_key"]: int(row["effective_available_qty"])
               for row in status["tool_pools"]}
    buffers = {row["machine_key"]: {
        "capacity": int(row["capacity_qty"]), "current": int(row["current_qty"]),
    } for row in conn.execute(
        """SELECT m.machine_key, wb.capacity_qty, wb.current_qty
           FROM wip_buffers wb JOIN machines m ON m.id=wb.machine_id"""
    ).fetchall()}
    windows = [dict(row) for row in conn.execute(
        "SELECT * FROM work_calendar_windows WHERE active=1"
    ).fetchall()]
    unavailable = [dict(row) for row in conn.execute(
        "SELECT * FROM resource_unavailability WHERE ends_at>=?", (simulated_at.isoformat(),)
    ).fetchall()]
    open_down = conn.execute(
        """SELECT m.machine_key FROM downtime_events de JOIN machines m ON m.id=de.machine_id
           WHERE de.status='open'"""
    ).fetchall()
    horizon_end = simulated_at + timedelta(days=HORIZON_DAYS)
    for row in open_down:
        unavailable.append({
            "resource_type": "machine", "resource_key": row["machine_key"],
            "starts_at": simulated_at.isoformat(), "ends_at": horizon_end.isoformat(),
            "reason": "open downtime",
        })
    return {
        "simulated_at": simulated_at,
        "horizon_s": HORIZON_DAYS * 86400,
        "readiness": status,
        "profiles": profiles,
        "labor": labor,
        "tooling": tooling,
        "buffers": buffers,
        "windows": windows,
        "unavailability": unavailable,
        "interval_cache": {},
    }


def _intersect(left: list[tuple[float, float]], right: list[tuple[float, float]]) -> list[tuple[float, float]]:
    result = []
    i = j = 0
    while i < len(left) and j < len(right):
        start = max(left[i][0], right[j][0])
        end = min(left[i][1], right[j][1])
        if start < end:
            result.append((start, end))
        if left[i][1] < right[j][1]:
            i += 1
        else:
            j += 1
    return result


def _subtract(intervals: list[tuple[float, float]], blocked: tuple[float, float]) -> list[tuple[float, float]]:
    result = []
    for start, end in intervals:
        if blocked[1] <= start or blocked[0] >= end:
            result.append((start, end))
            continue
        if blocked[0] > start:
            result.append((start, blocked[0]))
        if blocked[1] < end:
            result.append((blocked[1], end))
    return result


def _windows_for(context: dict, resource_type: str, resource_key: str) -> list[tuple[float, float]] | None:
    rows = [row for row in context["windows"]
            if row["resource_type"] == resource_type and row["resource_key"] == resource_key]
    if not rows:
        return None
    base = context["simulated_at"]
    result: list[tuple[float, float]] = []
    for row in rows:
        zone = ZoneInfo(row["timezone"])
        local_start = base.astimezone(zone).date() - timedelta(days=1)
        start_clock = time.fromisoformat(row["start_time"])
        end_clock = time.fromisoformat(row["end_time"])
        for offset in range(HORIZON_DAYS + 3):
            day = local_start + timedelta(days=offset)
            if day.weekday() != int(row["weekday"]):
                continue
            start_dt = datetime.combine(day, start_clock, tzinfo=zone)
            end_day = day + timedelta(days=1) if end_clock <= start_clock else day
            end_dt = datetime.combine(end_day, end_clock, tzinfo=zone)
            start_s = (start_dt.astimezone(timezone.utc) - base).total_seconds()
            end_s = (end_dt.astimezone(timezone.utc) - base).total_seconds()
            if end_s > 0 and start_s < context["horizon_s"]:
                result.append((max(0.0, start_s), min(float(context["horizon_s"]), end_s)))
    return sorted(result)


def _availability_intervals(context: dict, machine_key: str,
                            role_key: str | None, pool_key: str | None) -> list[tuple[float, float]]:
    cache_key = (machine_key, role_key, pool_key)
    if cache_key in context["interval_cache"]:
        return context["interval_cache"][cache_key]
    intervals = _windows_for(context, "factory", "factory") or [(0.0, float(context["horizon_s"]))]
    for resource_type, resource_key in (
        ("machine", machine_key), ("labor_role", role_key), ("tool_pool", pool_key),
    ):
        if not resource_key:
            continue
        specific = _windows_for(context, resource_type, resource_key)
        if specific is not None:
            intervals = _intersect(intervals, specific)
    base = context["simulated_at"]
    for item in context["unavailability"]:
        if (item["resource_type"], item["resource_key"]) not in {
            ("factory", "factory"), ("machine", machine_key),
            ("labor_role", role_key), ("tool_pool", pool_key),
        }:
            continue
        start = datetime.fromisoformat(item["starts_at"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(item["ends_at"].replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        blocked = ((start.astimezone(timezone.utc) - base).total_seconds(),
                   (end.astimezone(timezone.utc) - base).total_seconds())
        intervals = _subtract(intervals, blocked)
    context["interval_cache"][cache_key] = intervals
    return intervals


def next_available_delay(context: dict, machine_key: str, role_key: str | None,
                         pool_key: str | None, now_s: float, duration_s: float) -> float | None:
    for start, end in _availability_intervals(context, machine_key, role_key, pool_key):
        candidate = max(now_s, start)
        if candidate + duration_s <= end:
            return max(0.0, candidate - now_s)
    return None
