"""Tests for the safe remote machine setup scaffold."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import remote_setup
from db import init_db


CFG = Path(__file__).parent.parent / "config" / "machines.yaml"


def test_plan_is_explicitly_dry_run():
    result = remote_setup.plan(CFG, "morbidelli_cx100")
    assert result["mode"] == "dry_run"
    assert result["credentials_stored"] is False
    assert result["host"] == "192.168.1.104"


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


def test_folder_detection_returns_configured_candidate():
    result = remote_setup.detect_folders(CFG, {"machine_key": "stefani_kd"})
    assert result["mode"] == "dry_run"
    assert result["candidates"][0]["path"] == r"C:\SCM\Maestro\Logs"


def test_install_preview_never_executes():
    result = remote_setup.install_agent(CFG, {"machine_key": "stefani_kd"})
    assert result["will_execute"] is False
    assert result["install_dir"] == r"C:\HIVE-Agent"


def test_remote_setup_endpoints():
    from fastapi.testclient import TestClient
    import main

    conn = init_db(":memory:", check_same_thread=False)
    main.set_conn(conn)
    try:
        with TestClient(main.app) as client:
            main.set_conn(conn)
            response = client.get("/remote-setup/plan/stefani_kd")
            assert response.status_code == 200
            assert response.json()["mode"] == "dry_run"

            response = client.post("/remote-setup/install-agent", json={
                "machine_key": "stefani_kd",
            })
            assert response.status_code == 200
            assert response.json()["will_execute"] is False
    finally:
        conn.close()
