"""Tests for Maestro log watcher agent."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from maestro_agent import (
    _parse_log_line, _extract_cnc_file, _build_payload,
    _simulated_log_lines, run, CONFIG_PATH,
)


# --- Log line parser ---

def test_parse_machine_on():
    line = "2026-05-31 08:12:00 [INFO ] MACHINE_ON  unit=morbidelli_cx100"
    p = _parse_log_line(line)
    assert p["event_type"] == "power_on"
    assert p["ts"] == "2026-05-31 08:12:00"

def test_parse_machine_off():
    p = _parse_log_line("2026-05-31 09:00:00 [INFO ] MACHINE_OFF")
    assert p["event_type"] == "power_off"

def test_parse_cycle_start():
    p = _parse_log_line("2026-05-31 08:12:05 [INFO ] CYCLE_START program=r86b0002.xcs")
    assert p["event_type"] == "cycle_start"
    assert p["program"] == "r86b0002.xcs"

def test_parse_cycle_end():
    p = _parse_log_line("2026-05-31 08:12:47 [INFO ] CYCLE_END   program=r86b0002.xcs  duration=42s")
    assert p["event_type"] == "cycle_end"
    assert p["duration"] == "42s"

def test_parse_idle():
    p = _parse_log_line("2026-05-31 08:12:48 [INFO ] MACHINE_IDLE")
    assert p["event_type"] == "idle"

def test_parse_alarm():
    p = _parse_log_line("2026-05-31 08:15:00 [WARN ] ALARM       code=1042 msg=Feed_axis_overload")
    assert p["event_type"] == "alarm"
    assert p["code"] == "1042"

def test_parse_unknown_event_returns_none():
    p = _parse_log_line("2026-05-31 08:00:00 [INFO ] HEARTBEAT tick=1234")
    assert p is None

def test_parse_malformed_line_returns_none():
    assert _parse_log_line("not a log line") is None
    assert _parse_log_line("") is None


# --- CNC file extraction ---

def test_extract_cnc_file_from_cycle_start():
    p = _parse_log_line("2026-05-31 08:12:05 [INFO ] CYCLE_START program=r86b0002.xcs")
    assert _extract_cnc_file(p) == "r86b0002.xcs"

def test_extract_cnc_file_missing():
    p = _parse_log_line("2026-05-31 08:12:00 [INFO ] MACHINE_ON  unit=morbidelli_cx100")
    assert _extract_cnc_file(p) is None

def test_extract_cnc_file_no_xcs_extension():
    p = {"event_type": "cycle_start", "program": "somefile.txt"}
    assert _extract_cnc_file(p) is None


# --- Payload structure ---

def test_build_payload_fields():
    parsed = {"event_type": "cycle_start", "ts": "2026-05-31 08:12:05", "program": "r86b0002.xcs"}
    payload = _build_payload("morbidelli_cx100", parsed, cnc_file="r86b0002.xcs")
    assert payload["machine_key"] == "morbidelli_cx100"
    assert payload["event_type"]  == "cycle_start"
    assert payload["cnc_file"]    == "r86b0002.xcs"
    assert payload["source"]      == "maestro_log"
    assert payload["ts"]          == "2026-05-31 08:12:05"

def test_build_payload_alarm():
    parsed = {"event_type": "alarm", "ts": "2026-05-31 08:15:00", "code": "1042"}
    payload = _build_payload("morbidelli_cx100", parsed)
    assert payload["alarm_code"] == "1042"
    assert payload["cnc_file"]   is None

def test_build_payload_duration():
    parsed = {"event_type": "cycle_end", "ts": "2026-05-31 08:12:47",
              "program": "r86b0002.xcs", "duration": "42s"}
    payload = _build_payload("morbidelli_cx100", parsed, "r86b0002.xcs")
    assert payload["duration_s"] == "42"


# --- Simulated log generator ---

def test_simulated_lines_contain_all_event_types():
    lines = list(_simulated_log_lines("morbidelli_cx100", cycles=5))
    parsed = [_parse_log_line(l) for l in lines]
    parsed = [p for p in parsed if p]
    event_types = {p["event_type"] for p in parsed}
    assert "power_on"    in event_types
    assert "power_off"   in event_types
    assert "cycle_start" in event_types
    assert "cycle_end"   in event_types
    assert "idle"        in event_types
    assert "alarm"       in event_types

def test_simulated_lines_have_xcs_files():
    lines = list(_simulated_log_lines("morbidelli_cx100", cycles=3))
    parsed = [_parse_log_line(l) for l in lines if l.strip()]
    cnc_files = [_extract_cnc_file(p) for p in parsed if p]
    cnc_files = [f for f in cnc_files if f]
    assert len(cnc_files) > 0
    assert all(f.endswith(".xcs") for f in cnc_files)


# --- Full run loop ---

class CaptureMQTT:
    def __init__(self):
        self.messages = []
    def publish(self, topic, payload, qos=0):
        self.messages.append({"topic": topic, "payload": json.loads(payload)})
    def loop_start(self): pass
    def loop_stop(self): pass
    def disconnect(self): pass


def test_run_publishes_events():
    lines = list(_simulated_log_lines("morbidelli_cx100", cycles=3))
    mqtt  = CaptureMQTT()
    run("morbidelli_cx100", CONFIG_PATH,
        mqtt_client=mqtt,
        log_lines_iter=iter(lines),
        max_lines=len(lines))

    assert len(mqtt.messages) > 0
    event_types = {m["payload"]["event_type"] for m in mqtt.messages}
    assert "power_on" in event_types
    assert "cycle_start" in event_types

def test_run_topic_format():
    lines = list(_simulated_log_lines("morbidelli_cx100", cycles=1))
    mqtt  = CaptureMQTT()
    run("morbidelli_cx100", CONFIG_PATH,
        mqtt_client=mqtt,
        log_lines_iter=iter(lines),
        max_lines=len(lines))

    topics = {m["topic"] for m in mqtt.messages}
    assert "hive/machines/morbidelli_cx100/events" in topics

def test_run_cycle_start_includes_cnc_file():
    lines = list(_simulated_log_lines("morbidelli_cx100", cycles=2))
    mqtt  = CaptureMQTT()
    run("morbidelli_cx100", CONFIG_PATH,
        mqtt_client=mqtt,
        log_lines_iter=iter(lines),
        max_lines=len(lines))

    cycle_starts = [m for m in mqtt.messages if m["payload"]["event_type"] == "cycle_start"]
    assert len(cycle_starts) > 0
    assert all(m["payload"]["cnc_file"].endswith(".xcs") for m in cycle_starts)

def test_run_unknown_machine_raises():
    with pytest.raises(ValueError, match="No maestro_agent config"):
        run("nonexistent_machine", CONFIG_PATH,
            mqtt_client=CaptureMQTT(),
            log_lines_iter=iter([]),
            max_lines=0)


def test_idle_live_agent_publishes_heartbeat(monkeypatch):
    mqtt = CaptureMQTT()
    ticks = iter([60.0])
    monkeypatch.setattr("maestro_agent.time.monotonic", lambda: next(ticks))
    run("morbidelli_cx100", CONFIG_PATH,
        mqtt_client=mqtt,
        log_lines_iter=iter([None]),
        max_lines=1)
    assert mqtt.messages[0]["payload"]["event_type"] == "heartbeat"
