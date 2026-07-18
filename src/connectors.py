"""Versioned commissioning boundary for factory-specific data connectors."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

import commissioning
import cv_sql_connector
import operations


CONNECTOR_DEFINITIONS = {
    "cabinet_vision_sql": {
        "name": "Cabinet Vision SQL",
        "record_type": "job_part_row",
        "transport": "sql_server",
        "required": ("job_name", "part_name"),
        "fields": {
            "job_name": ("job_name", "JobName", "job", "order_name", "OrderName"),
            "client_name": ("client_name", "ClientName", "customer", "CustomerName"),
            "room_name": ("room_name", "RoomName", "room"),
            "job_date": ("job_date", "JobDate", "order_date"),
            "part_name": ("part_name", "PartName", "description", "Description"),
            "material": ("material", "Material", "MaterialName"),
            "length_mm": ("length_mm", "LengthMM", "Length", "part_length"),
            "width_mm": ("width_mm", "WidthMM", "Width", "part_width"),
            "thickness_mm": ("thickness_mm", "ThicknessMM", "Thickness"),
            "qty": ("qty", "Qty", "Quantity"),
            "cnc_file_back": ("cnc_file_back", "CncFileBack", "BackProgram", "Program"),
            "cnc_file_front": ("cnc_file_front", "CncFileFront", "FrontProgram"),
            "has_cnc": ("has_cnc", "HasCnc", "HasCNC"),
        },
    },
    "ottimo_barcode": {
        "name": "Ottimo barcode",
        "record_type": "barcode_event",
        "transport": "file_or_api",
        "required": ("barcode", "event_type"),
        "fields": {
            "barcode": ("barcode", "Barcode", "code", "Code", "label"),
            "external_event_id": ("external_event_id", "EventId", "ID", "id"),
            "job_name": ("job_name", "JobName", "job", "Order"),
            "part_name": ("part_name", "PartName", "part", "Description"),
            "station": ("station", "Station", "workstation", "Area"),
            "event_type": ("event_type", "event", "Event", "status", "Status"),
            "operator": ("operator", "Operator", "user", "User"),
            "ts": ("ts", "timestamp", "Timestamp", "event_time", "DateTime"),
            "notes": ("notes", "Notes", "message", "Message"),
        },
    },
    "maestro_logs": {
        "name": "SCM Maestro logs",
        "record_type": "machine_log",
        "transport": "file_tail",
        "required": (),
        "fields": {},
    },
}

CANONICAL_BARCODE_EVENTS = {
    "route_arrival", "operation_start", "operation_complete", "part_complete",
    "qc_pass", "qc_fail", "packed", "dispatched", "unknown",
}
DEFAULT_EVENT_VALUES = {
    "COMPLETE": "part_complete",
    "QC_OK": "qc_pass",
    "QC_FAIL": "qc_fail",
    "PACKED": "packed",
    "DISPATCH": "dispatched",
}
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,127}$")
SQL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$ .-]{0,127}$")


def _maestro_scope_keys(conn: sqlite3.Connection) -> list[str]:
    return [row["machine_key"] for row in conn.execute(
        "SELECT machine_key FROM machines WHERE active=1 AND has_maestro=1 ORDER BY id"
    )]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _loads(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


def sync_defaults(conn: sqlite3.Connection) -> None:
    now = _now()
    for key, definition in CONNECTOR_DEFINITIONS.items():
        conn.execute(
            """INSERT OR IGNORE INTO connector_profiles
               (connector_key,name,record_type,transport,created_at,updated_at)
               VALUES (?,?,?,?,?,?)""",
            (key, definition["name"], definition["record_type"],
             definition["transport"], now, now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO connector_sync_state (connector_key,status) VALUES (?,?)",
            (key, "not_configured"),
        )
    conn.commit()


def _profile_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    result = dict(row)
    result["enabled"] = bool(result["enabled"])
    result["verified"] = bool(result["verified"])
    result["settings"] = _loads(result.pop("settings_json"), {})
    mapping = None
    if result["active_mapping_id"]:
        mapped = conn.execute(
            "SELECT * FROM connector_mapping_versions WHERE id=?",
            (result["active_mapping_id"],),
        ).fetchone()
        if mapped:
            mapping = dict(mapped)
            mapping["mapping"] = _loads(mapping.pop("mapping_json"), {})
            mapping["source_columns"] = _loads(mapping.pop("source_columns_json"), [])
    result["active_mapping"] = mapping
    if result["connector_key"] == "maestro_logs":
        required = _maestro_scope_keys(conn)
        approved = []
        for mapping_row in conn.execute(
            """SELECT mapping_json FROM connector_mapping_versions
               WHERE connector_key='maestro_logs' AND status='approved' ORDER BY version"""
        ):
            scope = _loads(mapping_row["mapping_json"], {}).get("machine_key")
            if scope and scope not in approved:
                approved.append(scope)
        result["required_scopes"] = required
        result["approved_scopes"] = approved
    return result


def snapshot(conn: sqlite3.Connection) -> dict:
    sync_defaults(conn)
    profiles = []
    for row in conn.execute("SELECT * FROM connector_profiles ORDER BY connector_key"):
        item = _profile_dict(conn, row)
        recent = conn.execute(
            """SELECT id,scope_key,mode,status,records_seen,records_accepted,
                      records_rejected,records_imported,records_duplicate,file_name,
                      actor,completed_at
               FROM connector_commissioning_runs WHERE connector_key=?
               ORDER BY id DESC LIMIT 5""",
            (item["connector_key"],),
        ).fetchall()
        item["recent_runs"] = [dict(run) for run in recent]
        item["credential_available"] = bool(
            item["credential_env"] and os.environ.get(item["credential_env"])
        )
        profiles.append(item)
    return {
        "profiles": profiles,
        "guardrail": "Connectors remain disabled until a real sample passes validation and its mapping is explicitly approved.",
    }


def _contains_secret(settings: dict) -> bool:
    secret_words = ("password", "passwd", "pwd", "secret", "token", "connection_string")
    for key, value in settings.items():
        if any(word in str(key).lower() for word in secret_words):
            return True
        if isinstance(value, dict) and _contains_secret(value):
            return True
    return False


def update_profile(conn: sqlite3.Connection, connector_key: str, payload: dict) -> dict:
    sync_defaults(conn)
    row = conn.execute(
        "SELECT * FROM connector_profiles WHERE connector_key=?", (connector_key,)
    ).fetchone()
    if not row:
        raise KeyError(f"Unknown connector '{connector_key}'")
    expected = payload.get("expected_version")
    if expected is not None and expected != row["version"]:
        raise ValueError("Connector profile changed; refresh before saving")
    credential_env = payload.get("credential_env", row["credential_env"])
    if credential_env and not ENV_NAME.fullmatch(credential_env):
        raise ValueError("credential_env must be an uppercase environment variable name")
    settings = payload.get("settings", _loads(row["settings_json"], {}))
    if not isinstance(settings, dict) or _contains_secret(settings):
        raise ValueError("Settings may not contain credentials; use credential_env")
    if connector_key == "cabinet_vision_sql":
        source = settings.get("source_object")
        if source:
            _quote_object(source)
        max_rows = int(settings.get("max_rows", 5000))
        if not 1 <= max_rows <= 10000:
            raise ValueError("max_rows must be between 1 and 10000")
        settings["max_rows"] = max_rows
    enabled = bool(payload.get("enabled", row["enabled"]))
    if enabled and not row["verified"]:
        raise ValueError("Approve a passing commissioning run before enabling this connector")
    now = _now()
    conn.execute(
        """UPDATE connector_profiles SET enabled=?,credential_env=?,settings_json=?,
                  version=version+1,updated_at=? WHERE connector_key=?""",
        (int(enabled), credential_env or None, _json(settings), now, connector_key),
    )
    conn.commit()
    return _profile_dict(conn, conn.execute(
        "SELECT * FROM connector_profiles WHERE connector_key=?", (connector_key,)
    ).fetchone())


def _columns(records: list[dict]) -> list[str]:
    seen: dict[str, None] = {}
    for record in records[:100]:
        if not isinstance(record, dict):
            continue
        for key in record:
            seen.setdefault(str(key), None)
    return list(seen)


def _normalized_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def suggest_mapping(connector_key: str, source_columns: list[str], records: list[dict]) -> dict:
    definition = CONNECTOR_DEFINITIONS[connector_key]
    normalized = {_normalized_name(column): column for column in source_columns}
    fields = {}
    for target, aliases in definition["fields"].items():
        source = next((normalized[_normalized_name(alias)] for alias in aliases
                       if _normalized_name(alias) in normalized), None)
        if source:
            fields[target] = source
    values: dict[str, dict[str, str]] = {}
    event_source = fields.get("event_type")
    if connector_key == "ottimo_barcode" and event_source:
        observed = {str(record.get(event_source)) for record in records
                    if record.get(event_source) not in (None, "")}
        event_values = {}
        for value in sorted(observed):
            upper = value.strip().upper()
            canonical = DEFAULT_EVENT_VALUES.get(upper, value.strip().lower())
            if canonical in CANONICAL_BARCODE_EVENTS:
                event_values[value] = canonical
        values["event_type"] = event_values
    return {"fields": fields, "values": values}


def _coerce(target: str, value: Any) -> Any:
    if value is None or value == "":
        return None
    if target in {"length_mm", "width_mm", "thickness_mm"}:
        return float(str(value).replace(",", "."))
    if target == "qty":
        number = int(float(value))
        if number < 1:
            raise ValueError("must be at least 1")
        return number
    if target == "has_cnc":
        if isinstance(value, bool):
            return int(value)
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "y"}:
            return 1
        if normalized in {"0", "false", "no", "n"}:
            return 0
        raise ValueError("must be a boolean")
    return str(value).strip()


def _map_record(connector_key: str, record: dict, mapping: dict) -> dict:
    result = {}
    for target, source in mapping.get("fields", {}).items():
        if target not in CONNECTOR_DEFINITIONS[connector_key]["fields"]:
            continue
        result[target] = _coerce(target, record.get(source))
    for target, value_map in mapping.get("values", {}).items():
        if target in result and result[target] is not None:
            result[target] = value_map.get(str(result[target]), result[target])
    if connector_key == "cabinet_vision_sql":
        result["qty"] = result.get("qty") or 1
        result["has_cnc"] = int(bool(
            result.get("has_cnc") or result.get("cnc_file_back") or result.get("cnc_file_front")
        ))
    else:
        barcode = result.get("barcode") or ""
        barcode_parts = barcode.split("|", 1)
        result["job_name"] = result.get("job_name") or (barcode_parts[0] if barcode else None)
        result["part_name"] = result.get("part_name") or (
            barcode_parts[1] if len(barcode_parts) > 1 else None
        )
        result["source"] = "ottimo_commissioned"
        result["raw_payload"] = None
    return result


def _validate_records(connector_key: str, records: list[dict], mapping: dict) -> dict:
    definition = CONNECTOR_DEFINITIONS[connector_key]
    required = definition["required"]
    mapped_required = [field for field in required if mapping.get("fields", {}).get(field)]
    issues = []
    normalized = []
    if len(mapped_required) != len(required):
        for field in required:
            if field not in mapped_required:
                issues.append({"record_index": None, "field_key": field,
                               "code": "required_mapping_missing", "severity": "error",
                               "detail": f"Map a source column to {field}"})
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            issues.append({"record_index": index, "field_key": None,
                           "code": "invalid_record", "severity": "error",
                           "detail": "Record must be an object"})
            continue
        try:
            item = _map_record(connector_key, record, mapping)
        except (TypeError, ValueError) as error:
            issues.append({"record_index": index, "field_key": None,
                           "code": "conversion_failed", "severity": "error",
                           "detail": str(error)})
            continue
        missing = [field for field in required if item.get(field) in (None, "")]
        if connector_key == "ottimo_barcode" and item.get("event_type") not in CANONICAL_BARCODE_EVENTS:
            missing.append("event_type (unrecognized value)")
        if missing:
            issues.append({"record_index": index, "field_key": missing[0],
                           "code": "required_value_missing", "severity": "error",
                           "detail": "Missing or invalid: " + ", ".join(missing)})
            continue
        normalized.append(item)
    coverage = len(mapped_required) / len(required) if required else 1.0
    rejected = len(records) - len(normalized)
    return {
        "mapping": mapping,
        "coverage": round(coverage, 3),
        "records_seen": len(records),
        "records_accepted": len(normalized),
        "records_rejected": rejected,
        "ready_to_approve": bool(records) and coverage == 1 and rejected == 0,
        "issues": issues[:100],
        "_normalized": normalized,
    }


def _record_run(conn: sqlite3.Connection, connector_key: str, *, mode: str,
                source_sha256: str, status: str, summary: dict, actor: str,
                file_name: str | None = None, scope_key: str | None = None,
                mapping_version_id: int | None = None, imported: int = 0,
                duplicate: int = 0, issues: list[dict] | None = None) -> int:
    now = _now()
    cursor = conn.execute(
        """INSERT INTO connector_commissioning_runs
           (connector_key,scope_key,mapping_version_id,mode,source_sha256,file_name,
            status,records_seen,records_accepted,records_rejected,records_imported,
            records_duplicate,summary_json,actor,started_at,completed_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (connector_key, scope_key, mapping_version_id, mode, source_sha256,
         file_name, status, summary.get("records_seen", 0),
         summary.get("records_accepted", 0), summary.get("records_rejected", 0),
         imported, duplicate, _json(summary), actor, now, now),
    )
    run_id = cursor.lastrowid
    for issue in (issues or [])[:100]:
        conn.execute(
            """INSERT INTO connector_run_issues
               (run_id,record_index,field_key,code,severity,detail) VALUES (?,?,?,?,?,?)""",
            (run_id, issue.get("record_index"), issue.get("field_key"),
             issue["code"], issue["severity"], issue["detail"]),
        )
    conn.commit()
    return run_id


