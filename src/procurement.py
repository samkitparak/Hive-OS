"""Vendor-neutral procurement, receiving, and ERP exchange boundary."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import sqlite3
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import inventory
import resources


OBJECT_TYPES = {"component", "sheet"}
OPEN_PO_STATUSES = {"draft", "approved", "queued", "sent", "partially_received"}
CURRENCY = re.compile(r"^[A-Z]{3}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8-sig")).hexdigest()


def _supplier_key(value: str) -> str:
    key = re.sub(r"[^A-Z0-9]+", "_", str(value).strip().upper()).strip("_")
    if not key:
        raise ValueError("supplier_key cannot be empty")
    return key[:80]


def _component_key(value: str) -> str:
    key = re.sub(r"[^A-Z0-9]+", "_", str(value).strip().upper()).strip("_")
    if key and key[0].isdigit():
        key = f"ITEM_{key}"
    return key[:80]


def _material_key(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).strip()).upper()


def _currency(value: str | None, fallback: str = "INR") -> str:
    result = str(value or fallback).strip().upper()
    if not CURRENCY.fullmatch(result):
        raise ValueError("currency must be a three-letter ISO code")
    return result


def _truth(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "verified"}


def _parse_float(value: Any, *, default: float = 0) -> float:
    if value in (None, ""):
        return default
    return float(str(value).strip().replace(",", "."))


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _object(conn: sqlite3.Connection, object_type: str, object_key: str) -> dict:
    if object_type not in OBJECT_TYPES:
        raise ValueError("object_type must be component or sheet")
    if object_type == "component":
        key = _component_key(object_key)
        row = conn.execute(
            "SELECT id,item_key object_key,name,uom FROM inventory_items WHERE item_key=?", (key,)
        ).fetchone()
    else:
        key = _material_key(object_key)
        row = conn.execute(
            "SELECT id,material_key object_key,name,'sheet' uom FROM material_definitions WHERE material_key=?",
            (key,),
        ).fetchone()
    if not row:
        raise KeyError(f"Unknown {object_type} '{object_key}'")
    return {**dict(row), "object_type": object_type}


def _supplier_row(conn: sqlite3.Connection, supplier_key: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM procurement_suppliers WHERE supplier_key=?", (_supplier_key(supplier_key),)
    ).fetchone()
    if not row:
        raise KeyError(f"Supplier '{supplier_key}' not found")
    return row


def upsert_supplier(conn: sqlite3.Connection, supplier_key: str, payload: dict,
                    *, commit: bool = True) -> dict:
    key = _supplier_key(supplier_key)
    current = conn.execute(
        "SELECT * FROM procurement_suppliers WHERE supplier_key=?", (key,)
    ).fetchone()
    expected = payload.get("expected_version")
    if expected is not None and current and int(expected) != int(current["version"]):
        raise ValueError("Supplier changed; refresh before saving")
    name = str(payload.get("name") or (current["name"] if current else supplier_key)).strip()
    if not name:
        raise ValueError("Supplier name cannot be empty")
    currency = _currency(payload.get("currency"), current["currency"] if current else "INR")
    lead = int(payload.get("lead_time_days", current["lead_time_days"] if current else 0))
    if lead < 0 or lead > 3650:
        raise ValueError("lead_time_days must be between 0 and 3650")
    gln_value = payload.get("gln", current["gln"] if current else None)
    gln = str(gln_value).strip() if gln_value not in (None, "") else None
    if gln and (not gln.isdigit() or len(gln) != 13):
        raise ValueError("gln must contain exactly 13 digits")
    now = _now()
    conn.execute(
        """INSERT INTO procurement_suppliers
           (supplier_key,name,legal_name,currency,lead_time_days,gln,tax_id,email,
            external_system,source,active,verified,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,'manual',?,?,?,?)
           ON CONFLICT(supplier_key) DO UPDATE SET name=excluded.name,
             legal_name=excluded.legal_name,currency=excluded.currency,
             lead_time_days=excluded.lead_time_days,gln=excluded.gln,
             tax_id=excluded.tax_id,email=excluded.email,
             external_system=excluded.external_system,source=excluded.source,
             active=excluded.active,verified=excluded.verified,
             version=procurement_suppliers.version+1,updated_at=excluded.updated_at""",
        (key, name, payload.get("legal_name", current["legal_name"] if current else None),
         currency, lead, gln, payload.get("tax_id", current["tax_id"] if current else None),
         payload.get("email", current["email"] if current else None),
         payload.get("external_system", current["external_system"] if current else None),
         int(bool(payload.get("active", current["active"] if current else True))),
         int(bool(payload.get("verified", current["verified"] if current else False))), now, now),
    )
    if commit:
        conn.commit()
    return dict(_supplier_row(conn, key))


def mapping_row(conn: sqlite3.Connection, mapping_id: int) -> dict:
    row = conn.execute(
        """SELECT pim.*,ps.supplier_key,ps.name supplier_name,ps.lead_time_days,
                  ps.active supplier_active,ps.verified supplier_verified
           FROM procurement_item_mappings pim JOIN procurement_suppliers ps ON ps.id=pim.supplier_id
           WHERE pim.id=?""", (mapping_id,)
    ).fetchone()
    if not row:
        raise KeyError(f"Procurement mapping {mapping_id} not found")
    return dict(row)


def upsert_mapping(conn: sqlite3.Connection, supplier_key: str, object_type: str,
                   object_key: str, payload: dict, *, commit: bool = True) -> dict:
    supplier = _supplier_row(conn, supplier_key)
    item = _object(conn, object_type, object_key)
    current = conn.execute(
        """SELECT * FROM procurement_item_mappings
           WHERE supplier_id=? AND object_type=? AND object_key=?""",
        (supplier["id"], object_type, item["object_key"]),
    ).fetchone()
    expected = payload.get("expected_version")
    if expected is not None and current and int(expected) != int(current["version"]):
        raise ValueError("Supplier item mapping changed; refresh before saving")
    sku = str(payload.get("supplier_sku") or (current["supplier_sku"] if current else "")).strip()
    if not sku:
        raise ValueError("supplier_sku cannot be empty")
    purchase_uom = str(payload.get("purchase_uom") or
                       (current["purchase_uom"] if current else item["uom"])).strip()
    conversion = float(payload.get("conversion_factor", current["conversion_factor"] if current else 1))
    multiple = float(payload.get("order_multiple", current["order_multiple"] if current else 1))
    minimum = float(payload.get("min_order_qty", current["min_order_qty"] if current else 0))
    price = payload.get("unit_price", current["unit_price"] if current else None)
    if conversion <= 0 or multiple <= 0 or minimum < 0 or (price is not None and float(price) < 0):
        raise ValueError("Mapping conversion, order multiple, minimum, or price is invalid")
    currency = _currency(payload.get("currency"), current["currency"] if current else supplier["currency"])
    preferred = int(bool(payload.get("preferred", current["preferred"] if current else False)))
    if preferred:
        conn.execute(
            "UPDATE procurement_item_mappings SET preferred=0,version=version+1,updated_at=? WHERE object_type=? AND object_key=?",
            (_now(), object_type, item["object_key"]),
        )
    now = _now()
    conn.execute(
        """INSERT INTO procurement_item_mappings
           (supplier_id,object_type,object_key,supplier_sku,gtin,purchase_uom,
            conversion_factor,order_multiple,min_order_qty,unit_price,currency,
            preferred,source,verified,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(supplier_id,object_type,object_key) DO UPDATE SET
             supplier_sku=excluded.supplier_sku,gtin=excluded.gtin,
             purchase_uom=excluded.purchase_uom,conversion_factor=excluded.conversion_factor,
             order_multiple=excluded.order_multiple,min_order_qty=excluded.min_order_qty,
             unit_price=excluded.unit_price,currency=excluded.currency,
             preferred=excluded.preferred,source=excluded.source,verified=excluded.verified,
             version=procurement_item_mappings.version+1,updated_at=excluded.updated_at""",
        (supplier["id"], object_type, item["object_key"], sku, payload.get("gtin"),
         purchase_uom, conversion, multiple, minimum,
         float(price) if price is not None else None, currency, preferred,
         payload.get("source", "manual"), int(bool(payload.get("verified", False))), now, now),
    )
    result = conn.execute(
        """SELECT id FROM procurement_item_mappings
           WHERE supplier_id=? AND object_type=? AND object_key=?""",
        (supplier["id"], object_type, item["object_key"]),
    ).fetchone()
    if commit:
        conn.commit()
    return mapping_row(conn, result["id"])


def _preferred_mappings(conn: sqlite3.Connection) -> dict[tuple[str, str], dict]:
    rows = conn.execute(
        """SELECT pim.*,ps.supplier_key,ps.name supplier_name,ps.lead_time_days,
                  ps.active supplier_active,ps.verified supplier_verified
           FROM procurement_item_mappings pim JOIN procurement_suppliers ps ON ps.id=pim.supplier_id
           WHERE pim.preferred=1 AND ps.active=1"""
    ).fetchall()
    return {(row["object_type"], row["object_key"]): dict(row) for row in rows}


def _open_inbound(conn: sqlite3.Connection) -> dict[tuple[str, str], float]:
    result: dict[tuple[str, str], float] = defaultdict(float)
    rows = conn.execute(
        """SELECT pol.object_type,pol.object_key,pol.ordered_qty,pol.received_qty,
                  pol.rejected_qty,pol.conversion_factor
           FROM purchase_order_lines pol JOIN purchase_orders po ON po.id=pol.purchase_order_id
           WHERE po.status IN ('draft','approved','queued','sent','partially_received')
             AND pol.status IN ('open','partial')"""
    ).fetchall()
    for row in rows:
        remaining = max(0.0, float(row["ordered_qty"]) - float(row["received_qty"]) -
                        float(row["rejected_qty"]))
        result[(row["object_type"], row["object_key"])] += remaining * float(row["conversion_factor"])
    return dict(result)


def _need_by(conn: sqlite3.Connection, object_type: str, object_key: str) -> str | None:
    if object_type == "component":
        row = conn.execute(
            """SELECT MIN(po.due_at) due_at FROM component_requirements cr
               JOIN inventory_items ii ON ii.id=cr.item_id
               JOIN production_orders po ON po.id=cr.production_order_id
               WHERE ii.item_key=? AND cr.required_qty>0
                 AND po.status NOT IN ('completed','cancelled') AND po.due_at IS NOT NULL""",
            (object_key,),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT MIN(po.due_at) due_at FROM material_requirements mr
               JOIN material_definitions md ON md.id=mr.material_id
               JOIN production_orders po ON po.id=mr.production_order_id
               WHERE md.material_key=? AND mr.required_area_m2>0
                 AND po.status NOT IN ('completed','cancelled') AND po.due_at IS NOT NULL""",
            (object_key,),
        ).fetchone()
    return row["due_at"] if row else None


