"""Read-only industrial telemetry commissioning, polling, and retention."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import math
import os
import re
import sqlite3
import socket
import struct
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlparse

import event_pipeline
import energy_intelligence


SIGNAL_DEFINITIONS = {
    "power_w": {"unit": "W", "minimum": -1_000_000, "maximum": 20_000_000, "simulation": 6_250.0},
    "energy_kwh": {"unit": "kWh", "minimum": 0, "maximum": 1_000_000_000, "simulation": 12_345.6},
    "current_a": {"unit": "A", "minimum": 0, "maximum": 100_000, "simulation": 12.8},
    "voltage_v": {"unit": "V", "minimum": 0, "maximum": 1_500, "simulation": 415.2},
    "power_factor": {"unit": "ratio", "minimum": -1, "maximum": 1, "simulation": 0.92},
    "frequency_hz": {"unit": "Hz", "minimum": 0, "maximum": 100, "simulation": 50.0},
    "pressure_bar": {"unit": "bar", "minimum": -1, "maximum": 1000, "simulation": 7.2},
    "temperature_c": {"unit": "degC", "minimum": -100, "maximum": 1000, "simulation": 118.0},
    "running": {"unit": "bool", "minimum": 0, "maximum": 1, "simulation": True},
    "alarm_active": {"unit": "bool", "minimum": 0, "maximum": 1, "simulation": False},
    "cycle_counter": {"unit": "count", "minimum": 0, "maximum": 10_000_000_000, "simulation": 128},
    "recipe_id": {"unit": "text", "simulation": "RECIPE-01"},
    "program_id": {"unit": "text", "simulation": "PROGRAM-01"},
}

PROTOCOLS = {"modbus_tcp", "opcua", "mqtt_json"}
MODBUS_FUNCTIONS = {"input_register", "holding_register", "discrete_input", "coil"}
MODBUS_DATA_TYPES = {"float32", "float64", "int16", "uint16", "int32", "uint32", "bool"}
SIGNAL_KEY = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
PROFILE_KEY = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,127}$")


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


def _eastron_signal(key: str, address: int, unit: str, *, required: bool = False) -> dict:
    return {
        "key": key,
        "function": "input_register",
        "address": address,
        "data_type": "float32",
        "word_order": "big",
        "byte_order": "big",
        "scale": 1.0,
        "offset": 0.0,
        "unit": unit,
        "required": required,
    }


# Addresses are zero-based Modbus PDU addresses from Eastron SDM630 protocol
# v1.8. They are candidates until the purchased meter model is confirmed.
EASTRON_SDM630_SIGNALS = [
    _eastron_signal("power_w", 52, "W", required=True),
    _eastron_signal("energy_kwh", 72, "kWh"),
    _eastron_signal("current_a", 46, "A"),
    _eastron_signal("voltage_v", 42, "V"),
    _eastron_signal("power_factor", 62, "ratio"),
    _eastron_signal("frequency_hz", 70, "Hz"),
]

DEFAULT_SETTINGS = {
    "unit_id": 1,
    "timeout_s": 3,
    "on_threshold_w": 5_000,
    "idle_threshold_w": 500,
    "hysteresis_pct": 0.08,
    "debounce_samples": 2,
    "retention_days": 30,
    "signals": EASTRON_SDM630_SIGNALS,
}

PROFILE_SEEDS = (
    ("elgi_1_energy", "elgi_1", "Elgi 1 energy", "modbus_tcp", "eastron_sdm630_candidate", "192.168.1.51", DEFAULT_SETTINGS),
    ("elgi_2_energy", "elgi_2", "Elgi 2 energy", "modbus_tcp", "eastron_sdm630_candidate", "192.168.1.52", DEFAULT_SETTINGS),
    ("aarco_1_energy", "aarco_1", "Aarco 1 energy", "modbus_tcp", "eastron_sdm630_candidate", "192.168.1.53", {**DEFAULT_SETTINGS, "on_threshold_w": 2_000, "idle_threshold_w": 300}),
    ("aarco_2_energy", "aarco_2", "Aarco 2 energy", "modbus_tcp", "eastron_sdm630_candidate", "192.168.1.54", {**DEFAULT_SETTINGS, "on_threshold_w": 2_000, "idle_threshold_w": 300}),
    ("sergiani_opcua", "sergiani_gs120", "Sergiani GS 120 controller", "opcua", "siemens_opcua_discovery", None, {
        "timeout_s": 5,
        "security_policy": "Basic256Sha256",
        "debounce_samples": 2,
        "retention_days": 30,
        "signals": [],
    }),
    ("factory_mqtt_ingress", None, "Factory MQTT telemetry", "mqtt_json", "normalized_json", None, {
        "topic": "hive/telemetry/+",
        "debounce_samples": 2,
        "retention_days": 30,
        "signals": [],
    }),
)


def sync_defaults(conn: sqlite3.Connection) -> None:
    now = _now()
    for profile_key, machine_key, name, protocol, template_key, endpoint, settings in PROFILE_SEEDS:
        machine = conn.execute(
            "SELECT id FROM machines WHERE machine_key=?", (machine_key,)
        ).fetchone() if machine_key else None
        conn.execute(
            """INSERT OR IGNORE INTO industrial_profiles
               (profile_key,machine_id,name,protocol,template_key,endpoint,
                poll_interval_s,settings_json,created_at,updated_at)
               VALUES (?,?,?,?,?,?,15,?,?,?)""",
            (profile_key, machine["id"] if machine else None, name, protocol,
             template_key, endpoint, _json(settings), now, now),
        )
        conn.execute(
            """INSERT OR IGNORE INTO industrial_profile_state
               (profile_key,updated_at) VALUES (?,?)""",
            (profile_key, now),
        )
    conn.commit()


def _active_contract(conn: sqlite3.Connection, profile: sqlite3.Row) -> dict | None:
    if not profile["active_contract_id"]:
        return None
    row = conn.execute(
        "SELECT * FROM industrial_contract_versions WHERE id=?",
        (profile["active_contract_id"],),
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["signals"] = _loads(result.pop("signals_json"), [])
    result["settings"] = _loads(result.pop("settings_json"), {})
    return result


def _profile_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    item = dict(row)
    item["enabled"] = bool(item["enabled"])
    item["verified"] = bool(item["verified"])
    item["settings"] = _loads(item.pop("settings_json"), {})
    item["credential_available"] = bool(
        item["credential_env"] and os.environ.get(item["credential_env"])
    )
    item["active_contract"] = _active_contract(conn, row)
    machine = conn.execute(
        "SELECT machine_key,name FROM machines WHERE id=?", (item["machine_id"],)
    ).fetchone() if item["machine_id"] else None
    item["machine_key"] = machine["machine_key"] if machine else None
    item["machine_name"] = machine["name"] if machine else None
    state = conn.execute(
        "SELECT * FROM industrial_profile_state WHERE profile_key=?", (item["profile_key"],)
    ).fetchone()
    item["derived_state"] = dict(state) if state else None
    latest = conn.execute(
        """SELECT signal_key,value_num,value_text,unit,quality,source_ts,received_at
           FROM telemetry_latest WHERE profile_key=? ORDER BY signal_key""",
        (item["profile_key"],),
    ).fetchall()
    item["latest"] = [dict(sample) for sample in latest]
    recent = conn.execute(
        """SELECT id,mode,status,signals_seen,signals_good,actor,completed_at
           FROM industrial_commissioning_runs WHERE profile_key=?
           ORDER BY id DESC LIMIT 5""",
        (item["profile_key"],),
    ).fetchall()
    item["recent_runs"] = [dict(run) for run in recent]
    return item


def snapshot(conn: sqlite3.Connection) -> dict:
    sync_defaults(conn)
    profiles = [_profile_dict(conn, row) for row in conn.execute(
        "SELECT * FROM industrial_profiles ORDER BY profile_key"
    )]
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    total_power = sum(
        float(sample["value_num"] or 0)
        for profile in profiles for sample in profile["latest"]
        if sample["signal_key"] == "power_w" and sample["quality"] == "good"
    )
    energy_24h = 0.0
    for profile in profiles:
        first = conn.execute(
            """SELECT value_num FROM telemetry_samples
               WHERE profile_key=? AND signal_key='energy_kwh' AND quality='good'
                 AND source_ts>=? ORDER BY source_ts ASC,id ASC LIMIT 1""",
            (profile["profile_key"], cutoff),
        ).fetchone()
        last = conn.execute(
            """SELECT value_num FROM telemetry_samples
               WHERE profile_key=? AND signal_key='energy_kwh' AND quality='good'
                 AND source_ts>=? ORDER BY source_ts DESC,id DESC LIMIT 1""",
            (profile["profile_key"], cutoff),
        ).fetchone()
        if first and last and first["value_num"] is not None and last["value_num"] is not None:
            energy_24h += max(0.0, last["value_num"] - first["value_num"])
    return {
        "profiles": profiles,
        "signal_definitions": SIGNAL_DEFINITIONS,
        "energy": energy_intelligence.build(conn, hours=24),
        "summary": {
            "profiles": len(profiles),
            "verified": sum(1 for profile in profiles if profile["verified"]),
            "enabled": sum(1 for profile in profiles if profile["enabled"]),
            "current_power_w": round(total_power, 2),
            "energy_24h_kwh": round(energy_24h, 3),
        },
        "guardrail": "Industrial I/O is read-only. Simulation proves software only; a real probe and explicit approval are required before polling.",
    }


def _contains_secret(value: Any) -> bool:
    secret_words = ("password", "passwd", "pwd", "secret", "token", "private_key")
    if isinstance(value, dict):
        return any(
            any(word in str(key).lower() for word in secret_words) or _contains_secret(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_secret(item) for item in value)
    return False


def _validate_endpoint(protocol: str, endpoint: str | None, *, required: bool = False) -> str | None:
    endpoint = str(endpoint or "").strip() or None
    if not endpoint:
        if required:
            raise ValueError("Configure the device endpoint before a real probe")
        return None
    if protocol == "modbus_tcp":
        if "://" in endpoint or any(char in endpoint for char in "/?#@"):
            raise ValueError("Modbus endpoint must be a host or host:port")
        host, separator, port = endpoint.rpartition(":")
        if separator and port.isdigit():
            if not 1 <= int(port) <= 65535:
                raise ValueError("Modbus port must be between 1 and 65535")
            endpoint_host = host
        else:
            endpoint_host = endpoint
        if not endpoint_host or len(endpoint_host) > 253 or not re.fullmatch(r"[A-Za-z0-9_.-]+", endpoint_host):
            raise ValueError("Invalid Modbus host")
    elif protocol == "opcua":
        parsed = urlparse(endpoint)
        if parsed.scheme != "opc.tcp" or not parsed.hostname or not parsed.port:
            raise ValueError("OPC-UA endpoint must use opc.tcp://host:port")
        if parsed.username or parsed.password:
            raise ValueError("Do not put OPC-UA credentials in the endpoint")
    elif protocol == "mqtt_json" and endpoint:
        raise ValueError("MQTT profiles use the central broker and a topic, not an endpoint")
    return endpoint


def validate_signals(protocol: str, signals: list[dict]) -> list[dict]:
    if not isinstance(signals, list) or len(signals) > 64:
        raise ValueError("signals must be a list with at most 64 entries")
    normalized = []
    seen = set()
    for raw in signals:
        if not isinstance(raw, dict):
            raise ValueError("Each signal must be an object")
        signal = dict(raw)
        key = str(signal.get("key") or "").strip()
        if not SIGNAL_KEY.fullmatch(key) or key not in SIGNAL_DEFINITIONS:
            raise ValueError(f"Unknown normalized signal '{key}'")
        if key in seen:
            raise ValueError(f"Duplicate signal '{key}'")
        seen.add(key)
        signal["key"] = key
        signal["unit"] = str(signal.get("unit") or SIGNAL_DEFINITIONS[key]["unit"])
        signal["required"] = bool(signal.get("required", False))
        signal["scale"] = float(signal.get("scale", 1.0))
        signal["offset"] = float(signal.get("offset", 0.0))
        if protocol == "modbus_tcp":
            function = str(signal.get("function") or "input_register")
            data_type = str(signal.get("data_type") or "float32")
            if function not in MODBUS_FUNCTIONS:
                raise ValueError(f"Signal {key} uses a non-read Modbus function")
            if data_type not in MODBUS_DATA_TYPES:
                raise ValueError(f"Signal {key} has unsupported data_type")
            if function in {"discrete_input", "coil"} and data_type != "bool":
                raise ValueError(f"Signal {key} must use bool for a bit function")
            address = int(signal.get("address", -1))
            if not 0 <= address <= 65535:
                raise ValueError(f"Signal {key} needs a zero-based address")
            signal.update({
                "function": function,
                "data_type": data_type,
                "address": address,
                "word_order": str(signal.get("word_order") or "big"),
                "byte_order": str(signal.get("byte_order") or "big"),
            })
            if signal["word_order"] not in {"big", "little"} or signal["byte_order"] not in {"big", "little"}:
                raise ValueError(f"Signal {key} has invalid byte/word order")
        elif protocol == "opcua":
            node_id = str(signal.get("node_id") or "").strip()
            if not node_id or len(node_id) > 512:
                raise ValueError(f"Signal {key} needs an OPC-UA node_id")
            signal["node_id"] = node_id
        elif protocol == "mqtt_json":
            path = str(signal.get("path") or "").strip()
            if not path or len(path) > 256:
                raise ValueError(f"Signal {key} needs a JSON path")
            signal["path"] = path
        normalized.append(signal)
    return normalized


def update_profile(conn: sqlite3.Connection, profile_key: str, payload: dict) -> dict:
    sync_defaults(conn)
    row = conn.execute(
        "SELECT * FROM industrial_profiles WHERE profile_key=?", (profile_key,)
    ).fetchone()
    if not row:
        raise KeyError(f"Unknown industrial profile '{profile_key}'")
    expected = payload.get("expected_version")
    if expected is not None and expected != row["version"]:
        raise ValueError("Industrial profile changed; refresh before saving")
    protocol = str(payload.get("protocol", row["protocol"]))
    if protocol not in PROTOCOLS:
        raise ValueError("Unsupported industrial protocol")
    endpoint = _validate_endpoint(protocol, payload.get("endpoint", row["endpoint"]))
    credential_env = payload.get("credential_env", row["credential_env"])
    if credential_env and not ENV_NAME.fullmatch(str(credential_env)):
        raise ValueError("credential_env must be an uppercase environment variable name")
    settings = payload.get("settings", _loads(row["settings_json"], {}))
    if not isinstance(settings, dict) or _contains_secret(settings):
        raise ValueError("Settings may not contain credentials; use credential_env")
    settings = dict(settings)
    settings["signals"] = validate_signals(protocol, settings.get("signals", []))
    timeout_s = float(settings.get("timeout_s", 3))
    if not 0.25 <= timeout_s <= 30:
        raise ValueError("timeout_s must be between 0.25 and 30")
    settings["timeout_s"] = timeout_s
    settings["debounce_samples"] = int(settings.get("debounce_samples", 2))
    if not 1 <= settings["debounce_samples"] <= 20:
        raise ValueError("debounce_samples must be between 1 and 20")
    settings["retention_days"] = int(settings.get("retention_days", 30))
    if not 1 <= settings["retention_days"] <= 365:
        raise ValueError("retention_days must be between 1 and 365")
    settings["tariff_per_kwh"] = float(settings.get("tariff_per_kwh", 0) or 0)
    if not 0 <= settings["tariff_per_kwh"] <= 1_000_000:
        raise ValueError("tariff_per_kwh must be between 0 and 1000000")
    if protocol == "modbus_tcp":
        settings["unit_id"] = int(settings.get("unit_id", 1))
        if not 0 <= settings["unit_id"] <= 247:
            raise ValueError("unit_id must be between 0 and 247")
        idle = float(settings.get("idle_threshold_w", 300))
        on = float(settings.get("on_threshold_w", 2000))
        if idle < 0 or on <= idle:
            raise ValueError("on_threshold_w must be greater than idle_threshold_w")
        settings["idle_threshold_w"] = idle
        settings["on_threshold_w"] = on
        settings["hysteresis_pct"] = float(settings.get("hysteresis_pct", 0.08))
        if not 0 <= settings["hysteresis_pct"] <= 0.4:
            raise ValueError("hysteresis_pct must be between 0 and 0.4")
    if protocol == "mqtt_json":
        topic = str(settings.get("topic") or "").strip()
        if not topic or len(topic) > 256 or topic.startswith("$"):
            raise ValueError("Configure a non-system MQTT topic filter")
        settings["topic"] = topic
    if protocol == "opcua":
        policy = str(settings.get("security_policy", "Basic256Sha256"))
        if policy not in {"None", "Basic256Sha256", "Aes128Sha256RsaOaep", "Aes256Sha256RsaPss"}:
            raise ValueError("Unsupported OPC-UA security policy")
        settings["security_policy"] = policy
    poll_interval = float(payload.get("poll_interval_s", row["poll_interval_s"]))
    if not 1 <= poll_interval <= 3600:
        raise ValueError("poll_interval_s must be between 1 and 3600")
    enabled = bool(payload.get("enabled", row["enabled"]))
    contract_changed = any(key in payload for key in (
        "protocol", "endpoint", "credential_env", "settings", "poll_interval_s"
    ))
    if enabled and (contract_changed or not row["verified"]):
        raise ValueError("Approve a real probe for this exact contract before enabling")
    verified = bool(row["verified"]) and not contract_changed
    active_contract_id = row["active_contract_id"] if verified else None
    status = row["status"] if verified else "probe_required"
    now = _now()
    conn.execute(
        """UPDATE industrial_profiles SET protocol=?,endpoint=?,credential_env=?,
                  poll_interval_s=?,settings_json=?,enabled=?,verified=?,status=?,
                  active_contract_id=?,version=version+1,updated_at=? WHERE profile_key=?""",
        (protocol, endpoint, credential_env or None, poll_interval, _json(settings),
         int(enabled), int(verified), status, active_contract_id, now, profile_key),
    )
    conn.commit()
    return _profile_dict(conn, conn.execute(
        "SELECT * FROM industrial_profiles WHERE profile_key=?", (profile_key,)
    ).fetchone())


def _register_count(data_type: str) -> int:
    return {"bool": 1, "int16": 1, "uint16": 1, "float32": 2,
            "int32": 2, "uint32": 2, "float64": 4}[data_type]


def decode_modbus_registers(registers: list[int], data_type: str,
                            *, word_order: str = "big", byte_order: str = "big") -> Any:
    count = _register_count(data_type)
    if len(registers) < count:
        raise ValueError(f"Expected {count} registers, received {len(registers)}")
    words = [int(value) & 0xFFFF for value in registers[:count]]
    if word_order == "little" and len(words) > 1:
        words.reverse()
    payload = b"".join(word.to_bytes(2, byteorder=byte_order, signed=False) for word in words)
    # Byte/word options rearrange device bytes into canonical network order.
    prefix = ">"
    formats = {
        "float32": "f", "float64": "d", "int16": "h", "uint16": "H",
        "int32": "i", "uint32": "I", "bool": "H",
    }
    value = struct.unpack(prefix + formats[data_type], payload)[0]
    return bool(value) if data_type == "bool" else value


def _split_modbus_endpoint(endpoint: str) -> tuple[str, int]:
    host, separator, port = endpoint.rpartition(":")
    return (host, int(port)) if separator and port.isdigit() else (endpoint, 502)


def _assert_private_host(host: str, port: int) -> None:
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    except socket.gaierror as error:
        raise ValueError(f"Could not resolve industrial endpoint {host}") from error
    if not addresses:
        raise ValueError(f"Could not resolve industrial endpoint {host}")
    for address in addresses:
        parsed = ipaddress.ip_address(address)
        if not (parsed.is_private or parsed.is_loopback or parsed.is_link_local):
            raise ValueError("Industrial endpoints must resolve only to private factory-LAN addresses")


def _modbus_call(method: Callable, *, address: int, count: int, unit_id: int):
    try:
        return method(address=address, count=count, device_id=unit_id)
    except TypeError:
        return method(address=address, count=count, slave=unit_id)


def _read_modbus(profile: dict, signals: list[dict]) -> dict[str, Any]:
    try:
        from pymodbus.client import ModbusTcpClient
    except ImportError as error:
        raise RuntimeError("pymodbus is not installed") from error
    host, port = _split_modbus_endpoint(profile["endpoint"])
    _assert_private_host(host, port)
    settings = profile["settings"]
    client = ModbusTcpClient(host, port=port, timeout=settings.get("timeout_s", 3))
    values = {}
    try:
        if not client.connect():
            raise RuntimeError(f"Could not connect to Modbus device {host}:{port}")
        for signal in signals:
            function = signal["function"]
            if function == "input_register":
                method = client.read_input_registers
            elif function == "holding_register":
                method = client.read_holding_registers
            elif function == "discrete_input":
                method = client.read_discrete_inputs
            else:
                method = client.read_coils
            count = 1 if function in {"discrete_input", "coil"} else _register_count(signal["data_type"])
            result = _modbus_call(method, address=signal["address"], count=count,
                                   unit_id=settings.get("unit_id", 1))
            if result.isError():
                raise RuntimeError(f"{signal['key']}: Modbus exception {result}")
            if function in {"discrete_input", "coil"}:
                values[signal["key"]] = bool(result.bits[0])
            else:
                values[signal["key"]] = decode_modbus_registers(
                    result.registers, signal["data_type"],
                    word_order=signal["word_order"], byte_order=signal["byte_order"],
                )
    finally:
        client.close()
    return values


async def _opcua_values(endpoint: str, signals: list[dict], settings: dict,
                        credential: dict | None) -> dict[str, Any]:
    try:
        from asyncua import Client
    except ImportError as error:
        raise RuntimeError("asyncua is not installed") from error
    parsed = urlparse(endpoint)
    _assert_private_host(parsed.hostname, parsed.port)
    client = Client(url=endpoint, timeout=settings.get("timeout_s", 5))
    await _configure_opcua_client(client, settings, credential)
    async with client:
        values = await asyncio.gather(*[
            client.get_node(signal["node_id"]).read_value() for signal in signals
        ])
    return {signal["key"]: value for signal, value in zip(signals, values)}


def _credential(profile: dict) -> dict | None:
    env_name = profile.get("credential_env")
    if not env_name:
        return None
    raw = os.environ.get(env_name)
    if not raw:
        raise ValueError(f"Credential environment variable {env_name} is unavailable")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"{env_name} must contain a JSON credential object") from error
    if not isinstance(value, dict):
        raise ValueError(f"{env_name} must contain a JSON credential object")
    return value


def _read_opcua(profile: dict, signals: list[dict]) -> dict[str, Any]:
    settings = profile["settings"]
    credential = _credential(profile)
    return asyncio.run(_opcua_values(profile["endpoint"], signals, settings, credential))


async def _configure_opcua_client(client: Any, settings: dict,
                                  credential: dict | None) -> None:
    policy = settings.get("security_policy", "Basic256Sha256")
    if policy not in {"None", "Basic256Sha256", "Aes128Sha256RsaOaep", "Aes256Sha256RsaPss"}:
        raise ValueError("Unsupported OPC-UA security policy")
    security_string = credential.get("security_string") if credential else None
    if policy != "None" and not security_string:
        raise ValueError(
            "Secure OPC-UA requires credential_env JSON with a security_string"
        )
    if policy == "None" and credential and (credential.get("username") or credential.get("password")):
        raise ValueError("OPC-UA username/password cannot be used with SecurityPolicy None")
    if security_string:
        if not str(security_string).startswith(f"{policy},SignAndEncrypt,"):
            raise ValueError("OPC-UA security_string must match the selected policy and SignAndEncrypt")
        await client.set_security_string(str(security_string))
    if credential:
        if credential.get("username"):
            client.set_user(credential["username"])
        if credential.get("password"):
            client.set_password(credential["password"])


def _read_profile(profile: dict, signals: list[dict]) -> dict[str, Any]:
    if profile["protocol"] == "modbus_tcp":
        return _read_modbus(profile, signals)
    if profile["protocol"] == "opcua":
        return _read_opcua(profile, signals)
    raise ValueError("MQTT profiles receive pushed telemetry and cannot be polled")


def _simulation_values(signals: list[dict]) -> dict[str, Any]:
    return {signal["key"]: SIGNAL_DEFINITIONS[signal["key"]]["simulation"] for signal in signals}


def _normalize_value(signal: dict, raw: Any) -> dict:
    definition = SIGNAL_DEFINITIONS[signal["key"]]
    if raw is None:
        return {"key": signal["key"], "value": None, "unit": signal["unit"],
                "quality": "bad", "detail": "No value returned"}
    try:
        if definition["unit"] == "text":
            value: Any = str(raw)
        elif definition["unit"] == "bool":
            if isinstance(raw, str):
                lowered = raw.strip().lower()
                if lowered not in {"true", "false", "1", "0", "on", "off"}:
                    raise ValueError("not boolean")
                value = lowered in {"true", "1", "on"}
            else:
                if isinstance(raw, (int, float)) and raw not in (0, 1):
                    raise ValueError("not boolean")
                value = bool(raw)
        else:
            value = float(raw) * signal.get("scale", 1.0) + signal.get("offset", 0.0)
            if not math.isfinite(value):
                raise ValueError("not finite")
        minimum = signal.get("minimum", definition.get("minimum"))
        maximum = signal.get("maximum", definition.get("maximum"))
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if minimum is not None and value < float(minimum):
                raise ValueError(f"below plausible minimum {minimum}")
            if maximum is not None and value > float(maximum):
                raise ValueError(f"above plausible maximum {maximum}")
        return {"key": signal["key"], "value": value, "unit": signal["unit"],
                "quality": "good", "detail": "Plausibility check passed"}
    except (TypeError, ValueError) as error:
        return {"key": signal["key"], "value": None, "unit": signal["unit"],
                "quality": "bad", "detail": str(error)}


def _record_run(conn: sqlite3.Connection, profile_key: str, *, mode: str,
                status: str, evidence_sha256: str, values: list[dict],
                summary: dict, actor: str, contract_id: int | None = None) -> int:
    now = _now()
    cursor = conn.execute(
        """INSERT INTO industrial_commissioning_runs
           (profile_key,contract_id,mode,status,evidence_sha256,signals_seen,
            signals_good,summary_json,actor,started_at,completed_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (profile_key, contract_id, mode, status, evidence_sha256, len(values),
         sum(1 for value in values if value["quality"] == "good"),
         _json(summary), actor, now, now),
    )
    conn.commit()
    return cursor.lastrowid


