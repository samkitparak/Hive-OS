"""Read-only industrial I/O commissioning and telemetry contracts."""

import asyncio
import struct
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import industrial_gateway
from db import init_db


@pytest.fixture
def conn():
    connection = init_db(":memory:", check_same_thread=False)
    industrial_gateway.sync_defaults(connection)
    yield connection
    connection.close()


def _profile(conn, key):
    return next(profile for profile in industrial_gateway.snapshot(conn)["profiles"]
                if profile["profile_key"] == key)


def _configured_energy(conn):
    profile = _profile(conn, "elgi_1_energy")
    return industrial_gateway.update_profile(conn, "elgi_1_energy", {
        "expected_version": profile["version"],
        "endpoint": "10.10.0.51:502",
        "poll_interval_s": 5,
        "settings": profile["settings"],
    })


def _fake_energy(power=6250.0, energy=12345.6):
    def reader(profile, signals):
        values = {signal["key"]: industrial_gateway.SIGNAL_DEFINITIONS[signal["key"]]["simulation"]
                  for signal in signals}
        values.update({"power_w": power, "energy_kwh": energy})
        return values
    return reader


def test_seeded_profiles_are_candidate_only(conn):
    snapshot = industrial_gateway.snapshot(conn)
    assert {profile["profile_key"] for profile in snapshot["profiles"]} == {
        "elgi_1_energy", "elgi_2_energy", "aarco_1_energy",
        "aarco_2_energy", "sergiani_opcua", "factory_mqtt_ingress",
    }
    assert all(not profile["enabled"] and not profile["verified"]
               for profile in snapshot["profiles"])
    assert snapshot["summary"]["current_power_w"] == 0


def test_modbus_decoder_handles_word_and_byte_order():
    raw = struct.pack(">f", 12.5)
    registers = [int.from_bytes(raw[:2], "big"), int.from_bytes(raw[2:], "big")]
    assert industrial_gateway.decode_modbus_registers(registers, "float32") == 12.5
    assert industrial_gateway.decode_modbus_registers(
        list(reversed(registers)), "float32", word_order="little"
    ) == 12.5
    little_registers = [int.from_bytes(raw[:2], "little"), int.from_bytes(raw[2:], "little")]
    assert industrial_gateway.decode_modbus_registers(
        little_registers, "float32", byte_order="little"
    ) == pytest.approx(12.5)


def test_profile_rejects_write_functions_and_embedded_secrets(conn):
    profile = _profile(conn, "elgi_1_energy")
    settings = {**profile["settings"], "signals": [{
        "key": "power_w", "function": "write_register", "address": 52,
        "data_type": "float32", "unit": "W",
    }]}
    with pytest.raises(ValueError, match="non-read"):
        industrial_gateway.update_profile(conn, profile["profile_key"], {
            "expected_version": profile["version"], "settings": settings,
        })
    with pytest.raises(ValueError, match="credentials"):
        industrial_gateway.update_profile(conn, profile["profile_key"], {
            "expected_version": profile["version"],
            "settings": {**profile["settings"], "nested": [{"password": "bad"}]},
        })


def test_real_connections_are_restricted_to_private_factory_addresses(monkeypatch):
    monkeypatch.setattr(industrial_gateway.socket, "getaddrinfo", lambda *args, **kwargs: [
        (2, 1, 6, "", ("8.8.8.8", 502)),
    ])
    with pytest.raises(ValueError, match="private factory-LAN"):
        industrial_gateway._assert_private_host("meter.example", 502)


def test_opcua_security_policy_requires_matching_sign_and_encrypt_material():
    class FakeClient:
        def __init__(self):
            self.security = None
            self.username = None
            self.password = None

        async def set_security_string(self, value):
            self.security = value

        def set_user(self, value):
            self.username = value

        def set_password(self, value):
            self.password = value

    with pytest.raises(ValueError, match="security_string"):
        asyncio.run(industrial_gateway._configure_opcua_client(
            FakeClient(), {"security_policy": "Basic256Sha256"}, None
        ))
    client = FakeClient()
    asyncio.run(industrial_gateway._configure_opcua_client(
        client, {"security_policy": "Basic256Sha256"}, {
            "username": "observer", "password": "secret",
            "security_string": "Basic256Sha256,SignAndEncrypt,client.der,client.pem,server.der",
        }
    ))
    assert client.security.startswith("Basic256Sha256,SignAndEncrypt")
    assert client.username == "observer"


def test_simulation_cannot_be_approved(conn):
    profile = _configured_energy(conn)
    result = industrial_gateway.probe_profile(
        conn, profile["profile_key"], simulate=True, actor="test"
    )
    assert result["status"] == "passed"
    assert result["approvable"] is False
    with pytest.raises(ValueError, match="real probe"):
        industrial_gateway.approve_run(
            conn, profile["profile_key"], result["run_id"],
            expected_version=profile["version"], actor="test",
        )