def _rounded_purchase_qty(internal_qty: float, mapping: dict | None) -> tuple[float | None, float]:
    if internal_qty <= 1e-9:
        return 0.0, 0.0
    if not mapping:
        return None, internal_qty
    conversion = float(mapping["conversion_factor"])
    multiple = float(mapping["order_multiple"])
    raw = max(internal_qty / conversion, float(mapping["min_order_qty"]))
    purchase = math.ceil(raw / multiple - 1e-12) * multiple
    return round(purchase, 6), round(purchase * conversion, 6)


def recommendations(conn: sqlite3.Connection, job_names: list[str] | None = None) -> list[dict]:
    resource = resources.snapshot(conn, job_names)
    mappings = _preferred_mappings(conn)
    inbound = _open_inbound(conn)
    result = []
    applicable = bool(resource["applicable"])

    def add(object_type: str, object_key: str, name: str, uom: str, required: float,
            shortage: float, reorder_target: float) -> None:
        mapping = mappings.get((object_type, object_key))
        inbound_qty = float(inbound.get((object_type, object_key), 0))
        uncovered = max(0.0, reorder_target - inbound_qty)
        purchase_qty, covered_internal = _rounded_purchase_qty(uncovered, mapping)
        need_by = _need_by(conn, object_type, object_key)
        projected = None
        at_risk = False
        if mapping:
            projected_dt = datetime.now(timezone.utc) + timedelta(days=int(mapping["lead_time_days"]))
            projected = projected_dt.isoformat()
            due = _parse_time(need_by)
            at_risk = bool(due and projected_dt > due)
        result.append({
            "object_type": object_type, "object_key": object_key, "name": name,
            "internal_uom": uom, "required_qty": round(required, 3),
            "shortage_qty": round(shortage, 3), "reorder_target_qty": round(reorder_target, 3),
            "open_inbound_qty": round(inbound_qty, 3), "uncovered_qty": round(uncovered, 3),
            "recommended_purchase_qty": purchase_qty,
            "recommended_internal_qty": round(covered_internal, 3),
            "need_by_at": need_by, "projected_arrival_at": projected, "at_risk": at_risk,
            "mapping": mapping,
            "status": "covered_by_inbound" if uncovered <= 1e-9 else (
                "ready_to_draft" if mapping else "mapping_required"
            ),
        })

    for item in resource["warehouse"]["components"]:
        required = float(item["required_qty"] if applicable else item["open_required_qty"])
        shortage = float(item["shortage_qty"] if applicable else item["open_shortage_qty"])
        target = shortage if job_names else float(item["suggested_order_qty"])
        if target > 1e-9 or inbound.get(("component", item["item_key"]), 0) > 0:
            add("component", item["item_key"], item["name"], item["uom"], required, shortage, target)
    for item in resource["materials"]:
        required = float(item["required_sheets"] if applicable else item["open_required_sheets"])
        shortage = float(item["shortage_sheets"] if applicable else item["open_shortage_sheets"])
        if shortage > 1e-9 or inbound.get(("sheet", item["material_key"]), 0) > 0:
            add("sheet", item["material_key"], item["name"], "sheet", required, shortage, shortage)
    return sorted(result, key=lambda item: (not item["at_risk"], item["status"], item["name"]))


