"""Tests for deployment and connection diagnostics."""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import diagnostics
import deployment
import config_editor
from db import init_db


CFG = Path(__file__).parent.parent / "config" / "machines.yaml"


@pytest.fixture
def conn():
    connection = init_db(":memory:", check_same_thread=False)
    yield connection
    connection.close()


def test_diagnostics_reports_services_and_machines(conn):
    result = diagnostics.build(conn, CFG, mqtt_connected=False, cv_watcher_running=False)
    assert len(result["services"]) == 22
    changeover_service = next(item for item in result["services"]
                              if item["key"] == "changeover_intelligence")
    assert changeover_service["status"] == "needs_site_value"
    assert result["summary"]["verified_changeover_standards"] == 0
    assert result["summary"]["learned_changeover_models"] == 0
    constraint_service = next(item for item in result["services"]
                              if item["key"] == "constraint_intelligence")
    assert constraint_service["status"] == "learning"
    assert result["summary"]["constraint_runtime_status"] == "starting"
    loss_service = next(item for item in result["services"]
                        if item["key"] == "production_loss")
    assert loss_service["status"] == "needs_site_value"
    assert result["summary"]["loss_reporting_machines"] == 0
    assert result["summary"]["loss_decision_ready_machines"] == 0
    readiness_service = next(item for item in result["services"]
                             if item["key"] == "factory_readiness")
    assert readiness_service["status"] == "needs_site_value"
    assert result["summary"]["machine_passports_confirmed"] == 0
    assert result["summary"]["machines_plug_and_play_ready"] == 0
    assert result["summary"]["commissioning_missions_active"] == 0
    assert result["summary"]["commissioning_missions_completed"] == 0
    assert 0 <= result["summary"]["machines_offsite_ready"] <= 15
    lab_service = next(item for item in result["services"]
                       if item["key"] == "virtual_factory_lab")
    assert lab_service["status"] == "needs_site_value"
    assert result["summary"]["virtual_lab_runs"] == 0
    evidence_service = next(item for item in result["services"]
                            if item["key"] == "commissioning_evidence")
    assert evidence_service["status"] == "needs_site_value"
    assert result["summary"]["commissioning_observations"] == 0
    assert result["summary"]["verified_maintenance_plans"] == 0
    maintenance_service = next(item for item in result["services"]
                               if item["key"] == "maintenance")
    assert maintenance_service["status"] == "needs_site_value"
    remote_service = next(item for item in result["services"]
                          if item["key"] == "remote_commissioning")
    assert remote_service["status"] == "needs_site_value"
    assert result["summary"]["machines_agent_commissioned"] == 0
    assert result["summary"]["agent_commissioning_attention"] == 0
    connector_service = next(item for item in result["services"]
                             if item["key"] == "connectors")
    assert connector_service["status"] == "needs_site_value"
    industrial_service = next(item for item in result["services"]
                              if item["key"] == "industrial_io")
    assert industrial_service["status"] == "needs_site_value"
    warehouse_service = next(item for item in result["services"]
                             if item["key"] == "warehouse")
    assert warehouse_service["status"] == "needs_site_value"
    procurement_service = next(item for item in result["services"]
                               if item["key"] == "procurement")
    assert procurement_service["status"] == "needs_site_value"
    improvement_service = next(item for item in result["services"]
                               if item["key"] == "improvement_learning")
    assert improvement_service["status"] == "needs_site_value"
    root_cause_service = next(item for item in result["services"]
                              if item["key"] == "root_cause_diagnostics")
    assert root_cause_service["status"] == "needs_site_value"
    alert_service = next(item for item in result["services"]
                         if item["key"] == "alert_management")
    assert alert_service["status"] == "needs_site_value"
    access_service = next(item for item in result["services"]
                          if item["key"] == "access_control")
    assert access_service["status"] in {"offline", "needs_site_value"}
    forecast_service = next(item for item in result["services"]
                            if item["key"] == "production_forecast")
    assert forecast_service["status"] == "needs_site_value"
    assert result["summary"]["forecast_calibration_outcomes"] == 0
    recovery_service = next(item for item in result["services"]
                            if item["key"] == "schedule_recovery")
    assert recovery_service["status"] == "needs_site_value"
    assert result["summary"]["recovery_action_required"] is False
    assert result["summary"]["total_machines"] == 15
    assert len(result["machines"]) == 15


