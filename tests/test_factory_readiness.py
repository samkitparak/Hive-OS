"""Factory passports, field packs, and non-invasive connection readiness."""

import csv
import hashlib
import io
import json
import socket
import sys
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import factory_readiness
import commissioning_evidence
from db import init_db


ROOT = Path(__file__).parent.parent
CONFIG = ROOT / "config" / "machines.yaml"


@pytest.fixture
def conn():
    connection = init_db(":memory:", check_same_thread=False)
    yield connection
    connection.close()


def confirmed_payload(**overrides):
    payload = {
        "status": "confirmed",
        "asset_tag": "HAEEV-SAW-01",
        "physical_location": "Cutting bay A",
        "controller_vendor": "SCM",
        "controller_model": "Industrial PC",
        "controller_host": "10.20.0.15",
        "network_zone": "OT-VLAN-20",
        "ssh_port": 22,
        "log_folder": r"C:\SCM\Maestro\Logs",
        "telemetry_strategy": "maestro_agent",
    }
    payload.update(overrides)
    return payload


def test_snapshot_separates_research_from_site_evidence_and_pack_hashes(conn):
    state = factory_readiness.snapshot(conn, CONFIG)
    assert state["summary"]["machines"] == 15
    assert state["summary"]["passports_confirmed"] == 0
    action = next(item for item in state["machines"] if item["machine_key"] == "action_e")
    nova = next(item for item in state["machines"] if item["machine_key"] == "nova_si400")
    assert action["research"]["preferred_strategy"] == "operator_evidence"
    assert action["research"]["confidence"] == "high"
    assert nova["endpoint"] is None
    assert all(item["research"]["assumption_only"] for item in state["machines"])

    bundle, metadata = factory_readiness.field_pack(conn, CONFIG)
    assert metadata["sha256"] == hashlib.sha256(bundle).hexdigest()
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        names = set(archive.namelist())
        assert {
            "README.md", "machine-inventory.csv", "probe-plan.csv",
            "official-source-register.csv", "commissioning-plan.csv",
            "commissioning-plan.json", "manifest.json", "SHA256SUMS",
            "machines/action_e.md", "machines/sergiani_gs120.md",
        } <= names
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["format"] == factory_readiness.PACK_FORMAT
        assert manifest["production_eligible"] is False
        for entry in manifest["files"]:
            value = archive.read(entry["path"])
            assert len(value) == entry["size"]
            assert hashlib.sha256(value).hexdigest() == entry["sha256"]


def test_passport_confirmation_is_versioned_and_does_not_touch_production_truth(conn):
    factory_readiness.snapshot(conn, CONFIG)
    before = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("machine_events", "cycle_observations", "cycle_models", "industrial_contract_versions")
    }
    result = factory_readiness.update_passport(
        conn, "gabbiani_pt80", confirmed_payload(), actor="field engineer",
        expected_version=1,
    )
    assert result["status"] == "confirmed"
    assert result["version"] == 2
    assert result["confirmed_by"] == "field engineer"
    assert conn.execute("SELECT COUNT(*) FROM machine_passport_events").fetchone()[0] == 1
    with pytest.raises(ValueError, match="changed"):
        factory_readiness.update_passport(
            conn, "gabbiani_pt80", {"notes": "stale update"}, actor="stale",
            expected_version=1,
        )
    after = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in before
    }
    assert after == before


def test_confirmed_passport_requires_identity_location_strategy_and_network_host(conn):
    factory_readiness.snapshot(conn, CONFIG)
    with pytest.raises(ValueError, match="physical_location"):
        factory_readiness.update_passport(
            conn, "stefani_kd",
            {"status": "confirmed", "asset_tag": "EDGE-1", "telemetry_strategy": "maestro_agent",
             "controller_host": "10.0.0.8"},
            actor="test", expected_version=1,
        )
    with pytest.raises(ValueError, match="requires controller_host"):
        factory_readiness.update_passport(
            conn, "stefani_kd",
            {"status": "confirmed", "asset_tag": "EDGE-1", "physical_location": "Edge bay",
             "telemetry_strategy": "maestro_agent"},
            actor="test", expected_version=1,
        )
    manual = factory_readiness.update_passport(
        conn, "action_e",
        {"status": "confirmed", "asset_tag": "CLAMP-1", "physical_location": "Assembly",
         "telemetry_strategy": "operator_evidence"},
        actor="test", expected_version=1,
    )
    assert manual["controller_host"] is None