def _new_po_number() -> str:
    return f"HPO-{datetime.now(timezone.utc):%y%m%d}-{uuid.uuid4().hex[:6].upper()}"


def order_detail(conn: sqlite3.Connection, order_id: int) -> dict:
    row = conn.execute(
        """SELECT po.*,ps.supplier_key,ps.name supplier_name,ps.verified supplier_verified
           FROM purchase_orders po JOIN procurement_suppliers ps ON ps.id=po.supplier_id
           WHERE po.id=?""", (order_id,)
    ).fetchone()
    if not row:
        raise KeyError(f"Purchase order {order_id} not found")
    result = dict(row)
    lines = [dict(line) for line in conn.execute(
        "SELECT * FROM purchase_order_lines WHERE purchase_order_id=? ORDER BY line_number",
        (order_id,),
    ).fetchall()]
    for line in lines:
        line["remaining_qty"] = round(max(
            0.0, float(line["ordered_qty"]) - float(line["received_qty"]) - float(line["rejected_qty"])
        ), 6)
        line["line_total"] = round(float(line["ordered_qty"]) * float(line["unit_price"]), 2) \
            if line["unit_price"] is not None else None
    result["lines"] = lines
    priced = [line["line_total"] for line in lines if line["line_total"] is not None]
    result["total"] = round(sum(priced), 2) if len(priced) == len(lines) else None
    return result


def create_order(conn: sqlite3.Connection, payload: dict, *, commit: bool = True) -> dict:
    supplier = _supplier_row(conn, payload["supplier_key"])
    if not supplier["active"]:
        raise ValueError("Supplier is inactive")
    lines = payload.get("lines") or []
    if not lines:
        raise ValueError("Purchase order requires at least one line")
    currency = _currency(payload.get("currency"), supplier["currency"])
    seen: set[tuple[str, str]] = set()
    prepared = []
    for number, requested in enumerate(lines, start=1):
        item = _object(conn, requested["object_type"], requested["object_key"])
        identity = (item["object_type"], item["object_key"])
        if identity in seen:
            raise ValueError(f"Duplicate purchase-order line for {item['object_key']}")
        seen.add(identity)
        mapping = conn.execute(
            """SELECT * FROM procurement_item_mappings
               WHERE supplier_id=? AND object_type=? AND object_key=?""",
            (supplier["id"], item["object_type"], item["object_key"]),
        ).fetchone()
        if not mapping:
            raise ValueError(f"Map {item['object_key']} to {supplier['name']} before drafting")
        quantity = float(requested.get("ordered_qty") or 0)
        if quantity <= 0:
            raise ValueError("Purchase-order quantities must be positive")
        line_currency = _currency(requested.get("currency"), mapping["currency"])
        if line_currency != currency:
            raise ValueError("All purchase-order lines must use the order currency")
        price = requested.get("unit_price", mapping["unit_price"])
        prepared.append((number, requested, item, mapping, quantity, line_currency, price))
    now = _now()
    cursor = conn.execute(
        """INSERT INTO purchase_orders
           (po_number,supplier_id,status,currency,expected_at,external_id,source,notes,
            created_by,created_at,updated_at)
           VALUES (?,?,'draft',?,?,?,?,?,?,?,?)""",
        (payload.get("po_number") or _new_po_number(), supplier["id"], currency,
         payload.get("expected_at") or (datetime.now(timezone.utc) +
                                         timedelta(days=int(supplier["lead_time_days"]))).isoformat(),
         payload.get("external_id"), payload.get("source", "manual"), payload.get("notes"),
         payload.get("actor", "planner"), now, now),
    )
    for number, requested, item, mapping, quantity, line_currency, price in prepared:
        conn.execute(
            """INSERT INTO purchase_order_lines
               (purchase_order_id,line_number,mapping_id,object_type,object_key,item_name,
                supplier_sku,internal_uom,purchase_uom,conversion_factor,ordered_qty,
                unit_price,currency,need_by_at,status,notes,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'open',?,?,?)""",
            (cursor.lastrowid, number, mapping["id"], item["object_type"], item["object_key"],
             item["name"], mapping["supplier_sku"], item["uom"], mapping["purchase_uom"],
             mapping["conversion_factor"], quantity,
             float(price) if price is not None else None, line_currency,
             requested.get("need_by_at"), requested.get("notes"), now, now),
        )
    if commit:
        conn.commit()
    return order_detail(conn, cursor.lastrowid)


