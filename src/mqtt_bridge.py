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
from pathlib import Path

import paho.mqtt.client as mqtt
import yaml

import event_pipeline
import industrial_gateway
import mqtt_client as mqtt_client_config

log = logging.getLogger("mqtt_bridge")

CONFIG_PATH = Path(__file__).parent.parent / "config" / "machines.yaml"

# Each consumer gets its own queue so state tracking and all SSE clients receive
# every event instead of racing to drain one shared queue.
_subscribers: set[queue.Queue] = set()
_subscribers_lock = threading.Lock()


def subscribe_events(maxsize: int = 500) -> queue.Queue:
    subscriber = queue.Queue(maxsize=maxsize)
    with _subscribers_lock:
        _subscribers.add(subscriber)
    return subscriber


def unsubscribe_events(subscriber: queue.Queue) -> None:
    with _subscribers_lock:
        _subscribers.discard(subscriber)


def publish_event(event: dict) -> None:
    with _subscribers_lock:
        subscribers = list(_subscribers)

    for subscriber in subscribers:
        try:
            subscriber.put_nowait(event)
        except queue.Full:
            try:
                subscriber.get_nowait()
            except queue.Empty:
                pass
            subscriber.put_nowait(event)


def _on_message(conn: sqlite3.Connection, msg: mqtt.MQTTMessage) -> None:
    try:
        payload = json.loads(msg.payload.decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        log.warning("Bad payload on %s", msg.topic)
        return

    try:
        telemetry_results = industrial_gateway.ingest_mqtt_payload(
            conn, msg.topic, payload
        )
    except Exception:
        telemetry_results = []
        log.exception("Industrial MQTT ingestion failed on %s", msg.topic)

    # Approved MQTT telemetry contracts own pushed sample payloads. A payload
    # can still carry a valid machine event, in which case it continues below.
    if telemetry_results and payload.get("event_type") in (None, "telemetry"):
        log.info("← %s  %s telemetry profile(s)", msg.topic, len(telemetry_results))
        return

    machine_key = payload.get("machine_key")
    result = event_pipeline.ingest_event(conn, payload)
    if result["status"] == "rejected":
        log.warning("Rejected %s event: %s", payload.get("machine_key"), result["reason"])
        return
    if result["status"] == "duplicate":
        log.debug("Duplicate event ignored for %s", payload.get("machine_key"))
        return
    if result["status"] == "heartbeat":
        log.debug("Heartbeat from %s", payload.get("machine_key"))
        return

    broadcast = {
        **result["event"],
        "event_id": result["event_id"],
        "part_id": result.get("part_id"),
    }
    if result["status"] == "accepted":
        publish_event(broadcast)

    log.info("← %s  %s", machine_key, payload.get("event_type"))


def start(conn: sqlite3.Connection, cfg_path: Path = CONFIG_PATH) -> mqtt.Client:
    """
    Start the MQTT subscriber in a background thread.
    Returns the client so the caller can stop it on shutdown.
    """
    cfg          = yaml.safe_load(cfg_path.read_text())
    mqtt_cfg     = cfg["mqtt"]
    topic_prefix = mqtt_cfg["topic_prefix"]
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    def on_connect(c, userdata, flags, rc, props):
        if rc == 0:
            topic = f"{topic_prefix}/+/events"
            c.subscribe(topic, qos=1)
            log.info("MQTT subscribed to %s", topic)
        else:
            log.error("MQTT connect failed rc=%s", rc)

    def on_message(c, userdata, msg):
        _on_message(conn, msg)

    client.on_connect = on_connect
    client.on_message = on_message
    mqtt_client_config.configure(client, mqtt_cfg, cfg_path)

    client.connect(mqtt_cfg["broker_host"], mqtt_cfg["broker_port"],
                   keepalive=mqtt_cfg.get("keepalive", 60))

    thread = threading.Thread(target=client.loop_forever, daemon=True)
    thread.start()
    return client
