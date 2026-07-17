"""Local MQTT PKI, machine enrollment bundles, ACLs, and revocation."""

from __future__ import annotations

import argparse
import io
import ipaddress
import json
import os
import re
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


ROOT = Path(__file__).parent.parent
DEFAULT_PKI_DIR = ROOT / "data" / "mqtt-pki"
DEFAULT_MOSQUITTO_CONFIG = ROOT / "config" / "mosquitto.conf"
MACHINE_KEY = re.compile(r"^[a-z0-9_]+$")
DNS_NAME = re.compile(r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?))*$")
CENTRAL_CN = "hive-central"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _pki_dir(path: Path | None = None) -> Path:
    return Path(os.getenv("HIVE_MQTT_PKI_DIR", path or DEFAULT_PKI_DIR))


def _validate_host(value: str) -> str:
    host = (value or "").strip()
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        if not DNS_NAME.fullmatch(host):
            raise ValueError(f"Invalid broker DNS name or IP address: {host!r}")
        return host


def _secure_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(data)
    try:
        temp.chmod(mode)
    except OSError:
        pass
    temp.replace(path)


def _key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=3072)


def _key_pem(key: rsa.RSAPrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _name(common_name: str, unit: str) -> x509.Name:
    return x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "HIVE OS local factory"),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, unit),
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
    ])


def _load_authority(pki_dir: Path) -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    try:
        cert = x509.load_pem_x509_certificate((pki_dir / "ca.crt").read_bytes())
        key = serialization.load_pem_private_key((pki_dir / "ca.key").read_bytes(), password=None)
    except OSError as error:
        raise ValueError("MQTT security has not been initialized on this central PC") from error
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError("Unsupported MQTT certificate authority key type")
    return cert, key


def _signed_certificate(
    *, common_name: str, unit: str, public_key, ca_cert: x509.Certificate,
    ca_key: rsa.RSAPrivateKey, now: datetime, days: int,
    server_hosts: list[str] | None = None,
) -> x509.Certificate:
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(common_name, unit))
        .issuer_name(ca_cert.subject)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=True,
                data_encipherment=False, key_agreement=False, key_cert_sign=False,
                crl_sign=False, encipher_only=None, decipher_only=None,
            ),
            critical=True,
        )
    )
    if server_hosts:
        names: list[x509.GeneralName] = []
        for host in dict.fromkeys(server_hosts):
            try:
                names.append(x509.IPAddress(ipaddress.ip_address(host)))
            except ValueError:
                names.append(x509.DNSName(host))
        builder = builder.add_extension(x509.SubjectAlternativeName(names), critical=False)
        usage = ExtendedKeyUsageOID.SERVER_AUTH
    else:
        usage = ExtendedKeyUsageOID.CLIENT_AUTH
    return builder.add_extension(x509.ExtendedKeyUsage([usage]), critical=False).sign(ca_key, hashes.SHA256())


def _write_crl(conn: sqlite3.Connection, pki_dir: Path, now: datetime) -> Path:
    ca_cert, ca_key = _load_authority(pki_dir)
    builder = x509.CertificateRevocationListBuilder().issuer_name(ca_cert.subject)
    builder = builder.last_update(now).next_update(now + timedelta(days=825))
    rows = conn.execute(
        "SELECT certificate_serial,revoked_at FROM mqtt_enrollments WHERE status='revoked'"
    ).fetchall()
    for row in rows:
        revoked_at = datetime.fromisoformat(row["revoked_at"].replace("Z", "+00:00"))
        revoked = (
            x509.RevokedCertificateBuilder()
            .serial_number(int(row["certificate_serial"], 16))
            .revocation_date(revoked_at)
            .build()
        )
        builder = builder.add_revoked_certificate(revoked)
    path = pki_dir / "revocations.crl"
    _secure_write(path, builder.sign(ca_key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM), 0o644)
    return path


def _write_acl(conn: sqlite3.Connection, pki_dir: Path, topic_prefix: str) -> Path:
    lines = [
        f"user {CENTRAL_CN}",
        f"topic read {topic_prefix}/+/events",
        "topic read hive/telemetry/#",
        "",
        f"pattern write {topic_prefix}/%u/events",
        "",
    ]
    path = pki_dir / "mosquitto.acl"
    _secure_write(path, ("\n".join(lines) + "\n").encode("ascii"), 0o600)
    return path


