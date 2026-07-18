"""Factory passports, field packs, and non-invasive connection readiness."""

import csv
import hashlib
import io
import json
import socket
import sys
import threading
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import factory_readiness
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
            "official-source-register.csv", "manifest.json", "SHA256SUMS",
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
