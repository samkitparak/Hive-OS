"""Local identity, session, role, CSRF, and integration-credential security."""

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import access_control
from db import init_db


NOW = datetime(2026, 7, 14, 12, tzinfo=timezone.utc)
ADMIN_PASSWORD = "Correct horse battery staple 2026"
OPERATOR_PASSWORD = "Violet turbine window canvas 2026"


@pytest.fixture
def conn():
    connection = init_db(":memory:", check_same_thread=False)
    yield connection
    connection.close()


def _bootstrap_direct(conn, now=NOW):
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        token_path = Path(directory) / "bootstrap.token"
        token_path.write_text("test-bootstrap-token-with-entropy", encoding="ascii")
        return access_control.bootstrap(conn, {
            "bootstrap_token": "test-bootstrap-token-with-entropy",
            "username": "sam.admin", "display_name": "Sam Admin",
            "password": ADMIN_PASSWORD,
        }, now=now, token_path=token_path)


def test_passwords_sessions_lockout_and_revocation(conn):
    session = _bootstrap_direct(conn)
    assert session["user"]["role"] == "admin"
    stored = conn.execute("SELECT password_hash FROM auth_users").fetchone()[0]
    assert stored.startswith("$argon2id$") and ADMIN_PASSWORD not in stored
    stored_session = conn.execute("SELECT token_hash,csrf_token FROM auth_sessions").fetchone()
    assert session["token"] not in stored_session["token_hash"]
    assert stored_session["csrf_token"] == session["csrf_token"]

    for _ in range(5):
        with pytest.raises(ValueError, match="Invalid username or password"):
            access_control.login(conn, {"username": "sam.admin", "password": "wrong"}, now=NOW)
    user = conn.execute("SELECT failed_logins,locked_until FROM auth_users").fetchone()
    assert user["failed_logins"] == 5
    assert datetime.fromisoformat(user["locked_until"]) == NOW + timedelta(minutes=15)
    with pytest.raises(ValueError, match="Invalid username or password"):
        access_control.login(conn, {"username": "sam.admin", "password": ADMIN_PASSWORD}, now=NOW)
    recovered = access_control.login(
        conn, {"username": "sam.admin", "password": ADMIN_PASSWORD}, now=NOW + timedelta(minutes=16)
    )
    assert access_control.authenticate(conn, session_token=recovered["token"], now=NOW + timedelta(minutes=16))
    assert access_control.authenticate(conn, session_token=recovered["token"], now=NOW + timedelta(hours=13)) is None


def test_role_and_api_key_lifecycle(conn):
    session = _bootstrap_direct(conn)
    admin = access_control.authenticate(conn, session_token=session["token"], now=NOW)
    operator = access_control.create_user(conn, {
        "username": "floor.operator", "display_name": "Floor Operator", "role": "operator",
        "password": OPERATOR_PASSWORD,
    }, admin, now=NOW)
    assert operator["permissions"] == ["alerts", "operate", "view"]
    with pytest.raises(ValueError, match="own active admin"):
        access_control.update_user(conn, admin["id"], {"role": "viewer"}, admin, now=NOW)

    created = access_control.create_api_key(conn, {
        "name": "Maestro machine enrollment", "permissions": ["integration"],
    }, admin, now=NOW)
    with pytest.raises(ValueError, match="only use the integration permission"):
        access_control.create_api_key(conn, {
            "name": "Overpowered service key", "permissions": ["admin"],
        }, admin, now=NOW)
    assert created["token"].startswith("hive_")
    listed = access_control.list_api_keys(conn)[0]
    assert "token" not in listed and listed["key_prefix"] == created["key_prefix"]
    service = access_control.authenticate(conn, bearer_token=created["token"], now=NOW)
    assert service["permissions"] == {"integration"}
    access_control.revoke_api_key(conn, created["id"], admin, now=NOW)
    assert access_control.authenticate(conn, bearer_token=created["token"], now=NOW) is None