def probe_profile(conn: sqlite3.Connection, profile_key: str, *, simulate: bool = False,
                  actor: str = "operator",
                  reader: Callable[[dict, list[dict]], dict[str, Any]] | None = None) -> dict:
    sync_defaults(conn)
    row = conn.execute(
        "SELECT * FROM industrial_profiles WHERE profile_key=?", (profile_key,)
    ).fetchone()
    if not row:
        raise KeyError(f"Unknown industrial profile '{profile_key}'")
    profile = _profile_dict(conn, row)
    profile["endpoint"] = _validate_endpoint(profile["protocol"], profile["endpoint"], required=not simulate)
    signals = validate_signals(profile["protocol"], profile["settings"].get("signals", []))
    if not signals:
        raise ValueError("Configure at least one signal before testing this profile")
    try:
        raw_values = _simulation_values(signals) if simulate else (reader or _read_profile)(profile, signals)
        values = [_normalize_value(signal, raw_values.get(signal["key"])) for signal in signals]
        required_good = all(
            any(value["key"] == signal["key"] and value["quality"] == "good" for value in values)
            for signal in signals if signal.get("required")
        )
        status = "passed" if required_good and any(value["quality"] == "good" for value in values) else "failed"
        error = None
    except Exception as exc:
        values = [{"key": signal["key"], "value": None, "unit": signal["unit"],
                   "quality": "bad", "detail": str(exc)} for signal in signals]
        status = "failed"
        error = str(exc)
    contract_shape = {
        "protocol": profile["protocol"], "endpoint": profile["endpoint"],
        "settings": profile["settings"], "signals": signals,
    }
    evidence_sha = _hash({**contract_shape, "values": values, "mode": "simulate" if simulate else "probe"})
    summary = {
        **contract_shape, "values": values,
        "contract_sha256": _hash(contract_shape),
        "simulated": simulate, "error": error,
    }
    run_id = _record_run(
        conn, profile_key, mode="simulate" if simulate else "probe", status=status,
        evidence_sha256=evidence_sha, values=values, summary=summary, actor=actor,
    )
    now = _now()
    conn.execute(
        """UPDATE industrial_profiles SET status=?,last_probe_at=?,last_error=?,
                  updated_at=? WHERE profile_key=?""",
        ("simulation_ready" if simulate and status == "passed" else
         "probe_passed" if status == "passed" else "probe_failed",
         now, error[:500] if error else None, now, profile_key),
    )
    conn.commit()
    return {
        "run_id": run_id, "profile_key": profile_key, "mode": "simulate" if simulate else "probe",
        "status": status, "values": values, "evidence_sha256": evidence_sha,
        "approvable": not simulate and status == "passed",
        "detail": "Software path passed; real device evidence is still required" if simulate and status == "passed"
                  else "Real read-only probe passed" if status == "passed" else (error or "Signal validation failed"),
    }


