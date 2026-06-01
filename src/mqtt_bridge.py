"""
MQTT bridge — subscribes to all machine event topics, writes events to DB,
and pushes them onto an in-process queue so SSE can stream them to the dashboard.

Runs as a background thread inside the FastAPI process.
"""

import json
import logging
import queue
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import paho.mqtt.client as mqtt
import yaml

log = logging.getLogger("mqtt_bridge")

CONFIG_PATH = Path(__file__).parent.parent / "config" / "machines.yaml"

# Global broadcast queue — API layer reads from this for SSE
_event_queue: queue.Queue = queue.Queue(maxsize=500)


def get_event_queue() -> queue.Queue:
    return _event_queue


def _machine_id_map(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT machine_key, id FROM machines").fetchall()
    return {r["machine_key"]: r["id"] for r in rows}


def _resolve_part_id(conn: sqlite3.Connection, cnc_file: Optional[str]) -> Optional[int]:
    """Look up part by cnc_file_back or cnc_file_front."""
    if not cnc_file:
        return None
    # Strip .xcs extension if present
    stem = cnc_file.replace(".xcs", "").replace(".ard", "")
    row = conn.execute(
        "SELECT id FROM parts WHERE cnc_file_back=? OR cnc_file_front=? LIMIT 1",
        (stem, stem)
    ).fetchone()
    return row["id"] if row else None


def _write_event(conn: sqlite3.Connection, machine_id: int,
                 payload: dict, part_id: Optional[int]) -> int:
    conn.execute(
        """INSERT INTO machine_events
           (machine_id, event_type, part_id, cnc_file, raw_payload, ts)
           VALUES (?,?,?,?,?,?)""",
        (
            machine_id,
            payload.get("event_type", "unknown"),
            part_id,
            payload.get("cnc_file"),
            json.dumps(payload),
            payload.get("ts", datetime.now(timezone.utc).isoformat()),
        )
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _on_message(conn: sqlite3.Connection, machine_map: dict[str, int],
                msg: mqtt.MQTTMessage) -> None:
    try:
        payload = json.loads(msg.payload.decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        log.warning("Bad payload on %s", msg.topic)
        return

    machine_key = payload.get("machine_key")
    machine_id  = machine_map.get(machine_key)
    if not machine_id:
        log.debug("Unknown machine_key: %s", machine_key)
        return

    part_id = _resolve_part_id(conn, payload.get("cnc_file"))
    event_id = _write_event(conn, machine_id, payload, part_id)

    broadcast = {**payload, "event_id": event_id, "part_id": part_id}
    try:
        _event_queue.put_nowait(broadcast)
    except queue.Full:
        _event_queue.get_nowait()  # drop oldest, make room
        _event_queue.put_nowait(broadcast)

    log.info("← %s  %s", machine_key, payload.get("event_type"))


def start(conn: sqlite3.Connection, cfg_path: Path = CONFIG_PATH) -> mqtt.Client:
    """
    Start the MQTT subscriber in a background thread.
    Returns the client so the caller can stop it on shutdown.
    """
    cfg          = yaml.safe_load(cfg_path.read_text())
    mqtt_cfg     = cfg["mqtt"]
    topic_prefix = mqtt_cfg["topic_prefix"]
    machine_map  = _machine_id_map(conn)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    def on_connect(c, userdata, flags, rc, props):
        if rc == 0:
            topic = f"{topic_prefix}/+/events"
            c.subscribe(topic, qos=1)
            log.info("MQTT subscribed to %s", topic)
        else:
            log.error("MQTT connect failed rc=%s", rc)

    def on_message(c, userdata, msg):
        _on_message(conn, machine_map, msg)

    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(mqtt_cfg["broker_host"], mqtt_cfg["broker_port"],
                   keepalive=mqtt_cfg.get("keepalive", 60))

    thread = threading.Thread(target=client.loop_forever, daemon=True)
    thread.start()
    return client