def test_inventory_csv_preview_apply_and_atomic_failure(conn):
    factory_readiness.snapshot(conn, CONFIG)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=factory_readiness.CSV_FIELDS)
    writer.writeheader()
    writer.writerow({
        "machine_key": "gabbiani_pt80", "expected_version": 1,
        **confirmed_payload(),
    })
    writer.writerow({
        "machine_key": "action_e", "expected_version": 1, "status": "confirmed",
        "asset_tag": "CLAMP-1", "physical_location": "Assembly",
        "telemetry_strategy": "operator_evidence",
    })
    preview = factory_readiness.import_inventory(
        conn, output.getvalue(), apply=False, actor="importer",
    )
    assert preview["valid"] is True
    assert preview["rows_changed"] == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM machine_passports WHERE status='confirmed'"
    ).fetchone()[0] == 0
    applied = factory_readiness.import_inventory(
        conn, output.getvalue(), apply=True, actor="importer",
    )
    assert applied["rows_applied"] == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM machine_passports WHERE status='confirmed'"
    ).fetchone()[0] == 2

    bad = io.StringIO(newline="")
    writer = csv.DictWriter(bad, fieldnames=factory_readiness.CSV_FIELDS)
    writer.writeheader()
    writer.writerow({"machine_key": "stefani_kd", "expected_version": 1,
                     "status": "inventory", "asset_tag": "EDGE-1"})
    writer.writerow({"machine_key": "action_e", "expected_version": 1,
                     "notes": "stale"})
    preview_bad = factory_readiness.import_inventory(
        conn, bad.getvalue(), apply=False, actor="importer",
    )
    assert preview_bad["valid"] is False
    with pytest.raises(ValueError, match="atomic"):
        factory_readiness.import_inventory(
            conn, bad.getvalue(), apply=True, actor="importer",
        )
    assert factory_readiness.snapshot(conn, CONFIG)["machines"][0]["passport"]["version"] >= 1
    assert factory_readiness._passport(conn, "stefani_kd")["version"] == 1


def test_csv_rejects_unknown_and_duplicate_columns(conn):
    factory_readiness.snapshot(conn, CONFIG)
    with pytest.raises(ValueError, match="Unknown CSV columns"):
        factory_readiness.import_inventory(
            conn, "machine_key,expected_version,password\naction_e,1,nope\n",
            apply=False, actor="test",
        )
    with pytest.raises(ValueError, match="duplicate columns"):
        factory_readiness.import_inventory(
            conn, "machine_key,expected_version,notes,notes\naction_e,1,a,b\n",
            apply=False, actor="test",
        )


def test_read_only_ssh_probe_records_banner_but_approves_nothing(conn):
    factory_readiness.snapshot(conn, CONFIG)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def serve():
        client, _ = listener.accept()
        with client:
            client.sendall(b"SSH-2.0-HIVE-Test\r\n")
        listener.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    preview = factory_readiness.connection_probe(
        conn, CONFIG, "gabbiani_pt80", probe_type="ssh",
        host="127.0.0.1", port=port, execute=False, timeout_s=1, actor="test",
    )
    assert preview["status"] == "preview_ready"
    assert conn.execute("SELECT COUNT(*) FROM factory_connection_probes").fetchone()[0] == 0
    result = factory_readiness.connection_probe(
        conn, CONFIG, "gabbiani_pt80", probe_type="ssh",
        host="127.0.0.1", port=port, execute=True, timeout_s=1, actor="test",
    )
    thread.join(timeout=2)
    assert result["status"] == "reachable"
    assert result["protocol_evidence"].startswith("SSH-2.0")
    assert result["will_write_device"] is False
    assert conn.execute("SELECT COUNT(*) FROM industrial_contract_versions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM remote_setup_hosts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM machine_events").fetchone()[0] == 0


def test_probe_rejects_public_targets_before_connecting(conn):
    factory_readiness.snapshot(conn, CONFIG)
    with pytest.raises(ValueError, match="private"):
        factory_readiness.connection_probe(
            conn, CONFIG, "gabbiani_pt80", probe_type="tcp",
            host="8.8.8.8", port=53, execute=True, timeout_s=.25, actor="test",
        )