def _mosquitto_config(pki_dir: Path, config_path: Path, port: int, data_dir: Path,
                      log_file: Path) -> None:
    lines = [
        f"listener {port}",
        "allow_anonymous false",
        f"cafile {pki_dir / 'ca.crt'}",
        f"certfile {pki_dir / 'broker.crt'}",
        f"keyfile {pki_dir / 'broker.key'}",
        "require_certificate true",
        "use_identity_as_username true",
        f"acl_file {pki_dir / 'mosquitto.acl'}",
        f"crlfile {pki_dir / 'revocations.crl'}",
        "tls_version tlsv1.2",
        "persistence true",
        f"persistence_location {str(data_dir).rstrip(os.sep)}{os.sep}",
        f"log_dest file {log_file}",
        "connection_messages true",
    ]
    _secure_write(config_path, ("\n".join(lines) + "\n").encode("utf-8"), 0o600)


def provision_broker(
    conn: sqlite3.Connection, broker_host: str, *, additional_hosts: list[str] | None = None,
    port: int = 8883, topic_prefix: str = "hive/machines", pki_dir: Path | None = None,
    config_path: Path = DEFAULT_MOSQUITTO_CONFIG, data_dir: Path | None = None,
    log_file: Path | None = None, site_config_path: Path | None = None,
    now: datetime | None = None,
) -> dict:
    pki_dir = _pki_dir(pki_dir)
    now = now or _now()
    broker_host = _validate_host(broker_host)
    additional_hosts = [_validate_host(host) for host in (additional_hosts or [])]
    if (pki_dir / "ca.key").exists():
        raise ValueError("MQTT security is already initialized; do not initialize it again")
    pki_dir.mkdir(parents=True, exist_ok=True)
    try:
        pki_dir.chmod(0o700)
    except OSError:
        pass

    ca_key = _key()
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(_name("HIVE OS local MQTT CA", "Certificate Authority"))
        .issuer_name(_name("HIVE OS local MQTT CA", "Certificate Authority"))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(True, False, False, False, False, True, True, False, False),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    _secure_write(pki_dir / "ca.key", _key_pem(ca_key))
    _secure_write(pki_dir / "ca.crt", ca_cert.public_bytes(serialization.Encoding.PEM), 0o644)

    broker_key = _key()
    hosts = [broker_host, *additional_hosts]
    broker_cert = _signed_certificate(
        common_name=broker_host, unit="MQTT Broker", public_key=broker_key.public_key(),
        ca_cert=ca_cert, ca_key=ca_key, now=now, days=397, server_hosts=hosts,
    )
    _secure_write(pki_dir / "broker.key", _key_pem(broker_key))
    _secure_write(pki_dir / "broker.crt", broker_cert.public_bytes(serialization.Encoding.PEM), 0o644)

    central_key = _key()
    central_cert = _signed_certificate(
        common_name=CENTRAL_CN, unit="Central Bridge", public_key=central_key.public_key(),
        ca_cert=ca_cert, ca_key=ca_key, now=now, days=397,
    )
    _secure_write(pki_dir / "central.key", _key_pem(central_key))
    _secure_write(pki_dir / "central.crt", central_cert.public_bytes(serialization.Encoding.PEM), 0o644)
    _write_acl(conn, pki_dir, topic_prefix)
    _write_crl(conn, pki_dir, now)
    _mosquitto_config(
        pki_dir, config_path, port, data_dir or ROOT / "data",
        log_file or ROOT / "logs" / "mosquitto.log",
    )
    state = {
        "broker_host": broker_host, "broker_port": port,
        "additional_hosts": list(dict.fromkeys(additional_hosts)),
        "topic_prefix": topic_prefix, "initialized_at": _iso(now),
        "broker_expires_at": _iso(broker_cert.not_valid_after_utc),
        "ca_expires_at": _iso(ca_cert.not_valid_after_utc),
    }
    _secure_write(pki_dir / "state.json", json.dumps(state, indent=2).encode("utf-8"), 0o600)
    if site_config_path:
        apply_central_config(site_config_path, state, pki_dir)
    return {**state, "pki_dir": str(pki_dir), "mosquitto_config": str(config_path)}


