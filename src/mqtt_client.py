"""Shared, hostname-verifying MQTT client transport configuration."""

from __future__ import annotations

import ssl
from pathlib import Path

import paho.mqtt.client as mqtt


def _resolve(value: str, config_path: Path) -> str:
    path = Path(value)
    if not path.is_absolute():
        path = config_path.parent / path
    return str(path.resolve())


def configure(client: mqtt.Client, mqtt_cfg: dict, config_path: Path) -> None:
    """Apply mTLS and reconnect policy before the client connects."""
    tls = mqtt_cfg.get("tls") or {}
    enabled = bool(tls.get("enabled", False))
    if enabled:
        required = ("ca_cert", "client_cert", "client_key")
        missing = [name for name in required if not tls.get(name)]
        if missing:
            raise ValueError(f"MQTT TLS configuration is missing: {', '.join(missing)}")
        context = ssl.create_default_context(
            ssl.Purpose.SERVER_AUTH,
            cafile=_resolve(tls["ca_cert"], config_path),
        )
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_cert_chain(
            certfile=_resolve(tls["client_cert"], config_path),
            keyfile=_resolve(tls["client_key"], config_path),
        )
        client.tls_set_context(context)
    elif mqtt_cfg.get("require_tls", False):
        raise ValueError("MQTT TLS is required but no TLS client identity is configured")
    client.reconnect_delay_set(min_delay=1, max_delay=120)