def test_virtual_lab_requires_commission_or_optimize_permission():
    assert access_control.required_permissions("GET", "/commissioning-lab") == ("view",)
    assert access_control.required_permissions("POST", "/commissioning-lab/run") == (
        "commission", "optimize",
    )
    assert access_control.required_permissions("GET", "/commissioning-evidence") == ("view",)
    assert access_control.required_permissions("POST", "/commissioning-evidence/studies") == (
        "commission", "optimize",
    )
    assert access_control.required_permissions("GET", "/factory-readiness") == ("view",)
    assert access_control.required_permissions("GET", "/factory-readiness/pack") == ("view",)
    assert access_control.required_permissions(
        "PUT", "/factory-readiness/machines/action_e"
    ) == ("commission",)
    assert access_control.required_permissions(
        "POST", "/factory-readiness/machines/action_e/probe"
    ) == ("commission",)
    assert access_control.required_permissions(
        "POST", "/factory-readiness/machines/action_e/mission"
    ) == ("commission",)
    assert access_control.required_permissions(
        "POST", "/factory-readiness/machines/action_e/mission/action"
    ) == ("commission",)
    assert access_control.required_permissions("GET", "/bottlenecks") == ("view",)
    assert access_control.required_permissions("POST", "/constraints/sync") == (
        "optimize", "supervise",
    )
    assert access_control.required_permissions("PUT", "/constraints/settings") == (
        "optimize", "supervise",
    )
    assert access_control.required_permissions("GET", "/flow-intelligence") == ("view",)
    assert access_control.required_permissions("POST", "/flow-intelligence/sync") == (
        "optimize", "supervise",
    )
    assert access_control.required_permissions("GET", "/changeovers") == ("view",)
    assert access_control.required_permissions(
        "PUT", "/changeovers/machines/gabbiani_pt80/standard"
    ) == ("plan", "optimize", "commission")
    assert access_control.required_permissions(
        "POST", "/changeovers/observations"
    ) == ("plan", "optimize", "commission")