def probe_mqtt_payload(conn: sqlite3.Connection, profile_key: str, topic: str,
                       payload: dict, *, actor: str = "operator") -> dict:
    sync_defaults(conn)
    row = conn.execute(
        "SELECT * FROM industrial_profiles WHERE profile_key=?", (profile_key,)
    ).fetchone()
    if not row:
        raise KeyError(f"Unknown industrial profile '{profile_key}'")
    profile = _profile_dict(conn, row)
    if profile["protocol"] != "mqtt_json":
        raise ValueError("Sample payload commissioning is only for MQTT profiles")
    signals = validate_signals("mqtt_json", profile["settings"].get("signals", []))
    if not signals:
        raise ValueError("Configure at least one MQTT signal before testing")
    if not _topic_matches(profile["settings"].get("topic", ""), topic):
        raise ValueError("Sample topic does not match the configured topic filter")
    values = [_normalize_value(signal, _json_path(payload, signal["path"]))
              for signal in signals]
    required_good = all(
        any(value["key"] == signal["key"] and value["quality"] == "good" for value in values)
        for signal in signals if signal.get("required")
    )
    status = "passed" if required_good and any(value["quality"] == "good" for value in values) else "failed"
    contract_shape = {
        "protocol": profile["protocol"], "endpoint": profile["endpoint"],
        "settings": profile["settings"], "signals": signals,
    }
    evidence = _hash({"contract": contract_shape, "topic": topic,
                      "payload_sha256": _hash(payload), "values": values})
    summary = {
        **contract_shape, "values": values, "topic": topic,
        "payload_sha256": _hash(payload), "raw_payload_retained": False,
        "contract_sha256": _hash(contract_shape), "simulated": False,
    }
    run_id = _record_run(
        conn, profile_key, mode="probe", status=status,
        evidence_sha256=evidence, values=values, summary=summary, actor=actor,
    )
    now = _now()
    conn.execute(
        """UPDATE industrial_profiles SET status=?,last_probe_at=?,last_error=?,
                  updated_at=? WHERE profile_key=?""",
        ("probe_passed" if status == "passed" else "probe_failed", now,
         None if status == "passed" else "Sample signal validation failed", now, profile_key),
    )
    conn.commit()
    return {"run_id": run_id, "profile_key": profile_key, "mode": "probe",
            "status": status, "values": values, "evidence_sha256": evidence,
            "approvable": status == "passed", "raw_payload_retained": False,
            "detail": "Real MQTT sample contract passed" if status == "passed"
                      else "MQTT sample validation failed"}


