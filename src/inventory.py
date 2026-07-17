"""Auditable component inventory, usable remnants, and shortage intelligence."""

from __future__ import annotations

import json
import math
import re
import sqlite3
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any


EDGE_USAGE_FACTOR = 1.05
MIN_REMNANT_DIM_MM = 150.0
MIN_REMNANT_AREA_M2 = 0.05
CNC_LIKE_EDGE = re.compile(r"^\*?R\d+[BF](?:G)?\d+\*?$", re.IGNORECASE)
CATEGORIES = {"edge_band", "hardware", "consumable", "packaging"}
UOMS = {"m", "each", "kg", "l"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _material_key(value: str | None) -> str:
    if not value or not value.strip():
        return "UNSPECIFIED"
    return re.sub(r"\s+", " ", value.strip()).upper()


def _item_key(value: str) -> str:
    key = re.sub(r"[^A-Z0-9]+", "_", value.strip().upper()).strip("_")
    if not key:
        raise ValueError("Inventory item key cannot be empty")
    if key[0].isdigit():
        key = f"ITEM_{key}"
    return key[:80]


def record_movement(conn: sqlite3.Connection, *, object_type: str, object_key: str,
                    movement_type: str, quantity: float, uom: str,
                    balance_after: float | None, actor: str, source: str,
                    production_order_id: int | None = None,
                    scenario_id: int | None = None,
                    idempotency_key: str | None = None,
                    notes: str | None = None) -> None:
    conn.execute(
        """INSERT INTO inventory_movements
           (object_type,object_key,movement_type,quantity,uom,balance_after,
            production_order_id,scenario_id,source,actor,idempotency_key,notes,ts)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (object_type, object_key, movement_type, quantity, uom, balance_after,
         production_order_id, scenario_id, source, actor, idempotency_key,
         notes, _now()),
    )


def _orders(conn: sqlite3.Connection, job_names: list[str] | None,
            *, display: bool = False) -> list[dict]:
    if job_names:
        marks = ",".join("?" for _ in job_names)
        where, params = f"j.job_name IN ({marks})", tuple(job_names)
    elif display:
        where, params = "po.status NOT IN ('completed','cancelled')", ()
    else:
        where, params = "po.status IN ('ready','released','in_progress')", ()
    return [dict(row) for row in conn.execute(
        f"""SELECT po.id,po.job_id,po.status,po.due_at,j.job_name
            FROM production_orders po JOIN jobs j ON j.id=po.job_id
            WHERE {where} ORDER BY po.id""", params
    ).fetchall()]


def sync_requirements(conn: sqlite3.Connection, *, commit: bool = True) -> dict:
    """Derive edge-band metres from CV dimensions without inventing hardware BOMs."""
    now = _now()
    orders = conn.execute("SELECT id,job_id FROM production_orders").fetchall()
    requirement_count = issue_count = 0
    for order in orders:
        grouped: dict[str, dict[str, Any]] = {}
        keep_item_ids: list[int] = []
        conn.execute(
            "DELETE FROM inventory_sync_issues WHERE production_order_id=? AND issue_code IN ('missing_edge_dimension','suspected_shifted_cnc_value')",
            (order["id"],),
        )
        parts = conn.execute(
            """SELECT id,length_mm,width_mm,qty,eb1,eb2,eb3,eb4
               FROM parts WHERE job_id=?""", (order["job_id"],)
        ).fetchall()
        for part in parts:
            quantity = max(1, int(part["qty"] or 1))
            edges = (("eb1", part["eb1"], part["length_mm"]),
                     ("eb2", part["eb2"], part["length_mm"]),
                     ("eb3", part["eb3"], part["width_mm"]),
                     ("eb4", part["eb4"], part["width_mm"]))
            for field, raw_value, dimension_mm in edges:
                value = str(raw_value or "").strip()
                if not value:
                    continue
                if CNC_LIKE_EDGE.fullmatch(value):
                    conn.execute(
                        """INSERT INTO inventory_sync_issues
                           (production_order_id,part_id,source_field,raw_value,issue_code,status,updated_at)
                           VALUES (?,?,?,?,?,'open',?)
                           ON CONFLICT(production_order_id,part_id,source_field,issue_code)
                           DO UPDATE SET raw_value=excluded.raw_value,status='open',updated_at=excluded.updated_at""",
                        (order["id"], part["id"], field, value,
                         "suspected_shifted_cnc_value", now),
                    )
                    issue_count += 1
                    continue
                if not dimension_mm or float(dimension_mm) <= 0:
                    conn.execute(
                        """INSERT INTO inventory_sync_issues
                           (production_order_id,part_id,source_field,raw_value,issue_code,status,updated_at)
                           VALUES (?,?,?,?,?,'open',?)
                           ON CONFLICT(production_order_id,part_id,source_field,issue_code)
                           DO UPDATE SET raw_value=excluded.raw_value,status='open',updated_at=excluded.updated_at""",
                        (order["id"], part["id"], field, value,
                         "missing_edge_dimension", now),
                    )
                    issue_count += 1
                    continue
                key = _item_key(value)
                item = grouped.setdefault(key, {"name": value, "raw_metres": 0.0})
                item["raw_metres"] += float(dimension_mm) * quantity / 1000
        for key, demand in grouped.items():
            conn.execute(
                """INSERT OR IGNORE INTO inventory_items
                   (item_key,name,category,uom,usage_factor,source,verified,created_at,updated_at)
                   VALUES (?,?,'edge_band','m',?,'cv_edges',0,?,?)""",
                (key, demand["name"], EDGE_USAGE_FACTOR, now, now),
            )
            item = conn.execute(
                "SELECT id,usage_factor FROM inventory_items WHERE item_key=?", (key,)
            ).fetchone()
            required = round(demand["raw_metres"] * float(item["usage_factor"]), 3)
            conn.execute(
                """INSERT INTO component_requirements
                   (production_order_id,item_id,required_qty,source,confidence,updated_at)
                   VALUES (?,? ,?,'cv_edges','estimated',?)
                   ON CONFLICT(production_order_id,item_id) DO UPDATE SET
                     required_qty=excluded.required_qty,confidence='estimated',updated_at=excluded.updated_at
                   WHERE component_requirements.source='cv_edges'
                     AND (component_requirements.required_qty IS NOT excluded.required_qty
                          OR component_requirements.confidence!='estimated')""",
                (order["id"], item["id"], required, now),
            )
            keep_item_ids.append(item["id"])
            requirement_count += 1
        if keep_item_ids:
            marks = ",".join("?" for _ in keep_item_ids)
            conn.execute(
                f"""DELETE FROM component_requirements
                    WHERE production_order_id=? AND source='cv_edges'
                      AND item_id NOT IN ({marks})""",
                (order["id"], *keep_item_ids),
            )
        else:
            conn.execute(
                "DELETE FROM component_requirements WHERE production_order_id=? AND source='cv_edges'",
                (order["id"],),
            )
    if commit:
        conn.commit()
    return {"requirements": requirement_count, "issues": issue_count}


def upsert_item(conn: sqlite3.Connection, item_key: str, payload: dict) -> dict:
    key = _item_key(item_key)
    category = str(payload.get("category", "hardware"))
    uom = str(payload.get("uom", "each"))
    if category not in CATEGORIES or uom not in UOMS:
        raise ValueError("Unsupported inventory category or unit")
    usage_factor = float(payload.get("usage_factor", 1))
    reorder = float(payload.get("reorder_point", 0))
    safety = float(payload.get("safety_stock", 0))
    multiple = float(payload.get("order_multiple", 1))
    lead = int(payload.get("lead_time_days", 0))
    unit_cost = payload.get("unit_cost")
    if usage_factor < 1 or reorder < 0 or safety < 0 or multiple <= 0 or lead < 0:
        raise ValueError("Inventory policy values must be non-negative and usage_factor at least 1")
    if unit_cost is not None and float(unit_cost) < 0:
        raise ValueError("unit_cost cannot be negative")
    now = _now()
    conn.execute(
        """INSERT INTO inventory_items
           (item_key,name,category,uom,usage_factor,reorder_point,safety_stock,
            order_multiple,lead_time_days,unit_cost,preferred_supplier,source,
            verified,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(item_key) DO UPDATE SET name=excluded.name,
             category=excluded.category,uom=excluded.uom,usage_factor=excluded.usage_factor,
             reorder_point=excluded.reorder_point,safety_stock=excluded.safety_stock,
             order_multiple=excluded.order_multiple,lead_time_days=excluded.lead_time_days,
             unit_cost=excluded.unit_cost,preferred_supplier=excluded.preferred_supplier,
             source='manual',verified=excluded.verified,updated_at=excluded.updated_at""",
        (key, payload.get("name") or item_key, category, uom, usage_factor,
         reorder, safety, multiple, lead, float(unit_cost) if unit_cost is not None else None,
         payload.get("preferred_supplier"), "manual",
         int(bool(payload.get("verified", False))), now, now),
    )
    sync_requirements(conn, commit=False)
    conn.commit()
    return dict(conn.execute("SELECT * FROM inventory_items WHERE item_key=?", (key,)).fetchone())


def set_lot_balance(conn: sqlite3.Connection, item_key: str, lot_code: str,
                    payload: dict) -> dict:
    item = conn.execute(
        "SELECT * FROM inventory_items WHERE item_key=?", (_item_key(item_key),)
    ).fetchone()
    if not item:
        raise KeyError(f"Inventory item '{item_key}' not found")
    code = str(lot_code).strip()
    if not code:
        raise ValueError("lot_code cannot be empty")
    object_key = f"{item['item_key']}:{code}"
    current = conn.execute(
        "SELECT * FROM inventory_lots WHERE item_id=? AND lot_code=?",
        (item["id"], code),
    ).fetchone()
    idempotency_key = payload.get("idempotency_key")
    if idempotency_key:
        prior = conn.execute(
            "SELECT object_key FROM inventory_movements WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        if prior:
            if prior["object_key"] != object_key or not current:
                raise ValueError("idempotency_key was already used for another inventory object")
            return dict(conn.execute(
                """SELECT il.*,ii.item_key,ii.name,ii.uom FROM inventory_lots il
                   JOIN inventory_items ii ON ii.id=il.item_id
                   WHERE il.item_id=? AND il.lot_code=?""",
                (item["id"], code),
            ).fetchone())
    expected = payload.get("expected_version")
    if expected is not None and current and int(expected) != int(current["version"]):
        raise ValueError("Inventory lot changed; refresh before saving")
    balance = float(payload["on_hand_qty"])
    reserved = float(current["reserved_qty"] if current else 0)
    if balance < reserved:
        raise ValueError("On-hand quantity cannot be lower than committed reservations")
    previous = float(current["on_hand_qty"] if current else 0)
    now = _now()
    status = "depleted" if balance == 0 else "available"
    conn.execute(
        """INSERT INTO inventory_lots
           (item_id,lot_code,location,status,on_hand_qty,reserved_qty,source,
            verified,version,received_at,updated_at)
           VALUES (?,?,?,?,?,0,'manual',?,1,?,?)
           ON CONFLICT(item_id,lot_code) DO UPDATE SET location=excluded.location,
             status=CASE WHEN excluded.on_hand_qty=0 THEN 'depleted' ELSE 'available' END,
             on_hand_qty=excluded.on_hand_qty,source='manual',verified=excluded.verified,
             version=inventory_lots.version+1,updated_at=excluded.updated_at""",
        (item["id"], code, payload.get("location"), status, balance,
         int(bool(payload.get("verified", False))), payload.get("received_at") or now, now),
    )
    record_movement(
        conn, object_type="component_lot", object_key=object_key,
        movement_type=payload.get("movement_type", "adjustment"),
        quantity=balance - previous, uom=item["uom"], balance_after=balance,
        actor=payload.get("actor", "operator"), source="manual",
        idempotency_key=idempotency_key, notes=payload.get("notes"),
    )
    conn.commit()
    return dict(conn.execute(
        """SELECT il.*,ii.item_key,ii.name,ii.uom FROM inventory_lots il
           JOIN inventory_items ii ON ii.id=il.item_id WHERE il.item_id=? AND il.lot_code=?""",
        (item["id"], code),
    ).fetchone())


def set_requirement(conn: sqlite3.Connection, production_order_id: int,
                    item_key: str, payload: dict) -> dict:
    if not conn.execute("SELECT 1 FROM production_orders WHERE id=?", (production_order_id,)).fetchone():
        raise KeyError(f"Production order {production_order_id} not found")
    item = conn.execute(
        "SELECT id,item_key FROM inventory_items WHERE item_key=?", (_item_key(item_key),)
    ).fetchone()
    if not item:
        raise KeyError(f"Inventory item '{item_key}' not found")
    quantity = float(payload["required_qty"])
    if quantity < 0:
        raise ValueError("required_qty cannot be negative")
    now = _now()
    conn.execute(
        """INSERT INTO component_requirements
           (production_order_id,item_id,required_qty,source,confidence,notes,updated_at)
           VALUES (?, ?, ?, 'manual_bom', ?, ?, ?)
           ON CONFLICT(production_order_id,item_id) DO UPDATE SET
             required_qty=excluded.required_qty,source='manual_bom',
             confidence=excluded.confidence,notes=excluded.notes,updated_at=excluded.updated_at""",
        (production_order_id, item["id"], quantity,
         "verified" if payload.get("verified") else "operator_entered",
         payload.get("notes"), now),
    )
    conn.commit()
    return dict(conn.execute(
        "SELECT * FROM component_requirements WHERE production_order_id=? AND item_id=?",
        (production_order_id, item["id"]),
    ).fetchone())


def create_remnant(conn: sqlite3.Connection, payload: dict) -> dict:
    material = conn.execute(
        "SELECT * FROM material_definitions WHERE material_key=?",
        (_material_key(payload["material_key"]),),
    ).fetchone()
    if not material:
        raise KeyError(f"Material '{payload['material_key']}' not found")
    length = float(payload["length_mm"])
    width = float(payload["width_mm"])
    if length <= 0 or width <= 0:
        raise ValueError("Remnant dimensions must be positive")
    grain = str(payload.get("grain_direction", "length"))
    if grain not in {"length", "none"}:
        raise ValueError("grain_direction must be length or none")
    area = length * width / 1_000_000
    usable = length >= MIN_REMNANT_DIM_MM and width >= MIN_REMNANT_DIM_MM and area >= MIN_REMNANT_AREA_M2
    status = "available" if usable else "hold"
    key = str(payload.get("remnant_key") or
              f"REM-{datetime.now(timezone.utc):%y%m%d}-{uuid.uuid4().hex[:8].upper()}").strip().upper()
    if len(key) > 100:
        raise ValueError("remnant_key is too long")
    source_lot_id = payload.get("source_material_lot_id")
    if source_lot_id and not conn.execute(
        "SELECT 1 FROM material_lots WHERE id=? AND material_id=?", (source_lot_id, material["id"])
    ).fetchone():
        raise ValueError("Source sheet lot does not match the remnant material")
    now = _now()
    conn.execute(
        """INSERT INTO material_remnants
           (remnant_key,material_id,source_material_lot_id,length_mm,width_mm,
            thickness_mm,grain_direction,usable_area_m2,location,status,source,
            verified,created_by,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?, 'manual_measurement',?,?,?,?)""",
        (key, material["id"], source_lot_id, length, width,
         payload.get("thickness_mm"), grain, area, payload.get("location"), status,
         int(bool(payload.get("verified", False))), payload.get("actor", "operator"), now, now),
    )
    record_movement(
        conn, object_type="remnant", object_key=key, movement_type="create",
        quantity=1, uom="each", balance_after=1, actor=payload.get("actor", "operator"),
        source="manual_measurement", notes=(None if usable else "Below provisional usable-remnant threshold"),
    )
    conn.commit()
    return remnant_row(conn, key)


def remnant_row(conn: sqlite3.Connection, remnant_key: str) -> dict:
    row = conn.execute(
        """SELECT mr.*,md.material_key,md.name material_name
           FROM material_remnants mr JOIN material_definitions md ON md.id=mr.material_id
           WHERE mr.remnant_key=?""", (remnant_key.upper(),)
    ).fetchone()
    if not row:
        raise KeyError(f"Remnant '{remnant_key}' not found")
    return dict(row)


def update_remnant(conn: sqlite3.Connection, remnant_key: str, payload: dict) -> dict:
    row = conn.execute(
        "SELECT * FROM material_remnants WHERE remnant_key=?", (remnant_key.upper(),)
    ).fetchone()
    if not row:
        raise KeyError(f"Remnant '{remnant_key}' not found")
    if int(payload.get("expected_version", row["version"])) != int(row["version"]):
        raise ValueError("Remnant changed; refresh before saving")
    if conn.execute(
        "SELECT 1 FROM remnant_reservations WHERE remnant_id=? AND status='committed'", (row["id"],)
    ).fetchone():
        raise ValueError("Release the committed schedule before changing this remnant")
    status = str(payload.get("status", row["status"]))
    if status not in {"available", "hold", "scrapped"}:
        raise ValueError("Operator remnant status must be available, hold, or scrapped")
    verified = int(bool(payload.get("verified", row["verified"])))
    now = _now()
    conn.execute(
        """UPDATE material_remnants SET status=?,location=?,verified=?,version=version+1,
             updated_at=? WHERE id=?""",
        (status, payload.get("location", row["location"]), verified, now, row["id"]),
    )
    if status == "scrapped" and row["status"] != "scrapped":
        record_movement(
            conn, object_type="remnant", object_key=row["remnant_key"], movement_type="scrap",
            quantity=-1, uom="each", balance_after=0, actor=payload.get("actor", "operator"),
            source="manual", notes=payload.get("notes"),
        )
    conn.commit()
    return remnant_row(conn, row["remnant_key"])


def _fits(remnant: dict, part: dict) -> bool:
    length, width = float(part["length_mm"]), float(part["width_mm"])
    direct = length <= float(remnant["length_mm"]) and width <= float(remnant["width_mm"])
    rotated = width <= float(remnant["length_mm"]) and length <= float(remnant["width_mm"])
    return direct or (not bool(part["grain"]) and rotated)


def plan_remnants(conn: sqlite3.Connection, order_ids: list[int]) -> dict:
    if not order_ids:
        return {"assignments": [], "credits": {}, "candidate_count": 0}
    marks = ",".join("?" for _ in order_ids)
    parts = [dict(row) for row in conn.execute(
        f"""SELECT po.id production_order_id,p.id part_id,p.material,p.length_mm,
                   p.width_mm,p.qty,p.grain,md.id material_id
            FROM production_orders po JOIN parts p ON p.job_id=po.job_id
            JOIN material_definitions md ON md.material_key=UPPER(TRIM(p.material))
            WHERE po.id IN ({marks}) AND p.length_mm>0 AND p.width_mm>0
            ORDER BY (p.length_mm*p.width_mm) DESC,p.id""", tuple(order_ids)
    ).fetchall()]
    # Material keys can contain repeated whitespace, so recover any rows missed by SQL normalization.
    known_part_ids = {part["part_id"] for part in parts}
    definitions = {_material_key(row["material_key"]): row["id"] for row in conn.execute(
        "SELECT id,material_key FROM material_definitions"
    )}
    extra = conn.execute(
        f"""SELECT po.id production_order_id,p.id part_id,p.material,p.length_mm,
                   p.width_mm,p.qty,p.grain
            FROM production_orders po JOIN parts p ON p.job_id=po.job_id
            WHERE po.id IN ({marks}) AND p.length_mm>0 AND p.width_mm>0""", tuple(order_ids)
    ).fetchall()
    for row in extra:
        if row["part_id"] in known_part_ids:
            continue
        material_id = definitions.get(_material_key(row["material"]))
        if material_id:
            parts.append({**dict(row), "material_id": material_id})
    instances = []
    for part in parts:
        for ordinal in range(1, max(1, int(part["qty"] or 1)) + 1):
            instances.append({**part, "instance_ordinal": ordinal,
                              "part_area_m2": float(part["length_mm"]) * float(part["width_mm"]) / 1_000_000})
    instances.sort(key=lambda item: (-item["part_area_m2"], item["part_id"], item["instance_ordinal"]))
    existing = [dict(row) for row in conn.execute(
        f"""SELECT rr.production_order_id,rr.remnant_id,rr.part_id,rr.instance_ordinal,
                   rr.credited_area_m2,mr.remnant_key,mr.material_id,mr.length_mm,mr.width_mm
            FROM remnant_reservations rr JOIN material_remnants mr ON mr.id=rr.remnant_id
            WHERE rr.status='committed' AND rr.production_order_id IN ({marks})""", tuple(order_ids)
    ).fetchall()]
    assignments = [{**item, "existing": True} for item in existing]
    used_instances = {(item["part_id"], item["instance_ordinal"]) for item in existing}
    candidates = [dict(row) for row in conn.execute(
        """SELECT mr.* FROM material_remnants mr
           WHERE mr.status='available' AND mr.verified=1 AND NOT EXISTS (
             SELECT 1 FROM remnant_reservations rr
             WHERE rr.remnant_id=mr.id AND rr.status='committed')
           ORDER BY mr.usable_area_m2,mr.created_at"""
    ).fetchall()]
    candidate_total = len(candidates) + len(existing)
    available_by_material: dict[int, list[dict]] = defaultdict(list)
    for remnant in candidates:
        available_by_material[int(remnant["material_id"])].append(remnant)
    for part in instances:
        if (part["part_id"], part["instance_ordinal"]) in used_instances:
            continue
        pool = available_by_material.get(int(part["material_id"]), [])
        match_index = next((index for index, remnant in enumerate(pool) if _fits(remnant, part)), None)
        if match_index is None:
            continue
        remnant = pool.pop(match_index)
        assignments.append({
            "production_order_id": part["production_order_id"],
            "remnant_id": remnant["id"], "remnant_key": remnant["remnant_key"],
            "material_id": part["material_id"], "part_id": part["part_id"],
            "instance_ordinal": part["instance_ordinal"],
            "credited_area_m2": round(part["part_area_m2"], 6),
            "length_mm": remnant["length_mm"], "width_mm": remnant["width_mm"],
            "existing": False,
        })
    credits: dict[tuple[int, int], float] = defaultdict(float)
    for assignment in assignments:
        credits[(int(assignment["production_order_id"]), int(assignment["material_id"]))] += float(
            assignment["credited_area_m2"]
        )
    return {"assignments": assignments, "credits": dict(credits),
            "candidate_count": candidate_total}


def reserve_remnants(conn: sqlite3.Connection, scenario_id: int,
                     order_ids: list[int], actor: str = "planner") -> dict[tuple[int, int], float]:
    plan = plan_remnants(conn, order_ids)
    now = _now()
    for item in plan["assignments"]:
        if item["existing"]:
            continue
        conn.execute(
            """INSERT INTO remnant_reservations
               (scenario_id,production_order_id,remnant_id,part_id,instance_ordinal,
                credited_area_m2,status,created_at,updated_at)
               VALUES (?,?,?,?,?,?,'committed',?,?)""",
            (scenario_id, item["production_order_id"], item["remnant_id"], item["part_id"],
             item["instance_ordinal"], item["credited_area_m2"], now, now),
        )
        conn.execute(
            "UPDATE material_remnants SET status='reserved',version=version+1,updated_at=? WHERE id=?",
            (now, item["remnant_id"]),
        )
        record_movement(
            conn, object_type="remnant", object_key=item["remnant_key"],
            movement_type="reservation", quantity=1, uom="each", balance_after=1,
            actor=actor, source="planning", production_order_id=item["production_order_id"],
            scenario_id=scenario_id,
        )
    return plan["credits"]


def component_rows(conn: sqlite3.Connection, display_order_ids: list[int],
                   scope_order_ids: list[int]) -> list[dict]:
    result = []
    for row in conn.execute("SELECT * FROM inventory_items ORDER BY category,name").fetchall():
        item = dict(row)
        lots = [dict(lot) for lot in conn.execute(
            """SELECT id,lot_code,location,status,on_hand_qty,reserved_qty,source,
                      verified,version,received_at,updated_at
               FROM inventory_lots WHERE item_id=? ORDER BY status,received_at,id""",
            (item["id"],),
        ).fetchall()]
        on_hand = sum(float(lot["on_hand_qty"]) for lot in lots if lot["status"] == "available")
        reserved = sum(float(lot["reserved_qty"]) for lot in lots if lot["status"] == "available")
        def required(ids: list[int]) -> float:
            if not ids:
                return 0.0
            marks = ",".join("?" for _ in ids)
            return float(conn.execute(
                f"SELECT COALESCE(SUM(required_qty),0) value FROM component_requirements WHERE item_id=? AND production_order_id IN ({marks})",
                (item["id"], *ids),
            ).fetchone()["value"])
        scope_required = required(scope_order_ids)
        open_required = required(display_order_ids)
        held_scope = 0.0
        if scope_order_ids:
            marks = ",".join("?" for _ in scope_order_ids)
            held_scope = float(conn.execute(
                f"""SELECT COALESCE(SUM(cr.quantity),0) value FROM component_reservations cr
                    JOIN inventory_lots il ON il.id=cr.inventory_lot_id
                    WHERE il.item_id=? AND cr.status='committed'
                      AND cr.production_order_id IN ({marks})""",
                (item["id"], *scope_order_ids),
            ).fetchone()["value"])
        effective = on_hand - reserved + held_scope
        free = on_hand - reserved
        available_lots = [lot for lot in lots if lot["status"] == "available"]
        stock_verified = bool(available_lots) and all(bool(lot["verified"]) for lot in available_lots)
        target = max(float(item["reorder_point"]), open_required + float(item["safety_stock"]))
        shortage = max(0.0, scope_required - effective)
        open_shortage = max(0.0, open_required - free)
        suggested = max(0.0, target - free)
        multiple = float(item["order_multiple"])
        suggested = math.ceil(suggested / multiple - 1e-12) * multiple if suggested > 0 else 0.0
        item.update({
            "lots": lots, "on_hand_qty": round(on_hand, 3),
            "reserved_qty": round(reserved, 3), "effective_available_qty": round(effective, 3),
            "held_for_scope_qty": round(held_scope, 3), "required_qty": round(scope_required, 3),
            "open_required_qty": round(open_required, 3), "shortage_qty": round(shortage, 3),
            "open_shortage_qty": round(open_shortage, 3), "stock_verified": stock_verified,
            "feasible": bool(item["verified"]) and stock_verified and effective + 1e-9 >= scope_required,
            "open_feasible": bool(item["verified"]) and stock_verified and free + 1e-9 >= open_required,
            "suggested_order_qty": round(suggested, 3),
            "estimated_order_cost": round(suggested * float(item["unit_cost"]), 2)
            if suggested and item["unit_cost"] is not None else None,
        })
        result.append(item)
    return result


def reserve_components(conn: sqlite3.Connection, scenario_id: int,
                       order_ids: list[int], actor: str = "planner") -> None:
    now = _now()
    for order_id in order_ids:
        requirements = conn.execute(
            "SELECT item_id,required_qty FROM component_requirements WHERE production_order_id=? AND required_qty>0",
            (order_id,),
        ).fetchall()
        for requirement in requirements:
            remaining = float(requirement["required_qty"])
            lots = conn.execute(
                """SELECT il.*,ii.item_key,ii.uom FROM inventory_lots il
                   JOIN inventory_items ii ON ii.id=il.item_id
                   WHERE il.item_id=? AND il.status='available' AND il.verified=1
                   ORDER BY COALESCE(il.received_at,il.updated_at),il.id""",
                (requirement["item_id"],),
            ).fetchall()
            for lot in lots:
                available = float(lot["on_hand_qty"]) - float(lot["reserved_qty"])
                quantity = min(remaining, max(0.0, available))
                if quantity <= 1e-9:
                    continue
                conn.execute(
                    """INSERT INTO component_reservations
                       (scenario_id,production_order_id,inventory_lot_id,quantity,status,created_at,updated_at)
                       VALUES (?,?,?,?,'committed',?,?)""",
                    (scenario_id, order_id, lot["id"], quantity, now, now),
                )
                conn.execute(
                    "UPDATE inventory_lots SET reserved_qty=reserved_qty+?,version=version+1,updated_at=? WHERE id=?",
                    (quantity, now, lot["id"]),
                )
                record_movement(
                    conn, object_type="component_lot", object_key=f"{lot['item_key']}:{lot['lot_code']}",
                    movement_type="reservation", quantity=quantity, uom=lot["uom"],
                    balance_after=float(lot["on_hand_qty"]), actor=actor, source="planning",
                    production_order_id=order_id, scenario_id=scenario_id,
                )
                remaining -= quantity
                if remaining <= 1e-9:
                    break
            if remaining > 1e-9:
                raise ValueError("Component stock changed during approval; generate a fresh scenario")


def release_committed(conn: sqlite3.Connection, actor: str = "planner") -> None:
    now = _now()
    components = conn.execute(
        """SELECT cr.*,il.lot_code,il.on_hand_qty,ii.item_key,ii.uom
           FROM component_reservations cr JOIN inventory_lots il ON il.id=cr.inventory_lot_id
           JOIN inventory_items ii ON ii.id=il.item_id WHERE cr.status='committed'"""
    ).fetchall()
    for row in components:
        conn.execute(
            "UPDATE inventory_lots SET reserved_qty=MAX(0,reserved_qty-?),version=version+1,updated_at=? WHERE id=?",
            (row["quantity"], now, row["inventory_lot_id"]),
        )
        conn.execute("UPDATE component_reservations SET status='released',updated_at=? WHERE id=?", (now, row["id"]))
        record_movement(
            conn, object_type="component_lot", object_key=f"{row['item_key']}:{row['lot_code']}",
            movement_type="release", quantity=float(row["quantity"]), uom=row["uom"],
            balance_after=float(row["on_hand_qty"]), actor=actor, source="planning",
            production_order_id=row["production_order_id"], scenario_id=row["scenario_id"],
        )
    remnants = conn.execute(
        """SELECT rr.*,mr.remnant_key FROM remnant_reservations rr
           JOIN material_remnants mr ON mr.id=rr.remnant_id WHERE rr.status='committed'"""
    ).fetchall()
    for row in remnants:
        conn.execute("UPDATE remnant_reservations SET status='released',updated_at=? WHERE id=?", (now, row["id"]))
        conn.execute(
            "UPDATE material_remnants SET status='available',version=version+1,updated_at=? WHERE id=?",
            (now, row["remnant_id"]),
        )
        record_movement(
            conn, object_type="remnant", object_key=row["remnant_key"], movement_type="release",
            quantity=1, uom="each", balance_after=1, actor=actor, source="planning",
            production_order_id=row["production_order_id"], scenario_id=row["scenario_id"],
        )


def settle_order(conn: sqlite3.Connection, production_order_id: int,
                 completed: bool, actor: str) -> None:
    now = _now()
    components = conn.execute(
        """SELECT cr.*,il.lot_code,il.on_hand_qty,ii.item_key,ii.uom
           FROM component_reservations cr JOIN inventory_lots il ON il.id=cr.inventory_lot_id
           JOIN inventory_items ii ON ii.id=il.item_id
           WHERE cr.production_order_id=? AND cr.status='committed'""",
        (production_order_id,),
    ).fetchall()
    for row in components:
        if completed:
            conn.execute(
                """UPDATE inventory_lots SET on_hand_qty=MAX(0,on_hand_qty-?),
                     reserved_qty=MAX(0,reserved_qty-?),version=version+1,updated_at=? WHERE id=?""",
                (row["quantity"], row["quantity"], now, row["inventory_lot_id"]),
            )
            status, movement, quantity, balance = "consumed", "issue", -float(row["quantity"]), max(
                0.0, float(row["on_hand_qty"]) - float(row["quantity"])
            )
        else:
            conn.execute(
                "UPDATE inventory_lots SET reserved_qty=MAX(0,reserved_qty-?),version=version+1,updated_at=? WHERE id=?",
                (row["quantity"], now, row["inventory_lot_id"]),
            )
            status, movement, quantity, balance = "released", "release", float(row["quantity"]), float(row["on_hand_qty"])
        conn.execute("UPDATE component_reservations SET status=?,updated_at=? WHERE id=?", (status, now, row["id"]))
        record_movement(
            conn, object_type="component_lot", object_key=f"{row['item_key']}:{row['lot_code']}",
            movement_type=movement, quantity=quantity, uom=row["uom"], balance_after=balance,
            actor=actor, source="production", production_order_id=production_order_id,
            scenario_id=row["scenario_id"],
        )
    remnants = conn.execute(
        """SELECT rr.*,mr.remnant_key FROM remnant_reservations rr
           JOIN material_remnants mr ON mr.id=rr.remnant_id
           WHERE rr.production_order_id=? AND rr.status='committed'""",
        (production_order_id,),
    ).fetchall()
    for row in remnants:
        status = "consumed" if completed else "released"
        remnant_status = "consumed" if completed else "available"
        conn.execute("UPDATE remnant_reservations SET status=?,updated_at=? WHERE id=?", (status, now, row["id"]))
        conn.execute(
            "UPDATE material_remnants SET status=?,version=version+1,updated_at=? WHERE id=?",
            (remnant_status, now, row["remnant_id"]),
        )
        record_movement(
            conn, object_type="remnant", object_key=row["remnant_key"],
            movement_type="issue" if completed else "release", quantity=-1 if completed else 1,
            uom="each", balance_after=0 if completed else 1, actor=actor, source="production",
            production_order_id=production_order_id, scenario_id=row["scenario_id"],
        )


def snapshot(conn: sqlite3.Connection, job_names: list[str] | None = None,
             *, sync: bool = True) -> dict:
    if sync:
        sync_requirements(conn)
    scope_orders = _orders(conn, job_names)
    display_orders = _orders(conn, job_names, display=True)
    scope_ids = [order["id"] for order in scope_orders]
    display_ids = [order["id"] for order in display_orders]
    components = component_rows(conn, display_ids, scope_ids)
    requirement_key = "required_qty" if scope_ids else "open_required_qty"
    feasibility_key = "feasible" if scope_ids else "open_feasible"
    required_components = [item for item in components if item[requirement_key] > 0]
    component_ready = not required_components or all(item[feasibility_key] for item in required_components)
    remnant_plan = plan_remnants(conn, scope_ids)
    remnants = [dict(row) for row in conn.execute(
        """SELECT mr.*,md.material_key,md.name material_name,
                  (SELECT COUNT(*) FROM remnant_reservations rr
                   WHERE rr.remnant_id=mr.id AND rr.status='committed') committed
           FROM material_remnants mr JOIN material_definitions md ON md.id=mr.material_id
           ORDER BY CASE mr.status WHEN 'available' THEN 0 WHEN 'reserved' THEN 1 ELSE 2 END,
                    mr.updated_at DESC"""
    ).fetchall()]
    issues = [dict(row) for row in conn.execute(
        """SELECT isi.*,j.job_name,p.part_name FROM inventory_sync_issues isi
           LEFT JOIN production_orders po ON po.id=isi.production_order_id
           LEFT JOIN jobs j ON j.id=po.job_id LEFT JOIN parts p ON p.id=isi.part_id
           WHERE isi.status='open' ORDER BY isi.updated_at DESC LIMIT 100"""
    ).fetchall()]
    suggestions = [{
        "item_key": item["item_key"], "name": item["name"], "uom": item["uom"],
        "quantity": item["suggested_order_qty"], "supplier": item["preferred_supplier"],
        "lead_time_days": item["lead_time_days"], "estimated_cost": item["estimated_order_cost"],
        "reason": "shortage" if item["open_shortage_qty"] > 0 else "reorder_policy",
        "confidence": "verified" if item["verified"] and item["stock_verified"] else "commissioning",
    } for item in components if item["suggested_order_qty"] > 0]
    return {
        "scope_orders": scope_orders, "display_orders": display_orders,
        "components": components, "component_ready": component_ready,
        "remnants": remnants, "remnant_plan": {
            "assignments": remnant_plan["assignments"],
            "candidate_count": remnant_plan["candidate_count"],
            "credited_area_m2": round(sum(remnant_plan["credits"].values()), 3),
        },
        "purchase_suggestions": suggestions, "issues": issues,
        "summary": {
            "component_items": len(components),
            "component_shortages": sum(1 for item in components if item["open_shortage_qty"] > 0),
            "available_remnants": sum(1 for item in remnants if item["status"] == "available" and item["verified"]),
            "remnant_area_m2": round(sum(float(item["usable_area_m2"]) for item in remnants
                                         if item["status"] == "available" and item["verified"]), 3),
            "open_sync_issues": len(issues),
        },
        "assumptions": {
            "edge_usage_factor": EDGE_USAGE_FACTOR,
            "minimum_remnant_dimension_mm": MIN_REMNANT_DIM_MM,
            "minimum_remnant_area_m2": MIN_REMNANT_AREA_M2,
            "remnant_allocation": "One verified rectangle per physical part; smallest fitting remnant first",
            "hardware_bom": "Manual until a real Cabinet Vision/ERP hardware source is commissioned",
        },
    }


def movements(conn: sqlite3.Connection, *, limit: int = 200) -> list[dict]:
    return [dict(row) for row in conn.execute(
        "SELECT * FROM inventory_movements ORDER BY id DESC LIMIT ?", (max(1, min(limit, 1000)),)
    ).fetchall()]