def draft_recommendations(conn: sqlite3.Connection, payload: dict) -> dict:
    supplier_filter = payload.get("supplier_key")
    wanted = set(payload.get("object_keys") or [])
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in recommendations(conn):
        mapping = item["mapping"]
        if item["status"] != "ready_to_draft" or not mapping or not item["recommended_purchase_qty"]:
            continue
        if supplier_filter and mapping["supplier_key"] != _supplier_key(supplier_filter):
            continue
        if wanted and item["object_key"] not in wanted:
            continue
        grouped[mapping["supplier_key"]].append({
            "object_type": item["object_type"], "object_key": item["object_key"],
            "ordered_qty": item["recommended_purchase_qty"], "need_by_at": item["need_by_at"],
        })
    if not grouped:
        raise ValueError("No mapped uncovered recommendations are available to draft")
    created = []
    try:
        for supplier_key, lines in grouped.items():
            created.append(create_order(conn, {
                "supplier_key": supplier_key, "lines": lines,
                "source": "shortage_recommendation", "actor": payload.get("actor", "planner"),
                "notes": payload.get("notes"),
            }, commit=False))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"orders": created, "count": len(created)}


def canonical_order(conn: sqlite3.Connection, order_id: int) -> dict:
    order = order_detail(conn, order_id)
    return {
        "document_type": "Order", "profile": "hive-procurement-1.0",
        "id": order["po_number"], "issue_time": order["approved_at"] or order["created_at"],
        "status": order["status"], "currency": order["currency"],
        "expected_delivery": order["expected_at"],
        "seller": {"id": order["supplier_key"], "name": order["supplier_name"]},
        "lines": [{
            "id": line["line_number"], "hive_item_type": line["object_type"],
            "hive_item_id": line["object_key"], "seller_item_id": line["supplier_sku"],
            "name": line["item_name"], "quantity": line["ordered_qty"],
            "unit": line["purchase_uom"], "internal_unit": line["internal_uom"],
            "internal_units_per_order_unit": line["conversion_factor"],
            "unit_price": line["unit_price"], "need_by": line["need_by_at"],
        } for line in order["lines"]],
    }


def decide_order(conn: sqlite3.Connection, order_id: int, payload: dict) -> dict:
    order = order_detail(conn, order_id)
    expected = payload.get("expected_version")
    if expected is not None and int(expected) != int(order["version"]):
        raise ValueError("Purchase order changed; refresh before saving")
    action = payload["action"]
    now = _now()
    if action == "approve":
        if order["status"] != "draft":
            raise ValueError("Only draft purchase orders can be approved")
        if not order["supplier_verified"]:
            raise ValueError("Verify the supplier before approving this purchase order")
        mapping_ids = [line["mapping_id"] for line in order["lines"]]
        if not mapping_ids or any(not mapping_row(conn, mapping_id)["verified"] for mapping_id in mapping_ids):
            raise ValueError("Verify every supplier item mapping before approval")
        conn.execute(
            """UPDATE purchase_orders SET status='approved',approved_by=?,approved_at=?,
                 version=version+1,updated_at=? WHERE id=?""",
            (payload.get("actor", "planner"), now, now, order_id),
        )
    elif action == "queue":
        if order["status"] != "approved":
            raise ValueError("Approve the purchase order before queueing it")
        document = canonical_order(conn, order_id)
        encoded = _json(document)
        conn.execute(
            """INSERT INTO procurement_outbox
               (document_type,object_type,object_key,payload_json,payload_sha256,status,
                created_at,updated_at) VALUES ('Order','purchase_order',?,?,?,'pending',?,?)
               ON CONFLICT(document_type,object_type,object_key) DO UPDATE SET
                 payload_json=excluded.payload_json,payload_sha256=excluded.payload_sha256,
                 status='pending',last_error=NULL,updated_at=excluded.updated_at""",
            (order["po_number"], encoded, _hash(encoded), now, now),
        )
        conn.execute(
            "UPDATE purchase_orders SET status='queued',queued_at=?,version=version+1,updated_at=? WHERE id=?",
            (now, now, order_id),
        )
    elif action == "cancel":
        if order["status"] in {"received", "received_with_exceptions", "cancelled"}:
            raise ValueError("This purchase order can no longer be cancelled")
        if any(float(line["received_qty"]) > 0 for line in order["lines"]):
            raise ValueError("A received purchase order cannot be cancelled")
        conn.execute(
            "UPDATE purchase_orders SET status='cancelled',closed_at=?,version=version+1,updated_at=? WHERE id=?",
            (now, now, order_id),
        )
        conn.execute(
            "UPDATE purchase_order_lines SET status='cancelled',updated_at=? WHERE purchase_order_id=?",
            (now, order_id),
        )
    else:
        raise ValueError("Unsupported purchase-order action")
    conn.commit()
    return order_detail(conn, order_id)