def approve_run(conn: sqlite3.Connection, profile_key: str, run_id: int, *,
                expected_version: int, actor: str, enable: bool = True) -> dict:
    row = conn.execute(
        "SELECT * FROM industrial_profiles WHERE profile_key=?", (profile_key,)
    ).fetchone()
    if not row:
        raise KeyError(f"Unknown industrial profile '{profile_key}'")
    if row["version"] != expected_version:
        raise ValueError("Industrial profile changed; refresh before approving")
    run = conn.execute(
        """SELECT * FROM industrial_commissioning_runs
           WHERE id=? AND profile_key=?""", (run_id, profile_key)
    ).fetchone()
    if not run:
        raise KeyError("Industrial commissioning run not found")
    if run["mode"] != "probe" or run["status"] != "passed" or run["contract_id"]:
        raise ValueError("Only an unapproved passing real probe can be approved")
    summary = _loads(run["summary_json"], {})
    current_shape = {
        "protocol": row["protocol"], "endpoint": row["endpoint"],
        "settings": _loads(row["settings_json"], {}),
        "signals": _loads(row["settings_json"], {}).get("signals", []),
    }
    if summary.get("contract_sha256") != _hash(current_shape):
        raise ValueError("Profile settings changed after this probe; run a new probe")
    version = conn.execute(
        "SELECT COALESCE(MAX(version),0)+1 FROM industrial_contract_versions WHERE profile_key=?",
        (profile_key,),
    ).fetchone()[0]
    now = _now()
    cursor = conn.execute(
        """INSERT INTO industrial_contract_versions
           (profile_key,version,protocol,endpoint,signals_json,settings_json,
            evidence_sha256,approved_by,approved_at,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (profile_key, version, row["protocol"], row["endpoint"] or summary["settings"].get("topic"),
         _json(summary["signals"]), _json(summary["settings"]), run["evidence_sha256"],
         actor, now, now),
    )
    contract_id = cursor.lastrowid
    conn.execute(
        "UPDATE industrial_commissioning_runs SET contract_id=? WHERE id=?",
        (contract_id, run_id),
    )
    conn.execute(
        """UPDATE industrial_profiles SET active_contract_id=?,verified=1,enabled=?,
                  status=?,last_error=NULL,version=version+1,updated_at=?
           WHERE profile_key=?""",
        (contract_id, int(enable), "polling" if enable else "ready", now, profile_key),
    )
    conn.commit()
    return _profile_dict(conn, conn.execute(
        "SELECT * FROM industrial_profiles WHERE profile_key=?", (profile_key,)
    ).fetchone())


def _hour(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    return parsed.replace(minute=0, second=0, microsecond=0).isoformat()


def _store_samples(conn: sqlite3.Connection, profile: dict, contract: dict,
                   values: list[dict], source_ts: str) -> dict:
    received = _now()
    inserted = duplicates = 0
    for item in values:
        value = item["value"]
        value_num = float(value) if isinstance(value, (int, float, bool)) else None
        value_text = None if value_num is not None or value is None else str(value)
        fingerprint = _hash({
            "profile_key": profile["profile_key"], "signal_key": item["key"],
            "source_ts": source_ts, "value": value, "quality": item["quality"],
            "contract_id": contract["id"],
        })
        cursor = conn.execute(
            """INSERT OR IGNORE INTO telemetry_samples
               (profile_key,machine_id,signal_key,value_num,value_text,unit,quality,
                source_ts,received_at,fingerprint,contract_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (profile["profile_key"], profile["machine_id"], item["key"], value_num,
             value_text, item["unit"], item["quality"], source_ts, received,
             fingerprint, contract["id"]),
        )
        if not cursor.rowcount:
            duplicates += 1
            continue
        inserted += 1
        conn.execute(
            """INSERT INTO telemetry_latest
               (profile_key,signal_key,machine_id,value_num,value_text,unit,quality,
                source_ts,received_at,contract_id) VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(profile_key,signal_key) DO UPDATE SET
                 machine_id=excluded.machine_id,value_num=excluded.value_num,
                 value_text=excluded.value_text,unit=excluded.unit,quality=excluded.quality,
                 source_ts=excluded.source_ts,received_at=excluded.received_at,
                 contract_id=excluded.contract_id
               WHERE excluded.source_ts >= telemetry_latest.source_ts""",
            (profile["profile_key"], item["key"], profile["machine_id"], value_num,
             value_text, item["unit"], item["quality"], source_ts, received, contract["id"]),
        )
        if value_num is not None:
            good = 1 if item["quality"] == "good" else 0
            aggregate_value = value_num if good else None
            conn.execute(
                """INSERT INTO telemetry_hourly
                   (profile_key,signal_key,hour_ts,unit,sample_count,good_count,
                    min_value,max_value,avg_value,first_value,last_value)
                   VALUES (?,?,?,?,1,?,?,?,?,?,?)
                   ON CONFLICT(profile_key,signal_key,hour_ts) DO UPDATE SET
                     sample_count=telemetry_hourly.sample_count+1,
                     good_count=telemetry_hourly.good_count+excluded.good_count,
                     min_value=CASE WHEN excluded.good_count=1 THEN
                       MIN(COALESCE(telemetry_hourly.min_value,excluded.min_value),excluded.min_value)
                       ELSE telemetry_hourly.min_value END,
                     max_value=CASE WHEN excluded.good_count=1 THEN
                       MAX(COALESCE(telemetry_hourly.max_value,excluded.max_value),excluded.max_value)
                       ELSE telemetry_hourly.max_value END,
                     avg_value=CASE WHEN excluded.good_count=1 THEN
                       ((COALESCE(telemetry_hourly.avg_value,0)*telemetry_hourly.good_count)+excluded.avg_value)
                       /(telemetry_hourly.good_count+1) ELSE telemetry_hourly.avg_value END,
                     last_value=CASE WHEN excluded.good_count=1 THEN excluded.last_value
                       ELSE telemetry_hourly.last_value END""",
                (profile["profile_key"], item["key"], _hour(source_ts), item["unit"],
                 good, aggregate_value, aggregate_value, aggregate_value,
                 aggregate_value, aggregate_value),
            )
    conn.commit()
    return {"inserted": inserted, "duplicates": duplicates}


def _desired_state(power_w: float, current: str, settings: dict) -> str:
    idle = float(settings.get("idle_threshold_w", 300))
    on = float(settings.get("on_threshold_w", 2000))
    hysteresis = float(settings.get("hysteresis_pct", 0.08))
    if current == "on" and power_w >= on * (1 - hysteresis):
        return "on"
    if current == "idle" and idle * (1 - hysteresis) <= power_w < on * (1 + hysteresis):
        return "idle"
    if current == "off" and power_w < idle * (1 + hysteresis):
        return "off"
    if power_w >= on:
        return "on"
    if power_w >= idle:
        return "idle"
    return "off"


def _update_derived_state(conn: sqlite3.Connection, profile: dict, contract: dict,
                          values: list[dict], source_ts: str) -> dict | None:
    good = {item["key"]: item["value"] for item in values if item["quality"] == "good"}
    if "power_w" not in good and "running" not in good:
        return None
    state_row = conn.execute(
        "SELECT * FROM industrial_profile_state WHERE profile_key=?",
        (profile["profile_key"],),
    ).fetchone()
    current = state_row["current_state"] if state_row else "unknown"
    power_w = float(good["power_w"]) if "power_w" in good else None
    desired = _desired_state(power_w, current, contract["settings"]) if power_w is not None else (
        "on" if bool(good["running"]) else "off"
    )
    pending_state = state_row["pending_state"] if state_row else None
    pending_count = state_row["pending_count"] if state_row else 0
    if desired == current:
        pending_state, pending_count = None, 0
    elif desired == pending_state:
        pending_count += 1
    else:
        pending_state, pending_count = desired, 1
    debounce = int(contract["settings"].get("debounce_samples", 2))
    transitioned = pending_count >= debounce
    next_state = desired if transitioned else current
    conn.execute(
        """INSERT INTO industrial_profile_state
           (profile_key,current_state,pending_state,pending_count,last_power_w,
            last_transition_at,updated_at) VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(profile_key) DO UPDATE SET current_state=excluded.current_state,
             pending_state=excluded.pending_state,pending_count=excluded.pending_count,
             last_power_w=excluded.last_power_w,
             last_transition_at=COALESCE(excluded.last_transition_at,industrial_profile_state.last_transition_at),
             updated_at=excluded.updated_at""",
        (profile["profile_key"], next_state, None if transitioned else pending_state,
         0 if transitioned else pending_count, power_w,
         source_ts if transitioned else None, _now()),
    )
    conn.commit()
    event = None
    if transitioned and profile["machine_key"]:
        event = event_pipeline.ingest_event(conn, {
            "machine_key": profile["machine_key"],
            "event_type": f"state_{desired}",
            "previous_state": current,
            "power_w": power_w,
            "ts": source_ts,
            "source": f"industrial:{profile['profile_key']}",
        }, site_timezone="UTC")
    return {"state": next_state, "transitioned": transitioned, "event": event}


def poll_profile(conn: sqlite3.Connection, profile_key: str, *, actor: str = "system",
                 reader: Callable[[dict, list[dict]], dict[str, Any]] | None = None,
                 source_ts: str | None = None) -> dict:
    row = conn.execute(
        "SELECT * FROM industrial_profiles WHERE profile_key=?", (profile_key,)
    ).fetchone()
    if not row:
        raise KeyError(f"Unknown industrial profile '{profile_key}'")
    profile = _profile_dict(conn, row)
    contract = profile["active_contract"]
    if not profile["enabled"] or not profile["verified"] or not contract:
        raise ValueError("Profile must be approved and enabled before polling")
    if profile["protocol"] == "mqtt_json":
        raise ValueError("MQTT profiles are push-only")
    source_ts = event_pipeline.canonical_timestamp(source_ts, "UTC") if source_ts else _now()
    try:
        raw = (reader or _read_profile)(profile, contract["signals"])
        values = [_normalize_value(signal, raw.get(signal["key"])) for signal in contract["signals"]]
        required_ok = all(
            any(item["key"] == signal["key"] and item["quality"] == "good" for item in values)
            for signal in contract["signals"] if signal.get("required")
        )
        status = "passed" if required_ok else "failed"
        error = None if required_ok else "One or more required signals failed"
    except Exception as exc:
        values = [_normalize_value(signal, None) for signal in contract["signals"]]
        status, error = "failed", str(exc)
    evidence = _hash({"contract": contract["id"], "ts": source_ts, "values": values})
    run_id = _record_run(
        conn, profile_key, mode="poll", status=status, evidence_sha256=evidence,
        values=values, summary={"values": values, "source_ts": source_ts},
        actor=actor, contract_id=contract["id"],
    )
    storage = _store_samples(conn, profile, contract, values, source_ts)
    state = _update_derived_state(conn, profile, contract, values, source_ts)
    now = _now()
    conn.execute(
        """UPDATE industrial_profiles SET last_poll_at=?,last_success_at=?,last_error=?,
                  status=?,updated_at=? WHERE profile_key=?""",
        (now, now if status == "passed" else profile["last_success_at"],
         error[:500] if error else None, "polling" if status == "passed" else "poll_failed",
         now, profile_key),
    )
    conn.commit()
    return {"run_id": run_id, "status": status, "values": values,
            "storage": storage, "derived_state": state, "source_ts": source_ts}


def poll_due_profiles(conn: sqlite3.Connection, *, reader_factory: Callable | None = None) -> dict:
    now = datetime.now(timezone.utc)
    results = []
    for row in conn.execute(
        "SELECT * FROM industrial_profiles WHERE enabled=1 AND verified=1 AND protocol!='mqtt_json'"
    ).fetchall():
        last = datetime.fromisoformat(row["last_poll_at"]) if row["last_poll_at"] else None
        if last and (now - last).total_seconds() < row["poll_interval_s"]:
            continue
        reader = reader_factory(row["profile_key"]) if reader_factory else None
        try:
            result = poll_profile(conn, row["profile_key"], reader=reader)
            results.append({"profile_key": row["profile_key"], "status": result["status"]})
        except Exception as error:
            results.append({"profile_key": row["profile_key"], "status": "failed", "error": str(error)})
    return {"polled": len(results), "results": results}


def _topic_matches(topic_filter: str, topic: str) -> bool:
    wanted = topic_filter.split("/")
    actual = topic.split("/")
    for index, part in enumerate(wanted):
        if part == "#":
            return index == len(wanted) - 1
        if index >= len(actual) or (part != "+" and part != actual[index]):
            return False
    return len(actual) == len(wanted)


def _json_path(payload: Any, path: str) -> Any:
    value = payload
    for part in path.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return None
    return value


def ingest_mqtt_payload(conn: sqlite3.Connection, topic: str, payload: dict,
                        *, received_at: str | None = None) -> list[dict]:
    results = []
    for row in conn.execute(
        """SELECT * FROM industrial_profiles
           WHERE protocol='mqtt_json' AND enabled=1 AND verified=1"""
    ).fetchall():
        profile = _profile_dict(conn, row)
        contract = profile["active_contract"]
        if not contract or not _topic_matches(contract["settings"].get("topic", ""), topic):
            continue
        raw = {signal["key"]: _json_path(payload, signal["path"])
               for signal in contract["signals"]}
        values = [_normalize_value(signal, raw[signal["key"]]) for signal in contract["signals"]]
        source_ts = event_pipeline.canonical_timestamp(
            payload.get("ts") or received_at, "UTC"
        )
        storage = _store_samples(conn, profile, contract, values, source_ts)
        state = _update_derived_state(conn, profile, contract, values, source_ts)
        evidence = _hash({"topic": topic, "source_ts": source_ts, "values": values})
        run_id = _record_run(
            conn, profile["profile_key"], mode="mqtt", status="passed",
            evidence_sha256=evidence, values=values,
            summary={"topic": topic, "source_ts": source_ts, "values": values},
            actor="mqtt", contract_id=contract["id"],
        )
        results.append({"profile_key": profile["profile_key"], "run_id": run_id,
                        "storage": storage, "derived_state": state})
    return results


async def _browse_nodes(endpoint: str, settings: dict, credential: dict | None,
                        limit: int) -> list[dict]:
    try:
        from asyncua import Client
    except ImportError as error:
        raise RuntimeError("asyncua is not installed") from error
    parsed = urlparse(endpoint)
    _assert_private_host(parsed.hostname, parsed.port)
    client = Client(url=endpoint, timeout=settings.get("timeout_s", 5))
    await _configure_opcua_client(client, settings, credential)
    found = []
    async with client:
        pending = [(client.nodes.objects, 0)]
        while pending and len(found) < limit:
            node, depth = pending.pop(0)
            try:
                children = await node.get_children()
            except Exception:
                continue
            for child in children:
                if len(found) >= limit:
                    break
                try:
                    display = await child.read_display_name()
                    node_class = await child.read_node_class()
                    found.append({"node_id": child.nodeid.to_string(),
                                  "name": display.Text, "node_class": str(node_class)})
                    if depth < 3:
                        pending.append((child, depth + 1))
                except Exception:
                    continue
    return found


def browse_opcua(conn: sqlite3.Connection, profile_key: str, *, limit: int = 200) -> dict:
    row = conn.execute(
        "SELECT * FROM industrial_profiles WHERE profile_key=?", (profile_key,)
    ).fetchone()
    if not row:
        raise KeyError(f"Unknown industrial profile '{profile_key}'")
    profile = _profile_dict(conn, row)
    if profile["protocol"] != "opcua":
        raise ValueError("Node browsing is only available for OPC-UA profiles")
    endpoint = _validate_endpoint("opcua", profile["endpoint"], required=True)
    nodes = asyncio.run(_browse_nodes(endpoint, profile["settings"], _credential(profile), min(limit, 500)))
    return {"profile_key": profile_key, "endpoint": endpoint, "nodes": nodes,
            "truncated": len(nodes) >= min(limit, 500)}


def telemetry_history(conn: sqlite3.Connection, profile_key: str, *,
                      hours: int = 24, signal_key: str | None = None) -> dict:
    if not conn.execute(
        "SELECT 1 FROM industrial_profiles WHERE profile_key=?", (profile_key,)
    ).fetchone():
        raise KeyError(f"Unknown industrial profile '{profile_key}'")
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(1, min(hours, 24 * 365)))).isoformat()
    params: list[Any] = [profile_key, cutoff]
    where_signal = ""
    if signal_key:
        where_signal = " AND signal_key=?"
        params.append(signal_key)
    rows = conn.execute(
        f"""SELECT signal_key,hour_ts,unit,sample_count,good_count,min_value,
                   max_value,avg_value,first_value,last_value
            FROM telemetry_hourly WHERE profile_key=? AND hour_ts>=?{where_signal}
            ORDER BY hour_ts,signal_key""",
        params,
    ).fetchall()
    return {"profile_key": profile_key, "hours": hours,
            "series": [dict(row) for row in rows]}


def prune_raw_telemetry(conn: sqlite3.Connection) -> int:
    deleted = 0
    for row in conn.execute("SELECT profile_key,settings_json FROM industrial_profiles"):
        days = int(_loads(row["settings_json"], {}).get("retention_days", 30))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        deleted += conn.execute(
            "DELETE FROM telemetry_samples WHERE profile_key=? AND source_ts<?",
            (row["profile_key"], cutoff),
        ).rowcount
    conn.commit()
    return deleted
