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
    assert len(result["services"]) == 12
    assert result["summary"]["verified_maintenance_plans"] == 0
    maintenance_service = next(item for item in result["services"]
                               if item["key"] == "maintenance")
    assert maintenance_service["status"] == "needs_site_value"
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
    assert "install_tester" in keys
    assert "industrial_preflight" in keys

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
