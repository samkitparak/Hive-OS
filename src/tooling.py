"""Traceable tooling lifecycle, evidence ingestion, and service prediction."""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timezone
from pathlib import PurePath
from typing import Optional


USABLE_STATUSES = {"available", "allocated", "in_use"}
TERMINAL_STATUSES = {"broken", "retired"}
SERVICE_REASONS = {"worn", "quality"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cnc_key(value: str) -> str:
    return PurePath((value or "").replace("\\", "/")).name.strip().casefold()


def _machine(conn: sqlite3.Connection, machine_key: Optional[str]) -> Optional[dict]:
    if not machine_key:
        return None
    row = conn.execute(
        "SELECT id,machine_key,name FROM machines WHERE machine_key=?", (machine_key,)
    ).fetchone()
    if not row:
        raise KeyError(f"Machine '{machine_key}' not found")
    return dict(row)


def _asset(conn: sqlite3.Connection, tool_key: str) -> dict:
    row = conn.execute("SELECT * FROM tool_assets WHERE tool_key=?", (tool_key,)).fetchone()
    if not row:
        raise KeyError(f"Tool '{tool_key}' not found")
    return dict(row)


def _life_value(asset: dict) -> float:
    return float(asset[f"{asset['life_basis']}_used"])


def _learned_life(conn: sqlite3.Connection, asset: dict) -> dict:
    rows = conn.execute(
        """SELECT tsr.prior_life_value FROM tool_service_records tsr
           JOIN tool_assets ta ON ta.id=tsr.tool_id
           WHERE lower(ta.tool_type)=lower(?) AND ta.life_basis=?
             AND tsr.action IN ('recondition','replace','retire')
             AND tsr.end_reason IN ('worn','quality') AND tsr.prior_life_value>0
           ORDER BY tsr.prior_life_value""",
        (asset["tool_type"], asset["life_basis"]),
    ).fetchall()
    values = [float(row["prior_life_value"]) for row in rows]
    if len(values) < 5:
        return {"status": "collecting_evidence", "sample_count": len(values),
                "minimum_samples": 5, "conservative_life": None}
    position = (len(values) - 1) * 0.2
    low = math.floor(position)
    high = math.ceil(position)
    estimate = values[low] if low == high else (
        values[low] + (values[high] - values[low]) * (position - low)
    )
    return {"status": "available", "sample_count": len(values), "minimum_samples": 5,
            "conservative_life": round(estimate, 3), "method": "empirical_p20"}


def _quality_failures(conn: sqlite3.Connection, asset: dict) -> int:
    return int(conn.execute(
        """SELECT COUNT(*) count FROM tool_quality_links tql
           JOIN quality_checks qc ON qc.id=tql.quality_check_id
           WHERE tql.tool_id=? AND qc.result IN ('fail','rework') AND qc.ts>=?""",
        (asset["id"], asset["life_started_at"]),
    ).fetchone()["count"])


def _derived_status(conn: sqlite3.Connection, asset: dict) -> str:
    if asset["status"] in TERMINAL_STATUSES or asset["status"] == "in_service":
        return asset["status"]
    used = _life_value(asset)
    rated = asset["rated_life"]
    learned = _learned_life(conn, asset)["conservative_life"]
    limit = min(float(rated), learned) if rated is not None and learned is not None else (
        float(rated) if rated is not None else learned
    )
    if limit is not None and used >= limit:
        return "expired"
    if _quality_failures(conn, asset) >= 3:
        return "service_due"
    warning = asset["warning_remaining"]
    if limit is not None and warning is not None and limit - used <= float(warning):
        return "service_due"
    if asset["machine_id"]:
        return "in_use" if asset["status"] == "in_use" else "allocated"
    return "available"


def _refresh_status(conn: sqlite3.Connection, asset: dict) -> dict:
    status = _derived_status(conn, asset)
    if status != asset["status"]:
        now = _now()
        conn.execute(
            "UPDATE tool_assets SET status=?,version=version+1,updated_at=? WHERE id=?",
            (status, now, asset["id"]),
        )
        asset = _asset(conn, asset["tool_key"])
    return asset


def _public_asset(conn: sqlite3.Connection, asset: dict) -> dict:
    asset = _refresh_status(conn, asset)
    result = dict(conn.execute(
        """SELECT ta.*,tp.pool_key,tp.name pool_name,m.machine_key,m.name machine_name
           FROM tool_assets ta JOIN tool_pools tp ON tp.id=ta.pool_id
           LEFT JOIN machines m ON m.id=ta.machine_id WHERE ta.id=?""",
        (asset["id"],),
    ).fetchone())
    used = _life_value(result)
    learned = _learned_life(conn, result)
    rated = float(result["rated_life"]) if result["rated_life"] is not None else None
    predicted = learned["conservative_life"]
    if rated is not None and predicted is not None:
        predicted = min(rated, predicted)
    elif predicted is None:
        predicted = rated
    result["life_used"] = round(used, 3)
    result["life_limit"] = predicted
    result["life_limit_source"] = (
        "conservative_local_evidence" if learned["conservative_life"] is not None
        and (rated is None or learned["conservative_life"] < rated) else
        ("rated" if rated is not None else "not_configured")
    )
    result["remaining_life"] = None if predicted is None else round(max(0, predicted - used), 3)
    result["remaining_percent"] = None if not predicted else round(max(0, 100 * (predicted - used) / predicted), 1)
    result["quality_failures_this_life"] = _quality_failures(conn, result)
    result["learning"] = learned
    result["program_mappings"] = [dict(row) for row in conn.execute(
        """SELECT tpm.id,m.machine_key,m.name machine_name,tpm.cnc_file,
                  tpm.parts_per_cycle,tpm.cycles_per_event,tpm.verified,tpm.updated_at
           FROM tool_program_mappings tpm JOIN machines m ON m.id=tpm.machine_id
           WHERE tpm.tool_id=? ORDER BY m.name,tpm.cnc_file""", (result["id"],)
    ).fetchall()]
    result["barcode"] = f"HIVE:T:{result['tool_key']}"
    result["verified"] = bool(result["verified"])
    return result


def create_asset(conn: sqlite3.Connection, payload: dict) -> dict:
    pool = conn.execute("SELECT id FROM tool_pools WHERE pool_key=?", (payload["pool_key"],)).fetchone()
    if not pool:
        raise KeyError(f"Tool pool '{payload['pool_key']}' not found")
    now = _now()
    conn.execute(
        """INSERT INTO tool_assets
           (tool_key,pool_id,name,tool_type,manufacturer,manufacturer_part_number,
            serial_number,external_id,life_basis,rated_life,warning_remaining,location,
            recondition_limit,life_started_at,source,verified,created_by,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (payload["tool_key"], pool["id"], payload["name"], payload["tool_type"],
         payload.get("manufacturer"), payload.get("manufacturer_part_number"),
         payload.get("serial_number"), payload.get("external_id"), payload["life_basis"],
         payload.get("rated_life"), payload.get("warning_remaining"), payload.get("location"),
         payload.get("recondition_limit"), now, payload.get("source", "manual"),
         int(bool(payload.get("verified"))), payload.get("actor", "operator"), now, now),
    )
    conn.commit()
    return get_asset(conn, payload["tool_key"])


def update_asset(conn: sqlite3.Connection, tool_key: str, payload: dict) -> dict:
    asset = _asset(conn, tool_key)
    expected = payload.get("expected_version")
    if expected is not None and expected != asset["version"]:
        raise ValueError("Tool changed since it was loaded; refresh before saving")
    allowed = (
        "name", "tool_type", "manufacturer", "manufacturer_part_number", "serial_number",
        "external_id", "life_basis", "rated_life", "warning_remaining", "location",
        "recondition_limit", "verified",
    )
    updates = {key: payload[key] for key in allowed if key in payload}
    if "verified" in updates:
        updates["verified"] = int(bool(updates["verified"]))
    if not updates:
        return get_asset(conn, tool_key)
    updates.update({"updated_at": _now()})
    assignments = ",".join(f"{key}=?" for key in updates)
    conn.execute(
        f"UPDATE tool_assets SET {assignments},version=version+1 WHERE id=?",
        (*updates.values(), asset["id"]),
    )
    conn.commit()
    return get_asset(conn, tool_key)


def get_asset(conn: sqlite3.Connection, tool_key: str) -> dict:
    asset = _public_asset(conn, _asset(conn, tool_key))
    asset["usage_events"] = [dict(row) for row in conn.execute(
        """SELECT tue.*,m.machine_key FROM tool_usage_events tue
           LEFT JOIN machines m ON m.id=tue.machine_id WHERE tue.tool_id=?
           ORDER BY tue.occurred_at DESC,tue.id DESC LIMIT 100""", (asset["id"],)
    ).fetchall()]
    asset["service_records"] = [dict(row) for row in conn.execute(
        "SELECT * FROM tool_service_records WHERE tool_id=? ORDER BY performed_at DESC,id DESC",
        (asset["id"],),
    ).fetchall()]
    conn.commit()
    return asset


def record_usage(conn: sqlite3.Connection, tool_key: str, payload: dict,
                 *, machine_event_id: Optional[int] = None, commit: bool = True) -> dict:
    asset = _asset(conn, tool_key)
    if asset["status"] in TERMINAL_STATUSES or asset["status"] == "in_service":
        raise ValueError(f"Cannot record usage while tool is {asset['status'].replace('_', ' ')}")
    if not any(float(payload.get(key, 0) or 0) > 0 for key in
               ("delta_parts", "delta_cycles", "delta_runtime_minutes")) and all(
                   payload.get(key) is None for key in ("condition_percent", "measured_wear_mm")
               ):
        raise ValueError("Usage requires a positive counter or a condition measurement")
    existing = conn.execute(
        "SELECT id FROM tool_usage_events WHERE event_key=?", (payload["event_key"],)
    ).fetchone()
    if existing:
        return {"duplicate": True, "event_id": existing["id"],
                "tool": _public_asset(conn, _asset(conn, tool_key))}
    machine = _machine(conn, payload.get("machine_key"))
    machine_id = machine["id"] if machine else asset["machine_id"]
    now = _now()
    cursor = conn.execute(
        """INSERT INTO tool_usage_events
           (event_key,tool_id,machine_id,machine_event_id,event_type,delta_parts,
            delta_cycles,delta_runtime_minutes,condition_percent,measured_wear_mm,
            source,actor,notes,occurred_at,recorded_at)
           VALUES (?,?,?,?,'usage',?,?,?,?,?,?,?,?,?,?)""",
        (payload["event_key"], asset["id"], machine_id, machine_event_id,
         float(payload.get("delta_parts", 0) or 0), float(payload.get("delta_cycles", 0) or 0),
         float(payload.get("delta_runtime_minutes", 0) or 0), payload.get("condition_percent"),
         payload.get("measured_wear_mm"), payload.get("source", "manual"),
         payload.get("actor", "operator"), payload.get("notes"),
         payload.get("occurred_at") or now, now),
    )
    conn.execute(
        """UPDATE tool_assets SET parts_used=parts_used+?,cycles_used=cycles_used+?,
           runtime_minutes_used=runtime_minutes_used+?,version=version+1,updated_at=? WHERE id=?""",
        (float(payload.get("delta_parts", 0) or 0), float(payload.get("delta_cycles", 0) or 0),
         float(payload.get("delta_runtime_minutes", 0) or 0), now, asset["id"]),
    )
    refreshed = _refresh_status(conn, _asset(conn, tool_key))
    if commit:
        conn.commit()
    return {"duplicate": False, "event_id": cursor.lastrowid,
            "tool": _public_asset(conn, refreshed)}


def action(conn: sqlite3.Connection, tool_key: str, payload: dict) -> dict:
    asset = _asset(conn, tool_key)
    action_name = payload["action"]
    machine = _machine(conn, payload.get("machine_key"))
    now = _now()
    if action_name in {"allocate", "install"}:
        if not machine:
            raise ValueError("A machine is required to allocate or install a tool")
        if asset["status"] not in USABLE_STATUSES:
            raise ValueError(f"Tool is {asset['status'].replace('_', ' ')} and cannot be assigned")
        status = "allocated" if action_name == "allocate" else "in_use"
        conn.execute(
            """UPDATE tool_assets SET status=?,machine_id=?,pocket=?,location=NULL,
               version=version+1,updated_at=? WHERE id=?""",
            (status, machine["id"], payload.get("pocket"), now, asset["id"]),
        )
    elif action_name == "remove":
        conn.execute(
            """UPDATE tool_assets SET status='available',machine_id=NULL,pocket=NULL,location=?,
               version=version+1,updated_at=? WHERE id=?""",
            (payload.get("location") or asset["location"], now, asset["id"]),
        )
    else:
        status = {"service_start": "in_service", "broken": "broken", "retire": "retired"}[action_name]
        conn.execute(
            """UPDATE tool_assets SET status=?,machine_id=NULL,pocket=NULL,
               location=COALESCE(?,location),version=version+1,updated_at=? WHERE id=?""",
            (status, payload.get("location"), now, asset["id"]),
        )
    conn.execute(
        """INSERT INTO tool_usage_events
           (event_key,tool_id,machine_id,event_type,source,actor,notes,occurred_at,recorded_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (f"action:{tool_key}:{action_name}:{now}", asset["id"], machine["id"] if machine else asset["machine_id"],
         action_name, "manual", payload.get("actor", "operator"), payload.get("notes"), now, now),
    )
    conn.commit()
    return get_asset(conn, tool_key)


def record_service(conn: sqlite3.Connection, tool_key: str, payload: dict) -> dict:
    asset = _asset(conn, tool_key)
    action_name = payload["action"]
    if action_name == "recondition" and asset["recondition_limit"] is not None \
            and asset["recondition_count"] >= asset["recondition_limit"]:
        raise ValueError("Reconditioning limit reached; replace or retire this tool")
    now = payload.get("performed_at") or _now()
    prior_value = _life_value(asset)
    conn.execute(
        """INSERT INTO tool_service_records
           (tool_id,action,end_reason,prior_life_value,prior_parts,prior_cycles,
            prior_runtime_minutes,condition_percent,measured_wear_mm,cost,provider,
            actor,notes,performed_at,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (asset["id"], action_name, payload["end_reason"], prior_value,
         asset["parts_used"], asset["cycles_used"], asset["runtime_minutes_used"],
         payload.get("condition_percent"), payload.get("measured_wear_mm"), payload.get("cost"),
         payload.get("provider"), payload.get("actor", "operator"), payload.get("notes"), now, _now()),
    )
    if action_name in {"recondition", "replace"}:
        recondition_count = asset["recondition_count"] + 1 if action_name == "recondition" else 0
        conn.execute(
            """UPDATE tool_assets SET parts_used=0,cycles_used=0,runtime_minutes_used=0,
               status='available',machine_id=NULL,pocket=NULL,recondition_count=?,life_started_at=?,
               version=version+1,updated_at=? WHERE id=?""",
            (recondition_count, now, _now(), asset["id"]),
        )
    elif action_name == "retire":
        conn.execute(
            """UPDATE tool_assets SET status='retired',machine_id=NULL,pocket=NULL,
               version=version+1,updated_at=? WHERE id=?""", (_now(), asset["id"]),
        )
    open_links = conn.execute(
        """SELECT wo.id FROM tool_work_order_links twol JOIN maintenance_work_orders wo
           ON wo.id=twol.work_order_id WHERE twol.tool_id=? AND wo.status IN ('open','in_progress')""",
        (asset["id"],),
    ).fetchall()
    for row in open_links:
        conn.execute("UPDATE maintenance_work_orders SET status='done',closed_at=? WHERE id=?", (now, row["id"]))
        conn.execute(
            """INSERT INTO maintenance_work_order_events
               (work_order_id,event_type,to_status,actor,details_json,ts)
               VALUES (?,'completed','done',?,'{"source":"tool_service"}',?)""",
            (row["id"], payload.get("actor", "operator"), now),
        )
    conn.commit()
    return get_asset(conn, tool_key)


def upsert_program_mapping(conn: sqlite3.Connection, tool_key: str, payload: dict) -> dict:
    asset = _asset(conn, tool_key)
    machine = _machine(conn, payload["machine_key"])
    cnc_file = _cnc_key(payload["cnc_file"])
    if not cnc_file:
        raise ValueError("CNC file must contain a file name")
    now = _now()
    conn.execute(
        """INSERT INTO tool_program_mappings
           (tool_id,machine_id,cnc_file,parts_per_cycle,cycles_per_event,source,verified,
            created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(tool_id,machine_id,cnc_file) DO UPDATE SET
             parts_per_cycle=excluded.parts_per_cycle,cycles_per_event=excluded.cycles_per_event,
             source=excluded.source,verified=excluded.verified,updated_at=excluded.updated_at""",
        (asset["id"], machine["id"], cnc_file, payload.get("parts_per_cycle", 1),
         payload.get("cycles_per_event", 1), payload.get("source", "manual"),
         int(bool(payload.get("verified"))), payload.get("actor", "operator"), now, now),
    )
    conn.commit()
    return get_asset(conn, tool_key)


def sync_machine_usage(conn: sqlite3.Connection) -> dict:
    mappings = [dict(row) for row in conn.execute(
        """SELECT tpm.*,ta.tool_key FROM tool_program_mappings tpm
           JOIN tool_assets ta ON ta.id=tpm.tool_id WHERE tpm.verified=1 AND ta.verified=1"""
    ).fetchall()]
    by_machine: dict[int, list[dict]] = {}
    for mapping in mappings:
        by_machine.setdefault(mapping["machine_id"], []).append(mapping)
    imported = duplicates = 0
    for event in conn.execute(
        """SELECT id,machine_id,cnc_file,ts FROM machine_events
           WHERE event_type='cycle_end' AND cnc_file IS NOT NULL ORDER BY id"""
    ).fetchall():
        cnc_file = _cnc_key(event["cnc_file"])
        for mapping in by_machine.get(event["machine_id"], []):
            if mapping["cnc_file"] != cnc_file:
                continue
            result = record_usage(conn, mapping["tool_key"], {
                "event_key": f"machine-event:{event['id']}:tool:{mapping['tool_id']}",
                "machine_key": None, "delta_parts": mapping["parts_per_cycle"],
                "delta_cycles": mapping["cycles_per_event"], "source": "machine_program_mapping",
                "actor": "hive-tooling", "occurred_at": event["ts"],
            }, machine_event_id=event["id"], commit=False)
            duplicates += int(result["duplicate"])
            imported += int(not result["duplicate"])
    conn.commit()
    return {"mappings": len(mappings), "usage_events_imported": imported, "duplicates": duplicates}


def link_quality_check(conn: sqlite3.Connection, quality_check_id: int,
                       machine_id: Optional[int], commit: bool = True) -> Optional[dict]:
    if not machine_id:
        return None
    tools = conn.execute(
        """SELECT id,tool_key FROM tool_assets WHERE machine_id=? AND verified=1
           AND status IN ('allocated','in_use')""", (machine_id,)
    ).fetchall()
    if len(tools) != 1:
        return None
    now = _now()
    conn.execute(
        """INSERT OR IGNORE INTO tool_quality_links
           (quality_check_id,tool_id,attribution,created_at) VALUES (?,?,?,?)""",
        (quality_check_id, tools[0]["id"], "single_active_tool", now),
    )
    _refresh_status(conn, _asset(conn, tools[0]["tool_key"]))
    if commit:
        conn.commit()
    return {"tool_key": tools[0]["tool_key"], "attribution": "single_active_tool"}


def sync_service_work(conn: sqlite3.Connection) -> dict:
    created = 0
    now = _now()
    for raw in conn.execute("SELECT * FROM tool_assets ORDER BY id").fetchall():
        asset = _refresh_status(conn, dict(raw))
        if asset["status"] not in {"service_due", "expired", "broken"}:
            continue
        open_order = conn.execute(
            """SELECT wo.id FROM tool_work_order_links twol JOIN maintenance_work_orders wo
               ON wo.id=twol.work_order_id WHERE twol.tool_id=?
               AND wo.status IN ('open','in_progress') LIMIT 1""", (asset["id"],)
        ).fetchone()
        if open_order:
            continue
        priority = "urgent" if asset["status"] in {"expired", "broken"} else "high"
        cursor = conn.execute(
            """INSERT INTO maintenance_work_orders
               (machine_id,title,description,priority,status,source,created_at)
               VALUES (?,?,?,?,'open','tool_lifecycle',?)""",
            (asset["machine_id"], f"Service tool {asset['tool_key']}",
             f"Tool lifecycle status is {asset['status'].replace('_', ' ')}. Inspect and record the service outcome.",
             priority, now),
        )
        conn.execute(
            """INSERT INTO tool_work_order_links
               (tool_id,work_order_id,trigger_status,trigger_life_value,created_at)
               VALUES (?,?,?,?,?)""",
            (asset["id"], cursor.lastrowid, asset["status"], _life_value(asset), now),
        )
        conn.execute(
            """INSERT INTO maintenance_work_order_events
               (work_order_id,event_type,to_status,actor,details_json,ts)
               VALUES (?,'generated','open','hive-tooling','{"source":"tool_lifecycle"}',?)""",
            (cursor.lastrowid, now),
        )
        created += 1
    conn.commit()
    return {"work_orders_created": created}


def sync(conn: sqlite3.Connection) -> dict:
    usage = sync_machine_usage(conn)
    service = sync_service_work(conn)
    return {**usage, **service, "snapshot": snapshot(conn)}


def snapshot(conn: sqlite3.Connection, *, commit: bool = False) -> dict:
    assets = [_public_asset(conn, dict(row)) for row in conn.execute(
        "SELECT * FROM tool_assets ORDER BY tool_type,name,tool_key"
    ).fetchall()]
    if commit:
        conn.commit()
    pools = []
    for pool in conn.execute("SELECT * FROM tool_pools ORDER BY name").fetchall():
        items = [item for item in assets if item["pool_id"] == pool["id"]]
        commissioned = bool(items)
        usable = sum(bool(item["verified"] and item["status"] in USABLE_STATUSES) for item in items)
        pools.append({
            "pool_key": pool["pool_key"], "name": pool["name"],
            "commissioned": commissioned, "registered_assets": len(items),
            "verified_assets": sum(bool(item["verified"]) for item in items),
            "usable_assets": usable,
            "service_due": sum(item["status"] == "service_due" for item in items),
            "unavailable_assets": sum(item["status"] not in USABLE_STATUSES for item in items),
            "effective_available_qty": usable if commissioned else pool["available_qty"],
            "capacity_source": "asset_registry" if commissioned else "pool_fallback",
        })
    summary = {
        "registered": len(assets), "verified": sum(item["verified"] for item in assets),
        "usable": sum(item["verified"] and item["status"] in USABLE_STATUSES for item in assets),
        "service_due": sum(item["status"] == "service_due" for item in assets),
        "expired": sum(item["status"] == "expired" for item in assets),
        "broken": sum(item["status"] == "broken" for item in assets),
        "learning": sum(item["learning"]["status"] == "available" for item in assets),
    }
    return {"status": "commissioning" if not assets else "active", "summary": summary,
            "assets": assets, "pools": pools}
