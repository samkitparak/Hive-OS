"""Machine passports, field readiness orchestration, and safe transport probes."""

from __future__ import annotations

import csv
import hashlib
import io
import ipaddress
import json
import re
import socket
import sqlite3
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import yaml

import mqtt_security
import remote_setup


ROOT = Path(__file__).parent.parent
METHOD_VERSION = "factory-readiness-v2"
PACK_FORMAT = "hive-factory-readiness-pack"
MISSION_METHOD_VERSION = "factory-commissioning-mission-v1"
PASSPORT_FIELDS = (
    "status", "asset_tag", "serial_number", "manufacture_year", "physical_location",
    "controller_vendor", "controller_model", "controller_software", "controller_host",
    "mac_address", "network_zone", "ssh_port", "log_folder", "cnc_folder",
    "telemetry_strategy", "notes",
)
CSV_FIELDS = ("machine_key", "expected_version", *PASSPORT_FIELDS)
STRATEGIES = {
    "maestro_agent", "modbus_tcp", "opcua", "mqtt_json", "energy_meter",
    "operator_evidence",
}
NETWORK_STRATEGIES = {"maestro_agent", "modbus_tcp", "opcua", "mqtt_json", "energy_meter"}
PROBE_PORTS = {"tcp": None, "ssh": 22, "modbus_tcp": 502, "opcua": 4840}
PLACEHOLDER_HOSTS = {
    *(f"192.168.1.{number}" for number in range(51, 55)),
    *(f"192.168.1.{number}" for number in range(101, 111)),
}
MAC_ADDRESS = re.compile(r"^(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")
WINDOWS_PATH = re.compile(r"^[A-Za-z]:\\")
COMMISSIONING_ASSETS = (
    "deploy/windows/enable-hive-ssh.ps1",
    "deploy/windows/install-machine-agent.ps1",
    "deploy/windows/test-industrial-network.ps1",
    "deploy/windows/capture-maestro-logs.ps1",
    "src/commissioning_evidence.py",
    "src/industrial_gateway.py",
)
SITE_MINUTES = {
    "passport": 8, "endpoint": 5, "transport": 12,
    "contract": 20, "live": 12, "calibration": 30,
}


SOURCES = {
    "scm_iot": {
        "label": "SCM IoT Solution (formerly Maestro Connect)",
        "url": "https://www.scmgroup.com/en_US/scmwood/news-events/news/maestro-connect-becomes-iot-solution.n235793.html",
        "finding": "SCM confirms connected-machine monitoring and real-time production data, but does not publish a local integration contract.",
    },
    "gabbiani": {
        "label": "SCM Gabbiani P/PT technical catalogue",
        "url": "https://www.scmgroup.com/products/docs/sezionatura-gabbiani/gabbiani-p-pt/gabbiani_p-pt_rev03_nov23_EN.pdf",
        "finding": "The beam saw family is PC/PLC controlled; the installed PT 80 generation and available exports still require inspection.",
    },
    "n100": {
        "label": "SCM Morbidelli N100 catalogue",
        "url": "https://www.scmgroup.com/products/docs/CDL/morbidelli%20n/Catalogo%20morbidelli%20n100_EN.pdf",
        "finding": "The N100 uses the Windows-based Maestro software ecosystem and supports external design integration through Maestro connectors.",
    },
    "stefani": {
        "label": "SCM Stefani KD product information",
        "url": "https://www.scmgroup.com/en_GB/scmwood/products/edge-banders-squaring-edge-banders.c863/single-sided-automatic-edge-banders.865/stefani-kd.42159",
        "finding": "The machine uses electronically controlled setup; exact HMI generation and local data access are installation-specific.",
    },
    "superfici": {
        "label": "SCM Superfici Compact XL product information",
        "url": "https://www.scmgroup.com/en/scmwood/products/finishing-systems.c920/spraying-machines.929/compact-xl.148218",
        "finding": "Current Superfici systems use Maestro Active Finishing; the factory line model and installed software must be identified.",
    },
    "action_e": {
        "label": "SCM Action E product information",
        "url": "https://www.scmgroup.com/fr_FR/scmwood/products/assemblage.c42150/cadreuses-pour-meubles.862/action-fl---action-p.61512",
        "finding": "Action E is manually loaded and operated with machine buttons, so a Maestro PC must not be assumed.",
    },
    "nova": {
        "label": "SCM Nova SI 400 product information",
        "url": "https://www.scmgroup.com/en_US/scmwood/products/joinery-machines.c884/sliding-table-saws.896/nova-si-400.586",
        "finding": "Nova SI 400 is a programmed or manual sliding-table saw with substantial manual operation; a networked controller is not guaranteed.",
    },
    "sergiani": {
        "label": "SCM Sergiani GS 120 product information",
        "url": "https://shop.scmgroup.com/scmwood-na/us/en/Catalogs/Catalog/PRESSES/Presses/Presses---hot-presses/sergiani-gs-120/p/SERGIANIGS120_COMP2",
        "finding": "The GS 120 has a Siemens touch-screen controller and diagnostics, but no public OPC-UA or Modbus contract is stated.",
    },
    "elgi": {
        "label": "ELGi Neuron 4 connectivity information",
        "url": "https://www.elgi.com/us/press-coverage/elgi-expands-eg-sp-super-premium-series-air-compressors-to-unlock-energy-savings-in-industrial-applications/",
        "finding": "Neuron 4 supports Ethernet, RS485, TCP/IP, and Modbus; the installed compressor controller model must be confirmed.",
    },
    "cabinet_vision": {
        "label": "CABINET VISION system requirements",
        "url": "https://hexagon.com/products/product-groups/computer-aided-manufacturing-cad-cam-software/cabinet-vision/system-requirements",
        "finding": "CABINET VISION is Windows x64 software and installs a SQL Server component; exact schema access remains version and license dependent.",
    },
}


def _profile(strategy: str, confidence: str, rationale: str, sources: list[str],
             checks: list[str], *, probe_type: str | None = None,
             fallback: tuple[str, ...] = ()) -> dict:
    return {
        "preferred_strategy": strategy,
        "confidence": confidence,
        "rationale": rationale,
        "source_keys": sources,
        "probe_type": probe_type,
        "default_port": PROBE_PORTS.get(probe_type),
        "fallback_strategies": list(fallback),
        "verify_on_site": checks,
        "assumption_only": True,
    }


COMMON_PC_CHECKS = [
    "Photograph the machine nameplate and controller/HMI About screen.",
    "Record the PC hostname, static IP, Windows version, and controller software version.",
    "Confirm a recent production log/export exists before installing any agent.",
    "Compare the SSH host fingerprint physically before HIVE trusts the PC.",
]
MANUAL_CHECKS = [
    "Photograph the nameplate and controls.",
    "Confirm whether any counter, barcode, dry contact, or vendor communication option exists.",
    "Choose operator scans or timed evidence before adding external sensing.",
]


