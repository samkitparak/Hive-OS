"""Tests for energy meter agent — no real Modbus or MQTT broker needed."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from energy_agent import (
    MachineState, MeterConfig, MeterState,
    _derive_state, _build_payload, _build_telemetry,
    run, CONFIG_PATH,
)


# --- Fixtures ---

@pytest.fixture
def cfg() -> MeterConfig:
    return MeterConfig(
        machine_key="elgi_1",
        label="Elgi Compressor 1",
        modbus_host="192.168.1.51",
        modbus_port=502,
        unit_id=1,
        on_threshold_w=5000,
        idle_threshold_w=500,
    )

@pytest.fixture
def meter(cfg) -> MeterState:
    return MeterState(config=cfg, state_since="2026-05-31T00:00:00+00:00")


# --- State derivation ---

def test_state_off(cfg):
    assert _derive_state(0.0, cfg)   == MachineState.OFF
    assert _derive_state(499.9, cfg) == MachineState.OFF

def test_state_idle(cfg):
    assert _derive_state(500.0, cfg)  == MachineState.IDLE
    assert _derive_state(4999.9, cfg) == MachineState.IDLE

def test_state_on(cfg):
    assert _derive_state(5000.0, cfg) == MachineState.ON
    assert _derive_state(9000.0, cfg) == MachineState.ON

def test_state_exact_boundaries(cfg):
    assert _derive_state(cfg.idle_threshold_w, cfg) == MachineState.IDLE
    assert _derive_state(cfg.on_threshold_w, cfg)   == MachineState.ON


# --- Payload structure ---

def test_build_payload_fields(meter):
    payload = _build_payload(meter, MachineState.ON, 6200.0)
    assert payload["machine_key"]    == "elgi_1"
    assert payload["event_type"]     == "state_on"
    assert payload["previous_state"] == MachineState.OFF
    assert payload["power_w"]        == 6200.0
    assert payload["source"]         == "energy_meter"
    assert "ts" in payload

def test_build_payload_state_change_captured(meter):
    meter.current_state = MachineState.ON
    payload = _build_payload(meter, MachineState.IDLE, 800.0)
    assert payload["event_type"]     == "state_idle"
    assert payload["previous_state"] == MachineState.ON

def test_build_telemetry_fields(meter):
    meter.current_state = MachineState.ON
    payload = _build_telemetry(meter, 6100.0)
    assert payload["event_type"] == "telemetry"
    assert payload["state"]      == MachineState.ON
    assert payload["power_w"]    == 6100.0

def test_payload_power_rounded(meter):
    payload = _build_payload(meter, MachineState.ON, 6234.567)
    assert payload["power_w"] == 6234.6


# --- Full run loop with fake reader + fake MQTT ---

class FixedReader:
    """Returns a fixed sequence of power readings."""
    def __init__(self, readings: list):
        self._readings = iter(readings)

    def read_power_w(self, host, port, unit_id):
        return next(self._readings, None)


class CaptureMQTT:
    """Captures published MQTT messages for assertion."""
    def __init__(self):
        self.messages: list[dict] = []

    def publish(self, topic, payload, qos=0):
        self.messages.append({"topic": topic, "payload": json.loads(payload)})

    def loop_start(self): pass
    def loop_stop(self): pass
    def disconnect(self): pass


def test_state_change_published(tmp_path):
    """OFF → ON transition publishes a state_on event."""
    cfg_text = (CONFIG_PATH.read_text()
                .replace("127.0.0.1", "127.0.0.1"))  # no-op, just load real config
    cfg_file = tmp_path / "machines.yaml"
    cfg_file.write_text(cfg_text)

    # 4 machines × 2 cycles = 8 reads
    # cycle 1: all OFF (0W)
    # cycle 2: elgi1=7000W (ON, threshold=5000), elgi2=600W (IDLE, threshold=500),
    #          aarco1=0W (OFF), aarco2=0W (OFF)
    readings = [0, 0, 0, 0,
                7000, 600, 0, 0]

    mqtt = CaptureMQTT()
    run(cfg_file, reader=FixedReader(readings), mqtt_client=mqtt, max_cycles=2)

    event_types = [m["payload"]["event_type"] for m in mqtt.messages]
    assert "state_on"   in event_types
    assert "state_idle" in event_types

def test_no_publish_when_state_unchanged(tmp_path):
    """No event published if power stays in same state band across cycles."""
    cfg_file = tmp_path / "machines.yaml"
    cfg_file.write_text(CONFIG_PATH.read_text())

    # 4 machines × 3 cycles = 12 reads, all 0W (OFF throughout)
    # Only telemetry on cycle 0, no state_change events
    readings = [0] * 12
    mqtt = CaptureMQTT()
    run(cfg_file, reader=FixedReader(readings), mqtt_client=mqtt, max_cycles=3)

    state_changes = [m for m in mqtt.messages if m["payload"]["event_type"].startswith("state_")]
    assert len(state_changes) == 0

def test_read_failure_does_not_crash(tmp_path):
    """None reads (Modbus timeout) are handled gracefully."""
    cfg_file = tmp_path / "machines.yaml"
    cfg_file.write_text(CONFIG_PATH.read_text())

    readings = [None] * 8  # all timeouts
    mqtt = CaptureMQTT()
    run(cfg_file, reader=FixedReader(readings), mqtt_client=mqtt, max_cycles=2)
    # If we get here without exception, the test passes
    assert len(mqtt.messages) == 0

def test_mqtt_topic_format(tmp_path):
    """Events go to hive/machines/{machine_key}/events."""
    cfg_file = tmp_path / "machines.yaml"
    cfg_file.write_text(CONFIG_PATH.read_text())

    readings = [0, 0, 0, 0,
                7000, 7000, 7000, 7000]
    mqtt = CaptureMQTT()
    run(cfg_file, reader=FixedReader(readings), mqtt_client=mqtt, max_cycles=2)

    topics = {m["topic"] for m in mqtt.messages}
    assert "hive/machines/elgi_1/events" in topics
    assert "hive/machines/elgi_2/events" in topics

def test_config_loads_all_four_machines(tmp_path):
    """Config file has entries for all 4 utility machines."""
    cfg_file = tmp_path / "machines.yaml"
    cfg_file.write_text(CONFIG_PATH.read_text())

    readings = [0] * 4
    mqtt = CaptureMQTT()
    run(cfg_file, reader=FixedReader(readings), mqtt_client=mqtt, max_cycles=1)

    keys = {m["payload"]["machine_key"] for m in mqtt.messages}
    # First cycle all OFF → telemetry for all 4 on cycle 0
    assert keys == {"elgi_1", "elgi_2", "aarco_1", "aarco_2"}