def outbox(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    where, params = ("WHERE status=?", (status,)) if status else ("", ())
    rows = conn.execute(
        f"SELECT * FROM procurement_outbox {where} ORDER BY id DESC LIMIT 200", params
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        result.append(item)
    return result


def acknowledge_outbox(conn: sqlite3.Connection, outbox_id: int, payload: dict) -> dict:
    row = conn.execute("SELECT * FROM procurement_outbox WHERE id=?", (outbox_id,)).fetchone()
    if not row:
        raise KeyError(f"Outbox document {outbox_id} not found")
    now = _now()
    success = bool(payload["success"])
    conn.execute(
        """UPDATE procurement_outbox SET status=?,attempts=attempts+1,external_id=?,
             last_error=?,delivered_at=?,updated_at=? WHERE id=?""",
        ("delivered" if success else "failed", payload.get("external_id"),
         None if success else payload.get("error") or "Delivery failed",
         now if success else None, now, outbox_id),
    )
    if success and row["object_type"] == "purchase_order":
        conn.execute(
            """UPDATE purchase_orders SET status=CASE WHEN status='queued' THEN 'sent' ELSE status END,
                 external_id=COALESCE(?,external_id),sent_at=COALESCE(sent_at,?),
                 version=version+1,updated_at=? WHERE po_number=?""",
            (payload.get("external_id"), now, now, row["object_key"]),
        )
    conn.commit()
    updated = conn.execute("SELECT * FROM procurement_outbox WHERE id=?", (outbox_id,)).fetchone()
    result = dict(updated)
    result["payload"] = json.loads(result.pop("payload_json"))
    return result


def order_csv(conn: sqlite3.Connection, order_id: int) -> str:
    order = order_detail(conn, order_id)
    output = io.StringIO(newline="")
    fields = ["po_number", "supplier_key", "supplier_name", "currency", "expected_at",
              "line_number", "object_type", "hive_item_key", "supplier_sku", "item_name",
              "ordered_qty", "purchase_uom", "conversion_factor", "internal_uom",
              "unit_price", "need_by_at"]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for line in order["lines"]:
        writer.writerow({
            "po_number": order["po_number"], "supplier_key": order["supplier_key"],
            "supplier_name": order["supplier_name"], "currency": order["currency"],
            "expected_at": order["expected_at"], "line_number": line["line_number"],
            "object_type": line["object_type"], "hive_item_key": line["object_key"],
            "supplier_sku": line["supplier_sku"], "item_name": line["item_name"],
            "ordered_qty": line["ordered_qty"], "purchase_uom": line["purchase_uom"],
            "conversion_factor": line["conversion_factor"], "internal_uom": line["internal_uom"],
            "unit_price": line["unit_price"], "need_by_at": line["need_by_at"],
        })
    return output.getvalue()


def receipt_detail(conn: sqlite3.Connection, receipt_id: int) -> dict:
    row = conn.execute(
        """SELECT gr.*,po.po_number,ps.supplier_key,ps.name supplier_name
           FROM goods_receipts gr JOIN purchase_orders po ON po.id=gr.purchase_order_id
           JOIN procurement_suppliers ps ON ps.id=gr.supplier_id WHERE gr.id=?""",
        (receipt_id,),
    ).fetchone()
    if not row:
        raise KeyError(f"Goods receipt {receipt_id} not found")
    result = dict(row)
    result["lines"] = [dict(item) for item in conn.execute(
        """SELECT grl.*,pol.line_number,pol.object_type,pol.object_key,pol.item_name
           FROM goods_receipt_lines grl JOIN purchase_order_lines pol ON pol.id=grl.purchase_order_line_id
           WHERE grl.goods_receipt_id=? ORDER BY pol.line_number""", (receipt_id,)
    ).fetchall()]
    return result


def _receive_component(conn: sqlite3.Connection, line: sqlite3.Row, lot_code: str,
                       internal_qty: float, location: str | None, verified: bool,
                       actor: str, receipt_key: str) -> None:
    item = conn.execute("SELECT * FROM inventory_items WHERE item_key=?", (line["object_key"],)).fetchone()
    if not item:
        raise KeyError(f"Inventory item '{line['object_key']}' not found")
    current = conn.execute(
        "SELECT * FROM inventory_lots WHERE item_id=? AND lot_code=?", (item["id"], lot_code)
    ).fetchone()
    previous = float(current["on_hand_qty"] if current else 0)
    balance = previous + internal_qty
    lot_verified = int(verified and (bool(current["verified"]) if current else True))
    now = _now()
    conn.execute(
        """INSERT INTO inventory_lots
           (item_id,lot_code,location,status,on_hand_qty,reserved_qty,source,verified,
            version,received_at,updated_at) VALUES (?,?,?,'available',?,0,'procurement',?,1,?,?)
           ON CONFLICT(item_id,lot_code) DO UPDATE SET
             location=COALESCE(excluded.location,inventory_lots.location),status='available',
             on_hand_qty=inventory_lots.on_hand_qty+excluded.on_hand_qty,source='procurement',
             verified=excluded.verified,version=inventory_lots.version+1,
             received_at=excluded.received_at,updated_at=excluded.updated_at""",
        (item["id"], lot_code, location, internal_qty, lot_verified, now, now),
    )
    inventory.record_movement(
        conn, object_type="component_lot", object_key=f"{line['object_key']}:{lot_code}",
        movement_type="receipt", quantity=internal_qty, uom=line["internal_uom"],
        balance_after=balance, actor=actor, source="procurement",
        idempotency_key=f"goods-receipt:{receipt_key}:{line['id']}",
        notes=f"PO {line['po_number']}",
    )


def _receive_sheet(conn: sqlite3.Connection, line: sqlite3.Row, lot_code: str,
                   internal_qty: float, location: str | None, verified: bool,
                   actor: str, receipt_key: str) -> None:
    material = conn.execute(
        "SELECT * FROM material_definitions WHERE material_key=?", (line["object_key"],)
    ).fetchone()
    if not material:
        raise KeyError(f"Sheet material '{line['object_key']}' not found")
    current = conn.execute(
        "SELECT * FROM material_lots WHERE material_id=? AND lot_code=?", (material["id"], lot_code)
    ).fetchone()
    previous = float(current["on_hand_sheets"] if current else 0)
    balance = previous + internal_qty
    lot_verified = int(verified and (bool(current["verified"]) if current else True))
    now = _now()
    conn.execute(
        """INSERT INTO material_lots
           (material_id,lot_code,location,status,on_hand_sheets,reserved_sheets,source,verified,updated_at)
           VALUES (?,?,?,'available',?,0,'procurement',?,?)
           ON CONFLICT(material_id,lot_code) DO UPDATE SET
             location=COALESCE(excluded.location,material_lots.location),status='available',
             on_hand_sheets=material_lots.on_hand_sheets+excluded.on_hand_sheets,
             source='procurement',verified=excluded.verified,updated_at=excluded.updated_at""",
        (material["id"], lot_code, location, internal_qty, lot_verified, now),
    )
    inventory.record_movement(
        conn, object_type="sheet_lot", object_key=f"{line['object_key']}:{lot_code}",
        movement_type="receipt", quantity=internal_qty, uom="sheet", balance_after=balance,
        actor=actor, source="procurement",
        idempotency_key=f"goods-receipt:{receipt_key}:{line['id']}",
        notes=f"PO {line['po_number']}",
    )


def receive_order(conn: sqlite3.Connection, payload: dict, *, commit: bool = True) -> dict:
    receipt_key = str(payload["receipt_key"]).strip().upper()
    if not receipt_key:
        raise ValueError("receipt_key cannot be empty")
    duplicate = conn.execute("SELECT id FROM goods_receipts WHERE receipt_key=?", (receipt_key,)).fetchone()
    if duplicate:
        result = receipt_detail(conn, duplicate["id"])
        result["duplicate"] = True
        return result
    order = order_detail(conn, int(payload["purchase_order_id"]))
    if order["status"] not in {"approved", "queued", "sent", "partially_received"}:
        raise ValueError("Approve the purchase order before receiving it")
    requested = payload.get("lines") or []
    if not requested:
        raise ValueError("Goods receipt requires at least one line")
    resolved = []
    used: set[int] = set()
    for item in requested:
        line = next((candidate for candidate in order["lines"] if candidate["id"] == item.get("po_line_id") or
                     candidate["line_number"] == item.get("line_number")), None)
        if not line:
            raise KeyError("Purchase-order line was not found")
        if line["id"] in used:
            raise ValueError("A receipt cannot repeat the same purchase-order line")
        used.add(line["id"])
        accepted = float(item.get("accepted_qty", 0))
        rejected = float(item.get("rejected_qty", 0))
        if accepted < 0 or rejected < 0 or accepted + rejected <= 0:
            raise ValueError("Receipt quantities must be non-negative and include a received quantity")
        if accepted + rejected > float(line["remaining_qty"]) + 1e-9:
            raise ValueError("Receipt quantity exceeds the open purchase-order quantity")
        if rejected > 0 and not str(item.get("rejection_reason") or "").strip():
            raise ValueError("A rejection reason is required for rejected quantity")
        lot_code = str(item.get("lot_code") or receipt_key).strip().upper()
        if not lot_code:
            raise ValueError("lot_code cannot be empty")
        resolved.append((line, item, accepted, rejected, lot_code))
    now = _now()
    received_at = payload.get("received_at") or now
    cursor = conn.execute(
        """INSERT INTO goods_receipts
           (receipt_key,purchase_order_id,supplier_id,external_receipt_id,source_hash,
            received_at,location,status,source,verified,actor,notes,created_at)
           VALUES (?,?,?,?,?,?,?,'posted',?,?,?,?,?)""",
        (receipt_key, order["id"], order["supplier_id"], payload.get("external_receipt_id"),
         payload.get("source_hash"), received_at, payload.get("location"),
         payload.get("source", "manual"), int(bool(payload.get("verified", False))),
         payload.get("actor", "receiver"), payload.get("notes"), now),
    )
    any_rejected = False
    for line_data, item, accepted, rejected, lot_code in resolved:
        internal_qty = accepted * float(line_data["conversion_factor"])
        location = item.get("location") or payload.get("location")
        conn.execute(
            """INSERT INTO goods_receipt_lines
               (goods_receipt_id,purchase_order_line_id,lot_code,accepted_qty,rejected_qty,
                purchase_uom,conversion_factor,accepted_internal_qty,rejection_reason,
                location,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (cursor.lastrowid, line_data["id"], lot_code, accepted, rejected,
             line_data["purchase_uom"], line_data["conversion_factor"], internal_qty,
             item.get("rejection_reason"), location, now),
        )
        if accepted > 0:
            db_line = conn.execute(
                """SELECT pol.*,po.po_number FROM purchase_order_lines pol
                   JOIN purchase_orders po ON po.id=pol.purchase_order_id WHERE pol.id=?""",
                (line_data["id"],),
            ).fetchone()
            receiver = _receive_component if line_data["object_type"] == "component" else _receive_sheet
            receiver(conn, db_line, lot_code, internal_qty, location,
                     bool(payload.get("verified", False)), payload.get("actor", "receiver"), receipt_key)
        total = float(line_data["received_qty"]) + float(line_data["rejected_qty"]) + accepted + rejected
        status = "complete" if total + 1e-9 >= float(line_data["ordered_qty"]) else "partial"
        conn.execute(
            """UPDATE purchase_order_lines SET received_qty=received_qty+?,
                 rejected_qty=rejected_qty+?,status=?,updated_at=? WHERE id=?""",
            (accepted, rejected, status, now, line_data["id"]),
        )
        any_rejected = any_rejected or rejected > 0
    open_lines = conn.execute(
        "SELECT COUNT(*) count FROM purchase_order_lines WHERE purchase_order_id=? AND status IN ('open','partial')",
        (order["id"],),
    ).fetchone()["count"]
    if open_lines:
        po_status, closed = "partially_received", None
    else:
        rejects = conn.execute(
            "SELECT COALESCE(SUM(rejected_qty),0) value FROM purchase_order_lines WHERE purchase_order_id=?",
            (order["id"],),
        ).fetchone()["value"]
        po_status, closed = ("received_with_exceptions" if rejects else "received"), now
    conn.execute(
        "UPDATE purchase_orders SET status=?,closed_at=?,version=version+1,updated_at=? WHERE id=?",
        (po_status, closed, now, order["id"]),
    )
    if any_rejected:
        conn.execute("UPDATE goods_receipts SET status='posted_with_exceptions' WHERE id=?", (cursor.lastrowid,))
    if commit:
        conn.commit()
    return receipt_detail(conn, cursor.lastrowid)


def _supplier_metrics(conn: sqlite3.Connection) -> dict[int, dict]:
    result = {}
    suppliers = conn.execute("SELECT id,lead_time_days FROM procurement_suppliers").fetchall()
    for supplier in suppliers:
        rows = conn.execute(
            """SELECT gr.received_at,po.approved_at,po.expected_at,
                      COALESCE(SUM(grl.accepted_qty),0) accepted,
                      COALESCE(SUM(grl.rejected_qty),0) rejected
               FROM goods_receipts gr JOIN purchase_orders po ON po.id=gr.purchase_order_id
               JOIN goods_receipt_lines grl ON grl.goods_receipt_id=gr.id
               WHERE gr.supplier_id=? GROUP BY gr.id ORDER BY gr.received_at""",
            (supplier["id"],),
        ).fetchall()
        lead_samples = []
        on_time = 0
        accepted = rejected = 0.0
        for row in rows:
            received, approved, expected = (_parse_time(row["received_at"]),
                                            _parse_time(row["approved_at"]),
                                            _parse_time(row["expected_at"]))
            if received and approved:
                lead_samples.append(max(0.0, (received - approved).total_seconds() / 86400))
            if received and expected and received <= expected:
                on_time += 1
            accepted += float(row["accepted"])
            rejected += float(row["rejected"])
        observed = round(sum(lead_samples) / len(lead_samples), 2) if lead_samples else None
        configured = int(supplier["lead_time_days"])
        result[supplier["id"]] = {
            "receipt_samples": len(rows), "lead_time_samples": len(lead_samples),
            "observed_lead_days": observed,
            "on_time_rate": round(on_time / len(rows), 3) if rows else None,
            "rejection_rate": round(rejected / (accepted + rejected), 4) if accepted + rejected else None,
            "recommended_lead_days": math.ceil(observed) if len(lead_samples) >= 5 and
            observed is not None and abs(observed - configured) >= 2 else None,
        }
    return result


def _record_exchange(conn: sqlite3.Connection, *, document_type: str, source_sha: str,
                     file_name: str | None, mode: str, status: str, seen: int,
                     accepted: int, rejected: int, imported: int, actor: str,
                     summary: dict, issues: list[dict]) -> int:
    cursor = conn.execute(
        """INSERT INTO procurement_exchange_runs
           (direction,document_type,source_sha256,file_name,mode,status,records_seen,
            records_accepted,records_rejected,records_imported,summary_json,actor,created_at)
           VALUES ('import',?,?,?,?,?,?,?,?,?,?,?,?)""",
        (document_type, source_sha, file_name, mode, status, seen, accepted, rejected,
         imported, _json(summary), actor, _now()),
    )
    for issue in issues[:200]:
        conn.execute(
            """INSERT INTO procurement_exchange_issues
               (run_id,record_index,field_key,code,detail) VALUES (?,?,?,?,?)""",
            (cursor.lastrowid, issue.get("record_index"), issue.get("field_key"),
             issue["code"], issue["detail"]),
        )
    return cursor.lastrowid


def _csv_records(csv_text: str) -> tuple[list[str], list[dict]]:
    reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))
    fields = [str(field or "").strip() for field in (reader.fieldnames or [])]
    records = [{str(key or "").strip(): str(value or "").strip() for key, value in row.items()}
               for row in reader]
    return fields, records


def _validate_catalog(conn: sqlite3.Connection, records: list[dict], approve: bool) -> tuple[list[dict], list[dict]]:
    normalized, issues = [], []
    required = ("supplier_key", "supplier_name", "object_type", "object_key", "supplier_sku")
    for index, row in enumerate(records):
        missing = [field for field in required if not row.get(field)]
        if missing:
            issues.append({"record_index": index, "field_key": missing[0],
                           "code": "required_value_missing", "detail": ", ".join(missing)})
            continue
        try:
            item = _object(conn, row["object_type"], row["object_key"])
            normalized_row = {
                "supplier_key": _supplier_key(row["supplier_key"]), "supplier_name": row["supplier_name"],
                "object_type": item["object_type"], "object_key": item["object_key"],
                "supplier_sku": row["supplier_sku"], "purchase_uom": row.get("purchase_uom") or item["uom"],
                "conversion_factor": _parse_float(row.get("conversion_factor"), default=1),
                "order_multiple": _parse_float(row.get("order_multiple"), default=1),
                "min_order_qty": _parse_float(row.get("min_order_qty")),
                "unit_price": _parse_float(row.get("unit_price")) if row.get("unit_price") else None,
                "currency": _currency(row.get("currency")), "lead_time_days": int(_parse_float(row.get("lead_time_days"))),
                "gln": row.get("gln") or None, "gtin": row.get("gtin") or None,
                "preferred": _truth(row.get("preferred")), "verified": bool(approve),
            }
            if normalized_row["conversion_factor"] <= 0 or normalized_row["order_multiple"] <= 0:
                raise ValueError("conversion_factor and order_multiple must be positive")
            if normalized_row["min_order_qty"] < 0 or normalized_row["lead_time_days"] < 0:
                raise ValueError("minimum order and lead time cannot be negative")
            if normalized_row["unit_price"] is not None and normalized_row["unit_price"] < 0:
                raise ValueError("unit_price cannot be negative")
            if normalized_row["gln"] and (not normalized_row["gln"].isdigit() or
                                           len(normalized_row["gln"]) != 13):
                raise ValueError("gln must contain exactly 13 digits")
            normalized.append(normalized_row)
        except (KeyError, TypeError, ValueError) as error:
            issues.append({"record_index": index, "field_key": None,
                           "code": "validation_failed", "detail": str(error)})
    return normalized, issues


def _validate_receipt_csv(conn: sqlite3.Connection, records: list[dict]) -> tuple[dict | None, list[dict]]:
    issues = []
    required = ("receipt_key", "po_number", "line_number", "lot_code")
    if not records:
        return None, [{"record_index": None, "field_key": None,
                       "code": "empty_file", "detail": "CSV contains no receipt rows"}]
    receipt_keys = {row.get("receipt_key") for row in records}
    po_numbers = {row.get("po_number") for row in records}
    if len(receipt_keys) != 1 or len(po_numbers) != 1:
        issues.append({"record_index": None, "field_key": None, "code": "mixed_documents",
                       "detail": "One receipt CSV must reference one receipt key and one purchase order"})
        return None, issues
    order_row = conn.execute("SELECT id FROM purchase_orders WHERE po_number=?", (next(iter(po_numbers)),)).fetchone()
    if not order_row:
        issues.append({"record_index": None, "field_key": "po_number", "code": "unknown_order",
                       "detail": "Purchase order was not found"})
        return None, issues
    order = order_detail(conn, order_row["id"])
    if order["status"] not in {"approved", "queued", "sent", "partially_received"}:
        issues.append({"record_index": None, "field_key": "po_number", "code": "order_not_receivable",
                       "detail": "Approve the purchase order before importing a receipt"})
        return None, issues
    lines = []
    for index, row in enumerate(records):
        missing = [field for field in required if not row.get(field)]
        if missing:
            issues.append({"record_index": index, "field_key": missing[0],
                           "code": "required_value_missing", "detail": ", ".join(missing)})
            continue
        try:
            number = int(row["line_number"])
            line = next((item for item in order["lines"] if item["line_number"] == number), None)
            if not line:
                raise ValueError(f"PO line {number} was not found")
            accepted = _parse_float(row.get("accepted_qty"))
            rejected = _parse_float(row.get("rejected_qty"))
            if accepted + rejected <= 0 or accepted + rejected > float(line["remaining_qty"]) + 1e-9:
                raise ValueError("Receipt quantity is zero or exceeds the open PO quantity")
            if rejected > 0 and not row.get("rejection_reason"):
                raise ValueError("rejection_reason is required")
            lines.append({"line_number": number, "lot_code": row["lot_code"],
                          "accepted_qty": accepted, "rejected_qty": rejected,
                          "rejection_reason": row.get("rejection_reason") or None,
                          "location": row.get("location") or None})
        except (TypeError, ValueError) as error:
            issues.append({"record_index": index, "field_key": None,
                           "code": "validation_failed", "detail": str(error)})
    if issues:
        return None, issues
    first = records[0]
    return {
        "receipt_key": next(iter(receipt_keys)), "purchase_order_id": order["id"],
        "external_receipt_id": first.get("external_receipt_id") or None,
        "received_at": first.get("received_at") or None, "location": first.get("location") or None,
        "verified": _truth(first.get("verified")), "lines": lines,
    }, issues


def import_csv(conn: sqlite3.Connection, payload: dict) -> dict:
    document_type = payload["document_type"]
    mode = payload.get("mode", "validate")
    csv_text = payload["csv_text"]
    source_sha = _hash(csv_text)
    fields, records = _csv_records(csv_text)
    actor = payload.get("actor", "operator")
    prior = conn.execute(
        "SELECT run_id FROM procurement_import_batches WHERE document_type=? AND source_sha256=?",
        (document_type, source_sha),
    ).fetchone()
    if mode == "apply" and prior:
        run_id = _record_exchange(
            conn, document_type=document_type, source_sha=source_sha,
            file_name=payload.get("file_name"), mode=mode, status="duplicate",
            seen=len(records), accepted=len(records), rejected=0, imported=0, actor=actor,
            summary={"duplicate_of_run_id": prior["run_id"]}, issues=[],
        )
        conn.commit()
        return {"run_id": run_id, "status": "duplicate", "duplicate": True,
                "records_seen": len(records), "records_imported": 0, "issues": []}
    if document_type == "supplier_catalog":
        normalized, issues = _validate_catalog(conn, records, bool(payload.get("approve_master_data", False)))
        receipt_payload = None
    elif document_type == "goods_receipt":
        receipt_payload, issues = _validate_receipt_csv(conn, records)
        normalized = records if receipt_payload else []
    else:
        raise ValueError("document_type must be supplier_catalog or goods_receipt")
    status = "passed" if not issues else "failed"
    imported = 0
    receipt_result = None
    if mode == "apply" and not issues:
        try:
            if document_type == "supplier_catalog":
                for row in normalized:
                    upsert_supplier(conn, row["supplier_key"], {
                        "name": row["supplier_name"], "currency": row["currency"],
                        "lead_time_days": row["lead_time_days"], "gln": row["gln"],
                        "verified": row["verified"],
                    }, commit=False)
                    upsert_mapping(conn, row["supplier_key"], row["object_type"], row["object_key"], {
                        "supplier_sku": row["supplier_sku"], "purchase_uom": row["purchase_uom"],
                        "conversion_factor": row["conversion_factor"], "order_multiple": row["order_multiple"],
                        "min_order_qty": row["min_order_qty"], "unit_price": row["unit_price"],
                        "currency": row["currency"], "gtin": row["gtin"],
                        "preferred": row["preferred"], "verified": row["verified"], "source": "csv_import",
                    }, commit=False)
                    imported += 1
            else:
                receipt_payload.update({"actor": actor, "source": "csv_import", "source_hash": source_sha})
                receipt_result = receive_order(conn, receipt_payload, commit=False)
                imported = len(receipt_payload["lines"])
            status = "imported"
        except Exception:
            conn.rollback()
            raise
    summary = {"columns": fields, "ready_to_apply": not issues,
               "receipt_id": receipt_result["id"] if receipt_result else None}
    run_id = _record_exchange(
        conn, document_type=document_type, source_sha=source_sha,
        file_name=payload.get("file_name"), mode=mode, status=status,
        seen=len(records), accepted=len(normalized), rejected=len(records) - len(normalized),
        imported=imported, actor=actor, summary=summary, issues=issues,
    )
    if mode == "apply" and not issues:
        conn.execute(
            "INSERT INTO procurement_import_batches (document_type,source_sha256,run_id,imported_at) VALUES (?,?,?,?)",
            (document_type, source_sha, run_id, _now()),
        )
    conn.commit()
    return {"run_id": run_id, "status": status, "duplicate": False,
            "records_seen": len(records), "records_accepted": len(normalized),
            "records_rejected": len(records) - len(normalized), "records_imported": imported,
            "ready_to_apply": not issues, "issues": issues, "summary": summary}


def snapshot(conn: sqlite3.Connection, job_names: list[str] | None = None) -> dict:
    recs = recommendations(conn, job_names)
    metrics = _supplier_metrics(conn)
    suppliers = [dict(row) for row in conn.execute(
        "SELECT * FROM procurement_suppliers ORDER BY active DESC,name"
    ).fetchall()]
    for supplier in suppliers:
        supplier["metrics"] = metrics.get(supplier["id"], {})
    mappings = [mapping_row(conn, row["id"]) for row in conn.execute(
        "SELECT id FROM procurement_item_mappings ORDER BY object_type,object_key,preferred DESC"
    ).fetchall()]
    order_ids = [row["id"] for row in conn.execute(
        "SELECT id FROM purchase_orders ORDER BY id DESC LIMIT 100"
    ).fetchall()]
    orders = [order_detail(conn, order_id) for order_id in order_ids]
    receipt_ids = [row["id"] for row in conn.execute(
        "SELECT id FROM goods_receipts ORDER BY id DESC LIMIT 50"
    ).fetchall()]
    receipts = [receipt_detail(conn, receipt_id) for receipt_id in receipt_ids]
    runs = []
    for row in conn.execute(
        "SELECT * FROM procurement_exchange_runs ORDER BY id DESC LIMIT 20"
    ).fetchall():
        item = dict(row)
        item["summary"] = json.loads(item.pop("summary_json"))
        runs.append(item)
    needing = [item for item in recs if item["uncovered_qty"] > 1e-9]
    mapped = [item for item in needing if item["mapping"] and item["mapping"]["verified"] and
              item["mapping"]["supplier_verified"]]
    return {
        "suppliers": suppliers, "mappings": mappings, "recommendations": recs,
        "orders": orders, "receipts": receipts, "outbox": outbox(conn), "exchange_runs": runs,
        "commissioned": not needing or len(mapped) == len(needing),
        "summary": {
            "suppliers": len(suppliers),
            "verified_suppliers": sum(bool(item["verified"]) for item in suppliers),
            "mapped_shortages": len(mapped), "uncovered_shortages": len(needing),
            "supply_risks": sum(bool(item["at_risk"]) for item in needing),
            "open_purchase_orders": sum(item["status"] in OPEN_PO_STATUSES for item in orders),
            "pending_outbox": sum(item["status"] in {"pending", "failed"} for item in outbox(conn)),
            "receipts": len(receipts),
        },
        "guardrail": "Drafts may use unverified mappings; approval requires a verified supplier and every supplier-item mapping. Receipts are idempotent and only accepted quantity enters stock.",
    }
