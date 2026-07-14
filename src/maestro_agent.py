"""
Maestro log watcher agent — runs on each SCM machine PC (or central PC via
network share), tails Maestro log files for state events, watches CNC folder
for .xcs file access (Morbidelli only), publishes to MQTT.

MQTT topic:  hive/machines/{machine_key}/events
Payload:     JSON — see _build_payload()

# ── INDIA TODO ──────────────────────────────────────────────────────────────
# The exact format still needs validation against the installed Maestro version.
# On-site steps (30 min):
#   1. Open C:\\SCM\\Maestro\\Logs\\ on any SCM machine PC
#   2. Open the most recent .log file
#   3. Note the actual line format and event keywords
#   4. Run the HIVE commissioning analyzer and map any unknown event aliases
#   5. Update cnc_folder paths in config/machines.yaml
# See INDIA_CHECKLIST.md → "Maestro Log Watcher" section
# ─────────────────────────────────────────────────────────────────────────────

Usage:
    python src/maestro_agent.py --machine morbidelli_cx100
    python src/maestro_agent.py --machine morbidelli_cx100 --simulate
"""

import argparse
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import paho.mqtt.client as mqtt
import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
)
log = logging.getLogger("maestro_agent")

CONFIG_PATH = Path(__file__).parent.parent / "config" / "machines.yaml"

# ── INDIA TODO: replace these with real Maestro log patterns ────────────────
#
# Simulated format (what we generate in tests/simulate mode):
#   2026-05-31 08:12:00 [INFO ] MACHINE_ON  unit=morbidelli_cx100
#   2026-05-31 08:12:05 [INFO ] CYCLE_START program=r86b0002.xcs
#   2026-05-31 08:12:47 [INFO ] CYCLE_END   program=r86b0002.xcs  duration=42s
#   2026-05-31 08:12:48 [INFO ] MACHINE_IDLE
#   2026-05-31 08:15:00 [WARN ] ALARM       code=1042 msg=Feed_axis_overload
#   2026-05-31 09:00:00 [INFO ] MACHINE_OFF
#
# The exact pattern is followed by conservative aliases for common log formats.
# ─────────────────────────────────────────────────────────────────────────────

MAESTRO_LOG_PATTERN = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"
    r"\s+\[[^\]]+\]\s+"
    r"(?P<event>\w+)"
    r"(?:\s+(?P<fields>.+))?"
)

# ── INDIA TODO: replace keys with real event keywords from Maestro logs ─────
MAESTRO_EVENTS = {
    "MACHINE_ON":    "power_on",
    "MACHINE_OFF":   "power_off",
    "MACHINE_IDLE":  "idle",
    "CYCLE_START":   "cycle_start",
    "CYCLE_END":     "cycle_end",
    "ALARM":         "alarm",
}

# Conservative aliases used by the commissioning analyzer. Exact simulated
# lines still take the fast path above; these cover common industrial log terms
# without binding the rest of HIVE to one Maestro version or language pack.
MAESTRO_EVENT_ALIASES = {
    "power_on": ("MACHINE_ON", "POWER_ON", "MACHINE STARTED"),
    "power_off": ("MACHINE_OFF", "POWER_OFF", "MACHINE STOPPED"),
    "cycle_start": ("CYCLE_START", "CYCLE START", "PROGRAM START", "PROGRAM_STARTED"),
    "cycle_end": ("CYCLE_END", "CYCLE END", "PROGRAM END", "PROGRAM_COMPLETED", "PROGRAM COMPLETED"),
    "idle": ("MACHINE_IDLE", "MACHINE IDLE", "WAITING FOR PART"),
    "alarm": ("ALARM", "FAULT", "ERROR"),
    "part_complete": ("PART_COMPLETE", "PART COMPLETE", "SCAN_OUT", "QC_OK"),
}

FLEXIBLE_TIMESTAMP = re.compile(
    r"(?P<ts>\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}:\d{2}"
    r"|\d{2}[./-]\d{2}[./-]\d{4}[ T]\d{2}:\d{2}:\d{2})"
)
PROGRAM_FILE = re.compile(r"(?P<program>[^\s=;,\"']+\.(?:xcs|ard))", re.IGNORECASE)
# ─────────────────────────────────────────────────────────────────────────────


