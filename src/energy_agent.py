"""
Energy meter agent — polls Modbus TCP energy meters (one per machine),
derives on/idle/off state from power draw, publishes events to MQTT.

MQTT topic:  hive/machines/{machine_key}/events
Payload:     JSON — see _build_payload()

Runs on the central broker PC (or any PC on the factory LAN).
Config:      config/machines.yaml

Usage:
    python src/energy_agent.py                        # uses default config
    python src/energy_agent.py --config /path/to.yaml
    python src/energy_agent.py --simulate             # no real hardware needed
"""

import argparse
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol

import paho.mqtt.client as mqtt
import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
)
log = logging.getLogger("energy_agent")

CONFIG_PATH = Path(__file__).parent.parent / "config" / "machines.yaml"

# Legacy Eastron-compatible register defaults. New installations use the
# versioned contracts in industrial_gateway.py instead of these fixed values.
_REG_VOLTAGE   = 0x0000   # V    float32, 2 registers
_REG_CURRENT   = 0x0006   # A    float32
_REG_POWER_W   = 0x000C   # W    float32  ← primary OEE signal
_REG_POWER_VA  = 0x0012   # VA   float32
_REG_PF        = 0x001E   # PF   float32


# --- State machine ---

class MachineState:
    OFF  = "off"
    IDLE = "idle"
    ON   = "on"


@dataclass
class MeterConfig:
    machine_key:     str
    label:           str
    modbus_host:     str
    modbus_port:     int
    unit_id:         int
    on_threshold_w:  float
    idle_threshold_w: float


@dataclass
class MeterState:
    config:       MeterConfig
    current_state: str = MachineState.OFF
    last_power_w:  float = 0.0
    state_since:   str = ""
    consecutive_errors: int = 0


# --- Modbus reader protocol (allows easy swap with simulator) ---

class ModbusReader(Protocol):
    def read_power_w(self, host: str, port: int, unit_id: int) -> Optional[float]:
        ...


class RealModbusReader:
    """Reads float32 power register from a Modbus TCP energy meter."""

    def __init__(self):
        try:
            from pymodbus.client import ModbusTcpClient
            self._client_cls = ModbusTcpClient
        except ImportError:
            raise RuntimeError(
                "pymodbus not installed. Run: pip install pymodbus"
            )

    def read_power_w(self, host: str, port: int, unit_id: int) -> Optional[float]:
        import struct
        client = self._client_cls(host, port=port, timeout=2)
        try:
            if not client.connect():
                return None
            try:
                result = client.read_input_registers(
                    address=_REG_POWER_W, count=2, device_id=unit_id
                )
            except TypeError:
                result = client.read_input_registers(
                    address=_REG_POWER_W, count=2, slave=unit_id
                )
            if result.isError():
                return None
            # Two 16-bit registers → IEEE 754 float32
            raw = struct.pack(">HH", result.registers[0], result.registers[1])
            return struct.unpack(">f", raw)[0]
        except Exception:
            return None
        finally:
            client.close()


# --- State transitions ---

def _derive_state(power_w: float, cfg: MeterConfig) -> str:
    if power_w >= cfg.on_threshold_w:
        return MachineState.ON
    if power_w >= cfg.idle_threshold_w:
        return MachineState.IDLE
    return MachineState.OFF


def _build_payload(meter: MeterState, new_state: str, power_w: float) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "machine_key":  meter.config.machine_key,
        "label":        meter.config.label,
        "event_type":   f"state_{new_state}",       # state_on / state_idle / state_off
        "previous_state": meter.current_state,
        "power_w":      round(power_w, 1),
        "ts":           now,
        "source":       "energy_meter",
    }


def _build_telemetry(meter: MeterState, power_w: float) -> dict:
    """Periodic telemetry even when state hasn't changed."""
    return {
        "machine_key":  meter.config.machine_key,
        "event_type":   "telemetry",
        "state":        meter.current_state,
        "power_w":      round(power_w, 1),
        "ts":           datetime.now(timezone.utc).isoformat(),
        "source":       "energy_meter",
    }


# --- MQTT publisher ---

def _make_mqtt_client(broker_host: str, broker_port: int, keepalive: int) -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    def on_connect(c, userdata, flags, reason_code, properties):
        if reason_code == 0:
            log.info("MQTT connected to %s:%s", broker_host, broker_port)
        else:
            log.warning("MQTT connect failed: %s", reason_code)

    def on_disconnect(c, userdata, flags, reason_code, properties):
        log.warning("MQTT disconnected: %s", reason_code)

    client.on_connect    = on_connect
    client.on_disconnect = on_disconnect
    client.connect(broker_host, broker_port, keepalive)
    client.loop_start()
    return client