def test_recent_machine_event_marks_agent_online(conn):
    machine_id = conn.execute(
        "SELECT id FROM machines WHERE machine_key='morbidelli_cx100'"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO machine_events (machine_id,event_type,ts) VALUES (?,?,?)",
        (machine_id, "heartbeat", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    result = diagnostics.build(conn, CFG, mqtt_connected=True, cv_watcher_running=False)
    machine = next(item for item in result["machines"] if item["machine_key"] == "morbidelli_cx100")
    assert machine["status"] == "online"


def test_diagnostics_uses_newest_agent_or_production_signal(conn):
    machine_id = conn.execute(
        "SELECT id FROM machines WHERE machine_key='morbidelli_cx100'"
    ).fetchone()["id"]
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO agent_status
           (machine_id,source,last_heartbeat_at,last_received_at)
           VALUES (?,?,?,?)""",
        (machine_id, "test", "2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO machine_events (machine_id,event_type,ts) VALUES (?,?,?)",
        (machine_id, "cycle_start", now),
    )
    conn.commit()
    result = diagnostics.build(conn, CFG, mqtt_connected=True, cv_watcher_running=False)
    machine = next(item for item in result["machines"] if item["machine_key"] == "morbidelli_cx100")
    assert machine["last_seen"] == now
    assert machine["status"] == "online"


def test_diagnostics_endpoint(conn):
    from fastapi.testclient import TestClient
    import main

    main.set_conn(conn)
    with TestClient(main.app) as client:
        main.set_conn(conn)
        response = client.get("/diagnostics")
        assert response.status_code == 200
        assert "summary" in response.json()


def test_deployment_readiness_lists_windows_assets():
    result = deployment.build(CFG)
    assert result["install_dir"] == r"C:\HIVE-OS"
    keys = {asset["key"] for asset in result["assets"]}
    assert "central_installer" in keys
    assert "machine_agent_installer" in keys
    assert "ssh_bootstrap" in keys
    assert "install_tester" in keys
    assert "industrial_preflight" in keys
    assert "offline_verifier" in keys

    bootstrap = (CFG.parent.parent / "deploy/windows/enable-hive-ssh.ps1").read_text()
    assert "OpenSSH.Server" in bootstrap
    assert "administrators_authorized_keys" in bootstrap
    assert "RemoteAddress LocalSubnet" in bootstrap
    assert "ssh-keygen.exe -lf" in bootstrap

    central = (CFG.parent.parent / "deploy/windows/install-central.ps1").read_text()
    assert "data\\ssh\\id_ed25519" in central
    assert "HIVE Machine Bootstrap" in central
    assert "Start-Process -FilePath ssh-keygen.exe" in central

    preflight = (CFG.parent.parent / "deploy/windows/test-industrial-network.ps1").read_text()
    assert preflight.index('$Protocol -eq "mqtt_json"') < preflight.index("-not $Endpoint")


def test_deployment_endpoint(conn):
    from fastapi.testclient import TestClient
    import main

    main.set_conn(conn)
    with TestClient(main.app) as client:
        main.set_conn(conn)
        response = client.get("/deployment")
        assert response.status_code == 200
        assert "checklist" in response.json()


def test_config_editor_saves_backup(tmp_path):
    cfg_path = tmp_path / "machines.yaml"
    cfg_path.write_text(CFG.read_text(), encoding="utf-8")
    result = config_editor.save(cfg_path, {
        "mqtt": {"broker_host": "10.0.0.5", "broker_port": 1883},
        "cv_watch_folder": r"D:\CV\Exports",
    })
    assert result["mqtt"]["broker_host"] == "10.0.0.5"
    assert result["cv_watch_folder"] == r"D:\CV\Exports"
    backup_path = Path(result["backup_path"])
    assert backup_path.exists()
    assert backup_path.parent.name == "backups"
    assert not cfg_path.with_suffix(".yaml.tmp").exists()


def test_config_editor_locks_provisioned_mqtt_identity(tmp_path):
    cfg_path = tmp_path / "machines.yaml"
    cfg_path.write_text(
        "mqtt:\n  broker_host: 10.0.0.5\n  broker_port: 8883\n  keepalive: 60\n"
        "  topic_prefix: hive/machines\n  require_tls: true\n  tls:\n    enabled: true\n"
        "    ca_cert: ca.crt\n    client_cert: central.crt\n    client_key: central.key\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="locked"):
        config_editor.save(cfg_path, {"mqtt": {
            "broker_host": "10.0.0.6", "broker_port": 8883, "keepalive": 60,
            "topic_prefix": "hive/machines", "require_tls": True,
            "tls": {"enabled": True, "ca_cert": "ca.crt", "client_cert": "central.crt",
                    "client_key": "central.key"},
        }})


def test_config_endpoint(conn):
    from fastapi.testclient import TestClient
    import main

    main.set_conn(conn)
    with TestClient(main.app) as client:
        main.set_conn(conn)
        response = client.get("/config")
        assert response.status_code == 200
        assert "maestro_agents" in response.json()


def test_config_endpoint_rejects_incomplete_payload(conn):
    from fastapi.testclient import TestClient
    import main

    main.set_conn(conn)
    with TestClient(main.app) as client:
        main.set_conn(conn)
        response = client.put("/config", json={"mqtt": {"broker_host": "localhost"}})
        assert response.status_code == 422
