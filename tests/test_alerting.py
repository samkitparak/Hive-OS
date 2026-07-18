"""Rationalized alerts, lifecycle, escalation, and webhook delivery."""

import hashlib
import hmac
import json
import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import alerting
import industrial_gateway
from db import init_db


NOW = datetime(2026, 7, 14, 12, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    connection = init_db(":memory:", check_same_thread=False)
    yield connection
    connection.close()


def _machine(conn):
    return conn.execute(
        "SELECT id FROM machines WHERE machine_key='morbidelli_cx100'"
    ).fetchone()["id"]


def _downtime(conn, minutes=70):
    reason = conn.execute(
        "SELECT id FROM downtime_reasons WHERE code='breakdown'"
    ).fetchone()["id"]
    cursor = conn.execute(
        """INSERT INTO downtime_events
           (machine_id,reason_id,status,started_at,notes)
           VALUES (?,?,'open',?,?)""",
        (_machine(conn), reason, (NOW - timedelta(minutes=minutes)).isoformat(), "Axis stopped"),
    )
    conn.commit()
    return cursor.lastrowid


def _alarm(conn, when, code="E42"):
    cursor = conn.execute(
        """INSERT INTO machine_events (machine_id,event_type,raw_payload,ts)
           VALUES (?,'alarm',?,?)""",
        (_machine(conn), json.dumps({"alarm_code": code, "message": "Axis overload"}), when.isoformat()),
    )
    conn.commit()
    return cursor.lastrowid


def test_sync_rationalizes_deduplicates_and_clears_downtime(conn):
    downtime_id = _downtime(conn)
    first = alerting.sync(conn, now=NOW)
    alert = first["alerts"][0]
    assert first["sync"] == {
        "candidates": 1, "created": 1, "refreshed": 0,
        "recurred": 0, "resolved": 0, "escalated": 0,
    }
    assert alert["severity"] == "critical"
    assert alert["occurrence_count"] == 1
    assert len(alert["events"]) == 1

    second = alerting.sync(conn, now=NOW + timedelta(minutes=2))
    assert second["sync"]["refreshed"] == 1
    assert second["alerts"][0]["occurrence_count"] == 1
    assert len(second["alerts"][0]["events"]) == 1

    conn.execute(
        "UPDATE downtime_events SET status='closed',ended_at=? WHERE id=?",
        ((NOW + timedelta(minutes=3)).isoformat(), downtime_id),
    )
    conn.commit()
    cleared = alerting.sync(conn, now=NOW + timedelta(minutes=4))
    assert cleared["sync"]["resolved"] == 1
    assert cleared["alerts"][0]["status"] == "resolved"
    assert cleared["alerts"][0]["events"][0]["event_type"] == "source_cleared"


def test_new_alarm_evidence_reopens_an_acknowledged_alert(conn):
    _alarm(conn, NOW - timedelta(minutes=1))
    alert = alerting.sync(conn, now=NOW)["alerts"][0]
    acknowledged = alerting.act(conn, alert["id"], {
        "action": "acknowledge", "actor": "Maintenance Lead",
        "owner": "CNC technician", "expected_version": alert["version"],
    }, now=NOW)
    assert acknowledged["status"] == "acknowledged"

    unchanged = alerting.sync(conn, now=NOW + timedelta(minutes=1))["alerts"][0]
    assert unchanged["status"] == "acknowledged"
    assert unchanged["occurrence_count"] == 1

    _alarm(conn, NOW + timedelta(minutes=2))
    recurred = alerting.sync(conn, now=NOW + timedelta(minutes=3))["alerts"][0]
    assert recurred["status"] == "open"
    assert recurred["occurrence_count"] == 2
    assert recurred["events"][0]["event_type"] == "recurred"
    assert recurred["acknowledged_by"] is None


def test_resolved_event_alarm_waits_for_new_evidence_before_reopening(conn):
    _alarm(conn, NOW - timedelta(minutes=1))
    alert = alerting.sync(conn, now=NOW)["alerts"][0]
    resolved = alerting.act(conn, alert["id"], {
        "action": "resolve", "actor": "Maintenance Lead", "notes": "Alarm reset at controller",
    }, now=NOW)
    unchanged = alerting.sync(conn, now=NOW + timedelta(minutes=1))["alerts"][0]
    assert unchanged["status"] == "resolved"
    assert unchanged["occurrence_count"] == 1

    _alarm(conn, NOW + timedelta(minutes=2))
    reopened = alerting.sync(conn, now=NOW + timedelta(minutes=3))["alerts"][0]
    assert reopened["status"] == "open"
    assert reopened["occurrence_count"] == 2
    assert reopened["events"][0]["event_type"] == "reopened"


def test_snooze_expiry_and_deadline_escalation_are_audited_once(conn):
    _downtime(conn, minutes=20)
    alert = alerting.sync(conn, now=NOW)["alerts"][0]
    snoozed = alerting.act(conn, alert["id"], {
        "action": "snooze", "actor": "Shift Lead", "notes": "Technician is walking over",
        "snooze_minutes": 10, "expected_version": alert["version"],
    }, now=NOW)
    assert snoozed["status"] == "snoozed"

    still_snoozed = alerting.sync(conn, now=NOW + timedelta(minutes=5))["alerts"][0]
    assert still_snoozed["status"] == "snoozed"
    reopened = alerting.sync(conn, now=NOW + timedelta(minutes=11))["alerts"][0]
    assert reopened["status"] == "open"
    assert reopened["events"][0]["event_type"] == "snooze_expired"

    overdue = alerting.sync(conn, now=NOW + timedelta(minutes=27))["alerts"][0]
    assert overdue["escalation_level"] == 1
    assert overdue["events"][0]["event_type"] == "response_overdue"
    unchanged = alerting.sync(conn, now=NOW + timedelta(minutes=28))["alerts"][0]
    assert unchanged["escalation_level"] == 1
    assert sum(event["event_type"] == "response_overdue" for event in unchanged["events"]) == 1


def test_actions_require_named_actors_notes_and_current_versions(conn):
    _downtime(conn)
    alert = alerting.sync(conn, now=NOW)["alerts"][0]
    with pytest.raises(ValueError, match="named operator"):
        alerting.act(conn, alert["id"], {
            "action": "acknowledge", "actor": "operator",
        }, now=NOW)
    with pytest.raises(ValueError, match="disposition note"):
        alerting.act(conn, alert["id"], {
            "action": "resolve", "actor": "Shift Lead",
        }, now=NOW)
    resolved = alerting.act(conn, alert["id"], {
        "action": "resolve", "actor": "Shift Lead", "notes": "Machine restored",
        "expected_version": alert["version"],
    }, now=NOW)
    assert resolved["status"] == "resolved"
    with pytest.raises(ValueError, match="changed"):
        alerting.act(conn, alert["id"], {
            "action": "reopen", "actor": "Shift Lead", "expected_version": alert["version"],
        }, now=NOW)
    reopened = alerting.act(conn, alert["id"], {
        "action": "reopen", "actor": "Shift Lead", "expected_version": resolved["version"],
    }, now=NOW)
    assert reopened["status"] == "open"


def test_quality_and_integration_conditions_are_rationalized(conn):
    machine = _machine(conn)
    defect = conn.execute("SELECT id FROM defect_types WHERE code='drilling'").fetchone()["id"]
    for index in range(3):
        conn.execute(
            """INSERT INTO quality_checks (machine_id,defect_type_id,result,ts)
               VALUES (?,?,'fail',?)""",
            (machine, defect, (NOW - timedelta(minutes=index)).isoformat()),
        )
    industrial_gateway.sync_defaults(conn)
    profile_key = conn.execute(
        "SELECT profile_key FROM industrial_profiles ORDER BY profile_key LIMIT 1"
    ).fetchone()["profile_key"]
    conn.execute(
        """UPDATE industrial_profiles SET enabled=1,verified=1,last_error='Connection timeout',
           last_poll_at=?,updated_at=? WHERE profile_key=?""",
        (NOW.isoformat(), NOW.isoformat(), profile_key),
    )
    conn.commit()
    candidates = alerting.collect_candidates(conn, NOW)
    rules = {item["rule_key"] for item in candidates}
    assert "quality_recurrence" in rules
    assert "industrial_profile_failed" in rules
    assert next(item for item in candidates if item["rule_key"] == "industrial_profile_failed")["severity"] == "critical"


def test_constraint_worker_alert_requires_repeated_failures(conn):
    conn.execute(
        """UPDATE constraint_runtime_settings SET last_run_at=?,last_success_at=?,
             consecutive_failures=2,last_error='database busy' WHERE id=1""",
        (NOW.isoformat(), (NOW - timedelta(minutes=5)).isoformat()),
    )
    conn.commit()
    assert not any(item["rule_key"] == "constraint_worker_failed"
                   for item in alerting.collect_candidates(conn, NOW))
    conn.execute(
        "UPDATE constraint_runtime_settings SET consecutive_failures=3 WHERE id=1"
    )
    conn.commit()
    candidate = next(item for item in alerting.collect_candidates(conn, NOW)
                     if item["rule_key"] == "constraint_worker_failed")
    assert candidate["severity"] == "critical"
    assert candidate["owner_role"] == "site_engineer"


def test_decision_ready_forecast_creates_delivery_risk_candidate(conn, monkeypatch):
    monkeypatch.setattr(alerting.forecasting, "snapshot", lambda _conn: {
        "decision_ready": True,
        "calibration": {"status": "collecting"},
        "latest": {
            "id": 42, "generated_at": NOW.isoformat(),
            "result": {"policy": "current", "jobs": [{
                "production_order_id": 7, "job_name": "RISK-7",
                "late_probability": 0.82,
                "completion_at": {"p80": (NOW + timedelta(hours=3)).isoformat()},
            }]},
        },
    })
    candidate = next(item for item in alerting.collect_candidates(conn, NOW)
                     if item["rule_key"] == "forecast_delivery_risk")
    assert candidate["severity"] == "critical"
    assert candidate["owner_role"] == "production_planner"
    assert candidate["evidence"]["forecast_id"] == 42


class _CaptureHandler(BaseHTTPRequestHandler):
    captures = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.__class__.captures.append({"headers": dict(self.headers), "body": body})
        self.send_response(202)
        self.end_headers()
        self.wfile.write(b"accepted")

    def log_message(self, *_args):
        return


def test_live_webhook_commissioning_signs_and_dispatches_current_alerts(conn):
    _CaptureHandler.captures = []
    server = HTTPServer(("127.0.0.1", 0), _CaptureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    os.environ["HIVE_TEST_ALERT_SECRET"] = "test-secret"
    try:
        _downtime(conn)
        alerting.sync(conn, now=NOW)
        endpoint = f"http://127.0.0.1:{server.server_port}/alerts"
        destination = alerting.upsert_destination(conn, "shift_webhook", {
            "name": "Shift webhook", "endpoint": endpoint,
            "secret_env": "HIVE_TEST_ALERT_SECRET", "min_severity": "warning",
            "enabled": False, "actor": "Site Engineer",
        }, now=NOW)
        simulation = alerting.test_destination(conn, "shift_webhook", {
            "live": False, "actor": "Site Engineer",
        }, now=NOW)
        assert simulation["mode"] == "simulation"
        assert not _CaptureHandler.captures

        live = alerting.test_destination(conn, "shift_webhook", {
            "live": True, "actor": "Site Engineer",
        }, now=NOW)
        assert live["verified"] is True
        capture = _CaptureHandler.captures[0]
        expected = hmac.new(b"test-secret", capture["body"], hashlib.sha256).hexdigest()
        headers = {key.lower(): value for key, value in capture["headers"].items()}
        assert headers["x-hive-signature"] == f"sha256={expected}"
        assert json.loads(capture["body"])["specversion"] == "1.0"

        verified = live["destination"]
        alerting.upsert_destination(conn, "shift_webhook", {
            "name": "Shift webhook", "endpoint": endpoint,
            "secret_env": "HIVE_TEST_ALERT_SECRET", "min_severity": "warning",
            "enabled": True, "expected_version": verified["version"], "actor": "Site Engineer",
        }, now=NOW)
        result = alerting.dispatch(conn, actor="test", now=NOW)
        assert result == {"selected": 1, "delivered": 1, "failed": 0, "remaining": 0}
        delivered = json.loads(_CaptureHandler.captures[1]["body"])
        assert delivered["type"] == "com.hiveos.alert.current_state"
        assert delivered["data"]["severity"] == "critical"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        os.environ.pop("HIVE_TEST_ALERT_SECRET", None)


def test_failed_delivery_uses_bounded_retry_and_settings_require_readiness(conn, monkeypatch):
    _downtime(conn)
    alerting.sync(conn, now=NOW)
    destination = alerting.upsert_destination(conn, "failed_webhook", {
        "name": "Failed webhook", "endpoint": "http://127.0.0.1:9999/alerts",
        "min_severity": "warning", "enabled": False, "actor": "Site Engineer",
    }, now=NOW)
    with pytest.raises(ValueError, match="enabled, live-verified"):
        alerting.update_settings(conn, {
            "auto_sync": True, "auto_dispatch": True, "interval_seconds": 60,
            "actor": "Site Engineer",
        }, now=NOW)

    monkeypatch.setattr(alerting, "_post_webhook", lambda *_args, **_kwargs: (204, ""))
    live = alerting.test_destination(conn, "failed_webhook", {
        "live": True, "actor": "Site Engineer",
    }, now=NOW)
    alerting.upsert_destination(conn, "failed_webhook", {
        "name": "Failed webhook", "endpoint": destination["endpoint"],
        "min_severity": "warning", "enabled": True,
        "expected_version": live["destination"]["version"], "actor": "Site Engineer",
    }, now=NOW)
    settings = alerting.update_settings(conn, {
        "auto_sync": True, "auto_dispatch": True, "interval_seconds": 30,
        "actor": "Site Engineer",
    }, now=NOW)
    assert settings["auto_sync"] == 1 and settings["auto_dispatch"] == 1

    def fail(*_args, **_kwargs):
        raise RuntimeError("temporary outage")

    monkeypatch.setattr(alerting, "_post_webhook", fail)
    result = alerting.dispatch(conn, actor="test", now=NOW)
    assert result["failed"] == 1
    delivery = conn.execute("SELECT * FROM alert_deliveries").fetchone()
    assert delivery["status"] == "failed"
    assert delivery["attempts"] == 1
    assert delivery["next_attempt_at"] == (NOW + timedelta(seconds=60)).isoformat()


def test_alert_api_reads_without_writes_then_syncs_and_acknowledges(conn):
    _downtime(conn)
    import main

    main.set_conn(conn)
    with TestClient(main.app) as client:
        main.set_conn(conn)
        before = conn.execute("SELECT COUNT(*) FROM alert_instances").fetchone()[0]
        empty = client.get("/api/alerts")
        after = conn.execute("SELECT COUNT(*) FROM alert_instances").fetchone()[0]
        assert empty.status_code == 200 and before == after == 0
        assert client.post("/api/alerts/sync", json={}).status_code == 422
        assert client.post("/api/alerts/sync", json={"actor": "operator"}).status_code == 400
        assert client.post("/api/alerts/deliveries/dispatch", json={}).status_code == 422
        synced = client.post("/api/alerts/sync", json={"actor": "API test"})
        assert synced.status_code == 200
        alert = synced.json()["alerts"][0]
        acknowledged = client.post(f"/api/alerts/{alert['id']}/action", json={
            "action": "acknowledge", "actor": "Test Shift Lead",
            "expected_version": alert["version"],
        })
        assert acknowledged.status_code == 200
        assert acknowledged.json()["status"] == "acknowledged"
        assert client.get(f"/api/alerts/{alert['id']}").status_code == 200