RESEARCH_PROFILES = {
    "gabbiani_pt80": _profile(
        "maestro_agent", "medium",
        "Official material confirms PC/PLC control, but the PT 80 software generation and log format are unknown.",
        ["gabbiani", "scm_iot"], COMMON_PC_CHECKS, probe_type="ssh",
        fallback=("operator_evidence", "energy_meter"),
    ),
    "morbidelli_n100": _profile(
        "maestro_agent", "medium",
        "Official material confirms Windows-based Maestro; local log paths and permissions remain unverified.",
        ["n100", "scm_iot"], COMMON_PC_CHECKS, probe_type="ssh",
        fallback=("operator_evidence", "energy_meter"),
    ),
    "morbidelli_cx100": _profile(
        "maestro_agent", "medium",
        "The CX100 belongs to the Maestro CNC family, but installed software and event exports must be observed.",
        ["n100", "scm_iot"], COMMON_PC_CHECKS, probe_type="ssh",
        fallback=("operator_evidence", "energy_meter"),
    ),
    "stefani_kd": _profile(
        "maestro_agent", "low",
        "Electronic setup is documented; a Windows PC, accessible logs, and the exact HMI generation are not yet proven.",
        ["stefani", "scm_iot"], COMMON_PC_CHECKS, probe_type="ssh",
        fallback=("operator_evidence", "energy_meter"),
    ),
    "dmc60_rcs135": _profile(
        "maestro_agent", "low",
        "SCM sanding systems can use Maestro HMIs, but this installed RCS generation has no verified local interface.",
        ["scm_iot"], COMMON_PC_CHECKS, probe_type="ssh",
        fallback=("energy_meter", "operator_evidence"),
    ),
    "dmc90_xrt135": _profile(
        "maestro_agent", "low",
        "SCM sanding systems can use Maestro HMIs, but this installed XRT generation has no verified local interface.",
        ["scm_iot"], COMMON_PC_CHECKS, probe_type="ssh",
        fallback=("energy_meter", "operator_evidence"),
    ),
    "superfici": _profile(
        "maestro_agent", "low",
        "Current Superfici equipment uses Maestro Active Finishing, but the installed paint-line generation is unknown.",
        ["superfici", "scm_iot"], COMMON_PC_CHECKS, probe_type="ssh",
        fallback=("energy_meter", "operator_evidence"),
    ),
    "varie_osama": _profile(
        "operator_evidence", "low",
        "No verified controller or local software interface is available for the installed Osama glue line.",
        [], MANUAL_CHECKS, fallback=("energy_meter",),
    ),
    "action_e": _profile(
        "operator_evidence", "high",
        "Official documentation describes manual loading and button operation, contradicting a default Maestro-PC assumption.",
        ["action_e"], MANUAL_CHECKS, fallback=("energy_meter",),
    ),
    "nova_si400": _profile(
        "operator_evidence", "high",
        "Official documentation describes a programmed or manual sliding-table saw; network telemetry is not guaranteed.",
        ["nova"], MANUAL_CHECKS, fallback=("energy_meter",),
    ),
    "sergiani_gs120": _profile(
        "operator_evidence", "medium",
        "A Siemens touchscreen is documented, but OPC-UA availability is only a discovery candidate until the PLC is identified.",
        ["sergiani"], [
            *MANUAL_CHECKS,
            "Record the Siemens panel and PLC order numbers before attempting OPC-UA discovery.",
        ], fallback=("opcua", "energy_meter"),
    ),
    "elgi_1": _profile(
        "energy_meter", "medium",
        "A separate read-only energy meter is the dependable first path; direct compressor Modbus depends on the installed controller.",
        ["elgi"], [
            "Record compressor and controller model/serial numbers.",
            "Confirm the purchased energy-meter model and register map.",
            "Ask ELGi service whether the controller exposes licensed Modbus and obtain its map.",
        ], probe_type="modbus_tcp", fallback=("modbus_tcp", "operator_evidence"),
    ),
    "elgi_2": _profile(
        "energy_meter", "medium",
        "A separate read-only energy meter is the dependable first path; direct compressor Modbus depends on the installed controller.",
        ["elgi"], [
            "Record compressor and controller model/serial numbers.",
            "Confirm the purchased energy-meter model and register map.",
            "Ask ELGi service whether the controller exposes licensed Modbus and obtain its map.",
        ], probe_type="modbus_tcp", fallback=("modbus_tcp", "operator_evidence"),
    ),
    "aarco_1": _profile(
        "energy_meter", "medium",
        "Current-clamp energy metering avoids assumptions about the dust collector controller.",
        [], [
            "Record motor nameplate current and rated power.",
            "Confirm the purchased energy-meter model, CT ratio, IP, unit ID, and register map.",
        ], probe_type="modbus_tcp", fallback=("operator_evidence",),
    ),
    "aarco_2": _profile(
        "energy_meter", "medium",
        "Current-clamp energy metering avoids assumptions about the dust collector controller.",
        [], [
            "Record motor nameplate current and rated power.",
            "Confirm the purchased energy-meter model, CT ratio, IP, unit ID, and register map.",
        ], probe_type="modbus_tcp", fallback=("operator_evidence",),
    ),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _clean_text(value: object, field: str, limit: int = 1000) -> str | None:
    if value is None:
        return None
    result = " ".join(str(value).split())
    if not result:
        return None
    if len(result) > limit or any(ord(char) < 32 for char in result):
        raise ValueError(f"{field} is too long or contains control characters")
    return result


def _clean_path(value: object, field: str) -> str | None:
    if value in (None, ""):
        return None
    result = str(value).strip()
    if len(result) > 500 or not WINDOWS_PATH.match(result) or "\x00" in result:
        raise ValueError(f"{field} must be an absolute Windows drive path")
    return result


def _clean_host(value: object) -> str | None:
    if value in (None, ""):
        return None
    host = str(value).strip()
    if len(host) > 253 or "://" in host or any(char in host for char in "/?#@\\"):
        raise ValueError("controller_host must be a host name or IP address without a URL or port")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", host):
        raise ValueError("controller_host contains unsupported characters")
    return host


def sync_defaults(conn: sqlite3.Connection) -> None:
    now = _now()
    conn.execute(
        """INSERT OR IGNORE INTO machine_passports (machine_id,created_at,updated_at)
           SELECT id,?,? FROM machines""",
        (now, now),
    )
    conn.commit()


def _passport(conn: sqlite3.Connection, machine_key: str) -> dict:
    row = conn.execute(
        """SELECT mp.*,m.machine_key,m.name machine_name,m.type machine_type,m.brand,m.model
           FROM machine_passports mp JOIN machines m ON m.id=mp.machine_id
           WHERE m.machine_key=?""",
        (machine_key,),
    ).fetchone()
    if not row:
        raise KeyError(f"Unknown machine '{machine_key}'")
    return dict(row)


def _normalized_passport(current: dict, payload: dict) -> dict:
    result = {field: current.get(field) for field in PASSPORT_FIELDS}
    for field in PASSPORT_FIELDS:
        if field in payload:
            result[field] = payload[field]
    status = str(result.get("status") or "assumption")
    if status not in {"assumption", "inventory", "confirmed"}:
        raise ValueError("status must be assumption, inventory, or confirmed")
    result["status"] = status
    for field in (
        "asset_tag", "serial_number", "physical_location", "controller_vendor",
        "controller_model", "controller_software", "network_zone",
    ):
        result[field] = _clean_text(result.get(field), field, 200)
    result["notes"] = _clean_text(result.get("notes"), "notes", 2000)
    result["controller_host"] = _clean_host(result.get("controller_host"))
    result["log_folder"] = _clean_path(result.get("log_folder"), "log_folder")
    result["cnc_folder"] = _clean_path(result.get("cnc_folder"), "cnc_folder")
    year = result.get("manufacture_year")
    result["manufacture_year"] = None if year in (None, "") else int(year)
    if result["manufacture_year"] is not None and not 1900 <= result["manufacture_year"] <= 2200:
        raise ValueError("manufacture_year must be between 1900 and 2200")
    port = result.get("ssh_port")
    result["ssh_port"] = None if port in (None, "") else int(port)
    if result["ssh_port"] is not None and not 1 <= result["ssh_port"] <= 65535:
        raise ValueError("ssh_port must be between 1 and 65535")
    mac = str(result.get("mac_address") or "").strip() or None
    if mac and not MAC_ADDRESS.fullmatch(mac):
        raise ValueError("mac_address must contain six hexadecimal octets")
    result["mac_address"] = mac.upper().replace("-", ":") if mac else None
    strategy = str(result.get("telemetry_strategy") or "").strip() or None
    if strategy and strategy not in STRATEGIES:
        raise ValueError("Unsupported telemetry_strategy")
    result["telemetry_strategy"] = strategy
    if status == "confirmed":
        if not result["physical_location"]:
            raise ValueError("A confirmed passport requires physical_location")
        if not (result["asset_tag"] or result["serial_number"] or result["controller_model"]):
            raise ValueError("A confirmed passport requires an asset tag, serial number, or controller model")
        if not strategy:
            raise ValueError("A confirmed passport requires a telemetry_strategy decision")
        if strategy in NETWORK_STRATEGIES and not result["controller_host"]:
            raise ValueError("The selected network telemetry strategy requires controller_host")
    return result


def _update_passport(conn: sqlite3.Connection, machine_key: str, payload: dict, *,
                     actor: str, expected_version: int, commit: bool) -> dict:
    current = _passport(conn, machine_key)
    if int(current["version"]) != int(expected_version):
        raise ValueError(f"Passport for {machine_key} changed; refresh before saving")
    normalized = _normalized_passport(current, payload)
    changed = {field: normalized[field] for field in PASSPORT_FIELDS
               if normalized[field] != current.get(field)}
    if not changed:
        return current
    now = _now()
    if normalized["status"] == "confirmed":
        confirmed_by, confirmed_at = actor, now
    elif current.get("status") == "confirmed":
        confirmed_by, confirmed_at = None, None
    else:
        confirmed_by, confirmed_at = current.get("confirmed_by"), current.get("confirmed_at")
    assignments = ",".join(f"{field}=?" for field in PASSPORT_FIELDS)
    values = [normalized[field] for field in PASSPORT_FIELDS]
    cursor = conn.execute(
        f"""UPDATE machine_passports SET {assignments},confirmed_by=?,confirmed_at=?,
             version=version+1,updated_at=? WHERE id=? AND version=?""",
        (*values, confirmed_by, confirmed_at, now, current["id"], expected_version),
    )
    if cursor.rowcount != 1:
        raise ValueError(f"Passport for {machine_key} changed; refresh before saving")
    if normalized["status"] in {"inventory", "confirmed"}:
        conn.execute(
            "UPDATE machines SET has_maestro=?,has_opcua=? WHERE id=?",
            (int(normalized["telemetry_strategy"] == "maestro_agent"),
             int(normalized["telemetry_strategy"] == "opcua"), current["machine_id"]),
        )
    new_version = expected_version + 1
    details = {"machine_key": machine_key, "changed": changed,
               "from_status": current["status"], "to_status": normalized["status"]}
    conn.execute(
        """INSERT INTO machine_passport_events
           (machine_id,passport_version,event_type,actor,change_sha256,details_json,ts)
           VALUES (?,?,?,?,?,?,?)""",
        (current["machine_id"], new_version, "passport_updated", actor,
         _hash(details), _json(details), now),
    )
    if commit:
        conn.commit()
    return _passport(conn, machine_key)


def update_passport(conn: sqlite3.Connection, machine_key: str, payload: dict, *,
                    actor: str, expected_version: int) -> dict:
    sync_defaults(conn)
    return _update_passport(
        conn, machine_key, payload, actor=actor,
        expected_version=expected_version, commit=True,
    )


def _actual_host(value: object) -> str | None:
    host = str(value or "").strip()
    return host if host and host not in PLACEHOLDER_HOSTS and "TODO" not in host.upper() else None


def _endpoint_host(value: str | None) -> str | None:
    if not value:
        return None
    if value.startswith("opc.tcp://"):
        return urlparse(value).hostname
    host, separator, port = value.rpartition(":")
    return host if separator and port.isdigit() else value


def _hosts_match(first: object, second: object) -> bool:
    left = str(first or "").strip().rstrip(".").casefold()
    right = str(second or "").strip().rstrip(".").casefold()
    return bool(left and right and left == right)


def _age_seconds(ts: str | None) -> int | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - parsed).total_seconds()))
    except ValueError:
        return None