def test_commissioning_mission_is_resumable_and_evidence_derived(conn, monkeypatch):
    monkeypatch.setattr(factory_readiness.remote_setup, "identity_status", lambda: {
        "status": "ready", "detail": "Test deployment identity",
    })
    monkeypatch.setattr(factory_readiness.mqtt_security, "agent_payload_status", lambda: {
        "status": "ready", "ready": True, "detail": "Verified test payload",
    })
    state = factory_readiness.snapshot(conn, CONFIG)
    action = next(item for item in state["machines"] if item["machine_key"] == "action_e")
    assert action["mission"]["status"] == "not_started"
    assert action["mission"]["offsite_ready"] is True
    assert action["mission"]["current_step"]["key"] == "passport"

    started = factory_readiness.start_mission(
        conn, CONFIG, "action_e", actor="field lead", notes="Assembly bay",
    )
    assert started["status"] == "in_progress"
    assert started["progress_percent"] < 100
    assert next(step for step in started["steps"] if step["key"] == "passport")["complete"] is False
    assert any(not step["complete"] for step in started["steps"] if step["phase"] == "factory")

    paused = factory_readiness.mission_action(
        conn, CONFIG, "action_e", action="pause", actor="field lead",
        expected_version=started["version"],
    )
    assert paused["status"] == "paused"
    with pytest.raises(ValueError, match="changed"):
        factory_readiness.mission_action(
            conn, CONFIG, "action_e", action="resume", actor="stale",
            expected_version=started["version"],
        )
    resumed = factory_readiness.mission_action(
        conn, CONFIG, "action_e", action="resume", actor="field lead",
        expected_version=paused["version"],
    )
    assert resumed["status"] == "in_progress"
    assert [event["event_type"] for event in resumed["events"][:3]] == [
        "mission_resumed", "mission_paused", "mission_started",
    ]
    factory_readiness.update_passport(
        conn, "action_e", {
            "status": "confirmed", "asset_tag": "ACTION-E-PC",
            "physical_location": "Assembly bay", "telemetry_strategy": "maestro_agent",
            "controller_host": "10.20.0.30", "network_zone": "OT-VLAN-20",
        }, actor="field lead", expected_version=1,
    )
    drifted = factory_readiness.snapshot(conn, CONFIG)
    action = next(item for item in drifted["machines"] if item["machine_key"] == "action_e")
    assert action["mission"]["strategy_drift"] is True
    assert action["mission"]["strategy"] == "operator_evidence"
    assert action["mission"]["current_strategy"] == "maestro_agent"
    assert "start a new mission" in action["mission"]["blockers"][0]


