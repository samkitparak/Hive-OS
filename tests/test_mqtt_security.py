"""Mutual-TLS broker provisioning, enrollment bundles, and revocation."""

import io
import hashlib
import json
import ssl
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import mqtt_client
import mqtt_security
import access_control
from db import init_db


NOW = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)


def _agent_payload(root: Path) -> Path:
    files = {
        "install-machine-agent.ps1": b"# installer",
        "payload/src/maestro_agent.py": b"# agent",
        "payload/src/mqtt_client.py": b"# mqtt",
        "payload/runtime/python-3.12-x64.exe": b"python-installer",
        "payload/requirements-agent.txt": b"paho-mqtt\nPyYAML\n",
        "payload/wheels/paho_mqtt-2.1.0-py3-none-any.whl": b"wheel-one",
        "payload/wheels/PyYAML-6.0.2-cp312-cp312-win_amd64.whl": b"wheel-two",
    }
    entries = []
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        entries.append({
            "path": relative, "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        })
    manifest = {
        "format": "hive-offline-agent-payload", "format_version": 1,
        "version": "0.24.0", "target": "windows-x64",
        "python_version": "3.12-64", "files": entries,
    }
    encoded = json.dumps(manifest, indent=2).encode()
    (root / "agent-payload.json").write_bytes(encoded)
    (root / "agent-payload.json.sha256").write_text(
        f"{hashlib.sha256(encoded).hexdigest()}  agent-payload.json\n", encoding="ascii",
    )
    return root


@pytest.fixture
def secure_site(tmp_path):
    conn = init_db(tmp_path / "hive.db")
    pki_dir = tmp_path / "pki"
    mosquitto_config = tmp_path / "mosquitto.conf"
    site_config = tmp_path / "machines.yaml"
    site_config.write_text("mqtt:\n  broker_host: 127.0.0.1\n  broker_port: 1883\n", encoding="utf-8")
    result = mqtt_security.provision_broker(
        conn, "10.20.30.40", additional_hosts=["hive-central"], pki_dir=pki_dir,
        config_path=mosquitto_config, site_config_path=site_config,
        data_dir=tmp_path / "data", log_file=tmp_path / "mosquitto.log", now=NOW,
    )
    yield conn, pki_dir, mosquitto_config, site_config, result
    conn.close()


def test_provisioned_broker_requires_client_certificates(secure_site):
    conn, pki_dir, mosquitto_config, site_config, result = secure_site
    assert result["broker_port"] == 8883
    config = mosquitto_config.read_text()
    assert "listener 8883" in config
    assert "allow_anonymous false" in config
    assert "require_certificate true" in config
    assert "use_identity_as_username true" in config
    assert "pattern write hive/machines/%u/events" in (pki_dir / "mosquitto.acl").read_text()
    broker = x509.load_pem_x509_certificate((pki_dir / "broker.crt").read_bytes())
    san = broker.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "10.20.30.40" in san.get_values_for_type(x509.IPAddress)[0].compressed
    assert "hive-central" in san.get_values_for_type(x509.DNSName)
    central = yaml.safe_load(site_config.read_text())["mqtt"]
    assert central["require_tls"] is True
    assert central["tls"]["client_cert"].endswith("central.crt")
    assert mqtt_security.status(conn, pki_dir)["initialized"] is True
    assert access_control.required_permissions("GET", "/mqtt-security") == ("admin",)
    with pytest.raises(ValueError, match="Invalid broker"):
        mqtt_security.provision_broker(conn, "https://not-a-host", pki_dir=pki_dir / "bad")


def test_machine_bundle_is_self_contained_and_private_key_is_not_stored(secure_site):
    conn, pki_dir, _, _, _ = secure_site
    bundle, manifest = mqtt_security.issue_bundle(
        conn, "morbidelli_cx100", "Test Admin", pki_dir=pki_dir, now=NOW,
    )
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        names = set(archive.namelist())
        assert {"enrollment.json", "config/machines.yaml", "certs/ca.crt",
                "certs/client.crt", "certs/client.key", "install-machine-agent.ps1",
                "payload/src/maestro_agent.py", "payload/src/mqtt_client.py"}.issubset(names)
        enrollment = json.loads(archive.read("enrollment.json"))
        assert enrollment["machine_key"] == "morbidelli_cx100"
        assert enrollment["broker_port"] == 8883
        assert enrollment["runtime_mode"] == "online_prerequisites_required"
        cert = x509.load_pem_x509_certificate(archive.read("certs/client.crt"))
        assert cert.subject.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME)[0].value == "morbidelli_cx100"
        assert cert.fingerprint(hashes.SHA256()).hex() == manifest["certificate_sha256"]
        private_key = archive.read("certs/client.key").decode("ascii")
    row = dict(conn.execute("SELECT * FROM mqtt_enrollments").fetchone())
    assert row["certificate_sha256"] == manifest["certificate_sha256"]
    assert private_key not in json.dumps(row)
    assert mqtt_security.status(conn, pki_dir)["broker_restart_required"] is False


def test_verified_agent_payload_is_embedded_in_enrollment(secure_site, tmp_path):
    conn, pki_dir, _, _, _ = secure_site
    payload_dir = _agent_payload(tmp_path / "offline-agent")
    payload = mqtt_security.agent_payload_status(payload_dir)
    assert payload["ready"] is True
    assert payload["file_count"] == 7
    bundle, manifest = mqtt_security.issue_bundle(
        conn, "morbidelli_cx100", "Test Admin", pki_dir=pki_dir,
        agent_payload_dir=payload_dir, now=NOW,
    )
    assert manifest["runtime_mode"] == "bundled_offline"
    assert manifest["agent_payload_sha256"] == payload["manifest_sha256"]
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        names = set(archive.namelist())
        assert "agent-payload.json.sha256" in names
        assert "payload/runtime/python-3.12-x64.exe" in names
        assert "payload/wheels/paho_mqtt-2.1.0-py3-none-any.whl" in names


def test_agent_payload_tampering_and_unsafe_paths_fail_closed(secure_site, tmp_path):
    conn, pki_dir, _, _, _ = secure_site
    payload_dir = _agent_payload(tmp_path / "tampered")
    (payload_dir / "payload/src/maestro_agent.py").write_bytes(b"changed")
    status = mqtt_security.agent_payload_status(payload_dir)
    assert status["status"] == "invalid"
    assert "hash does not match" in status["detail"] or "size does not match" in status["detail"]
    with pytest.raises(ValueError, match="payload is invalid"):
        mqtt_security.issue_bundle(
            conn, "action_e", "Admin", pki_dir=pki_dir,
            agent_payload_dir=payload_dir, now=NOW,
        )
    assert conn.execute("SELECT COUNT(*) FROM mqtt_enrollments").fetchone()[0] == 0

    payload_dir = _agent_payload(tmp_path / "unsafe")
    manifest_path = payload_dir / "agent-payload.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][0]["path"] = "../install-machine-agent.ps1"
    encoded = json.dumps(manifest, indent=2).encode()
    manifest_path.write_bytes(encoded)
    (payload_dir / "agent-payload.json.sha256").write_text(
        f"{hashlib.sha256(encoded).hexdigest()}  agent-payload.json\n", encoding="ascii",
    )
    status = mqtt_security.agent_payload_status(payload_dir)
    assert status["status"] == "invalid"
    assert "Unsafe agent payload path" in status["detail"]

    (payload_dir / "agent-payload.json.sha256").write_text("", encoding="ascii")
    status = mqtt_security.agent_payload_status(payload_dir)
    assert status["status"] == "invalid"
    assert "hash is missing" in status["detail"]


def test_revocation_writes_crl_and_preserves_replacement(secure_site):
    conn, pki_dir, _, _, _ = secure_site
    _, first = mqtt_security.issue_bundle(conn, "stefani_kd", "Admin", pki_dir=pki_dir, now=NOW)
    _, second = mqtt_security.issue_bundle(conn, "stefani_kd", "Admin", pki_dir=pki_dir, now=NOW)
    result = mqtt_security.revoke(
        conn, first["enrollment_id"], "Admin", "Rotated", pki_dir=pki_dir, now=NOW,
    )
    assert result["broker_restart_required"] is True
    status = mqtt_security.status(conn, pki_dir)
    assert status["active_enrollments"] == 1
    assert status["broker_restart_required"] is True
    assert next(item for item in status["enrollments"] if item["id"] == second["enrollment_id"])["status"] == "active"
    crl = x509.load_pem_x509_crl((pki_dir / "revocations.crl").read_bytes())
    revoked_serial = int(conn.execute(
        "SELECT certificate_serial FROM mqtt_enrollments WHERE id=?", (first["enrollment_id"],)
    ).fetchone()[0], 16)
    assert [entry.serial_number for entry in crl] == [revoked_serial]


class CaptureClient:
    def __init__(self):
        self.context = None
        self.reconnect = None

    def tls_set_context(self, context):
        self.context = context

    def reconnect_delay_set(self, min_delay, max_delay):
        self.reconnect = (min_delay, max_delay)


def test_shared_client_transport_requires_hostname_verifying_mtls(secure_site, tmp_path):
    conn, pki_dir, _, _, _ = secure_site
    bundle, _ = mqtt_security.issue_bundle(conn, "action_e", "Admin", pki_dir=pki_dir, now=NOW)
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        archive.extractall(tmp_path / "agent")
    config_path = tmp_path / "agent" / "config" / "machines.yaml"
    cfg = yaml.safe_load(config_path.read_text())["mqtt"]
    client = CaptureClient()
    mqtt_client.configure(client, cfg, config_path)
    assert client.context.verify_mode == ssl.CERT_REQUIRED
    assert client.context.check_hostname is True
    assert client.context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert client.reconnect == (1, 120)
    with pytest.raises(ValueError, match="required"):
        mqtt_client.configure(CaptureClient(), {"require_tls": True}, config_path)


def test_enrollment_api_is_admin_and_csrf_protected(tmp_path, monkeypatch):
    conn = init_db(tmp_path / "api.db", check_same_thread=False)
    token_path = tmp_path / "bootstrap.token"
    token_path.write_text("test-bootstrap-token-with-entropy", encoding="ascii")
    session = access_control.bootstrap(conn, {
        "bootstrap_token": "test-bootstrap-token-with-entropy",
        "username": "mqtt.admin", "display_name": "MQTT Admin",
        "password": "Correct horse battery staple 2026",
    }, token_path=token_path)
    pki_dir = tmp_path / "api-pki"
    mqtt_security.provision_broker(
        conn, "10.20.30.40", pki_dir=pki_dir,
        config_path=tmp_path / "mosquitto.conf", now=datetime.now(timezone.utc),
    )
    monkeypatch.setenv("HIVE_AUTH_MODE", "required")
    monkeypatch.setenv("HIVE_ALLOW_INSECURE_AUTH", "1")
    monkeypatch.setenv("HIVE_MQTT_PKI_DIR", str(pki_dir))
    import main
    client = TestClient(main.app)
    try:
        main.set_conn(conn)
        client.cookies.set(access_control.SESSION_COOKIE, session["token"])
        assert client.get("/api/mqtt-security").status_code == 200
        payload = {"machine_key": "nova_si400", "validity_days": 397}
        assert client.post("/api/mqtt-security/enrollments", json=payload).status_code == 403
        response = client.post(
            "/api/mqtt-security/enrollments", json=payload,
            headers={"X-CSRF-Token": session["csrf_token"]},
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert response.headers["cache-control"] == "no-store"
        assert "certs/client.key" in zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    finally:
        client.close()
        conn.close()
