"""Secure remote machine setup, trust, execution, and audit behavior."""

import base64
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import remote_setup
from db import init_db


CFG = Path(__file__).parent.parent / "config" / "machines.yaml"
HOST_BLOB = base64.b64encode(b"hive-test-host-key").decode("ascii")
FINGERPRINT = remote_setup._fingerprint(HOST_BLOB)


@pytest.fixture
def conn():
    connection = init_db(":memory:", check_same_thread=False)
    yield connection
    connection.close()


@pytest.fixture
def ssh_site(tmp_path, monkeypatch):
    ssh_dir = tmp_path / "ssh"
    ssh_dir.mkdir()
    (ssh_dir / "id_ed25519").write_text("private-test-key", encoding="utf-8")
    (ssh_dir / "id_ed25519.pub").write_text(
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest hive-test", encoding="utf-8"
    )
    monkeypatch.setenv("HIVE_SSH_DIR", str(ssh_dir))
    monkeypatch.setattr(remote_setup, "_tool_status", lambda: {
        "ssh": "/usr/bin/ssh", "scp": "/usr/bin/scp",
        "ssh-keyscan": "/usr/bin/ssh-keyscan", "ssh-keygen": "/usr/bin/ssh-keygen",
    })
    monkeypatch.setattr(remote_setup, "_scan_keys", lambda host, port, timeout_s=5: [{
        "key_type": "ssh-ed25519", "fingerprint": FINGERPRINT,
        "known_hosts_line": f"{host if port == 22 else f'[{host}]:{port}'} ssh-ed25519 {HOST_BLOB}",
    }])
    return ssh_dir


def _trust(conn):
    return remote_setup.trust_host(conn, CFG, {
        "machine_key": "morbidelli_cx100", "host": "10.0.0.104",
        "port": 22, "username": "hiveadmin", "fingerprint": FINGERPRINT,
    }, "Admin")


def test_plan_exposes_real_commissioning_gate(conn, ssh_site):
    result = remote_setup.plan(conn, CFG, "morbidelli_cx100")
    assert result["mode"] == "commissioning"
    assert result["credentials_stored"] is False
    assert result["ready_for_live_execution"] is False
    assert result["identity"]["status"] == "ready"


def test_connection_probe_reports_reachable(monkeypatch):
    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr("remote_setup.socket.create_connection", lambda *args, **kwargs: FakeSocket())
    result = remote_setup.test_connection({"host": "10.0.0.10", "port": 22})
    assert result["reachable"] is True
    assert result["status"] == "reachable"


def test_connection_probe_rejects_public_targets():
    with pytest.raises(ValueError, match="private factory-LAN"):
        remote_setup.test_connection({"host": "8.8.8.8", "port": 22})


def test_host_key_requires_matching_fingerprint(conn, ssh_site):
    scanned = remote_setup.scan_host_key(conn, CFG, {
        "machine_key": "morbidelli_cx100", "host": "10.0.0.104", "port": 22,
    })
    assert scanned["keys"][0]["fingerprint"] == FINGERPRINT
    with pytest.raises(ValueError, match="does not match"):
        remote_setup.trust_host(conn, CFG, {
            "machine_key": "morbidelli_cx100", "host": "10.0.0.104", "port": 22,
            "username": "hiveadmin", "fingerprint": "SHA256:not-the-machine",
        }, "Admin")

    profile = _trust(conn)
    assert profile["status"] == "trusted"
    assert profile["host_key_sha256"] == FINGERPRINT
    known_hosts = (ssh_site / "known_hosts").read_text(encoding="utf-8")
    assert "10.0.0.104 ssh-ed25519" in known_hosts


def test_live_execution_rejects_endpoint_or_user_drift(conn, ssh_site):
    _trust(conn)
    with pytest.raises(ValueError, match="differs from the trusted SSH endpoint"):
        remote_setup.authenticate(conn, CFG, {
            "machine_key": "morbidelli_cx100", "host": "10.0.0.105", "port": 22,
        }, "Admin")
    with pytest.raises(ValueError, match="username differs"):
        remote_setup.authenticate(conn, CFG, {
            "machine_key": "morbidelli_cx100", "host": "10.0.0.104", "port": 22,
            "username": "someone-else",
        }, "Admin")


def test_ssh_command_is_noninteractive_and_strict(ssh_site, monkeypatch):
    captured = {}
    monkeypatch.setattr(remote_setup.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(args, timeout_s=30):
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, "{}", "")

    monkeypatch.setattr(remote_setup, "_run_process", fake_run)
    remote_setup._ssh({
        "host": "10.0.0.104", "port": 22, "username": "hiveadmin",
    }, "@{} | ConvertTo-Json")
    args = captured["args"]
    assert "BatchMode=yes" in args
    assert "PasswordAuthentication=no" in args
    assert "StrictHostKeyChecking=yes" in args
    assert "IdentitiesOnly=yes" in args
    assert "shell=True" not in args