def analyze_records(conn: sqlite3.Connection, connector_key: str, records: list[dict],
                    *, mapping: dict | None = None, file_name: str | None = None,
                    actor: str = "operator") -> dict:
    sync_defaults(conn)
    if connector_key not in {"cabinet_vision_sql", "ottimo_barcode"}:
        raise ValueError("This connector expects machine log evidence")
    if not records:
        raise ValueError("Choose a non-empty sample")
    if len(records) > 10000:
        raise ValueError("Samples are limited to 10,000 records")
    source_columns = _columns(records)
    selected_mapping = mapping or suggest_mapping(connector_key, source_columns, records)
    result = _validate_records(connector_key, records, selected_mapping)
    result.pop("_normalized")
    result["source_columns"] = source_columns
    result["sample_sha256"] = _hash(records)
    result["raw_sample_retained"] = False
    status = "passed" if result["ready_to_approve"] else "failed"
    run_id = _record_run(
        conn, connector_key, mode="analyze", source_sha256=result["sample_sha256"],
        status=status, summary={key: value for key, value in result.items() if key != "issues"},
        actor=actor, file_name=file_name, issues=result["issues"],
    )
    return {**result, "run_id": run_id, "status": status}


def analyze_maestro(conn: sqlite3.Connection, machine_key: str, log_text: str,
                    *, file_name: str | None = None, actor: str = "operator") -> dict:
    sync_defaults(conn)
    if machine_key not in _maestro_scope_keys(conn):
        raise KeyError(f"Unknown Maestro machine '{machine_key}'")
    result = commissioning.analyze_log(machine_key, log_text)
    source_sha = _hash(log_text)
    retained = {key: value for key, value in result.items() if key != "unknown_samples"}
    retained.update({"records_seen": result["nonempty_lines"],
                     "records_accepted": result["recognized_lines"],
                     "records_rejected": result["nonempty_lines"] - result["recognized_lines"],
                     "sample_sha256": source_sha, "raw_sample_retained": False,
                     "mapping": {"parser": "maestro_v1", "machine_key": machine_key},
                     "source_columns": []})
    status = "passed" if result["ready_to_replay"] else "failed"
    run_id = _record_run(conn, "maestro_logs", mode="analyze", source_sha256=source_sha,
                         status=status, summary=retained, actor=actor,
                         file_name=file_name, scope_key=machine_key)
    return {**result, "sample_sha256": source_sha, "raw_sample_retained": False,
            "run_id": run_id, "status": status}