def _stage(key: str, label: str, ready: bool, detail: str, *,
           not_applicable: bool = False) -> dict:
    return {
        "key": key, "label": label,
        "status": "not_applicable" if not_applicable else ("ready" if ready else "pending"),
        "ready": ready or not_applicable, "detail": detail,
    }


def _next_action(stages: list[dict], profile: dict) -> str:
    pending = next((stage for stage in stages if not stage["ready"]), None)
    if not pending:
        return "Connection and calibration path is ready for production observation"
    actions = {
        "passport": "Capture the nameplate, physical location, controller, and chosen evidence strategy",
        "endpoint": "Enter the real static host/IP and network zone from the machine screen or switch",
        "transport": (
            "Run the read-only transport check, then use fingerprint-pinned SSH commissioning"
            if profile.get("probe_type") == "ssh"
            else "Run the read-only transport check from the central PC"
        ),
        "contract": "Commission and explicitly approve the machine data contract",
        "live": "Run a representative job and confirm timestamped events reach HIVE",
        "calibration": "Collect varied linked cycles until an active cycle model passes validation",
    }
    return actions[pending["key"]]


def _loads(value: str | None, default: object) -> object:
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


def _mission_row(conn: sqlite3.Connection, machine_id: int, *, active_only: bool = False) -> dict | None:
    condition = "AND status IN ('in_progress','paused')" if active_only else ""
    row = conn.execute(
        f"""SELECT * FROM factory_commissioning_missions
            WHERE machine_id=? {condition}
            ORDER BY CASE WHEN status IN ('in_progress','paused') THEN 0 ELSE 1 END,id DESC
            LIMIT 1""",
        (machine_id,),
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["step_state"] = _loads(result.pop("step_state_json"), {})
    return result


def _mission_event(conn: sqlite3.Connection, mission_id: int, event_type: str,
                   actor: str, details: dict) -> None:
    conn.execute(
        """INSERT INTO factory_commissioning_mission_events
           (mission_id,event_type,actor,details_json,evidence_sha256,ts)
           VALUES (?,?,?,?,?,?)""",
        (mission_id, event_type, actor, _json(details), _hash(details), _now()),
    )


def _preflight_context(industrial: dict[str, list[dict]]) -> dict:
    return {
        "tooling_ready": all((ROOT / path).is_file() for path in COMMISSIONING_ASSETS),
        "identity": remote_setup.identity_status(),
        "agent_payload": mqtt_security.agent_payload_status(),
        "industrial_keys": set(industrial),
    }


def _preflight_steps(machine: dict, context: dict) -> list[dict]:
    strategy = machine["effective_strategy"]
    steps = [{
        "key": "tooling", "label": "Commissioning tooling",
        "complete": context["tooling_ready"], "requires_site": False,
        "detail": "Required read-only probes, capture tools, and evidence workflows are present",
        "action_surface": "field_pack", "action_label": "Download field pack",
    }]
    if strategy == "maestro_agent":
        identity = context["identity"]
        payload = context["agent_payload"]
        steps.extend([
            {
                "key": "ssh_identity", "label": "Deployment identity",
                "complete": identity["status"] == "ready", "requires_site": False,
                "detail": identity.get("detail") or f"SSH identity {identity['status']}",
                "action_surface": "setup", "action_label": "Prepare SSH identity",
            },
            {
                "key": "agent_payload", "label": "Offline agent payload",
                "complete": bool(payload.get("ready")), "requires_site": False,
                "detail": payload.get("detail") or f"Agent payload {payload.get('status', 'missing')}",
                "action_surface": "setup", "action_label": "Prepare agent payload",
            },
        ])
    elif strategy in {"modbus_tcp", "opcua", "mqtt_json", "energy_meter"}:
        seeded = machine["machine_key"] in context["industrial_keys"]
        steps.append({
            "key": "industrial_template", "label": "Industrial contract template",
            "complete": seeded, "requires_site": False,
            "detail": "A draft read-only industrial profile is available" if seeded
                      else "No draft industrial profile exists for this machine",
            "action_surface": "industrial", "action_label": "Review contract template",
        })
    else:
        steps.append({
            "key": "evidence_workflow", "label": "Evidence workflow",
            "complete": (ROOT / "src/commissioning_evidence.py").is_file(),
            "requires_site": False,
            "detail": "Timed operator-evidence capture and review workflow is available",
            "action_surface": "evidence", "action_label": "Review evidence template",
        })
    return steps


def _step_action(strategy: str, key: str) -> tuple[str, str]:
    if key in {"passport", "endpoint"}:
        return "machine_links", "Capture site facts"
    if key == "transport":
        return ("setup", "Commission SSH") if strategy == "maestro_agent" else (
            "industrial", "Test read-only transport")
    if key == "contract":
        if strategy == "maestro_agent":
            return "machine_logs", "Approve representative log"
        if strategy == "operator_evidence":
            return "evidence", "Capture field evidence"
        return "industrial", "Approve signal contract"
    if key == "live":
        if strategy == "maestro_agent":
            return "setup", "Verify agent heartbeat"
        if strategy == "operator_evidence":
            return "evidence", "Record representative run"
        return "industrial", "Poll representative run"
    return "evidence", "Calibrate cycle model"


def _playbook(machine: dict, context: dict) -> dict:
    strategy = machine["effective_strategy"]
    preflight = _preflight_steps(machine, context)
    prior_complete = all(step["complete"] for step in preflight)
    steps = []
    for item in preflight:
        steps.append({
            **item, "phase": "offsite", "estimated_minutes": 0,
            "status": "complete" if item["complete"] else "available",
        })
    for stage in machine["stages"]:
        surface, action_label = _step_action(strategy, stage["key"])
        complete = bool(stage["ready"])
        status = "complete" if complete else "available" if prior_complete else "blocked"
        steps.append({
            "key": stage["key"], "label": stage["label"], "phase": "factory",
            "complete": complete, "status": status, "requires_site": True,
            "detail": stage["detail"], "estimated_minutes": SITE_MINUTES[stage["key"]],
            "action_surface": surface, "action_label": action_label,
        })
        if not complete:
            prior_complete = False
    completed = sum(step["complete"] for step in steps)
    current = next((step for step in steps if not step["complete"]), None)
    evidence_state = {step["key"]: "complete" if step["complete"] else "pending" for step in steps}
    evidence_fingerprint = {
        "strategy": strategy, "passport_version": machine["passport"]["version"],
        "steps": evidence_state,
    }
    return {
        "method_version": MISSION_METHOD_VERSION, "strategy": strategy,
        "steps": steps, "current_step": current,
        "offsite_ready": all(step["complete"] for step in steps if step["phase"] == "offsite"),
        "progress_percent": round(completed / len(steps) * 100) if steps else 100,
        "estimated_site_minutes_remaining": sum(
            step["estimated_minutes"] for step in steps
            if step["phase"] == "factory" and not step["complete"]
        ),
        "evidence_state": evidence_state, "evidence_sha256": _hash(evidence_fingerprint),
    }


def _sync_active_mission(conn: sqlite3.Connection, machine: dict, context: dict) -> tuple[dict | None, bool]:
    mission = _mission_row(conn, machine["passport"]["machine_id"], active_only=True)
    if not mission:
        return None, False
    plan = _playbook(machine, context)
    drift = mission["strategy"] != machine["effective_strategy"]
    complete = all(value == "complete" for value in plan["evidence_state"].values()) and not drift
    target_status = "completed" if complete else mission["status"]
    if mission["evidence_sha256"] == plan["evidence_sha256"] and target_status == mission["status"]:
        return mission, False
    now = _now()
    conn.execute(
        """UPDATE factory_commissioning_missions
           SET status=?,step_state_json=?,evidence_sha256=?,completed_at=?,
               version=version+1,updated_at=? WHERE id=?""",
        (target_status, _json(plan["evidence_state"]), plan["evidence_sha256"],
         now if target_status == "completed" else mission.get("completed_at"), now, mission["id"]),
    )
    event_type = "mission_completed" if target_status == "completed" else "evidence_reconciled"
    _mission_event(conn, mission["id"], event_type, "evidence-reconciler", {
        "machine_key": machine["machine_key"], "strategy_drift": drift,
        "evidence_state": plan["evidence_state"],
    })
    return _mission_row(conn, machine["passport"]["machine_id"]), True


def _mission_view(conn: sqlite3.Connection, machine: dict, mission: dict | None,
                  context: dict) -> dict:
    plan = _playbook(machine, context)
    events = []
    if mission:
        events = [dict(row) for row in conn.execute(
            """SELECT id,event_type,actor,details_json,evidence_sha256,ts
               FROM factory_commissioning_mission_events WHERE mission_id=?
               ORDER BY id DESC LIMIT 20""",
            (mission["id"],),
        )]
        for event in events:
            event["details"] = _loads(event.pop("details_json"), {})
    drift = bool(mission and mission["strategy"] != machine["effective_strategy"])
    blockers = [step["detail"] for step in plan["steps"]
                if step["phase"] == "offsite" and not step["complete"]]
    if drift:
        blockers.insert(0, "Telemetry strategy changed after this mission started; cancel and start a new mission")
    return {
        **plan,
        "id": mission["id"] if mission else None,
        "status": mission["status"] if mission else "not_started",
        "version": mission["version"] if mission else None,
        "strategy": mission["strategy"] if mission else machine["effective_strategy"],
        "current_strategy": machine["effective_strategy"],
        "strategy_drift": drift, "passport_version_at_start": mission.get("passport_version_at_start") if mission else None,
        "started_by": mission.get("started_by") if mission else None,
        "started_at": mission.get("started_at") if mission else None,
        "paused_at": mission.get("paused_at") if mission else None,
        "completed_at": mission.get("completed_at") if mission else None,
        "cancelled_at": mission.get("cancelled_at") if mission else None,
        "notes": mission.get("notes") if mission else None,
        "evidence_current": bool(mission and mission["evidence_sha256"] == plan["evidence_sha256"]),
        "blockers": blockers, "events": events,
        "guardrail": "Mission steps complete only from authoritative HIVE evidence; operators cannot tick gates complete manually.",
    }


def snapshot(conn: sqlite3.Connection, cfg_path: Path) -> dict:
    sync_defaults(conn)
    cfg = yaml.safe_load(Path(cfg_path).read_text()) or {}
    maestro = {item["machine_key"]: item for item in cfg.get("maestro_agents", [])}
    energy = {item["machine_key"]: item for item in cfg.get("energy_meters", [])}
    remote = {row["machine_key"]: dict(row) for row in conn.execute(
        """SELECT m.machine_key,rsh.* FROM remote_setup_hosts rsh
           JOIN machines m ON m.id=rsh.machine_id"""
    )}
    industrial: dict[str, list[dict]] = {}
    for row in conn.execute(
        """SELECT ip.*,m.machine_key FROM industrial_profiles ip
           LEFT JOIN machines m ON m.id=ip.machine_id"""
    ):
        if row["machine_key"]:
            industrial.setdefault(row["machine_key"], []).append(dict(row))
    latest_probe = {row["machine_key"]: dict(row) for row in conn.execute(
        """SELECT m.machine_key,fcp.* FROM factory_connection_probes fcp
           JOIN machines m ON m.id=fcp.machine_id
           WHERE fcp.id IN (SELECT MAX(id) FROM factory_connection_probes GROUP BY machine_id)"""
    )}
    latest_signal = {row["machine_key"]: row["latest_ts"] for row in conn.execute(
        """SELECT m.machine_key,MAX(x.ts) latest_ts FROM machines m LEFT JOIN (
             SELECT machine_id,last_heartbeat_at ts FROM agent_status
             UNION ALL SELECT machine_id,last_event_at ts FROM agent_status
           ) x ON x.machine_id=m.id GROUP BY m.id"""
    )}
    latest_operator_evidence = {row["machine_key"]: row["latest_ts"] for row in conn.execute(
        """SELECT m.machine_key,MAX(ceo.measured_at) latest_ts FROM machines m
           LEFT JOIN commissioning_evidence_studies ces ON ces.machine_id=m.id
           LEFT JOIN commissioning_evidence_observations ceo ON ceo.study_id=ces.id
                AND ceo.validity='accepted' GROUP BY m.id"""
    )}
    installed = {row["machine_key"] for row in conn.execute(
        """SELECT DISTINCT m.machine_key FROM remote_setup_runs rsr
           JOIN machines m ON m.id=rsr.machine_id
           WHERE rsr.action='install' AND rsr.status='succeeded'"""
    )}
    approved_maestro = set()
    for row in conn.execute(
        """SELECT mapping_json FROM connector_mapping_versions
           WHERE connector_key='maestro_logs' AND status='approved'"""
    ):
        scope = _loads(row["mapping_json"], {}).get("machine_key")
        if scope:
            approved_maestro.add(scope)
    evidence = {row["machine_key"]: int(row["count"]) for row in conn.execute(
        """SELECT m.machine_key,COUNT(ceo.id) count FROM machines m
           LEFT JOIN commissioning_evidence_studies ces ON ces.machine_id=m.id
           LEFT JOIN commissioning_evidence_observations ceo ON ceo.study_id=ces.id
                AND ceo.validity='accepted' GROUP BY m.id"""
    )}
    active_models = {row["machine_key"] for row in conn.execute(
        """SELECT DISTINCT m.machine_key FROM cycle_models cm
           JOIN machines m ON m.id=cm.machine_id WHERE cm.status='active'"""
    )}
    preflight_context = _preflight_context(industrial)
    machines = []
    for row in conn.execute("SELECT * FROM machines WHERE active=1 ORDER BY id"):
        machine = dict(row)
        key = machine["machine_key"]
        passport = _passport(conn, key)
        research = RESEARCH_PROFILES.get(key, _profile(
            "operator_evidence", "low", "No researched machine interface is available.",
            [], MANUAL_CHECKS,
        ))
        preferred = passport.get("telemetry_strategy") or research["preferred_strategy"]
        cfg_item = maestro.get(key) or energy.get(key) or {}
        cfg_host = _actual_host(cfg_item.get("host") or cfg_item.get("modbus_host"))
        industrial_host = next((
            _actual_host(_endpoint_host(item.get("endpoint")))
            for item in industrial.get(key, []) if item.get("endpoint")
        ), None)
        endpoint = _actual_host(passport.get("controller_host")) or cfg_host or industrial_host
        network_required = preferred in NETWORK_STRATEGIES
        remote_profile = remote.get(key)
        probe = latest_probe.get(key)
        industrial_verified = any(
            bool(item["verified"])
            and _hosts_match(_endpoint_host(item.get("endpoint")), endpoint)
            for item in industrial.get(key, [])
        )
        transport_ready = bool(
            (remote_profile and remote_profile["status"] == "trusted"
             and _hosts_match(remote_profile["host"], endpoint))
            or (probe and probe["status"] == "reachable"
                and _hosts_match(probe["endpoint_host"], endpoint))
            or industrial_verified
        )
        accepted = evidence.get(key, 0)
        contract_ready = bool(
            key in approved_maestro or industrial_verified
            or (preferred == "operator_evidence" and accepted > 0)
        )
        if key in approved_maestro:
            contract_detail = "Machine-specific Maestro log contract approved"
        elif industrial_verified:
            contract_detail = "Immutable industrial signal contract approved"
        elif preferred == "operator_evidence" and accepted > 0:
            contract_detail = f"{accepted} accepted field observations"
        elif key in installed:
            contract_detail = "Agent installed; representative log contract still needs approval"
        else:
            contract_detail = "No approved real data path"
        signal_at = (latest_operator_evidence.get(key)
                     if preferred == "operator_evidence" else latest_signal.get(key))
        age = _age_seconds(signal_at)
        live_ready = age is not None and age <= 180
        stages = [
            _stage("passport", "Machine passport", passport["status"] == "confirmed",
                   f"Version {passport['version']} - {passport['status']}"),
            _stage("endpoint", "Site endpoint", bool(endpoint),
                   endpoint or "No site-confirmed host/IP", not_applicable=not network_required),
            _stage("transport", "Transport reachability", transport_ready,
                   "Trusted or read-only transport evidence exists" if transport_ready else "No passing site transport evidence",
                   not_applicable=not network_required),
            _stage("contract", "Data contract", contract_ready,
                   contract_detail),
            _stage("live", "Live reporting", live_ready,
                   f"Last signal {age}s ago" if age is not None else "No machine signal received"),
            _stage("calibration", "Cycle calibration", key in active_models,
                   "Active production cycle model" if key in active_models else f"{accepted} accepted field observations"),
        ]
        weights = {"passport": 15, "endpoint": 15, "transport": 20,
                   "contract": 25, "live": 15, "calibration": 10}
        score = sum(weights[stage["key"]] for stage in stages if stage["ready"])
        sources = [{**SOURCES[source_key], "key": source_key}
                   for source_key in research["source_keys"] if source_key in SOURCES]
        machines.append({
            "machine_key": key, "name": machine["name"], "type": machine["type"],
            "brand": machine["brand"], "model": machine["model"],
            "passport": passport, "research": {**research, "sources": sources},
            "effective_strategy": preferred, "endpoint": endpoint,
            "agent_installed": key in installed,
            "maestro_contract_approved": key in approved_maestro,
            "latest_probe": probe, "last_signal_at": signal_at,
            "age_seconds": age, "accepted_field_observations": accepted,
            "stages": stages, "readiness_score": score,
            "status": "ready" if score == 100 else "commissioning" if score >= 50 else "needs_site_value",
            "next_action": _next_action(stages, research),
        })
    mission_changed = False
    for machine in machines:
        mission, changed = _sync_active_mission(conn, machine, preflight_context)
        mission_changed = mission_changed or changed
        if not mission:
            mission = _mission_row(conn, machine["passport"]["machine_id"])
        machine["mission"] = _mission_view(conn, machine, mission, preflight_context)
    if mission_changed:
        conn.commit()
    summary = {
        "machines": len(machines),
        "passports_confirmed": sum(item["passport"]["status"] == "confirmed" for item in machines),
        "endpoints_ready": sum(next(stage for stage in item["stages"] if stage["key"] == "endpoint")["ready"] for item in machines),
        "transports_ready": sum(next(stage for stage in item["stages"] if stage["key"] == "transport")["ready"] for item in machines),
        "contracts_ready": sum(next(stage for stage in item["stages"] if stage["key"] == "contract")["ready"] for item in machines),
        "online": sum(next(stage for stage in item["stages"] if stage["key"] == "live")["ready"] for item in machines),
        "calibrated": sum(next(stage for stage in item["stages"] if stage["key"] == "calibration")["ready"] for item in machines),
        "plug_and_play_ready": sum(item["readiness_score"] == 100 for item in machines),
        "missions_active": sum(item["mission"]["status"] in {"in_progress", "paused"} for item in machines),
        "missions_completed": sum(item["mission"]["status"] == "completed" for item in machines),
        "offsite_ready": sum(item["mission"]["offsite_ready"] for item in machines),
    }
    return {
        "generated_at": _now(), "method_version": METHOD_VERSION,
        "summary": summary, "machines": machines,
        "central_sources": [SOURCES["cabinet_vision"]],
        "guardrail": (
            "Research profiles are assumptions. A passing transport check proves reachability only; "
            "real data contracts and calibration require separate approval."
        ),
    }


def start_mission(conn: sqlite3.Connection, cfg_path: Path, machine_key: str, *,
                  actor: str, notes: str | None = None) -> dict:
    actor_name = _clean_text(actor, "actor", 120)
    if not actor_name:
        raise ValueError("A named commissioning owner is required")
    note = _clean_text(notes, "notes", 2000)
    state = snapshot(conn, cfg_path)
    machine = next((item for item in state["machines"] if item["machine_key"] == machine_key), None)
    if not machine:
        raise KeyError(f"Unknown machine '{machine_key}'")
    active = _mission_row(conn, machine["passport"]["machine_id"], active_only=True)
    if active:
        return machine["mission"]
    now = _now()
    plan = machine["mission"]
    cursor = conn.execute(
        """INSERT INTO factory_commissioning_missions
           (machine_id,strategy,status,passport_version_at_start,step_state_json,
            evidence_sha256,started_by,started_at,notes,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (machine["passport"]["machine_id"], machine["effective_strategy"], "in_progress",
         machine["passport"]["version"], _json(plan["evidence_state"]),
         plan["evidence_sha256"], actor_name, now, note, now, now),
    )
    _mission_event(conn, cursor.lastrowid, "mission_started", actor_name, {
        "machine_key": machine_key, "strategy": machine["effective_strategy"],
        "passport_version": machine["passport"]["version"], "notes": note,
    })
    conn.commit()
    refreshed = snapshot(conn, cfg_path)
    return next(item["mission"] for item in refreshed["machines"] if item["machine_key"] == machine_key)


def mission_action(conn: sqlite3.Connection, cfg_path: Path, machine_key: str, *,
                   action: str, actor: str, expected_version: int,
                   notes: str | None = None) -> dict:
    if action not in {"pause", "resume", "cancel"}:
        raise ValueError("Mission action must be pause, resume, or cancel")
    actor_name = _clean_text(actor, "actor", 120)
    if not actor_name:
        raise ValueError("A named commissioning owner is required")
    note = _clean_text(notes, "notes", 2000)
    passport = _passport(conn, machine_key)
    mission = _mission_row(conn, passport["machine_id"], active_only=True)
    if not mission:
        raise ValueError("No active commissioning mission exists for this machine")
    if mission["version"] != int(expected_version):
        raise ValueError("Commissioning mission changed; refresh before updating it")
    allowed = {
        "pause": mission["status"] == "in_progress",
        "resume": mission["status"] == "paused",
        "cancel": mission["status"] in {"in_progress", "paused"},
    }
    if not allowed[action]:
        raise ValueError(f"Cannot {action} a {mission['status']} commissioning mission")
    now = _now()
    target = {"pause": "paused", "resume": "in_progress", "cancel": "cancelled"}[action]
    paused_at = now if action == "pause" else None if action == "resume" else mission.get("paused_at")
    cancelled_at = now if action == "cancel" else None
    conn.execute(
        """UPDATE factory_commissioning_missions
           SET status=?,paused_at=?,cancelled_at=?,notes=COALESCE(?,notes),
               version=version+1,updated_at=? WHERE id=? AND version=?""",
        (target, paused_at, cancelled_at, note, now, mission["id"], expected_version),
    )
    event_type = {"pause": "mission_paused", "resume": "mission_resumed",
                  "cancel": "mission_cancelled"}[action]
    _mission_event(conn, mission["id"], event_type, actor_name, {
        "machine_key": machine_key, "from_status": mission["status"],
        "to_status": target, "notes": note,
    })
    conn.commit()
    refreshed = snapshot(conn, cfg_path)
    return next(item["mission"] for item in refreshed["machines"] if item["machine_key"] == machine_key)


def _assert_private_host(host: str, port: int) -> None:
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )}
    except socket.gaierror as error:
        raise ValueError(f"Could not resolve factory endpoint '{host}'") from error
    if not addresses:
        raise ValueError(f"Could not resolve factory endpoint '{host}'")
    if any(not (
        ipaddress.ip_address(address).is_private
        or ipaddress.ip_address(address).is_loopback
        or ipaddress.ip_address(address).is_link_local
    ) for address in addresses):
        raise ValueError("Factory probes are limited to private, loopback, or link-local endpoints")


def connection_probe(conn: sqlite3.Connection, cfg_path: Path, machine_key: str,
                     *, probe_type: str, host: str | None, port: int | None,
                     execute: bool, timeout_s: float, actor: str) -> dict:
    if probe_type not in PROBE_PORTS:
        raise ValueError("Unsupported probe_type")
    if not 0.25 <= timeout_s <= 10:
        raise ValueError("timeout_s must be between 0.25 and 10 seconds")
    state = snapshot(conn, cfg_path)
    machine = next((item for item in state["machines"] if item["machine_key"] == machine_key), None)
    if not machine:
        raise KeyError(f"Unknown machine '{machine_key}'")
    target_host = _clean_host(host) or machine["endpoint"]
    target_port = int(port or PROBE_PORTS[probe_type] or machine["passport"].get("ssh_port") or 0)
    if not target_host:
        raise ValueError("Enter a site-confirmed host before probing")
    if not 1 <= target_port <= 65535:
        raise ValueError("A valid target port is required")
    preview = {
        "machine_key": machine_key, "probe_type": probe_type,
        "host": target_host, "port": target_port, "execute": execute,
        "mode": "live" if execute else "preview",
        "will_write_device": False, "will_enable_contract": False,
        "guardrail": "This check opens a TCP connection only. It never writes a register or sends a control command.",
    }
    if not execute:
        return {**preview, "status": "preview_ready"}
    _assert_private_host(target_host, target_port)
    started = time.monotonic()
    protocol_evidence = None
    try:
        with socket.create_connection((target_host, target_port), timeout=timeout_s) as stream:
            stream.settimeout(timeout_s)
            if probe_type == "ssh":
                try:
                    banner = stream.recv(255).decode("ascii", errors="replace").strip()
                except socket.timeout:
                    banner = ""
                protocol_evidence = banner[:255] or None
                status = "reachable" if banner.startswith("SSH-") else "protocol_mismatch"
                detail = "SSH banner received" if status == "reachable" else "TCP connected, but no SSH banner was received"
            else:
                status = "reachable"
                detail = "TCP connection accepted; protocol data was not read"
    except OSError as error:
        status = "unreachable"
        detail = str(error)[:1000]
    latency_ms = round((time.monotonic() - started) * 1000, 2)
    evidence = {
        "machine_key": machine_key, "probe_type": probe_type, "host": target_host,
        "port": target_port, "status": status, "latency_ms": latency_ms,
        "protocol_evidence": protocol_evidence, "detail": detail,
    }
    machine_id = machine["passport"]["machine_id"]
    cursor = conn.execute(
        """INSERT INTO factory_connection_probes
           (machine_id,probe_type,endpoint_host,endpoint_port,status,latency_ms,
            protocol_evidence,detail,evidence_sha256,actor,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (machine_id, probe_type, target_host, target_port, status, latency_ms,
         protocol_evidence, detail, _hash(evidence), actor, _now()),
    )
    conn.commit()
    return {**preview, **evidence, "probe_id": cursor.lastrowid,
            "evidence_sha256": _hash(evidence)}


def _parse_csv(csv_text: str) -> tuple[list[str], list[dict]]:
    if len(csv_text.encode("utf-8")) > 10_000_000:
        raise ValueError("Factory inventory CSV is limited to 10 MB")
    try:
        reader = csv.DictReader(io.StringIO(csv_text))
        headers = [str(header or "").strip() for header in (reader.fieldnames or [])]
        if not headers:
            raise ValueError("CSV header is missing")
        if len(headers) != len(set(headers)):
            raise ValueError("CSV contains duplicate columns")
        unknown = sorted(set(headers) - set(CSV_FIELDS))
        if unknown:
            raise ValueError("Unknown CSV columns: " + ", ".join(unknown))
        if "machine_key" not in headers or "expected_version" not in headers:
            raise ValueError("CSV requires machine_key and expected_version columns")
        return headers, [dict(row) for row in reader]
    except csv.Error as error:
        raise ValueError(f"Invalid CSV: {error}") from error


def import_inventory(conn: sqlite3.Connection, csv_text: str, *, apply: bool,
                     actor: str) -> dict:
    sync_defaults(conn)
    headers, rows = _parse_csv(csv_text)
    if not rows:
        raise ValueError("CSV has no inventory rows")
    if len(rows) > 500:
        raise ValueError("CSV is limited to 500 inventory rows")
    seen = set()
    results = []
    prepared = []
    for index, raw in enumerate(rows, start=2):
        key = str(raw.get("machine_key") or "").strip()
        errors = []
        if not key:
            errors.append("machine_key is required")
        elif key in seen:
            errors.append("duplicate machine_key in CSV")
        seen.add(key)
        try:
            current = _passport(conn, key) if key else None
        except KeyError as error:
            current = None
            errors.append(str(error))
        try:
            expected = int(str(raw.get("expected_version") or "").strip())
        except ValueError:
            expected = None
            errors.append("expected_version must be an integer")
        if current and expected is not None and expected != current["version"]:
            errors.append(f"passport version is {current['version']}, not {expected}")
        payload = {}
        if current:
            for field in PASSPORT_FIELDS:
                if field not in headers:
                    continue
                value = raw.get(field)
                if value is None or not str(value).strip():
                    continue
                payload[field] = value
            if payload and "status" not in payload and current["status"] == "assumption":
                payload["status"] = "inventory"
            try:
                normalized = _normalized_passport(current, payload)
                changed = any(normalized[field] != current.get(field) for field in PASSPORT_FIELDS)
            except (TypeError, ValueError) as error:
                normalized = None
                changed = False
                errors.append(str(error))
        else:
            normalized = None
            changed = False
        result = {"row": index, "machine_key": key, "valid": not errors,
                  "changed": changed, "errors": errors}
        results.append(result)
        if not errors and changed:
            prepared.append((key, payload, expected))
    valid = not any(result["errors"] for result in results)
    applied = 0
    if apply:
        if not valid:
            raise ValueError("Inventory import is atomic; fix every invalid row before applying")
        try:
            for key, payload, expected in prepared:
                _update_passport(conn, key, payload, actor=actor,
                                 expected_version=expected, commit=False)
                applied += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {
        "mode": "apply" if apply else "preview", "valid": valid,
        "rows_seen": len(rows), "rows_changed": len(prepared),
        "rows_applied": applied, "results": results,
        "source_sha256": hashlib.sha256(csv_text.encode("utf-8")).hexdigest(),
    }


def _csv_bytes(fields: tuple[str, ...], rows: list[dict]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, "") if row.get(field) is not None else ""
                         for field in fields})
    return output.getvalue().encode("utf-8")