def status(conn: sqlite3.Connection, pki_dir: Path | None = None) -> dict:
    pki_dir = _pki_dir(pki_dir)
    state_path = pki_dir / "state.json"
    initialized = state_path.exists() and (pki_dir / "ca.key").exists()
    state = json.loads(state_path.read_text()) if initialized else {}
    rows = conn.execute(
        """SELECT e.*,m.machine_key,m.name machine_name FROM mqtt_enrollments e
           JOIN machines m ON m.id=e.machine_id ORDER BY e.issued_at DESC"""
    ).fetchall()
    now = _now()
    enrollments = []
    for raw in rows:
        row = dict(raw)
        effective = row["status"]
        if effective == "active" and datetime.fromisoformat(row["expires_at"]) <= now:
            effective = "expired"
        enrollments.append({
            "id": row["id"], "machine_key": row["machine_key"],
            "machine_name": row["machine_name"], "common_name": row["common_name"],
            "certificate_serial": row["certificate_serial"],
            "certificate_sha256": row["certificate_sha256"], "status": effective,
            "issued_by": row["issued_by"], "issued_at": row["issued_at"],
            "expires_at": row["expires_at"], "revoked_by": row["revoked_by"],
            "revoked_at": row["revoked_at"], "revocation_reason": row["revocation_reason"],
            "bundle_downloaded_at": row["bundle_downloaded_at"], "version": row["version"],
        })
    active = sum(item["status"] == "active" for item in enrollments)
    expiring = sum(
        item["status"] == "active" and datetime.fromisoformat(item["expires_at"]) <= now + timedelta(days=30)
        for item in enrollments
    )
    return {
        "initialized": initialized, **state, "active_enrollments": active,
        "expiring_within_30_days": expiring, "enrollments": enrollments,
        "broker_restart_required": (pki_dir / "restart-required").exists(),
    }