def _parse_log_line(line: str) -> Optional[dict]:
    """
    Parse one Maestro log line → structured dict, or None if unrecognised.

    # INDIA TODO: if the real log format doesn't match MAESTRO_LOG_PATTERN,
    # rewrite this function. Everything else in the agent stays the same.
    """
    clean = line.strip()
    m = MAESTRO_LOG_PATTERN.match(clean)
    if not m:
        ts_match = FLEXIBLE_TIMESTAMP.search(clean)
        if not ts_match:
            return None
        upper = clean.upper()
        event_type = next(
            (canonical for canonical, aliases in MAESTRO_EVENT_ALIASES.items()
             if any(alias in upper for alias in aliases)),
            None,
        )
        if not event_type:
            return None
        fields = {}
        for key, value in re.findall(r"([A-Za-z_][\w.-]*)\s*=\s*([^\s;,]+)", clean):
            fields[key.lower()] = value.strip('"\'')
        program_match = PROGRAM_FILE.search(clean)
        if program_match and "program" not in fields:
            program = program_match.group("program")
            fields["program"] = program.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        ts = ts_match.group("ts").replace("/", "-").replace("T", " ")
        if re.match(r"\d{2}[.-]\d{2}[.-]\d{4}", ts):
            ts = datetime.strptime(ts.replace(".", "-"), "%d-%m-%Y %H:%M:%S").strftime("%Y-%m-%d %H:%M:%S")
        return {"event_type": event_type, "ts": ts, "raw": clean, **fields}

    event_raw = m.group("event")
    event_type = MAESTRO_EVENTS.get(event_raw)
    if not event_type:
        return None

    fields_raw = m.group("fields") or ""
    fields: dict = {}
    for token in fields_raw.split():
        if "=" in token:
            k, v = token.split("=", 1)
            fields[k] = v

    return {
        "event_type": event_type,
        "ts":         m.group("ts"),
        "raw":        clean,
        **fields,
    }


def _extract_cnc_file(parsed: dict) -> Optional[str]:
    """Pull the .xcs filename out of CYCLE_START / CYCLE_END events."""
    prog = parsed.get("program", "")
    if str(prog).lower().endswith((".xcs", ".ard")):
        return prog
    return None


def _build_payload(machine_key: str, parsed: dict, cnc_file: Optional[str] = None) -> dict:
    return {
        "machine_key": machine_key,
        "event_type":  parsed["event_type"],
        "cnc_file":    cnc_file,
        "alarm_code":  parsed.get("code"),
        "duration_s":  parsed.get("duration", "").rstrip("s") or None,
        "ts":          parsed["ts"],
        "source":      "maestro_log",
    }


# ── Log tail ─────────────────────────────────────────────────────────────────