def _publish(client: mqtt.Client, topic_prefix: str,
             machine_key: str, payload: dict) -> None:
    topic = f"{topic_prefix}/{machine_key}/events"
    client.publish(topic, json.dumps(payload), qos=1)
    log.info("→ %s  %s  %.0fW", machine_key, payload["event_type"], payload.get("power_w", 0))


# --- Main poll loop ---

def run(cfg_path: Path = CONFIG_PATH,
        reader: Optional[ModbusReader] = None,
        mqtt_client: Optional[mqtt.Client] = None,
        max_cycles: Optional[int] = None) -> None:
    """
    Poll all energy meters in config, publish state changes + periodic telemetry.
    max_cycles: stop after N cycles (used in tests / simulate mode).
    """
    cfg = yaml.safe_load(cfg_path.read_text())

    mqtt_cfg      = cfg["mqtt"]
    topic_prefix  = mqtt_cfg["topic_prefix"]
    defaults      = cfg.get("energy_defaults", {})
    poll_interval = defaults.get("poll_interval_s", 5)

    # Build meter states
    meters: list[MeterState] = []
    for m in cfg.get("energy_meters", []):
        mc = MeterConfig(
            machine_key      = m["machine_key"],
            label            = m["label"],
            modbus_host      = m["modbus_host"],
            modbus_port      = m.get("modbus_port", 502),
            unit_id          = m.get("unit_id", 1),
            on_threshold_w   = m.get("on_threshold_w",   defaults.get("on_threshold_w",  2000)),
            idle_threshold_w = m.get("idle_threshold_w", defaults.get("idle_threshold_w", 300)),
        )
        meters.append(MeterState(config=mc, state_since=datetime.now(timezone.utc).isoformat()))

    if reader is None:
        reader = RealModbusReader()

    owns_mqtt = mqtt_client is None
    if owns_mqtt:
        mqtt_client = _make_mqtt_client(
            mqtt_cfg["broker_host"], mqtt_cfg["broker_port"], mqtt_cfg.get("keepalive", 60)
        )
        time.sleep(0.5)  # let MQTT handshake complete

    telemetry_every = max(1, 60 // poll_interval)  # publish telemetry ~every 60s
    cycle = 0

    try:
        while True:
            for meter in meters:
                power_w = reader.read_power_w(
                    meter.config.modbus_host,
                    meter.config.modbus_port,
                    meter.config.unit_id,
                )

                if power_w is None:
                    meter.consecutive_errors += 1
                    if meter.consecutive_errors == 3:
                        log.warning("%s: 3 consecutive read failures", meter.config.machine_key)
                    continue

                meter.consecutive_errors = 0
                new_state = _derive_state(power_w, meter.config)

                if new_state != meter.current_state:
                    payload = _build_payload(meter, new_state, power_w)
                    _publish(mqtt_client, topic_prefix, meter.config.machine_key, payload)
                    meter.current_state = new_state
                    meter.state_since   = payload["ts"]
                elif cycle % telemetry_every == 0:
                    payload = _build_telemetry(meter, power_w)
                    _publish(mqtt_client, topic_prefix, meter.config.machine_key, payload)

                meter.last_power_w = power_w

            cycle += 1
            if max_cycles is not None and cycle >= max_cycles:
                break

            time.sleep(poll_interval)

    finally:
        if owns_mqtt:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()


# --- Simulator ---

def simulate(cfg_path: Path = CONFIG_PATH, cycles: int = 20) -> None:
    """
    Run the agent with fake power readings that cycle through off→idle→on→idle→off.
    No real Modbus or MQTT broker needed — prints to stdout.
    """
    import math

    class FakeReader:
        def __init__(self):
            self._tick = 0

        def read_power_w(self, host, port, unit_id) -> float:
            self._tick += 1
            # Sine wave oscillating 0–8000W so we cross both thresholds
            return max(0.0, 4000 * math.sin(self._tick / 5) + 3000)

    class PrintMQTT:
        def publish(self, topic, payload, qos=0):
            data = json.loads(payload)
            print(f"  MQTT → {topic}")
            print(f"         {data['event_type']:20s}  power={data.get('power_w',''):.0f}W  state={data.get('state', data.get('event_type'))}")

        def loop_start(self): pass
        def loop_stop(self): pass
        def disconnect(self): pass

    log.info("=== SIMULATE MODE — no real hardware needed ===")
    run(cfg_path, reader=FakeReader(), mqtt_client=PrintMQTT(), max_cycles=cycles)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config",   default=str(CONFIG_PATH))
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--cycles",   type=int, default=20)
    args = parser.parse_args()

    cfg_path = Path(args.config)
    if args.simulate:
        simulate(cfg_path, cycles=args.cycles)
    else:
        run(cfg_path)