def test_folder_detection_defaults_to_preview_and_live_is_audited(conn, ssh_site, monkeypatch):
    preview = remote_setup.detect_folders(conn, CFG, {
        "machine_key": "morbidelli_cx100", "host": "10.0.0.104",
    }, "Admin")
    assert preview["mode"] == "dry_run"
    assert preview["will_execute"] is False
    assert preview["log_candidates"][0]["path"] == r"C:\SCM\Maestro\Logs"

    _trust(conn)
    monkeypatch.setattr(remote_setup, "_ssh", lambda *args, **kwargs: subprocess.CompletedProcess(
        [], 0, '{"status":"completed","log_candidates":[{"path":"C:\\\\SCM\\\\Maestro\\\\Logs","exists":true}],"cnc_candidates":[]}', ""
    ))
    live = remote_setup.detect_folders(conn, CFG, {
        "machine_key": "morbidelli_cx100", "host": "10.0.0.104", "execute": True,
    }, "Admin")
    assert live["mode"] == "live"
    assert live["status"] == "completed"
    run = conn.execute("SELECT * FROM remote_setup_runs").fetchone()
    assert run["action"] == "detect_folders"
    assert run["status"] == "succeeded"


def test_live_install_issues_ephemeral_bundle_and_audits_success(conn, ssh_site, monkeypatch):
    _trust(conn)
    monkeypatch.setattr(remote_setup.mqtt_security, "issue_bundle", lambda *args, **kwargs: (
        b"ephemeral-enrollment", {"enrollment_id": 41}
    ))
    monkeypatch.setattr(remote_setup, "_scp", lambda *args, **kwargs: subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(remote_setup, "_ssh", lambda *args, **kwargs: subprocess.CompletedProcess(
        [], 0, '{"status":"installed","agent_installed":true,"task_state":"Running"}', ""
    ))
    result = remote_setup.install_agent(conn, CFG, {
        "machine_key": "morbidelli_cx100", "host": "10.0.0.104",
        "log_folder": r"C:\SCM\Maestro\Logs", "execute": True,
    }, "Admin")
    assert result["status"] == "installed"
    assert result["enrollment_id"] == 41
    run = conn.execute("SELECT * FROM remote_setup_runs WHERE action='install'").fetchone()
    assert run["status"] == "succeeded"
    assert "private" not in (run["stdout_tail"] or "").lower()


def test_failed_install_revokes_orphaned_enrollment(conn, ssh_site, monkeypatch):
    _trust(conn)
    revoked = []
    monkeypatch.setattr(remote_setup.mqtt_security, "issue_bundle", lambda *args, **kwargs: (
        b"ephemeral-enrollment", {"enrollment_id": 42}
    ))
    monkeypatch.setattr(remote_setup.mqtt_security, "revoke", lambda *args, **kwargs: revoked.append(args[1]))
    monkeypatch.setattr(remote_setup, "_scp", lambda *args, **kwargs: subprocess.CompletedProcess(
        [], 1, "", "Permission denied"
    ))
    with pytest.raises(ValueError, match="Permission denied"):
        remote_setup.install_agent(conn, CFG, {
            "machine_key": "morbidelli_cx100", "host": "10.0.0.104",
            "log_folder": r"C:\SCM\Maestro\Logs", "execute": True,
        }, "Admin")
    assert revoked == [42]
    run = conn.execute("SELECT * FROM remote_setup_runs WHERE action='install'").fetchone()
    assert run["status"] == "failed"


def test_forget_host_removes_it_from_strict_known_hosts(conn, ssh_site):
    _trust(conn)
    result = remote_setup.forget_host(conn, "morbidelli_cx100", "Admin")
    assert result["status"] == "revoked"
    assert (ssh_site / "known_hosts").read_text(encoding="utf-8") == ""


def test_remote_setup_endpoints_keep_execution_opt_in(conn, monkeypatch):
    monkeypatch.setenv("HIVE_AUTH_MODE", "disabled")
    from fastapi.testclient import TestClient
    import main

    main.set_conn(conn)
    with TestClient(main.app) as client:
        main.set_conn(conn)
        response = client.get("/remote-setup/plan/stefani_kd")
        assert response.status_code == 200
        assert response.json()["mode"] == "commissioning"

        response = client.post("/remote-setup/install-agent", json={
            "machine_key": "stefani_kd",
        })
        assert response.status_code == 200
        assert response.json()["will_execute"] is False

        response = client.get("/remote-setup/snapshot")
        assert response.status_code == 200
        assert response.json()["summary"]["trusted_hosts"] == 0