def _find_latest_log(log_folder: Path) -> Optional[Path]:
    logs = sorted(log_folder.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return logs[0] if logs else None


class LogTailer:
    """Tails a log file, yielding new lines as they appear. Handles rotation."""

    def __init__(self, log_folder: Path):
        self.log_folder = log_folder
        self._path: Optional[Path] = None
        self._pos: int = 0

    def _open_latest(self):
        latest = _find_latest_log(self.log_folder)
        if latest and latest != self._path:
            self._path = latest
            self._pos  = latest.stat().st_size  # start from end for live mode
            log.info("Watching log: %s", latest)

    def lines(self):
        self._open_latest()
        if not self._path:
            return

        # Recheck for log rotation
        latest = _find_latest_log(self.log_folder)
        if latest != self._path:
            self._path = latest
            self._pos  = 0

        try:
            with open(self._path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self._pos)
                for line in f:
                    yield line
                self._pos = f.tell()
        except FileNotFoundError:
            self._path = None
            self._pos  = 0


# ── MQTT ─────────────────────────────────────────────────────────────────────

def _make_mqtt_client(broker_host: str, broker_port: int) -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    def on_connect(c, userdata, flags, rc, props):
        if rc == 0:
            log.info("MQTT connected to %s:%s", broker_host, broker_port)

    client.on_connect = on_connect
    client.connect(broker_host, broker_port, keepalive=60)
    client.loop_start()
    return client


def _publish(client: mqtt.Client, topic_prefix: str, machine_key: str, payload: dict):
    topic = f"{topic_prefix}/{machine_key}/events"
    client.publish(topic, json.dumps(payload), qos=1)
    log.info("→ %s  %s  cnc=%s", machine_key, payload["event_type"], payload.get("cnc_file"))


# ── Main run loop ─────────────────────────────────────────────────────────────

def run(machine_key: str,
        cfg_path: Path = CONFIG_PATH,
        mqtt_client: Optional[mqtt.Client] = None,
        log_lines_iter=None,
        max_lines: Optional[int] = None) -> None:
    """
    Watch Maestro logs for one machine, publish events to MQTT.

    log_lines_iter: injectable iterator of log lines (used in tests/simulate).
                    If None, uses real LogTailer on configured log_folder.
    max_lines:      stop after processing N lines (tests only).
    """
    cfg = yaml.safe_load(cfg_path.read_text())
    mqtt_cfg     = cfg["mqtt"]
    topic_prefix = mqtt_cfg["topic_prefix"]

    # Find this machine's config
    agents = cfg.get("maestro_agents", [])
    machine_cfg = next((a for a in agents if a["machine_key"] == machine_key), None)
    if not machine_cfg:
        raise ValueError(f"No maestro_agent config found for '{machine_key}'")

    owns_mqtt = mqtt_client is None
    if owns_mqtt:
        mqtt_client = _make_mqtt_client(mqtt_cfg["broker_host"], mqtt_cfg["broker_port"])
        time.sleep(0.5)

    if log_lines_iter is None:
        log_folder = Path(machine_cfg["log_folder"])
        tailer     = LogTailer(log_folder)

        def _live_iter():
            while True:
                yield from tailer.lines()
                yield None
                time.sleep(1)

        log_lines_iter = _live_iter()

    lines_processed = 0
    last_heartbeat = 0.0
    try:
        for line in log_lines_iter:
            now = time.monotonic()
            if line is None:
                if now - last_heartbeat >= 60:
                    _publish(mqtt_client, topic_prefix, machine_key, {
                        "machine_key": machine_key,
                        "event_type": "heartbeat",
                        "cnc_file": None,
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "source": "maestro_log",
                    })
                    last_heartbeat = now
                continue

            parsed = _parse_log_line(line)
            if parsed:
                cnc_file = _extract_cnc_file(parsed)
                payload  = _build_payload(machine_key, parsed, cnc_file)
                _publish(mqtt_client, topic_prefix, machine_key, payload)

            lines_processed += 1
            if max_lines is not None and lines_processed >= max_lines:
                break
    finally:
        if owns_mqtt:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()


# ── Simulator ────────────────────────────────────────────────────────────────

def _simulated_log_lines(machine_key: str, cycles: int = 5):
    """Generate realistic Maestro log lines for testing."""
    cnc_files = ["r86b0002.xcs", "r86b0006.xcs", "r86b0043.xcs", "r86b0048.xcs"]
    ts = datetime(2026, 5, 31, 8, 0, 0)

    def _ts():
        return ts.strftime("%Y-%m-%d %H:%M:%S")

    yield f"{_ts()} [INFO ] MACHINE_ON  unit={machine_key}\n"

    for i in range(cycles):
        import datetime as dt
        ts += dt.timedelta(seconds=10)
        xcs = cnc_files[i % len(cnc_files)]
        yield f"{_ts()} [INFO ] CYCLE_START program={xcs}\n"

        ts += dt.timedelta(seconds=42)
        yield f"{_ts()} [INFO ] CYCLE_END   program={xcs}  duration=42s\n"

        ts += dt.timedelta(seconds=5)
        yield f"{_ts()} [INFO ] MACHINE_IDLE\n"

        if i == 2:
            ts += dt.timedelta(seconds=3)
            yield f"{_ts()} [WARN ] ALARM       code=1042 msg=Feed_axis_overload\n"

        ts += dt.timedelta(seconds=8)

    yield f"{_ts()} [INFO ] MACHINE_OFF\n"


def simulate(machine_key: str, cfg_path: Path = CONFIG_PATH, cycles: int = 5):
    class PrintMQTT:
        def publish(self, topic, payload, qos=0):
            data = json.loads(payload)
            cnc = f"  cnc={data['cnc_file']}" if data.get("cnc_file") else ""
            alarm = f"  alarm={data['alarm_code']}" if data.get("alarm_code") else ""
            print(f"  MQTT → {topic}")
            print(f"         {data['event_type']:15s}{cnc}{alarm}")
        def loop_start(self): pass
        def loop_stop(self): pass
        def disconnect(self): pass

    log.info("=== SIMULATE MODE — machine=%s ===", machine_key)
    lines = list(_simulated_log_lines(machine_key, cycles))
    run(machine_key, cfg_path,
        mqtt_client=PrintMQTT(),
        log_lines_iter=iter(lines),
        max_lines=len(lines))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine",  required=True, help="machine_key from config")
    parser.add_argument("--config",   default=str(CONFIG_PATH))
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--cycles",   type=int, default=5)
    args = parser.parse_args()

    cfg_path = Path(args.config)
    if args.simulate:
        simulate(args.machine, cfg_path, cycles=args.cycles)
    else:
        run(args.machine, cfg_path)