def approve_run(conn: sqlite3.Connection, connector_key: str, run_id: int, *,
                expected_version: int, actor: str, enable: bool = False) -> dict:
    sync_defaults(conn)
    profile = conn.execute(
        "SELECT * FROM connector_profiles WHERE connector_key=?", (connector_key,)
    ).fetchone()
    if not profile:
        raise KeyError(f"Unknown connector '{connector_key}'")
    if profile["version"] != expected_version:
        raise ValueError("Connector profile changed; refresh before approving")
    run = conn.execute(
        "SELECT * FROM connector_commissioning_runs WHERE id=? AND connector_key=?",
        (run_id, connector_key),
    ).fetchone()
    if not run:
        raise KeyError("Commissioning run not found")
    if run["status"] != "passed" or run["mode"] != "analyze":
        raise ValueError("Only a passing analysis run can be approved")
    if run["mapping_version_id"]:
        raise ValueError("This commissioning run is already approved")
    summary = _loads(run["summary_json"], {})
    mapping = summary.get("mapping")
    if not isinstance(mapping, dict):
        raise ValueError("Commissioning run has no mapping contract")
    version = conn.execute(
        "SELECT COALESCE(MAX(version),0)+1 FROM connector_mapping_versions WHERE connector_key=?",
        (connector_key,),
    ).fetchone()[0]
    now = _now()
    cursor = conn.execute(
        """INSERT INTO connector_mapping_versions
           (connector_key,version,mapping_json,source_columns_json,sample_sha256,
            coverage,approved_by,approved_at,created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (connector_key, version, _json(mapping), _json(summary.get("source_columns", [])),
         run["source_sha256"], summary.get("coverage", 1.0), actor, now, now),
    )
    mapping_id = cursor.lastrowid
    conn.execute(
        "UPDATE connector_commissioning_runs SET mapping_version_id=? WHERE id=?",
        (mapping_id, run_id),
    )
    verified = True
    status = "ready"
    requested_enable = enable
    if connector_key == "maestro_logs":
        required_scopes = len(_maestro_scope_keys(conn))
        approved_scopes = set()
        for item in conn.execute(
            """SELECT mapping_json FROM connector_mapping_versions
               WHERE connector_key='maestro_logs' AND status='approved'"""
        ):
            scope = _loads(item["mapping_json"], {}).get("machine_key")
            if scope:
                approved_scopes.add(scope)
        verified = bool(required_scopes) and len(approved_scopes) >= required_scopes
        status = "ready" if verified else "partially_verified"
        requested_enable = enable and verified
    conn.execute(
        """UPDATE connector_profiles SET active_mapping_id=?,verified=1,enabled=?,
                  status=?,last_test_at=?,last_error=NULL,version=version+1,
                  updated_at=? WHERE connector_key=?""",
        (mapping_id, int(requested_enable), status, now, now, connector_key),
    )
    if not verified:
        conn.execute(
            "UPDATE connector_profiles SET verified=0 WHERE connector_key=?",
            (connector_key,),
        )
    conn.execute(
        """UPDATE connector_sync_state SET status='commissioned',last_error=NULL,
                  updated_at=? WHERE connector_key=?""",
        (now, connector_key),
    )
    conn.commit()
    return _profile_dict(conn, conn.execute(
        "SELECT * FROM connector_profiles WHERE connector_key=?", (connector_key,)
    ).fetchone())


def import_records(conn: sqlite3.Connection, connector_key: str, records: list[dict],
                   *, file_name: str | None = None, actor: str = "operator") -> dict:
    sync_defaults(conn)
    profile = conn.execute(
        "SELECT * FROM connector_profiles WHERE connector_key=?", (connector_key,)
    ).fetchone()
    if not profile or connector_key not in {"cabinet_vision_sql", "ottimo_barcode"}:
        raise KeyError(f"Unknown row connector '{connector_key}'")
    if not profile["enabled"] or not profile["verified"] or not profile["active_mapping_id"]:
        raise ValueError("Connector must be approved and enabled before import")
    if not records or len(records) > 10000:
        raise ValueError("Import must contain between 1 and 10,000 records")
    source_sha = _hash(records)
    previous = conn.execute(
        "SELECT run_id FROM connector_import_batches WHERE connector_key=? AND source_sha256=?",
        (connector_key, source_sha),
    ).fetchone()
    if previous:
        return {"status": "duplicate", "run_id": previous["run_id"],
                "records_seen": len(records), "records_imported": 0,
                "records_duplicate": len(records), "source_sha256": source_sha}
    mapping_row = conn.execute(
        "SELECT * FROM connector_mapping_versions WHERE id=?", (profile["active_mapping_id"],)
    ).fetchone()
    mapping = _loads(mapping_row["mapping_json"], {})
    validation = _validate_records(connector_key, records, mapping)
    summary = {key: value for key, value in validation.items()
               if key not in {"issues", "_normalized"}}
    summary.update({"sample_sha256": source_sha, "raw_sample_retained": False})
    if not validation["ready_to_approve"]:
        run_id = _record_run(
            conn, connector_key, mode="import", source_sha256=source_sha,
            status="rejected", summary=summary, actor=actor, file_name=file_name,
            mapping_version_id=mapping_row["id"], issues=validation["issues"],
        )
        raise ValueError(f"Import rejected by validation (audit run {run_id})")
    if connector_key == "cabinet_vision_sql":
        imported_result = cv_sql_connector.upsert_normalized_rows(
            conn, validation["_normalized"]
        )
        imported = imported_result["parts_imported"]
        details = imported_result
    else:
        imported = 0
        for item in validation["_normalized"]:
            operations.create_barcode_event(conn, item)
            imported += 1
        details = {"events_imported": imported}
    summary.update(details)
    run_id = _record_run(
        conn, connector_key, mode="import", source_sha256=source_sha, status="imported",
        summary=summary, actor=actor, file_name=file_name,
        mapping_version_id=mapping_row["id"], imported=imported,
    )
    now = _now()
    conn.execute(
        """INSERT INTO connector_import_batches
           (connector_key,source_sha256,mapping_version_id,run_id,imported_at)
           VALUES (?,?,?,?,?)""",
        (connector_key, source_sha, mapping_row["id"], run_id, now),
    )
    conn.execute(
        """UPDATE connector_sync_state SET status='synced',last_sync_at=?,last_cursor=?,
                  last_error=NULL,updated_at=? WHERE connector_key=?""",
        (now, f"batch:{source_sha[:12]}", now, connector_key),
    )
    conn.commit()
    return {"status": "imported", "run_id": run_id,
            "records_seen": len(records), "records_imported": imported,
            "records_duplicate": 0, "source_sha256": source_sha, **details}


def _quote_identifier(value: str) -> str:
    if not SQL_IDENTIFIER.fullmatch(value) or "]" in value:
        raise ValueError(f"Unsafe SQL identifier '{value}'")
    return f"[{value}]"


def _quote_object(value: str) -> str:
    parts = value.split(".")
    if not 1 <= len(parts) <= 3:
        raise ValueError("source_object must be a table or schema.table name")
    return ".".join(_quote_identifier(part) for part in parts)


def _sql_connection(profile: sqlite3.Row):
    env_name = profile["credential_env"]
    connection_string = os.environ.get(env_name or "")
    if not env_name or not connection_string:
        raise ValueError("The configured SQL credential environment variable is not available")
    if "applicationintent=" not in connection_string.lower():
        connection_string = connection_string.rstrip(";") + ";ApplicationIntent=ReadOnly"
    try:
        import pyodbc  # type: ignore
    except ImportError as error:
        raise RuntimeError("pyodbc is not installed on the HIVE central PC") from error
    return pyodbc.connect(connection_string, timeout=5, readonly=True)


def discover_sql(conn: sqlite3.Connection) -> dict:
    sync_defaults(conn)
    profile = conn.execute(
        "SELECT * FROM connector_profiles WHERE connector_key='cabinet_vision_sql'"
    ).fetchone()
    settings = _loads(profile["settings_json"], {})
    source = settings.get("source_object")
    readiness = {
        "credential_env": profile["credential_env"],
        "credential_available": bool(profile["credential_env"] and os.environ.get(profile["credential_env"])),
        "source_object": source,
        "source_configured": bool(source),
    }
    if not readiness["credential_available"] or not source:
        return {**readiness, "connected": False, "columns": [],
                "detail": "Configure the environment variable and approved source view first"}
    quoted = _quote_object(source)
    try:
        with _sql_connection(profile) as remote:
            cursor = remote.cursor()
            cursor.execute(f"SELECT TOP 0 * FROM {quoted}")
            columns = [description[0] for description in cursor.description or []]
    except Exception as error:
        now = _now()
        conn.execute(
            """UPDATE connector_profiles SET last_test_at=?,last_error=?,status='connection_failed',
                      updated_at=? WHERE connector_key='cabinet_vision_sql'""",
            (now, str(error)[:500], now),
        )
        conn.commit()
        raise ValueError(str(error)) from error
    now = _now()
    conn.execute(
        """UPDATE connector_profiles SET last_test_at=?,last_error=NULL,status='source_discovered',
                  version=version+1,updated_at=? WHERE connector_key='cabinet_vision_sql'""",
        (now, now),
    )
    conn.commit()
    return {**readiness, "connected": True, "columns": columns,
            "detail": f"Read-only metadata query returned {len(columns)} columns"}


def sync_sql(conn: sqlite3.Connection, *, actor: str = "system") -> dict:
    sync_defaults(conn)
    profile = conn.execute(
        "SELECT * FROM connector_profiles WHERE connector_key='cabinet_vision_sql'"
    ).fetchone()
    if not profile["enabled"] or not profile["active_mapping_id"]:
        raise ValueError("Cabinet Vision SQL is not approved and enabled")
    settings = _loads(profile["settings_json"], {})
    source = _quote_object(settings.get("source_object", ""))
    mapping_row = conn.execute(
        "SELECT * FROM connector_mapping_versions WHERE id=?", (profile["active_mapping_id"],)
    ).fetchone()
    mapping = _loads(mapping_row["mapping_json"], {})
    source_columns = list(dict.fromkeys(mapping.get("fields", {}).values()))
    if not source_columns:
        raise ValueError("Approved mapping has no SQL source columns")
    columns_sql = ",".join(_quote_identifier(column) for column in source_columns)
    max_rows = int(settings.get("max_rows", 5000))
    with _sql_connection(profile) as remote:
        cursor = remote.cursor()
        cursor.execute(f"SELECT TOP {max_rows} {columns_sql} FROM {source}")
        rows = [dict(zip(source_columns, row)) for row in cursor.fetchall()]
    return import_records(conn, "cabinet_vision_sql", rows,
                          file_name=f"sql:{settings.get('source_object')}", actor=actor)