def _machine_checklist(machine: dict) -> bytes:
    research = machine["research"]
    lines = [
        f"# {machine['name']} ({machine['machine_key']})",
        "",
        f"Preferred first path: `{research['preferred_strategy']}`",
        f"Research confidence: **{research['confidence']}**",
        "",
        research["rationale"],
        "",
        "## Capture on site",
        "",
    ]
    lines.extend(f"- [ ] {item}" for item in research["verify_on_site"])
    lines.extend([
        "", "## Commissioning sequence", "",
        "- [ ] Fill and import this machine's row in `machine-inventory.csv`.",
        "- [ ] Confirm the passport in HIVE with the physical machine in view.",
        "- [ ] Run only the proposed read-only transport check.",
        "- [ ] Commission and approve the exact data contract separately.",
        "- [ ] Run a representative job and confirm timestamps and identifiers.",
        "- [ ] Capture varied field evidence before enabling optimization decisions.",
        "", "## Official evidence", "",
    ])
    if research["sources"]:
        lines.extend(f"- [{item['label']}]({item['url']}): {item['finding']}"
                     for item in research["sources"])
    else:
        lines.append("- No model-specific official integration evidence is available; inspect before choosing a connector.")
    return ("\n".join(lines) + "\n").encode("utf-8")


def field_pack(conn: sqlite3.Connection, cfg_path: Path) -> tuple[bytes, dict]:
    state = snapshot(conn, cfg_path)
    files: dict[str, bytes] = {}
    inventory_rows = []
    probe_rows = []
    mission_rows = []
    mission_machines = []
    for machine in state["machines"]:
        passport = machine["passport"]
        inventory_rows.append({
            "machine_key": machine["machine_key"],
            "expected_version": passport["version"],
            **{field: passport.get(field) for field in PASSPORT_FIELDS},
        })
        research = machine["research"]
        probe_rows.append({
            "machine_key": machine["machine_key"], "machine_name": machine["name"],
            "preferred_strategy": research["preferred_strategy"],
            "probe_type": research.get("probe_type") or "not_applicable",
            "host": machine.get("endpoint") or "", "port": research.get("default_port") or "",
            "confidence": research["confidence"], "execute": "NO - preview first",
        })
        mission = machine["mission"]
        mission_machines.append({
            "machine_key": machine["machine_key"], "machine_name": machine["name"],
            "strategy": machine["effective_strategy"], "offsite_ready": mission["offsite_ready"],
            "estimated_site_minutes_remaining": mission["estimated_site_minutes_remaining"],
            "blockers": mission["blockers"], "steps": mission["steps"],
        })
        for position, step in enumerate(mission["steps"], start=1):
            mission_rows.append({
                "machine_key": machine["machine_key"], "machine_name": machine["name"],
                "strategy": machine["effective_strategy"], "position": position,
                "phase": step["phase"], "step_key": step["key"], "step": step["label"],
                "status": step["status"], "requires_site": "yes" if step["requires_site"] else "no",
                "estimated_minutes": step["estimated_minutes"], "evidence": step["detail"],
                "action": step["action_label"],
            })
        files[f"machines/{machine['machine_key']}.md"] = _machine_checklist(machine)
    files["machine-inventory.csv"] = _csv_bytes(CSV_FIELDS, inventory_rows)
    files["probe-plan.csv"] = _csv_bytes(
        ("machine_key", "machine_name", "preferred_strategy", "probe_type", "host",
         "port", "confidence", "execute"), probe_rows,
    )
    files["official-source-register.csv"] = _csv_bytes(
        ("source_key", "label", "url", "finding"),
        [{"source_key": key, **value} for key, value in SOURCES.items()],
    )
    files["commissioning-plan.csv"] = _csv_bytes(
        ("machine_key", "machine_name", "strategy", "position", "phase", "step_key",
         "step", "status", "requires_site", "estimated_minutes", "evidence", "action"),
        mission_rows,
    )
    files["commissioning-plan.json"] = (
        json.dumps({
            "method_version": MISSION_METHOD_VERSION, "generated_at": state["generated_at"],
            "guardrail": "Step completion is evidence-derived; this file is a plan, not approval.",
            "machines": mission_machines,
        }, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    files["README.md"] = (
        "# HIVE OS Factory Readiness Pack\n\n"
        "This pack converts the first factory visit into a controlled evidence exercise.\n\n"
        "1. Keep the central PC disconnected from machine control outputs.\n"
        "2. Fill `machine-inventory.csv` from physical nameplates, HMIs, and the managed switch.\n"
        "3. Import in preview mode; fix every row before atomic apply.\n"
        "4. Confirm each passport while standing at the matching machine.\n"
        "5. Preview and then run only HIVE's read-only transport checks.\n"
        "6. Commission SSH agents or industrial data contracts through their separate approval gates.\n"
        "7. Follow `commissioning-plan.csv`; HIVE derives completion from recorded evidence.\n"
        "8. Run representative jobs and collect field evidence before optimization.\n\n"
        "A researched candidate is never proof that the installed machine exposes that interface.\n"
    ).encode("utf-8")
    generated = _now()
    declared = [
        {"path": path, "size": len(content), "sha256": _sha_bytes(content)}
        for path, content in sorted(files.items())
    ]
    manifest = {
        "format": PACK_FORMAT, "format_version": 1,
        "method_version": METHOD_VERSION, "generated_at": generated,
        "production_eligible": False, "files": declared,
        "summary": state["summary"],
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    files["manifest.json"] = manifest_bytes
    checksum_lines = [f"{_sha_bytes(content)}  {path}" for path, content in sorted(files.items())]
    files["SHA256SUMS"] = ("\n".join(checksum_lines) + "\n").encode("ascii")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in sorted(files.items()):
            archive.writestr(path, content)
    bundle = output.getvalue()
    metadata = {
        "filename": f"hive-factory-readiness-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.zip",
        "sha256": _sha_bytes(bundle), "size": len(bundle), "file_count": len(files),
        "generated_at": generated, "format": PACK_FORMAT,
    }
    return bundle, metadata