def test_http_setup_session_csrf_roles_actor_binding_and_service_scope(conn, monkeypatch):
    monkeypatch.setenv("HIVE_AUTH_MODE", "required")
    monkeypatch.setenv("HIVE_BOOTSTRAP_TOKEN", "installer-bootstrap-token-with-entropy")
    monkeypatch.setenv("HIVE_ALLOW_INSECURE_AUTH", "1")
    import main

    main.set_conn(conn)
    with TestClient(main.app, raise_server_exceptions=True) as admin_client:
        main.set_conn(conn)
        assert admin_client.get("/api/health").status_code == 200
        assert admin_client.get("/api/machines").status_code == 428
        status = admin_client.get("/api/auth/status").json()
        assert status["setup_required"] is True
        bad = admin_client.post("/api/auth/bootstrap", json={
            "bootstrap_token": "wrong-bootstrap-token-value", "username": "sam.admin",
            "display_name": "Sam Admin", "password": ADMIN_PASSWORD,
        })
        assert bad.status_code == 400
        bootstrap = admin_client.post("/api/auth/bootstrap", json={
            "bootstrap_token": "installer-bootstrap-token-with-entropy", "username": "sam.admin",
            "display_name": "Sam Admin", "password": ADMIN_PASSWORD,
        })
        assert bootstrap.status_code == 200
        assert "hive_session=" in bootstrap.headers["set-cookie"]
        assert "HttpOnly" in bootstrap.headers["set-cookie"] and "SameSite=strict" in bootstrap.headers["set-cookie"]
        assert "token" not in bootstrap.json()
        csrf = bootstrap.json()["csrf_token"]
        assert admin_client.get("/api/machines").status_code == 200
        assert admin_client.post("/api/alerts/sync", json={"actor": "Spoofed Name"}).status_code == 403
        assert admin_client.post(
            "/api/alerts/sync", headers={"X-CSRF-Token": "wrong-token"},
            json={"actor": "Spoofed Name"},
        ).status_code == 403
        machine_id = conn.execute(
            "SELECT id FROM machines WHERE machine_key='morbidelli_cx100'"
        ).fetchone()["id"]
        reason_id = conn.execute(
            "SELECT id FROM downtime_reasons WHERE code='breakdown'"
        ).fetchone()["id"]
        conn.execute(
            """INSERT INTO downtime_events
               (machine_id,reason_id,status,started_at,notes) VALUES (?,?,'open',?,?)""",
            (machine_id, reason_id,
             (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(), "Security test"),
        )
        conn.commit()
        synced = admin_client.post("/api/alerts/sync", headers={"X-CSRF-Token": csrf},
                                   json={"actor": "Spoofed Name"})
        assert synced.status_code == 200
        assert conn.execute(
            "SELECT actor FROM alert_events WHERE event_type='opened' ORDER BY id DESC LIMIT 1"
        ).fetchone()["actor"] == "Sam Admin"
        created_event = conn.execute(
            "SELECT actor_name FROM auth_events WHERE event_type='api_request' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert created_event["actor_name"] == "Sam Admin"

        user = admin_client.post("/api/auth/users", headers={"X-CSRF-Token": csrf}, json={
            "username": "floor.operator", "display_name": "Floor Operator", "role": "operator",
            "password": OPERATOR_PASSWORD,
        })
        assert user.status_code == 200
        key_response = admin_client.post("/api/auth/api-keys", headers={"X-CSRF-Token": csrf}, json={
            "name": "Maestro enrollment", "permissions": ["integration"],
        })
        assert key_response.status_code == 200
        service_token = key_response.json()["token"]

        admin_client.cookies.clear()
        login = admin_client.post("/api/auth/login", json={
            "username": "floor.operator", "password": OPERATOR_PASSWORD,
        })
        assert login.status_code == 200
        operator_csrf = login.json()["csrf_token"]
        assert admin_client.get("/api/machines").status_code == 200
        forbidden = admin_client.post("/api/planning/scenarios", headers={"X-CSRF-Token": operator_csrf}, json={})
        assert forbidden.status_code == 403
        assert admin_client.post("/api/recovery/analyze", headers={"X-CSRF-Token": operator_csrf}, json={}).status_code == 403
        allowed = admin_client.post("/api/downtime", headers={"X-CSRF-Token": operator_csrf}, json={
            "machine_key": "morbidelli_cx100", "reason_code": "breakdown", "notes": "Test stop",
        })
        assert allowed.status_code == 200

        admin_client.cookies.clear()
        headers = {"Authorization": f"Bearer {service_token}"}
        sample = admin_client.post("/api/commissioning/log/analyze", headers=headers, json={
            "machine_key": "morbidelli_cx100", "log_text": "2026-07-14 cycle start",
            "persist": False, "site_timezone": "Asia/Kolkata",
        })
        assert sample.status_code == 200
        persistent_sample = admin_client.post("/api/commissioning/log/analyze", headers=headers, json={
            "machine_key": "morbidelli_cx100", "log_text": "2026-07-14 cycle start",
            "persist": True, "site_timezone": "Asia/Kolkata",
        })
        assert persistent_sample.status_code == 403
        assert admin_client.get("/api/machines", headers=headers).status_code == 403
        assert admin_client.post("/api/alerts/sync", headers=headers, json={"actor": "machine"}).status_code == 403
        assert admin_client.post("/api/execution/sync", headers=headers).status_code == 403
        assert admin_client.post("/api/connectors/maestro_logs/approve", headers=headers, json={}).status_code == 403

        admin_login = admin_client.post("/api/auth/login", json={
            "username": "sam.admin", "password": ADMIN_PASSWORD,
        })
        assert admin_login.status_code == 200
        events = admin_client.get("/api/auth/events").json()["events"]
        assert any(event["event_type"] == "api_request" and event["actor_name"] == "Floor Operator" for event in events)
        assert any(
            event["event_type"] == "api_request" and event["target_key"] == "POST /planning/scenarios"
            and not event["success"] and event["details"]["status_code"] == 403
            for event in events
        )
        assert admin_client.post("/api/auth/bootstrap", json={
            "bootstrap_token": "installer-bootstrap-token-with-entropy", "username": "other.admin",
            "display_name": "Other Admin", "password": "Another correct battery staple 2026",
        }).status_code == 400


def test_remote_plain_http_rejects_credentials(conn, monkeypatch):
    session = _bootstrap_direct(conn)
    monkeypatch.setenv("HIVE_AUTH_MODE", "required")
    monkeypatch.setenv("HIVE_BOOTSTRAP_TOKEN", "installer-bootstrap-token-with-entropy")
    monkeypatch.delenv("HIVE_ALLOW_INSECURE_AUTH", raising=False)
    admin = access_control.authenticate(conn, session_token=session["token"], now=NOW)
    service = access_control.create_api_key(conn, {
        "name": "Remote transport test", "permissions": ["integration"],
    }, admin, now=NOW)
    import main
    main.set_conn(conn)
    sent = []

    async def downstream(scope, receive, send):
        raise AssertionError("Insecure credential request reached the application")

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": "/api/commissioning/log/analyze",
        "raw_path": b"/api/commissioning/log/analyze", "query_string": b"",
        "server": ("192.168.10.20", 80), "client": ("192.168.10.21", 50000),
        "headers": [
            (b"host", b"192.168.10.20"),
            (b"authorization", f"Bearer {service['token']}".encode("ascii")),
        ],
    }
    asyncio.run(main.AccessControlMiddleware(downstream)(scope, receive, send))
    assert sent[0]["status"] == 400
    assert json.loads(sent[1]["body"])["detail"] == "Credentials require HTTPS or central PC localhost"
    assert main.AccessControlMiddleware._transport_acceptable(
        {"scheme": "http", "client": ("192.168.10.21", 50000)}, {"host": "localhost"}
    ) is False


def test_remote_install_and_host_trust_require_administrator():
    assert access_control.required_permissions("POST", "/remote-setup/install-agent") == ("commission",)
    assert access_control.required_permissions("POST", "/remote-setup/install-agent/live") == ("admin",)
    assert access_control.required_permissions("POST", "/remote-setup/trust-host") == ("admin",)
    assert access_control.required_permissions("POST", "/remote-setup/commission-agent/live") == ("admin",)
    assert access_control.required_permissions("POST", "/remote-setup/commission-agent/8/verify") == ("admin",)
    assert access_control.required_permissions("DELETE", "/remote-setup/trust-host/morbidelli_cx100") == ("admin",)


def test_failed_mutation_rolls_back_before_audit(conn, monkeypatch):
    session = _bootstrap_direct(conn, now=datetime.now(timezone.utc))
    monkeypatch.setenv("HIVE_AUTH_MODE", "required")
    monkeypatch.setenv("HIVE_ALLOW_INSECURE_AUTH", "1")
    import main
    main.set_conn(conn)
    machine_id = conn.execute(
        "SELECT id FROM machines WHERE machine_key='morbidelli_cx100'"
    ).fetchone()["id"]

    async def downstream(scope, receive, send):
        conn.execute(
            "INSERT INTO downtime_events (machine_id,status,started_at,notes) VALUES (?,'open',?,?)",
            (machine_id, NOW.isoformat(), "must roll back"),
        )
        raise RuntimeError("simulated route failure")

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message):
        pass

    scope = {
        "type": "http", "http_version": "1.1", "method": "POST", "scheme": "http",
        "path": "/api/downtime", "raw_path": b"/api/downtime", "query_string": b"",
        "server": ("127.0.0.1", 80), "client": ("127.0.0.1", 50000),
        "headers": [
            (b"host", b"127.0.0.1"), (b"content-type", b"application/json"),
            (b"cookie", f"hive_session={session['token']}".encode("ascii")),
            (b"x-csrf-token", session["csrf_token"].encode("ascii")),
        ],
    }
    with pytest.raises(RuntimeError, match="simulated route failure"):
        asyncio.run(main.AccessControlMiddleware(downstream)(scope, receive, send))
    assert conn.execute("SELECT COUNT(*) FROM downtime_events WHERE notes='must roll back'").fetchone()[0] == 0
    audit = conn.execute(
        "SELECT success,details_json FROM auth_events WHERE event_type='api_request' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert audit["success"] == 0
    assert json.loads(audit["details_json"])["status_code"] == 500