def test_real_probe_approval_polling_dedup_rollup_and_state_debounce(conn):
    profile = _configured_energy(conn)
    probe = industrial_gateway.probe_profile(
        conn, profile["profile_key"], actor="test", reader=_fake_energy()
    )
    assert probe["approvable"] is True
    approved = industrial_gateway.approve_run(
        conn, profile["profile_key"], probe["run_id"],
        expected_version=profile["version"], actor="test", enable=True,
    )
    assert approved["verified"] is True and approved["enabled"] is True

    first = industrial_gateway.poll_profile(
        conn, profile["profile_key"], reader=_fake_energy(6250, 12345.6),
        source_ts="2026-07-14T10:00:00+00:00",
    )
    second = industrial_gateway.poll_profile(
        conn, profile["profile_key"], reader=_fake_energy(6400, 12345.8),
        source_ts="2026-07-14T10:00:05+00:00",
    )
    duplicate = industrial_gateway.poll_profile(
        conn, profile["profile_key"], reader=_fake_energy(6400, 12345.8),
        source_ts="2026-07-14T10:00:05+00:00",
    )
    assert first["derived_state"]["transitioned"] is False
    assert second["derived_state"]["transitioned"] is True
    assert second["derived_state"]["state"] == "on"
    assert duplicate["storage"]["duplicates"] == 6
    assert conn.execute("SELECT COUNT(*) FROM telemetry_samples").fetchone()[0] == 12
    assert conn.execute("SELECT COUNT(*) FROM telemetry_hourly").fetchone()[0] == 6
    event = conn.execute("SELECT event_type FROM machine_events").fetchone()
    assert event["event_type"] == "state_on"


def test_contract_edit_disables_and_invalidates_live_profile(conn):
    profile = _configured_energy(conn)
    probe = industrial_gateway.probe_profile(
        conn, profile["profile_key"], reader=_fake_energy()
    )
    approved = industrial_gateway.approve_run(
        conn, profile["profile_key"], probe["run_id"],
        expected_version=profile["version"], actor="test", enable=False,
    )
    changed = industrial_gateway.update_profile(conn, profile["profile_key"], {
        "expected_version": approved["version"],
        "settings": {**approved["settings"], "on_threshold_w": 7000},
    })
    assert changed["verified"] is False
    assert changed["active_contract"] is None


def test_mqtt_sample_commissioning_and_duplicate_ingestion(conn):
    profile = _profile(conn, "factory_mqtt_ingress")
    settings = {
        **profile["settings"],
        "topic": "factory/utilities/+",
        "signals": [
            {"key": "power_w", "path": "metrics.power", "unit": "W", "required": True},
            {"key": "running", "path": "state.running", "unit": "bool"},
        ],
    }
    configured = industrial_gateway.update_profile(conn, profile["profile_key"], {
        "expected_version": profile["version"], "settings": settings,
    })
    payload = {"metrics": {"power": 1800}, "state": {"running": True},
               "ts": "2026-07-14T11:00:00+00:00"}
    probe = industrial_gateway.probe_mqtt_payload(
        conn, profile["profile_key"], "factory/utilities/compressor", payload,
        actor="test",
    )
    assert probe["approvable"] is True
    assert probe["raw_payload_retained"] is False
    approved = industrial_gateway.approve_run(
        conn, profile["profile_key"], probe["run_id"],
        expected_version=configured["version"], actor="test", enable=True,
    )
    assert approved["enabled"] is True
    first = industrial_gateway.ingest_mqtt_payload(
        conn, "factory/utilities/compressor", payload
    )
    second = industrial_gateway.ingest_mqtt_payload(
        conn, "factory/utilities/compressor", payload
    )
    assert first[0]["storage"]["inserted"] == 2
    assert second[0]["storage"]["duplicates"] == 2
    stored = conn.execute(
        "SELECT summary_json FROM industrial_commissioning_runs WHERE id=?",
        (probe["run_id"],),
    ).fetchone()[0]
    assert '"metrics":{' not in stored


def test_industrial_api_exposes_simulator(conn):
    import main

    main.set_conn(conn)
    with TestClient(main.app) as client:
        main.set_conn(conn)
        snapshot = client.get("/api/industrial/snapshot")
        assert snapshot.status_code == 200
        profile = next(item for item in snapshot.json()["profiles"]
                       if item["profile_key"] == "elgi_1_energy")
        updated = client.put("/api/industrial/profiles/elgi_1_energy", json={
            "expected_version": profile["version"],
            "endpoint": "10.10.0.51",
            "settings": profile["settings"],
            "actor": "test",
        })
        assert updated.status_code == 200
        simulated = client.post("/api/industrial/profiles/elgi_1_energy/simulate", json={
            "actor": "test",
        })
        assert simulated.status_code == 200
        assert simulated.json()["approvable"] is False