def test_agent_install_does_not_substitute_for_maestro_contract_approval(conn):
    state = factory_readiness.snapshot(conn, CONFIG)
    machine = next(item for item in state["machines"] if item["machine_key"] == "gabbiani_pt80")
    machine_id = machine["passport"]["machine_id"]
    conn.execute(
        """INSERT INTO remote_setup_runs
           (machine_id,action,mode,status,host,port,username,command_summary,exit_code,
            actor,started_at,completed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (machine_id, "install", "live", "succeeded", "10.20.0.15", 22, "hiveadmin",
         "Install agent", 0, "test", "2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00"),
    )
    conn.commit()
    installed = factory_readiness.snapshot(conn, CONFIG)
    gabbiani = next(item for item in installed["machines"] if item["machine_key"] == "gabbiani_pt80")
    assert gabbiani["agent_installed"] is True
    assert next(step for step in gabbiani["stages"] if step["key"] == "contract")["ready"] is False

    conn.execute(
        """INSERT INTO connector_mapping_versions
           (connector_key,version,status,mapping_json,source_columns_json,sample_sha256,
            coverage,approved_by,approved_at,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        ("maestro_logs", 1, "approved", json.dumps({"machine_key": "gabbiani_pt80"}),
         "[]", "a" * 64, 1.0, "test", "2026-01-01T00:02:00+00:00",
         "2026-01-01T00:02:00+00:00"),
    )
    conn.commit()
    approved = factory_readiness.snapshot(conn, CONFIG)
    gabbiani = next(item for item in approved["machines"] if item["machine_key"] == "gabbiani_pt80")
    assert gabbiani["maestro_contract_approved"] is True
    assert next(step for step in gabbiani["stages"] if step["key"] == "contract")["ready"] is True


def test_transport_evidence_is_invalidated_when_the_passport_endpoint_changes(conn):
    factory_readiness.snapshot(conn, CONFIG)
    passport = factory_readiness.update_passport(
        conn, "gabbiani_pt80", confirmed_payload(controller_host="10.20.0.15"),
        actor="field lead", expected_version=1,
    )
    conn.execute(
        """INSERT INTO factory_connection_probes
           (machine_id,probe_type,endpoint_host,endpoint_port,status,latency_ms,
            detail,evidence_sha256,actor,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (passport["machine_id"], "ssh", "10.20.0.15", 22, "reachable", 4.2,
         "SSH banner received", "b" * 64, "field lead", "2026-01-01T00:00:00+00:00"),
    )
    conn.commit()
    matching = factory_readiness.snapshot(conn, CONFIG)
    gabbiani = next(item for item in matching["machines"] if item["machine_key"] == "gabbiani_pt80")
    assert next(step for step in gabbiani["stages"] if step["key"] == "transport")["ready"] is True

    factory_readiness.update_passport(
        conn, "gabbiani_pt80", {"controller_host": "10.20.0.16"},
        actor="field lead", expected_version=passport["version"],
    )
    changed = factory_readiness.snapshot(conn, CONFIG)
    gabbiani = next(item for item in changed["machines"] if item["machine_key"] == "gabbiani_pt80")
    assert next(step for step in gabbiani["stages"] if step["key"] == "transport")["ready"] is False


def test_raw_or_demo_event_does_not_count_as_live_connection_evidence(conn):
    state = factory_readiness.snapshot(conn, CONFIG)
    gabbiani = next(item for item in state["machines"] if item["machine_key"] == "gabbiani_pt80")
    machine_id = gabbiani["passport"]["machine_id"]
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO machine_events (machine_id,event_type,ts) VALUES (?,?,?)",
        (machine_id, "cycle_end", now),
    )
    conn.commit()
    raw_only = factory_readiness.snapshot(conn, CONFIG)
    gabbiani = next(item for item in raw_only["machines"] if item["machine_key"] == "gabbiani_pt80")
    assert next(step for step in gabbiani["stages"] if step["key"] == "live")["ready"] is False

    conn.execute(
        """INSERT INTO agent_status
           (machine_id,source,last_heartbeat_at,last_event_at,last_received_at)
           VALUES (?,?,?,?,?)""",
        (machine_id, "maestro_agent", now, now, now),
    )
    conn.commit()
    agent_live = factory_readiness.snapshot(conn, CONFIG)
    gabbiani = next(item for item in agent_live["machines"] if item["machine_key"] == "gabbiani_pt80")
    assert next(step for step in gabbiani["stages"] if step["key"] == "live")["ready"] is True


def test_mission_auto_completes_only_after_all_authoritative_gates(conn, monkeypatch):
    monkeypatch.setattr(factory_readiness.remote_setup, "identity_status", lambda: {
        "status": "ready", "detail": "Test deployment identity",
    })
    monkeypatch.setattr(factory_readiness.mqtt_security, "agent_payload_status", lambda: {
        "status": "ready", "ready": True, "detail": "Verified test payload",
    })
    factory_readiness.snapshot(conn, CONFIG)
    mission = factory_readiness.start_mission(conn, CONFIG, "action_e", actor="field lead")
    assert mission["status"] == "in_progress"
    passport = factory_readiness.update_passport(
        conn, "action_e", {
            "status": "confirmed", "asset_tag": "ACTION-E-01",
            "physical_location": "Assembly bay", "telemetry_strategy": "operator_evidence",
        }, actor="field lead", expected_version=1,
    )
    assert passport["status"] == "confirmed"
    study = commissioning_evidence.create_study(conn, {
        "machine_key": "action_e", "target_samples": 5, "target_strata": 1,
        "actor": "field lead",
    })
    commissioning_evidence.add_observation(conn, study["id"], {
        "source_record_id": "action-run-1", "measured_at": datetime.now(timezone.utc).isoformat(),
        "measurement_method": "stopwatch", "observer": "field lead",
        "product_family": "cabinet", "process_s": 60, "actor": "field lead",
    })
    machine_id = passport["machine_id"]
    conn.execute(
        "INSERT INTO machine_events (machine_id,event_type,ts) VALUES (?,?,?)",
        (machine_id, "part_complete", datetime.now(timezone.utc).isoformat()),
    )
    conn.execute(
        """INSERT INTO cycle_models
           (machine_id,version,training_signature,sample_count,train_count,validation_count,
            inlier_count,coefficients_json,identified_features_json,confidence,status,trained_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (machine_id, 1, "action-e-test-model", 20, 15, 5, 18, "{}", "[]", "medium",
         "active", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    state = factory_readiness.snapshot(conn, CONFIG)
    action = next(item for item in state["machines"] if item["machine_key"] == "action_e")
    assert action["readiness_score"] == 100
    assert action["mission"]["status"] == "completed"
    assert action["mission"]["progress_percent"] == 100
    assert action["mission"]["events"][0]["event_type"] == "mission_completed"
