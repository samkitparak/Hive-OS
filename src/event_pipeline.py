"""Validated, idempotent machine-event ingestion for every telemetry source."""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo


VALID_EVENT_TYPES = {
    "power_on", "power_off", "cycle_start", "cycle_end", "idle", "alarm",
    "state_on", "state_off", "state_idle", "part_complete", "heartbeat",
}


def canonical_timestamp(value: Optional[str], site_timezone: str = "Asia/Kolkata") -> str:
    """Return an ISO UTC timestamp; naive machine timestamps are site-local."""
    if not value:
        return datetime.now(timezone.utc).isoformat()
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    if parsed.tzinfo is None:
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(site_timezone))
        except KeyError as error:
            raise ValueError(f"Unknown timezone '{site_timezone}'") from error
    return parsed.astimezone(timezone.utc).isoformat()


def _fingerprint(payload: dict) -> str:
    identity = {
        "machine_key": payload.get("machine_key"),
        "event_type": payload.get("event_type"),
        "ts": payload.get("ts"),
        "cnc_file": payload.get("cnc_file"),
        "alarm_code": payload.get("alarm_code"),
    }
    raw = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _resolve_part_id(conn: sqlite3.Connection, cnc_file: Optional[str]) -> Optional[int]:
    if not cnc_file:
        return None
    stem = str(cnc_file).rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    for suffix in (".xcs", ".XCS", ".ard", ".ARD"):
        if stem.endswith(suffix):
            stem = stem[:-len(suffix)]
            break
    row = conn.execute(
        "SELECT id FROM parts WHERE cnc_file_back=? OR cnc_file_front=? LIMIT 1",
        (stem, stem),
    ).fetchone()
    return row["id"] if row else None


def _log_ingestion(conn: sqlite3.Connection, *, machine_id: Optional[int],
                   event_id: Optional[int], payload: dict, status: str,
                   reason: Optional[str], received_at: str) -> None:
    conn.execute(
        """INSERT INTO event_ingestion_log
           (machine_id,event_id,source,status,reason,event_type,event_ts,received_at,raw_payload)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (machine_id, event_id, payload.get("source", "unknown"), status, reason,
         payload.get("event_type"), payload.get("ts"), received_at,
         json.dumps(payload, sort_keys=True)),
    )


def ingest_event(conn: sqlite3.Connection, payload: dict, *,
                 site_timezone: str = "Asia/Kolkata",
                 received_at: Optional[str] = None) -> dict:
    """Validate and store one event, returning its disposition and canonical form."""
    received = canonical_timestamp(received_at, "UTC") if received_at else datetime.now(timezone.utc).isoformat()
    normalized = dict(payload)
    machine_key = str(normalized.get("machine_key") or "").strip()
    event_type = str(normalized.get("event_type") or "").strip().lower()
    normalized["machine_key"] = machine_key
    normalized["event_type"] = event_type
    normalized["source"] = str(normalized.get("source") or "unknown")

    machine = conn.execute(
        "SELECT id FROM machines WHERE machine_key=?", (machine_key,)
    ).fetchone()
    machine_id = machine["id"] if machine else None

    reason = None
    if not machine_id:
        reason = "unknown_machine"
    elif event_type not in VALID_EVENT_TYPES:
        reason = "unknown_event_type"
    else:
        try:
            normalized["ts"] = canonical_timestamp(normalized.get("ts"), site_timezone)
        except (ValueError, KeyError):
            reason = "invalid_timestamp"

    if reason:
        _log_ingestion(conn, machine_id=machine_id, event_id=None, payload=normalized,
                       status="rejected", reason=reason, received_at=received)
        conn.commit()
        return {"status": "rejected", "reason": reason, "event": normalized, "event_id": None}

    event_dt = datetime.fromisoformat(normalized["ts"])
    received_dt = datetime.fromisoformat(received)
    clock_skew_s = (event_dt - received_dt).total_seconds()

    if event_type == "heartbeat":
        conn.execute(
            """INSERT INTO agent_status
               (machine_id,source,last_heartbeat_at,last_event_at,last_received_at,clock_skew_s,raw_payload)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(machine_id) DO UPDATE SET
                 source=excluded.source,
                 last_heartbeat_at=excluded.last_heartbeat_at,
                 last_received_at=excluded.last_received_at,
                 clock_skew_s=excluded.clock_skew_s,
                 raw_payload=excluded.raw_payload""",
            (machine_id, normalized["source"], normalized["ts"], None, received,
             clock_skew_s, json.dumps(normalized, sort_keys=True)),
        )
        conn.commit()
        return {"status": "heartbeat", "reason": None, "event": normalized, "event_id": None}

    fingerprint = _fingerprint(normalized)
    inserted = conn.execute(
        "INSERT OR IGNORE INTO event_fingerprints (fingerprint) VALUES (?)", (fingerprint,)
    ).rowcount
    if not inserted:
        _log_ingestion(conn, machine_id=machine_id, event_id=None, payload=normalized,
                       status="duplicate", reason="duplicate_fingerprint", received_at=received)
        conn.commit()
        return {"status": "duplicate", "reason": "duplicate_fingerprint",
                "event": normalized, "event_id": None}

    part_id = _resolve_part_id(conn, normalized.get("cnc_file"))
    cursor = conn.execute(
        """INSERT INTO machine_events
           (machine_id,event_type,part_id,cnc_file,raw_payload,ts)
           VALUES (?,?,?,?,?,?)""",
        (machine_id, event_type, part_id, normalized.get("cnc_file"),
         json.dumps(normalized, sort_keys=True), normalized["ts"]),
    )
    event_id = cursor.lastrowid
    conn.execute(
        "UPDATE event_fingerprints SET event_id=? WHERE fingerprint=?",
        (event_id, fingerprint),
    )
    conn.execute(
        """INSERT INTO agent_status
           (machine_id,source,last_heartbeat_at,last_event_at,last_received_at,clock_skew_s,raw_payload)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(machine_id) DO UPDATE SET
             source=excluded.source,
             last_event_at=excluded.last_event_at,
             last_received_at=excluded.last_received_at,
             clock_skew_s=excluded.clock_skew_s,
             raw_payload=excluded.raw_payload""",
        (machine_id, normalized["source"], None, normalized["ts"], received,
         clock_skew_s, json.dumps(normalized, sort_keys=True)),
    )
    _log_ingestion(conn, machine_id=machine_id, event_id=event_id, payload=normalized,
                   status="accepted", reason=None, received_at=received)
    conn.commit()
    return {"status": "accepted", "reason": None, "event": normalized,
            "event_id": event_id, "part_id": part_id}