def issue_bundle(
    conn: sqlite3.Connection, machine_key: str, issued_by: str, *, days: int = 397,
    pki_dir: Path | None = None, now: datetime | None = None,
) -> tuple[bytes, dict]:
    if len(machine_key) > 63 or not MACHINE_KEY.fullmatch(machine_key):
        raise ValueError("Invalid machine key")
    if not 30 <= days <= 825:
        raise ValueError("Certificate validity must be 30-825 days")
    machine = conn.execute(
        "SELECT id,name,machine_key,active FROM machines WHERE machine_key=?", (machine_key,)
    ).fetchone()
    if not machine:
        raise KeyError(f"Machine '{machine_key}' not found")
    if not machine["active"]:
        raise ValueError("Cannot enroll an inactive machine")
    pki_dir = _pki_dir(pki_dir)
    state = status(conn, pki_dir)
    if not state["initialized"]:
        raise ValueError("Initialize MQTT security before enrolling machines")
    now = now or _now()
    ca_cert, ca_key = _load_authority(pki_dir)
    client_key = _key()
    common_name = machine_key
    cert = _signed_certificate(
        common_name=common_name, unit="Machine Agent", public_key=client_key.public_key(),
        ca_cert=ca_cert, ca_key=ca_key, now=now, days=days,
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    fingerprint = cert.fingerprint(hashes.SHA256()).hex()
    serial = format(cert.serial_number, "x")
    expires = cert.not_valid_after_utc
    cursor = conn.execute(
        """INSERT INTO mqtt_enrollments
           (machine_id,common_name,certificate_serial,certificate_sha256,certificate_pem,
            issued_by,issued_at,expires_at,bundle_downloaded_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (machine["id"], common_name, serial, fingerprint, cert_pem.decode("ascii"),
         issued_by, _iso(now), _iso(expires), _iso(now)),
    )
    _write_acl(conn, pki_dir, state.get("topic_prefix", "hive/machines"))
    conn.commit()
    manifest = {
        "format": "hive-mqtt-enrollment-v1", "enrollment_id": cursor.lastrowid,
        "machine_key": machine_key, "machine_name": machine["name"],
        "broker_host": state["broker_host"], "broker_port": state["broker_port"],
        "topic_prefix": state.get("topic_prefix", "hive/machines"),
        "common_name": common_name, "certificate_sha256": fingerprint,
        "issued_at": _iso(now), "expires_at": _iso(expires),
    }
    config = {
        "mqtt": {
            "broker_host": state["broker_host"], "broker_port": state["broker_port"],
            "keepalive": 60, "topic_prefix": state.get("topic_prefix", "hive/machines"),
            "require_tls": True,
            "tls": {
                "enabled": True, "ca_cert": "../certs/ca.crt",
                "client_cert": "../certs/client.crt", "client_key": "../certs/client.key",
            },
        },
        "maestro_agents": [{
            "machine_key": machine_key, "label": machine["name"], "host": "localhost",
            "log_folder": r"C:\SCM\Maestro\Logs", "cnc_folder": None,
        }],
    }
    import yaml
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("enrollment.json", json.dumps(manifest, indent=2))
        archive.writestr("config/machines.yaml", yaml.safe_dump(config, sort_keys=False))
        archive.writestr("certs/ca.crt", (pki_dir / "ca.crt").read_bytes())
        archive.writestr("certs/client.crt", cert_pem)
        archive.writestr("certs/client.key", _key_pem(client_key))
        payload_files = {
            ROOT / "deploy" / "windows" / "install-machine-agent.ps1": "install-machine-agent.ps1",
            ROOT / "src" / "maestro_agent.py": "payload/src/maestro_agent.py",
            ROOT / "src" / "mqtt_client.py": "payload/src/mqtt_client.py",
        }
        for source, destination in payload_files.items():
            if source.exists():
                archive.writestr(destination, source.read_bytes())
    return output.getvalue(), manifest


def apply_central_config(config_path: Path, state: dict, pki_dir: Path) -> None:
    import yaml
    current = yaml.safe_load(config_path.read_text()) or {}
    current["mqtt"] = {
        "broker_host": state["broker_host"],
        "broker_port": state["broker_port"],
        "keepalive": 60,
        "topic_prefix": state.get("topic_prefix", "hive/machines"),
        "require_tls": True,
        "tls": central_tls_config(pki_dir),
    }
    backup = config_path.with_suffix(config_path.suffix + ".pre-mqtt-tls")
    if not backup.exists():
        backup.write_bytes(config_path.read_bytes())
    _secure_write(
        config_path,
        yaml.safe_dump(current, sort_keys=False, allow_unicode=False).encode("utf-8"),
        0o600,
    )


def revoke(
    conn: sqlite3.Connection, enrollment_id: int, revoked_by: str, reason: str | None = None,
    *, pki_dir: Path | None = None, now: datetime | None = None,
) -> dict:
    row = conn.execute("SELECT * FROM mqtt_enrollments WHERE id=?", (enrollment_id,)).fetchone()
    if not row:
        raise KeyError(f"MQTT enrollment {enrollment_id} not found")
    if row["status"] != "active":
        raise ValueError("MQTT enrollment is already revoked")
    now = now or _now()
    pki_dir = _pki_dir(pki_dir)
    conn.execute(
        """UPDATE mqtt_enrollments SET status='revoked',revoked_by=?,revoked_at=?,
           revocation_reason=?,version=version+1 WHERE id=?""",
        (revoked_by, _iso(now), (reason or "").strip()[:500] or None, enrollment_id),
    )
    state = status(conn, pki_dir)
    _write_acl(conn, pki_dir, state.get("topic_prefix", "hive/machines"))
    _write_crl(conn, pki_dir, now)
    _secure_write(pki_dir / "restart-required", b"CRL and ACL changed\n")
    conn.commit()
    return {"id": enrollment_id, "status": "revoked", "revoked_at": _iso(now),
            "broker_restart_required": True}


def central_tls_config(pki_dir: Path | None = None) -> dict:
    pki_dir = _pki_dir(pki_dir)
    return {
        "enabled": True, "ca_cert": str(pki_dir / "ca.crt"),
        "client_cert": str(pki_dir / "central.crt"),
        "client_key": str(pki_dir / "central.key"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision HIVE MQTT mutual TLS")
    parser.add_argument("broker_host")
    parser.add_argument("--additional-host", action="append", default=[])
    parser.add_argument("--port", type=int, default=8883)
    parser.add_argument("--db", type=Path, default=ROOT / "hive.db")
    parser.add_argument("--pki-dir", type=Path, default=DEFAULT_PKI_DIR)
    parser.add_argument("--config", type=Path, default=DEFAULT_MOSQUITTO_CONFIG)
    args = parser.parse_args()
    from db import init_db
    conn = init_db(args.db)
    result = provision_broker(
        conn, args.broker_host, additional_hosts=args.additional_host, port=args.port,
        pki_dir=args.pki_dir, config_path=args.config,
        site_config_path=ROOT / "config" / "machines.yaml",
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
