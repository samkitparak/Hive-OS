"""
HIVE OS — FastAPI backend

Endpoints:
  GET  /machines              — all machines + current state
  GET  /machines/{key}        — one machine detail + latest OEE
  GET  /jobs                  — all jobs (most recent first)
  GET  /jobs/{job_name}/parts — parts for a job, with current machine assignment
  GET  /oee                   — OEE for all active machines (last shift)
  GET  /oee/{machine_key}     — OEE for one machine
  GET  /events/stream         — SSE stream of live machine events
  POST /events/simulate       — inject a fake event (dev/demo only)

Run:
  uvicorn src.main:app --reload --port 8000
"""

import asyncio
import hmac
import json
import logging
import os
import sqlite3
import threading
import zipfile
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

import db as db_module
import mqtt_bridge
import mqtt_security as mqtt_security_module
import cv_watcher
import oee as oee_module
import progress as progress_module
import score as score_module
import shift_report as shift_report_module
import cycle_time as cycle_time_module
import sequencer as sequencer_module
import bottleneck as bottleneck_module
import data_quality as data_quality_module
import digital_twin as digital_twin_module
import forecasting as forecasting_module
import recovery as recovery_module
import learning as learning_module
import routing as routing_module
import commissioning as commissioning_module
import commissioning_lab as commissioning_lab_module
import commissioning_evidence as commissioning_evidence_module
import factory_readiness as factory_readiness_module
import event_pipeline
import optimization as optimization_module
import improvement as improvement_module
import root_cause as root_cause_module
import alerting as alerting_module
import access_control as access_control_module
import planning as planning_module
import production_control as production_control_module
import resources as resources_module
import inventory as inventory_module
import procurement as procurement_module
import execution as execution_module
import identity as identity_module
import diagnostics as diagnostics_module
import deployment as deployment_module
import config_editor as config_editor_module
import remote_setup as remote_setup_module
import resilience as resilience_module
import operations as operations_module
import maintenance as maintenance_module
import tooling as tooling_module
import connectors as connectors_module
import industrial_gateway as industrial_gateway_module
import energy_intelligence as energy_intelligence_module
import changeovers as changeovers_module
import ottimo_connector
import cv_sql_connector
from api_models import (
    BarcodeEventCreate,
    CloseRequest,
    CommissioningLogRequest,
    VirtualLabRunRequest,
    CommissioningCsvImport,
    CommissioningObservationCreate,
    CommissioningObservationExclude,
    CommissioningStudyAction,
    CommissioningStudyCreate,
    FactoryConnectionProbe,
    FactoryInventoryImport,
    FactoryMissionAction,
    FactoryMissionStart,
    MachinePassportUpdate,
    ChangeoverObservationCreate,
    ChangeoverObservationExclude,
    ChangeoverStandardUpdate,
    ChangeoverSyncRequest,
    ConnectorAnalyzeRequest,
    ConnectorApprovalRequest,
    ConnectorImportRequest,
    ConnectorProfileUpdate,
    ConnectorSyncRequest,
    IndustrialApprovalRequest,
    IndustrialMqttProbeRequest,
    IndustrialProbeRequest,
    IndustrialProfileUpdate,
    ImprovementAction,
    ImprovementSyncRequest,
    ConstraintSyncRequest,
    ConstraintSettingsUpdate,
    RootCauseDecision,
    RootCauseSyncRequest,
    AlertAction,
    AlertDestinationTest,
    AlertDestinationUpsert,
    AlertDispatchRequest,
    AlertSettingsUpdate,
    AlertSyncRequest,
    AuthApiKeyCreate,
    AuthBootstrap,
    AuthLogin,
    AuthPasswordChange,
    AuthPasswordReset,
    AuthUserCreate,
    AuthUserUpdate,
    MqttEnrollmentCreate,
    MqttEnrollmentRevoke,
    InventoryItemUpdate,
    InventoryLotBalanceUpdate,
    InventoryRequirementUpdate,
    GoodsReceiptCreate,
    ProcurementCsvImport,
    ProcurementDraftRequest,
    ProcurementMappingUpdate,
    ProcurementOutboxAck,
    ProcurementSupplierUpdate,
    PurchaseOrderAction,
    PurchaseOrderCreate,
    DigitalTwinRequest,
    ForecastRefreshRequest,
    ExecutionActionRequest,
    ExecutionExceptionDecision,
    IdentityMaterializeRequest,
    LabelJobCreate,
    LabelPrintConfirmation,
    FactoryCalendarUpdate,
    LaborRoleUpdate,
    MachineResourceProfileUpdate,
    MaintenanceCompletion,
    MaintenanceConditionCreate,
    MaintenancePlanCreate,
    MaintenancePlanUpdate,
    MaintenanceWorkOrderUpdate,
    MaterialStockUpdate,
    PartRouteUpdate,
    PlanningDecision,
    PlanningScenarioCreate,
    RecoveryAnalyzeRequest,
    RecoveryDecision,
    ProductionOrderUpdate,
    CvSqlRow,
    DowntimeCreate,
    OttimoPlaceholder,
    QualityCheckCreate,
    RemoteConnectionRequest,
    RemoteMachineRequest,
    RemoteTrustRequest,
    RemnantCreate,
    RemnantUpdate,
    ResourceUnavailabilityCreate,
    RouteExceptionDecision,
    SiteConfigUpdate,
    SparePartCreate,
    SpareStockUpdate,
    ToolPoolUpdate,
    ToolActionCreate,
    ToolAssetCreate,
    ToolAssetUpdate,
    ToolProgramMappingCreate,
    ToolServiceCreate,
    ToolUsageCreate,
    UnitAliasCreate,
    WipBufferUpdate,
    WorkOrderCreate,
)
from db import DB_PATH, init_db

log = logging.getLogger("main")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s")

CONFIG_PATH = Path(__file__).parent.parent / "config" / "machines.yaml"
DASHBOARD_DIST = Path(__file__).parent.parent / "dashboard" / "dist"
APP_VERSION = "0.29.0"


class ApiPrefixMiddleware:
    """Expose every backend route under /api while preserving legacy paths."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path", "").startswith("/api/"):
            scope = dict(scope)
            scope["path"] = scope["path"][4:]
            scope["raw_path"] = scope["path"].encode("utf-8")
        await self.app(scope, receive, send)


class AccessControlMiddleware:
    """Authenticate API traffic, authorize permissions, and bind audit actors."""

    def __init__(self, app):
        self.app = app

    @staticmethod
    def _path(scope) -> str:
        path = scope.get("path", "")
        return path[4:] if path.startswith("/api/") else path

    @staticmethod
    def _headers(scope) -> dict[str, str]:
        return {key.decode("latin1").lower(): value.decode("latin1")
                for key, value in scope.get("headers", [])}

    @staticmethod
    def _static(path: str) -> bool:
        return path == "/" or path.startswith("/assets/") or path in {"/favicon.ico"}

    @staticmethod
    def _transport_acceptable(scope, headers: dict[str, str]) -> bool:
        if scope.get("scheme") == "https" or os.getenv("HIVE_ALLOW_INSECURE_AUTH") == "1":
            return True
        client = (scope.get("client") or (None,))[0]
        return client in {"127.0.0.1", "::1"}

    @staticmethod
    async def _json(scope, receive, send, status_code: int, detail: str) -> None:
        await JSONResponse({"detail": detail}, status_code=status_code)(scope, receive, send)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not access_control_module.auth_required():
            await self.app(scope, receive, send)
            return
        path = self._path(scope)
        method = scope.get("method", "GET").upper()
        mutation = method in {"POST", "PUT", "PATCH", "DELETE"}
        if self._static(path) or method == "OPTIONS":
            await self.app(scope, receive, send)
            return
        headers = self._headers(scope)
        if path in access_control_module.PUBLIC_PATHS:
            scope.setdefault("state", {})["transport_acceptable"] = self._transport_acceptable(scope, headers)
            await self.app(scope, receive, send)
            return
        conn = _get_conn()
        if access_control_module.setup_required(conn):
            await self._json(scope, receive, send, 428, "Create the first administrator before using HIVE OS")
            return
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get("cookie", ""))
        except Exception:
            cookie = SimpleCookie()
        session_token = cookie.get(access_control_module.SESSION_COOKIE)
        authorization = headers.get("authorization", "")
        bearer = authorization[7:].strip() if authorization.lower().startswith("bearer ") else None
        transport_acceptable = self._transport_acceptable(scope, headers)
        if (session_token or bearer) and not transport_acceptable:
            await self._json(scope, receive, send, 400, "Credentials require HTTPS or central PC localhost")
            return
        principal = access_control_module.authenticate(
            conn, session_token=session_token.value if session_token else None, bearer_token=bearer,
        )
        if not principal:
            await self._json(scope, receive, send, 401, "Authentication required")
            return

        def audit_request(status_code: int) -> None:
            try:
                access_control_module.record_request(
                    conn, principal, method, path, status_code,
                    client_ip=(scope.get("client") or (None,))[0], user_agent=headers.get("user-agent"),
                )
            except Exception:
                conn.rollback()
                log.exception("Failed to record access audit for %s %s", method, path)

        required = access_control_module.required_permissions(method, path)
        if not access_control_module.authorize(principal, required):
            audit_request(403)
            await self._json(scope, receive, send, 403, f"Permission required: {' or '.join(required)}")
            return
        if mutation and principal["kind"] == "user":
            supplied_csrf = headers.get("x-csrf-token", "")
            if not supplied_csrf or not hmac.compare_digest(supplied_csrf, principal["csrf_token"]):
                audit_request(403)
                await self._json(scope, receive, send, 403, "Valid CSRF token required")
                return
        scope.setdefault("state", {})["principal"] = principal
        scope["state"]["transport_acceptable"] = transport_acceptable
        replay_receive = receive
        if mutation and "application/json" in headers.get("content-type", "") and not path.startswith("/auth/"):
            body = b""
            more = True
            while more:
                message = await receive()
                body += message.get("body", b"")
                more = message.get("more_body", False)
                if len(body) > 10_000_000:
                    await self._json(scope, receive, send, 413, "JSON request exceeds 10 MB")
                    return
            try:
                bound = access_control_module.bind_actor(json.loads(body or b"{}"), principal)
                body = json.dumps(bound, separators=(",", ":")).encode("utf-8")
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
            sent = False

            async def replay_receive():
                nonlocal sent
                if sent:
                    return {"type": "http.request", "body": b"", "more_body": False}
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}

        response_status = 500

        async def audit_send(message):
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message["status"]
            await send(message)

        try:
            await self.app(scope, replay_receive, audit_send)
        except Exception:
            conn.rollback()
            raise
        finally:
            if mutation:
                audit_request(response_status)

# ── App lifecycle ─────────────────────────────────────────────────────────────

_mqtt_client = None
_conn        = None
_cv_observer = None
_event_watch_task = None
_learning_watch_task = None
_industrial_watch_task = None
_alert_watch_task = None
_constraint_watch_task = None
_route_conn_override = None
_route_connections = threading.local()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _mqtt_client, _conn, _cv_observer, _event_watch_task, _learning_watch_task, _industrial_watch_task, _alert_watch_task, _constraint_watch_task
    _conn = init_db(DB_PATH, check_same_thread=False)
    if access_control_module.auth_required():
        token_path = access_control_module.ensure_bootstrap_token(_conn)
        if token_path:
            log.warning("HIVE access setup required; bootstrap token stored at %s", token_path)
    production_control_module.sync_all(_conn)
    resources_module.sync_defaults(_conn)
    identity_module.sync_controlled_orders(_conn)
    execution_module.sync(_conn)
    maintenance_module.sync_defaults(_conn)
    maintenance_module.sync(_conn)
    tooling_module.sync(_conn)
    connectors_module.sync_defaults(_conn)
    industrial_gateway_module.sync_defaults(_conn)
    try:
        _mqtt_client = mqtt_bridge.start(_conn, CONFIG_PATH)
        log.info("MQTT bridge started")
    except Exception as e:
        log.warning("MQTT bridge failed to start (no broker?): %s", e)
    _cv_observer = cv_watcher.start(_conn, CONFIG_PATH)
    _event_watch_task = asyncio.create_task(_watch_events())
    _learning_watch_task = asyncio.create_task(_watch_learning())
    _industrial_watch_task = asyncio.create_task(_watch_industrial_io())
    _alert_watch_task = asyncio.create_task(_watch_alerts())
    _constraint_watch_task = asyncio.create_task(_watch_constraints())
    yield
    if _constraint_watch_task:
        _constraint_watch_task.cancel()
        try:
            await _constraint_watch_task
        except asyncio.CancelledError:
            pass
    if _alert_watch_task:
        _alert_watch_task.cancel()
        try:
            await _alert_watch_task
        except asyncio.CancelledError:
            pass
    if _industrial_watch_task:
        _industrial_watch_task.cancel()
        try:
            await _industrial_watch_task
        except asyncio.CancelledError:
            pass
    if _learning_watch_task:
        _learning_watch_task.cancel()
        try:
            await _learning_watch_task
        except asyncio.CancelledError:
            pass
    if _event_watch_task:
        _event_watch_task.cancel()
        try:
            await _event_watch_task
        except asyncio.CancelledError:
            pass
    if _cv_observer:
        _cv_observer.stop()
        _cv_observer.join()
    if _mqtt_client:
        _mqtt_client.loop_stop()
        _mqtt_client.disconnect()
    if _conn:
        _conn.close()


app = FastAPI(title="HIVE OS", version=APP_VERSION, lifespan=lifespan)

cors_origins = [
    origin.strip()
    for origin in os.getenv("HIVE_CORS_ORIGINS", "").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-CSRF-Token"],
)
app.add_middleware(ApiPrefixMiddleware)
app.add_middleware(AccessControlMiddleware)


def _get_conn():
    if _route_conn_override is not None:
        return _route_conn_override
    conn = getattr(_route_connections, "conn", None)
    if conn is None:
        conn = db_module.get_connection(DB_PATH, check_same_thread=False)
        _route_connections.conn = conn
    return conn


def set_conn(conn):
    global _conn, _route_conn_override
    _conn = conn
    _route_conn_override = conn


# ── Machine state cache ───────────────────────────────────────────────────────
# Holds the latest event per machine so /machines returns current state
# without querying every event row.

_machine_state: dict[str, dict] = {}


async def _watch_events():
    """Background task — drains the MQTT event queue, updates state cache."""
    q = mqtt_bridge.subscribe_events()
    try:
        while True:
            while not q.empty():
                event = q.get_nowait()
                key   = event.get("machine_key")
                if key:
                    _machine_state[key] = event
            await asyncio.sleep(0.2)
    finally:
        mqtt_bridge.unsubscribe_events(q)


async def _watch_learning():
    """Periodically derive learning evidence without blocking request handling."""
    while True:
        await asyncio.sleep(30)
        try:
            conn = _get_conn()
            await asyncio.to_thread(learning_module.refresh_all, conn)
            await asyncio.to_thread(routing_module.refresh_observations, conn)
            await asyncio.to_thread(production_control_module.sync_all, conn)
            await asyncio.to_thread(resources_module.sync_defaults, conn)
            await asyncio.to_thread(execution_module.sync, conn)
            await asyncio.to_thread(execution_module.reconcile_machine_events, conn)
            await asyncio.to_thread(maintenance_module.sync, conn)
            await asyncio.to_thread(tooling_module.sync, conn)
            await asyncio.to_thread(forecasting_module.refresh_if_needed, conn)
            await asyncio.to_thread(recovery_module.refresh_if_needed, conn)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("cycle learning refresh failed")


async def _watch_industrial_io():
    """Poll due approved industrial profiles without blocking API requests."""
    prune_counter = 0
    while True:
        await asyncio.sleep(1)
        try:
            conn = _get_conn()
            await asyncio.to_thread(industrial_gateway_module.poll_due_profiles, conn)
            prune_counter += 1
            if prune_counter >= 3600:
                await asyncio.to_thread(industrial_gateway_module.prune_raw_telemetry, conn)
                prune_counter = 0
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("industrial I/O poll failed")


async def _watch_alerts():
    """Run only the alert automation explicitly commissioned by the site."""
    while True:
        try:
            settings = alerting_module.runtime_settings(_get_conn())
            await asyncio.sleep(max(15, int(settings["interval_seconds"])))
            conn = _get_conn()
            settings = alerting_module.runtime_settings(conn)
            if settings["auto_sync"]:
                await asyncio.to_thread(alerting_module.sync, conn, "hive-alert-worker")
            if settings["auto_dispatch"]:
                await asyncio.to_thread(alerting_module.dispatch, conn, 50, "hive-alert-worker")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("alert automation failed")


def _run_constraint_worker() -> dict:
    owned = _route_conn_override is None
    conn = (db_module.get_connection(DB_PATH, check_same_thread=False)
            if owned else _route_conn_override)
    try:
        return bottleneck_module.automatic_sync(conn)
    except Exception as error:
        conn.rollback()
        bottleneck_module.record_runtime_failure(conn, error)
        raise
    finally:
        if owned:
            conn.close()


async def _watch_constraints():
    """Continuously append due read-only constraint evidence snapshots."""
    await asyncio.sleep(2)
    while True:
        try:
            settings = bottleneck_module.runtime_settings(_get_conn())
            if settings["auto_sync"]:
                last_run = settings.get("last_run_at")
                due = not last_run or (
                    datetime.now(timezone.utc) - datetime.fromisoformat(
                        last_run.replace("Z", "+00:00")
                    )
                ).total_seconds() >= int(settings["interval_seconds"])
                if due:
                    await asyncio.to_thread(_run_constraint_worker)
            await asyncio.sleep(5 if settings["auto_sync"] else 15)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("constraint intelligence automation failed")
            await asyncio.sleep(15)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _machine_rows() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, name, machine_key, type, brand, has_maestro, has_opcua, active "
        "FROM machines ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def _enrich_machine(m: dict) -> dict:
    state = _machine_state.get(m["machine_key"], {})
    return {
        **m,
        "state":       state.get("state") or _infer_state(state.get("event_type")),
        "power_w":     state.get("power_w"),
        "current_cnc": state.get("cnc_file"),
        "last_event":  state.get("event_type"),
        "last_seen":   state.get("ts"),
    }


def _infer_state(event_type: Optional[str]) -> str:
    if not event_type:
        return "unknown"
    mapping = {
        "power_on": "on", "cycle_start": "on", "state_on": "on",
        "idle": "idle", "cycle_end": "idle", "state_idle": "idle",
        "power_off": "off", "state_off": "off",
        "alarm": "alarm",
    }
    return mapping.get(event_type, "unknown")


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "hive-os", "version": APP_VERSION}


def _auth_context(request: Request) -> dict:
    return {
        "client_ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }


def _set_session_cookie(response: Response, token: str, request: Request) -> None:
    secure = request.url.scheme == "https" or os.getenv("HIVE_COOKIE_SECURE") == "1"
    response.set_cookie(
        access_control_module.SESSION_COOKIE, token, httponly=True, secure=secure,
        samesite="strict", path="/",
    )
    response.headers["Cache-Control"] = "no-store"


def _principal(request: Request) -> dict:
    principal = getattr(request.state, "principal", None)
    if not principal:
        raise HTTPException(401, "Authentication required")
    return principal


def _principal_name(request: Request) -> str:
    principal = getattr(request.state, "principal", None)
    if principal:
        return principal["display_name"]
    if not access_control_module.auth_required():
        return "Development Admin"
    raise HTTPException(401, "Authentication required")


@app.get("/auth/status")
def get_auth_status(request: Request):
    return access_control_module.status(
        _get_conn(), transport_acceptable=bool(getattr(request.state, "transport_acceptable", False))
    )


@app.post("/auth/bootstrap")
def post_auth_bootstrap(payload: AuthBootstrap, request: Request, response: Response):
    if not getattr(request.state, "transport_acceptable", False):
        raise HTTPException(400, "Administrator setup requires HTTPS or the central PC localhost")
    try:
        result = access_control_module.bootstrap(
            _get_conn(), payload.model_dump(), **_auth_context(request)
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    _set_session_cookie(response, result.pop("token"), request)
    result.pop("session_id", None)
    return result


@app.post("/auth/login")
def post_auth_login(payload: AuthLogin, request: Request, response: Response):
    if not getattr(request.state, "transport_acceptable", False):
        raise HTTPException(400, "Sign-in requires HTTPS or the central PC localhost")
    try:
        result = access_control_module.login(
            _get_conn(), payload.model_dump(), **_auth_context(request)
        )
    except ValueError as error:
        raise HTTPException(401, str(error)) from error
    _set_session_cookie(response, result.pop("token"), request)
    result.pop("session_id", None)
    return result


@app.get("/auth/me")
def get_auth_me(request: Request, response: Response):
    principal = _principal(request)
    if principal["kind"] != "user":
        raise HTTPException(403, "Human session required")
    response.headers["Cache-Control"] = "no-store"
    return {"user": principal["user"], "csrf_token": principal["csrf_token"],
            "expires_at": principal["expires_at"]}


@app.post("/auth/logout")
def post_auth_logout(request: Request, response: Response):
    access_control_module.revoke_session(_get_conn(), _principal(request))
    response.delete_cookie(access_control_module.SESSION_COOKIE, path="/", samesite="strict")
    response.headers["Clear-Site-Data"] = '"cache", "cookies", "storage"'
    response.headers["Cache-Control"] = "no-store"
    return {"logged_out": True}


@app.post("/auth/password")
def post_auth_password(payload: AuthPasswordChange, request: Request):
    try:
        return access_control_module.change_password(
            _get_conn(), payload.model_dump(), _principal(request)
        )
    except (KeyError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.get("/auth/users")
def get_auth_users():
    return {"users": access_control_module.list_users(_get_conn()),
            "roles": [{"key": role, "permissions": sorted(permissions)}
                      for role, permissions in access_control_module.ROLE_PERMISSIONS.items()]}


@app.post("/auth/users")
def post_auth_user(payload: AuthUserCreate, request: Request):
    try:
        return access_control_module.create_user(
            _get_conn(), payload.model_dump(), _principal(request)
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.put("/auth/users/{user_id}")
def put_auth_user(user_id: int, payload: AuthUserUpdate, request: Request):
    try:
        return access_control_module.update_user(
            _get_conn(), user_id, payload.model_dump(exclude_none=True), _principal(request)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/auth/users/{user_id}/reset-password")
def post_auth_password_reset(user_id: int, payload: AuthPasswordReset, request: Request):
    try:
        return access_control_module.reset_password(
            _get_conn(), user_id, payload.password, _principal(request)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/auth/api-keys")
def get_auth_api_keys():
    return {"api_keys": access_control_module.list_api_keys(_get_conn())}


@app.post("/auth/api-keys")
def post_auth_api_key(payload: AuthApiKeyCreate, request: Request):
    try:
        return access_control_module.create_api_key(
            _get_conn(), payload.model_dump(exclude_none=True), _principal(request)
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.delete("/auth/api-keys/{key_id}")
def delete_auth_api_key(key_id: int, request: Request):
    try:
        return access_control_module.revoke_api_key(_get_conn(), key_id, _principal(request))
    except KeyError as error:
        raise HTTPException(404, str(error)) from error


@app.get("/auth/events")
def get_auth_events(limit: int = Query(default=100, ge=1, le=500)):
    return {"events": access_control_module.recent_events(_get_conn(), limit)}


@app.get("/mqtt-security")
def get_mqtt_security():
    return mqtt_security_module.status(_get_conn())


@app.post("/mqtt-security/enrollments")
def post_mqtt_enrollment(payload: MqttEnrollmentCreate, request: Request):
    principal = _principal(request)
    try:
        bundle, manifest = mqtt_security_module.issue_bundle(
            _get_conn(), payload.machine_key, principal["display_name"],
            days=payload.validity_days,
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    filename = f"hive-enrollment-{payload.machine_key}.zip"
    return Response(
        content=bundle,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-HIVE-Certificate-SHA256": manifest["certificate_sha256"],
        },
    )


@app.post("/mqtt-security/enrollments/{enrollment_id}/revoke")
def post_mqtt_enrollment_revoke(
    enrollment_id: int, payload: MqttEnrollmentRevoke, request: Request,
):
    principal = _principal(request)
    try:
        return mqtt_security_module.revoke(
            _get_conn(), enrollment_id, principal["display_name"], payload.reason,
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error

@app.get("/machines")
def get_machines():
    return [_enrich_machine(m) for m in _machine_rows()]


@app.get("/machines/{machine_key}")
def get_machine(machine_key: str):
    machines = _machine_rows()
    m = next((x for x in machines if x["machine_key"] == machine_key), None)
    if not m:
        raise HTTPException(404, f"Machine '{machine_key}' not found")

    conn = _get_conn()
    recent_events = conn.execute(
        """SELECT event_type, cnc_file, ts FROM machine_events
           WHERE machine_id=? ORDER BY ts DESC LIMIT 20""",
        (m["id"],)
    ).fetchall()

    return {
        **_enrich_machine(m),
        "recent_events": [dict(r) for r in recent_events],
    }


@app.get("/jobs")
def get_jobs(limit: int = Query(50, le=200)):
    conn = _get_conn()
    rows = conn.execute(
        """SELECT j.job_name, j.room_name, j.job_date, j.beamsaw_run_id,
                  j.total_parts, c.name as client_name, po.id production_order_id,
                  po.status order_status, po.due_at, po.priority, po.release_sequence
           FROM jobs j LEFT JOIN clients c ON j.client_id=c.id
           LEFT JOIN production_orders po ON po.job_id=j.id
           ORDER BY j.job_date DESC, j.id DESC LIMIT ?""",
        (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/jobs/{job_name}/parts")
def get_job_parts(job_name: str):
    conn = _get_conn()
    job = conn.execute(
        "SELECT id FROM jobs WHERE job_name=?", (job_name,)
    ).fetchone()
    if not job:
        raise HTTPException(404, f"Job '{job_name}' not found")

    rows = conn.execute(
        """SELECT p.id, p.part_name, p.material, p.length_mm, p.width_mm,
                  p.thickness_mm, p.qty, p.has_cnc,
                  p.cnc_file_back, p.cnc_file_front, p.beamsaw_seq,
                  a.assembly_name,
                  me.event_type as last_event, me.ts as last_seen
           FROM parts p
           LEFT JOIN assemblies a ON p.assembly_id=a.id
           LEFT JOIN (
               SELECT part_id, event_type, ts,
                      ROW_NUMBER() OVER (PARTITION BY part_id ORDER BY ts DESC) rn
               FROM machine_events WHERE part_id IS NOT NULL
           ) me ON me.part_id=p.id AND me.rn=1
           WHERE p.job_id=?
           ORDER BY p.beamsaw_seq""",
        (job["id"],)
    ).fetchall()

    return [dict(r) for r in rows]


@app.get("/jobs/active")
def get_active_jobs():
    conn = _get_conn()
    jobs = progress_module.get_active_jobs(conn)
    return [vars(j) for j in jobs]


@app.get("/jobs/{job_name}/progress")
def get_job_progress(job_name: str):
    conn = _get_conn()
    result = progress_module.get_job_progress(conn, job_name)
    if not result:
        raise HTTPException(404, f"Job '{job_name}' not found")
    return vars(result)


@app.get("/score/daily")
def get_daily_score():
    conn = _get_conn()
    return vars(score_module.get_daily_score(conn))


@app.get("/jobs/{job_name}/cycle-times")
def get_job_cycle_times(job_name: str):
    conn = _get_conn()
    result = cycle_time_module.estimate_job(conn, job_name)
    if not result:
        raise HTTPException(404, f"Job '{job_name}' not found")
    return result


@app.get("/sequence")
def get_sequence(jobs: Optional[str] = None):
    """
    Returns optimal job sequence.
    ?jobs=JOB1,JOB2,JOB3 to sequence specific jobs, omit for all jobs.
    """
    conn      = _get_conn()
    job_list  = [j.strip() for j in jobs.split(",")] if jobs else None
    plan      = sequencer_module.sequence(conn, job_list)
    return {
        "generated_at": plan.generated_at,
        "total_jobs":   plan.total_jobs,
        "uncalibrated": plan.uncalibrated,
        "shift_hours":  plan.shift_hours,
        "jobs": [vars(j) for j in plan.jobs],
    }


@app.get("/bottlenecks")
def get_bottlenecks(window_hours: int = Query(8, ge=1, le=24)):
    report = bottleneck_module.detect(_get_conn(), window_hours)
    return {
        "generated_at": report.generated_at,
        "window_hours": report.window_hours,
        "method_version": report.method_version,
        "evidence_sha256": report.evidence_sha256,
        "guardrail": report.guardrail,
        "episode": report.episode,
        "current": vars(report.current) if report.current else None,
        "candidate": vars(report.candidate) if report.candidate else None,
        "focus": vars(report.focus) if report.focus else None,
        "machines": [vars(machine) for machine in report.machines],
    }


@app.post("/constraints/sync")
def post_constraint_sync(payload: ConstraintSyncRequest):
    return bottleneck_module.sync(
        _get_conn(), actor=payload.actor, window_hours=payload.window_hours
    )


@app.get("/constraints/timeline")
def get_constraint_timeline(days: int = Query(30, ge=1, le=3650),
                            limit: int = Query(100, ge=1, le=500)):
    return bottleneck_module.timeline(_get_conn(), days=days, limit=limit)


@app.get("/constraints/runtime")
def get_constraint_runtime():
    return bottleneck_module.timeline(_get_conn(), days=7, limit=20)["runtime"]


@app.put("/constraints/settings")
def put_constraint_settings(payload: ConstraintSettingsUpdate):
    try:
        return bottleneck_module.update_runtime_settings(_get_conn(), payload.model_dump())
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/data-quality")
def get_data_quality(window_hours: int = Query(8, ge=1, le=168)):
    return data_quality_module.build(_get_conn(), window_hours)


@app.get("/optimization")
def get_optimization(window_hours: int = Query(8, ge=1, le=24)):
    return optimization_module.build(_get_conn(), window_hours)


@app.get("/improvements")
def get_improvements():
    return improvement_module.snapshot(_get_conn())


@app.post("/improvements/sync")
def post_improvement_sync(payload: ImprovementSyncRequest):
    return improvement_module.sync(
        _get_conn(), actor=payload.actor, window_hours=payload.window_hours
    )


@app.get("/improvements/recommendations/{recommendation_id}")
def get_improvement_recommendation(recommendation_id: int):
    try:
        return improvement_module.recommendation_detail(_get_conn(), recommendation_id)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error


@app.post("/improvements/recommendations/{recommendation_id}/action")
def post_improvement_action(recommendation_id: int, payload: ImprovementAction):
    try:
        return improvement_module.act(
            _get_conn(), recommendation_id, payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/root-causes")
def get_root_causes(status: Optional[str] = Query(default=None, pattern="^(open|confirmed|dismissed)$")):
    return root_cause_module.snapshot(_get_conn(), status=status)


@app.post("/root-causes/sync")
def post_root_cause_sync(payload: RootCauseSyncRequest):
    return root_cause_module.sync(
        _get_conn(), lookback_days=payload.lookback_days, actor=payload.actor
    )


@app.get("/root-causes/{case_id}")
def get_root_cause(case_id: int):
    try:
        return root_cause_module.case_detail(_get_conn(), case_id)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error


@app.post("/root-causes/{case_id}/decision")
def post_root_cause_decision(case_id: int, payload: RootCauseDecision):
    try:
        return root_cause_module.decide(
            _get_conn(), case_id, payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/alerts")
def get_alerts(status: Optional[str] = Query(default=None, pattern="^(open|acknowledged|snoozed|resolved)$")):
    return alerting_module.snapshot(_get_conn(), status=status)


@app.post("/alerts/sync")
def post_alert_sync(payload: AlertSyncRequest):
    try:
        return alerting_module.sync(_get_conn(), actor=payload.actor)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.put("/alerts/settings")
def put_alert_settings(payload: AlertSettingsUpdate):
    try:
        return alerting_module.update_settings(
            _get_conn(), payload.model_dump(exclude_none=True)
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.put("/alerts/destinations/{destination_key}")
def put_alert_destination(destination_key: str, payload: AlertDestinationUpsert):
    try:
        return alerting_module.upsert_destination(
            _get_conn(), destination_key, payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/alerts/destinations/{destination_key}/test")
def post_alert_destination_test(destination_key: str, payload: AlertDestinationTest):
    try:
        return alerting_module.test_destination(
            _get_conn(), destination_key, payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/alerts/deliveries/dispatch")
def post_alert_dispatch(payload: AlertDispatchRequest):
    try:
        return alerting_module.dispatch(
            _get_conn(), limit=payload.limit, actor=payload.actor
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/alerts/{alert_id}")
def get_alert(alert_id: int):
    try:
        return alerting_module.alert_detail(_get_conn(), alert_id)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error


@app.post("/alerts/{alert_id}/action")
def post_alert_action(alert_id: int, payload: AlertAction):
    try:
        return alerting_module.act(
            _get_conn(), alert_id, payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/learning/status")
def get_learning_status():
    return learning_module.status(_get_conn())


@app.post("/learning/refresh")
def post_learning_refresh():
    conn = _get_conn()
    result = learning_module.refresh_all(conn)
    result["routing"] = routing_module.refresh_observations(conn)
    return result


@app.get("/routing/graph")
def get_routing_graph():
    return routing_module.graph(_get_conn())


@app.get("/digital-twin/readiness")
def get_digital_twin_readiness():
    return digital_twin_module.readiness(_get_conn())


@app.post("/digital-twin/compare")
def post_digital_twin_compare(payload: DigitalTwinRequest):
    try:
        return digital_twin_module.compare(
            _get_conn(), payload.job_names, payload.policies,
            payload.stochastic, payload.seed,
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/forecast")
def get_forecast():
    return forecasting_module.snapshot(_get_conn())


@app.get("/forecast/history")
def get_forecast_history(limit: int = Query(20, ge=1, le=100)):
    return forecasting_module.history(_get_conn(), limit)


@app.post("/forecast/refresh")
def post_forecast_refresh(payload: ForecastRefreshRequest):
    try:
        return forecasting_module.refresh(
            _get_conn(), job_names=payload.job_names, policy=payload.policy,
            samples=payload.samples, seed=payload.seed, force=payload.force,
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/production/sync")
def post_production_sync():
    conn = _get_conn()
    result = production_control_module.sync_all(conn)
    result["identity_sync"] = identity_module.sync_controlled_orders(conn)
    result["execution_sync"] = execution_module.sync(conn)
    result["reconciliation"] = execution_module.reconcile_machine_events(conn)
    return result


@app.get("/production/orders")
def get_production_orders(status: Optional[str] = None):
    return production_control_module.list_orders(_get_conn(), status)


@app.get("/production/readiness")
def get_production_readiness():
    return production_control_module.readiness(_get_conn())


@app.put("/production/orders/{order_id}")
def put_production_order(order_id: int, payload: ProductionOrderUpdate):
    conn = _get_conn()
    try:
        result = production_control_module.update_order(
            conn, order_id, payload.model_dump(exclude_none=True), commit=False
        )
        if result["status"] in (*identity_module.CONTROLLED_ORDER_STATES, "cancelled"):
            identity_module.materialize_order(conn, order_id, payload.actor, commit=False)
        execution_module.sync(conn, commit=False)
        conn.commit()
        return result
    except production_control_module.VersionConflict as error:
        conn.rollback()
        raise HTTPException(409, str(error)) from error
    except KeyError as error:
        conn.rollback()
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        conn.rollback()
        raise HTTPException(400, str(error)) from error


@app.get("/production/routes/{job_name}")
def get_production_routes(job_name: str):
    try:
        return production_control_module.get_job_routes(_get_conn(), job_name)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error


@app.put("/production/routes/parts/{part_id}")
def put_part_route(part_id: int, payload: PartRouteUpdate):
    try:
        return production_control_module.replace_part_route(
            _get_conn(), part_id, payload.machine_keys, payload.actor, payload.notes
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/production/route-exceptions")
def get_route_exceptions(status: str = "open"):
    return production_control_module.list_exceptions(_get_conn(), status)


@app.post("/production/route-exceptions/{exception_id}/resolve")
def post_route_exception_resolution(exception_id: int, payload: RouteExceptionDecision):
    try:
        return production_control_module.resolve_exception(
            _get_conn(), exception_id, payload.status, payload.actor, payload.notes
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/planning/scenarios")
def get_planning_scenarios(limit: int = Query(20, ge=1, le=100)):
    return planning_module.list_scenarios(_get_conn(), limit)


@app.post("/planning/scenarios")
def post_planning_scenario(payload: PlanningScenarioCreate):
    try:
        return planning_module.create_scenario(
            _get_conn(), payload.model_dump(exclude_none=True)
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/planning/scenarios/{scenario_id}")
def get_planning_scenario(scenario_id: int):
    try:
        return planning_module.get_scenario(_get_conn(), scenario_id)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error


@app.post("/planning/scenarios/{scenario_id}/decision")
def post_planning_decision(scenario_id: int, payload: PlanningDecision):
    try:
        result = planning_module.decide(
            _get_conn(), scenario_id, payload.decision, payload.actor,
            payload.selected_policy, payload.notes,
        )
        execution_module.sync(_get_conn())
        return result
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/planning/active-schedule")
def get_active_schedule():
    return planning_module.active_schedule(_get_conn())


@app.get("/recovery")
def get_schedule_recovery():
    return recovery_module.snapshot(_get_conn())


@app.get("/recovery/history")
def get_schedule_recovery_history(limit: int = Query(20, ge=1, le=100)):
    return recovery_module.history(_get_conn(), limit)


@app.post("/recovery/analyze")
def post_schedule_recovery_analysis(payload: RecoveryAnalyzeRequest):
    try:
        return recovery_module.analyze(
            _get_conn(), actor=payload.actor, force=payload.force,
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/recovery/{assessment_id}/decision")
def post_schedule_recovery_decision(assessment_id: int, payload: RecoveryDecision):
    try:
        return recovery_module.decide(
            _get_conn(), assessment_id, payload.decision, payload.actor,
            payload.selected_policy, payload.notes,
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/execution/snapshot")
def get_execution_snapshot():
    return execution_module.snapshot(_get_conn())


@app.post("/execution/sync")
def post_execution_sync():
    return execution_module.sync(_get_conn())


@app.get("/execution/jobs")
def get_execution_jobs(machine_key: Optional[str] = None,
                       include_terminal: bool = False,
                       limit: int = Query(500, ge=1, le=2000)):
    return execution_module.list_jobs(_get_conn(), machine_key, include_terminal, limit)


@app.post("/execution/jobs/{execution_job_id}/action")
def post_execution_action(execution_job_id: int, payload: ExecutionActionRequest):
    try:
        return execution_module.apply_action(
            _get_conn(), execution_job_id, payload.model_dump(exclude_none=True)
        )
    except execution_module.VersionConflict as error:
        raise HTTPException(409, str(error)) from error
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/execution/events")
def get_execution_events(limit: int = Query(100, ge=1, le=1000)):
    return execution_module.list_events(_get_conn(), limit)


@app.get("/execution/exceptions")
def get_execution_exceptions(status: str = "open",
                             limit: int = Query(100, ge=1, le=1000)):
    return execution_module.list_exceptions(_get_conn(), status, limit)


@app.post("/execution/exceptions/{exception_id}/resolve")
def post_execution_exception_resolution(exception_id: int,
                                        payload: ExecutionExceptionDecision):
    try:
        return execution_module.resolve_exception(
            _get_conn(), exception_id, payload.status, payload.actor, payload.notes
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/traceability/events")
def get_traceability_events(object_key: Optional[str] = None,
                            part_id: Optional[int] = None,
                            limit: int = Query(100, ge=1, le=1000)):
    return execution_module.list_traceability(_get_conn(), object_key, part_id, limit)


@app.get("/identity/snapshot")
def get_identity_snapshot():
    return identity_module.snapshot(_get_conn())


@app.post("/identity/orders/{order_id}/materialize")
def post_identity_materialization(order_id: int, payload: IdentityMaterializeRequest):
    try:
        return identity_module.materialize_order(_get_conn(), order_id, payload.actor)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error


@app.get("/identity/orders/{order_id}/units")
def get_identity_order_units(order_id: int, include_void: bool = False):
    return identity_module.list_order_units(_get_conn(), order_id, include_void)


@app.get("/identity/units/{unit_key}")
def get_identity_unit(unit_key: str):
    try:
        return identity_module.get_unit(_get_conn(), unit_key)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error


@app.get("/identity/resolve")
def get_identity_resolution(value: str = Query(min_length=1, max_length=500)):
    return identity_module.resolve_identifier(_get_conn(), value)


@app.post("/identity/units/{unit_key}/aliases")
def post_identity_alias(unit_key: str, payload: UnitAliasCreate):
    try:
        return identity_module.add_alias(
            _get_conn(), unit_key, payload.scheme, payload.value,
            payload.actor, payload.source,
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/labels/jobs")
def get_label_jobs(limit: int = Query(50, ge=1, le=500)):
    return identity_module.list_print_jobs(_get_conn(), limit)


@app.post("/labels/jobs")
def post_label_job(payload: LabelJobCreate):
    try:
        return identity_module.create_print_job(
            _get_conn(), payload.order_id, payload.requested_by,
            payload.only_unprinted, payload.part_ids, payload.template_key,
            payload.printer_key, payload.notes,
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/labels/jobs/{print_job_id}")
def get_label_job(print_job_id: int):
    try:
        return identity_module.get_print_job(_get_conn(), print_job_id)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error


@app.get("/labels/jobs/{print_job_id}/print", response_class=HTMLResponse)
def get_label_job_print_view(print_job_id: int):
    try:
        return identity_module.print_job_html(_get_conn(), print_job_id)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error


@app.get("/labels/jobs/{print_job_id}/zpl")
def get_label_job_zpl(print_job_id: int):
    try:
        content = identity_module.print_job_zpl(_get_conn(), print_job_id)
        return Response(
            content, media_type="text/plain",
            headers={"Content-Disposition": f'attachment; filename="hive-labels-{print_job_id}.zpl"'},
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error


@app.get("/labels/units/{unit_key}/svg")
def get_unit_label_svg(unit_key: str):
    try:
        return Response(identity_module.unit_label_svg(_get_conn(), unit_key),
                        media_type="image/svg+xml")
    except KeyError as error:
        raise HTTPException(404, str(error)) from error


@app.post("/labels/jobs/{print_job_id}/printed")
def post_label_job_printed(print_job_id: int, payload: LabelPrintConfirmation):
    try:
        return identity_module.mark_printed(
            _get_conn(), print_job_id, payload.actor, payload.notes
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/resources/snapshot")
def get_resource_snapshot(job_name: Optional[list[str]] = Query(default=None)):
    return resources_module.snapshot(_get_conn(), job_name)


@app.get("/changeovers")
def get_changeovers(job_name: Optional[list[str]] = Query(default=None)):
    return changeovers_module.snapshot(_get_conn(), job_name)


@app.put("/changeovers/machines/{machine_key}/standard")
def put_changeover_standard(machine_key: str, payload: ChangeoverStandardUpdate):
    try:
        return changeovers_module.update_standard(
            _get_conn(), machine_key, payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/changeovers/observations")
def post_changeover_observation(payload: ChangeoverObservationCreate):
    try:
        return changeovers_module.record_observation(
            _get_conn(), payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/changeovers/observations/{observation_id}/exclude")
def post_changeover_observation_exclusion(
    observation_id: int, payload: ChangeoverObservationExclude,
):
    try:
        return changeovers_module.exclude_observation(
            _get_conn(), observation_id, payload.reason, payload.actor,
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/changeovers/sync")
def post_changeover_sync(payload: ChangeoverSyncRequest):
    if payload.include_downtime:
        return changeovers_module.sync_downtime_observations(
            _get_conn(), actor=payload.actor,
        )
    return changeovers_module.sync_models(_get_conn(), actor=payload.actor)


@app.put("/resources/materials/{material_key}")
def put_material_stock(material_key: str, payload: MaterialStockUpdate):
    try:
        return resources_module.set_material_stock(
            _get_conn(), material_key, payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/inventory/snapshot")
def get_inventory_snapshot(job_name: Optional[list[str]] = Query(default=None)):
    return inventory_module.snapshot(_get_conn(), job_name)


@app.get("/inventory/movements")
def get_inventory_movements(limit: int = Query(default=200, ge=1, le=1000)):
    return {"movements": inventory_module.movements(_get_conn(), limit=limit)}


@app.put("/inventory/items/{item_key}")
def put_inventory_item(item_key: str, payload: InventoryItemUpdate):
    try:
        return inventory_module.upsert_item(
            _get_conn(), item_key, payload.model_dump(exclude_none=True)
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.put("/inventory/items/{item_key}/lots/{lot_code}")
def put_inventory_lot(item_key: str, lot_code: str,
                      payload: InventoryLotBalanceUpdate):
    try:
        return inventory_module.set_lot_balance(
            _get_conn(), item_key, lot_code, payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except (sqlite3.IntegrityError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.put("/inventory/orders/{order_id}/requirements/{item_key}")
def put_inventory_requirement(order_id: int, item_key: str,
                              payload: InventoryRequirementUpdate):
    try:
        return inventory_module.set_requirement(
            _get_conn(), order_id, item_key, payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/inventory/remnants")
def post_inventory_remnant(payload: RemnantCreate):
    try:
        return inventory_module.create_remnant(
            _get_conn(), payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except (sqlite3.IntegrityError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.patch("/inventory/remnants/{remnant_key}")
def patch_inventory_remnant(remnant_key: str, payload: RemnantUpdate):
    try:
        return inventory_module.update_remnant(
            _get_conn(), remnant_key, payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/procurement/snapshot")
def get_procurement_snapshot(job_name: Optional[list[str]] = Query(default=None)):
    return procurement_module.snapshot(_get_conn(), job_name)


@app.put("/procurement/suppliers/{supplier_key}")
def put_procurement_supplier(supplier_key: str, payload: ProcurementSupplierUpdate):
    try:
        return procurement_module.upsert_supplier(
            _get_conn(), supplier_key, payload.model_dump(exclude_none=True)
        )
    except (sqlite3.IntegrityError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.put("/procurement/suppliers/{supplier_key}/mappings/{object_type}/{object_key}")
def put_procurement_mapping(supplier_key: str, object_type: str, object_key: str,
                            payload: ProcurementMappingUpdate):
    try:
        return procurement_module.upsert_mapping(
            _get_conn(), supplier_key, object_type, object_key,
            payload.model_dump(exclude_none=True),
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except (sqlite3.IntegrityError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.post("/procurement/orders")
def post_purchase_order(payload: PurchaseOrderCreate):
    try:
        return procurement_module.create_order(
            _get_conn(), payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except (sqlite3.IntegrityError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.post("/procurement/orders/draft-recommendations")
def post_procurement_drafts(payload: ProcurementDraftRequest):
    try:
        return procurement_module.draft_recommendations(
            _get_conn(), payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except (sqlite3.IntegrityError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.get("/procurement/orders/{order_id}")
def get_purchase_order(order_id: int):
    try:
        return procurement_module.order_detail(_get_conn(), order_id)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error


@app.post("/procurement/orders/{order_id}/action")
def post_purchase_order_action(order_id: int, payload: PurchaseOrderAction):
    try:
        return procurement_module.decide_order(
            _get_conn(), order_id, payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except (sqlite3.IntegrityError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.get("/procurement/orders/{order_id}/export.csv")
def get_purchase_order_csv(order_id: int):
    try:
        order = procurement_module.order_detail(_get_conn(), order_id)
        content = procurement_module.order_csv(_get_conn(), order_id)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    return Response(
        content=content, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{order["po_number"]}.csv"'},
    )


@app.get("/procurement/outbox")
def get_procurement_outbox(status: Optional[str] = Query(default=None)):
    return {"documents": procurement_module.outbox(_get_conn(), status)}


@app.post("/procurement/outbox/{outbox_id}/ack")
def post_procurement_outbox_ack(outbox_id: int, payload: ProcurementOutboxAck):
    try:
        return procurement_module.acknowledge_outbox(
            _get_conn(), outbox_id, payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error


@app.post("/procurement/receipts")
def post_goods_receipt(payload: GoodsReceiptCreate):
    try:
        return procurement_module.receive_order(
            _get_conn(), payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except (sqlite3.IntegrityError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.post("/procurement/imports/csv")
def post_procurement_csv(payload: ProcurementCsvImport):
    try:
        return procurement_module.import_csv(
            _get_conn(), payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except (sqlite3.IntegrityError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.put("/resources/labor/{role_key}")
def put_labor_role(role_key: str, payload: LaborRoleUpdate):
    try:
        return resources_module.update_labor_role(
            _get_conn(), role_key, payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error


@app.put("/resources/tooling/{pool_key}")
def put_tool_pool(pool_key: str, payload: ToolPoolUpdate):
    try:
        return resources_module.update_tool_pool(
            _get_conn(), pool_key, payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.put("/resources/machines/{machine_key}")
def put_machine_resource_profile(machine_key: str, payload: MachineResourceProfileUpdate):
    try:
        return resources_module.update_machine_profile(
            _get_conn(), machine_key, payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.put("/resources/calendar/factory")
def put_factory_calendar(payload: FactoryCalendarUpdate):
    try:
        return resources_module.update_factory_calendar(
            _get_conn(), payload.model_dump(exclude_none=True)
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.put("/resources/wip/{machine_key}")
def put_wip_buffer(machine_key: str, payload: WipBufferUpdate):
    try:
        return resources_module.update_wip_buffer(
            _get_conn(), machine_key, payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/resources/unavailability")
def post_resource_unavailability(payload: ResourceUnavailabilityCreate):
    try:
        return resources_module.create_unavailability(
            _get_conn(), payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.delete("/resources/unavailability/{unavailability_id}")
def delete_resource_unavailability(unavailability_id: int, actor: str = Query("operator", min_length=1)):
    try:
        return resources_module.delete_unavailability(_get_conn(), unavailability_id, actor)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error


@app.post("/commissioning/log/analyze")
def post_commissioning_log_analysis(payload: CommissioningLogRequest, request: Request):
    principal = getattr(request.state, "principal", None)
    if principal and principal["kind"] == "api_key" and payload.persist:
        raise HTTPException(403, "Machine credentials may analyze evidence but cannot persist history")
    conn = _get_conn()
    machine = conn.execute(
        "SELECT id FROM machines WHERE machine_key=?", (payload.machine_key,)
    ).fetchone()
    if not machine:
        raise HTTPException(404, f"Machine '{payload.machine_key}' not found")
    try:
        return commissioning_module.replay_log(
            conn, payload.machine_key, payload.log_text,
            persist=payload.persist, site_timezone=payload.site_timezone,
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/commissioning-lab")
def get_commissioning_lab():
    return commissioning_lab_module.snapshot(_get_conn())


@app.get("/commissioning-lab/history")
def get_commissioning_lab_history(limit: int = Query(20, ge=1, le=100)):
    return commissioning_lab_module.history(_get_conn(), limit)


@app.post("/commissioning-lab/run")
def post_commissioning_lab_run(payload: VirtualLabRunRequest):
    try:
        return commissioning_lab_module.run(
            _get_conn(), samples=payload.samples, seed=payload.seed, actor=payload.actor,
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/commissioning-evidence")
def get_commissioning_evidence():
    return commissioning_evidence_module.snapshot(_get_conn())


@app.get("/commissioning-evidence/pack")
def get_commissioning_evidence_pack():
    bundle, manifest = commissioning_evidence_module.build_pack(_get_conn())
    return Response(
        content=bundle, media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{manifest["filename"]}"',
            "Cache-Control": "no-store",
            "X-HIVE-Pack-SHA256": manifest["bundle_sha256"],
            "X-HIVE-Assumptions-SHA256": manifest["assumptions_sha256"],
        },
    )


@app.post("/commissioning-evidence/studies")
def post_commissioning_evidence_study(payload: CommissioningStudyCreate):
    try:
        return commissioning_evidence_module.create_study(
            _get_conn(), payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/commissioning-evidence/studies/{study_id}")
def get_commissioning_evidence_study(study_id: int):
    try:
        return commissioning_evidence_module.study_detail(_get_conn(), study_id)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error


@app.post("/commissioning-evidence/studies/{study_id}/observations")
def post_commissioning_evidence_observation(
    study_id: int, payload: CommissioningObservationCreate,
):
    try:
        return commissioning_evidence_module.add_observation(
            _get_conn(), study_id, payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/commissioning-evidence/studies/{study_id}/import")
def post_commissioning_evidence_import(study_id: int, payload: CommissioningCsvImport):
    try:
        return commissioning_evidence_module.import_csv(
            _get_conn(), study_id, payload.csv_text, apply=payload.apply, actor=payload.actor,
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/commissioning-evidence/studies/{study_id}/analyze")
def post_commissioning_evidence_analysis(study_id: int, request: Request):
    try:
        principal = getattr(request.state, "principal", None)
        actor = principal["display_name"] if principal else "commissioning"
        return commissioning_evidence_module.persist_analysis(_get_conn(), study_id, actor)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/commissioning-evidence/studies/{study_id}/action")
def post_commissioning_evidence_action(study_id: int, payload: CommissioningStudyAction):
    try:
        return commissioning_evidence_module.action(
            _get_conn(), study_id, payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/commissioning-evidence/studies/{study_id}/observations/{observation_id}/exclude")
def post_commissioning_evidence_exclusion(
    study_id: int, observation_id: int, payload: CommissioningObservationExclude,
):
    try:
        return commissioning_evidence_module.exclude_observation(
            _get_conn(), study_id, observation_id, payload.reason, payload.actor,
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/factory-readiness")
def get_factory_readiness():
    return factory_readiness_module.snapshot(_get_conn(), CONFIG_PATH)


@app.get("/factory-readiness/pack")
def get_factory_readiness_pack():
    bundle, metadata = factory_readiness_module.field_pack(_get_conn(), CONFIG_PATH)
    return Response(
        content=bundle, media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{metadata["filename"]}"',
            "Cache-Control": "no-store",
            "X-HIVE-Pack-SHA256": metadata["sha256"],
        },
    )


@app.put("/factory-readiness/machines/{machine_key}")
def put_machine_passport(machine_key: str, payload: MachinePassportUpdate):
    values = payload.model_dump(exclude_unset=True)
    actor = values.pop("actor", payload.actor)
    expected_version = values.pop("expected_version")
    try:
        return factory_readiness_module.update_passport(
            _get_conn(), machine_key, values, actor=actor,
            expected_version=expected_version,
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/factory-readiness/import")
def post_factory_inventory_import(payload: FactoryInventoryImport):
    try:
        return factory_readiness_module.import_inventory(
            _get_conn(), payload.csv_text, apply=payload.apply, actor=payload.actor,
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/factory-readiness/machines/{machine_key}/probe")
def post_factory_connection_probe(machine_key: str, payload: FactoryConnectionProbe):
    try:
        return factory_readiness_module.connection_probe(
            _get_conn(), CONFIG_PATH, machine_key,
            probe_type=payload.probe_type, host=payload.host, port=payload.port,
            execute=payload.execute, timeout_s=payload.timeout_s, actor=payload.actor,
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/factory-readiness/machines/{machine_key}/mission")
def post_factory_mission(machine_key: str, payload: FactoryMissionStart):
    try:
        return factory_readiness_module.start_mission(
            _get_conn(), CONFIG_PATH, machine_key,
            actor=payload.actor, notes=payload.notes,
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/factory-readiness/machines/{machine_key}/mission/action")
def post_factory_mission_action(machine_key: str, payload: FactoryMissionAction):
    try:
        return factory_readiness_module.mission_action(
            _get_conn(), CONFIG_PATH, machine_key, action=payload.action,
            actor=payload.actor, expected_version=payload.expected_version,
            notes=payload.notes,
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/connectors/snapshot")
def get_connector_snapshot():
    return connectors_module.snapshot(_get_conn())


@app.put("/connectors/{connector_key}")
def put_connector_profile(connector_key: str, payload: ConnectorProfileUpdate):
    try:
        return connectors_module.update_profile(
            _get_conn(), connector_key, payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/connectors/{connector_key}/analyze")
def post_connector_analysis(connector_key: str, payload: ConnectorAnalyzeRequest):
    try:
        if connector_key == "maestro_logs":
            if not payload.scope_key or not payload.log_text:
                raise ValueError("Maestro analysis requires scope_key and log_text")
            return connectors_module.analyze_maestro(
                _get_conn(), payload.scope_key, payload.log_text,
                file_name=payload.file_name, actor=payload.actor,
            )
        return connectors_module.analyze_records(
            _get_conn(), connector_key, payload.records, mapping=payload.mapping,
            file_name=payload.file_name, actor=payload.actor,
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/connectors/{connector_key}/approve")
def post_connector_approval(connector_key: str, payload: ConnectorApprovalRequest):
    try:
        return connectors_module.approve_run(
            _get_conn(), connector_key, payload.run_id,
            expected_version=payload.expected_version, actor=payload.actor,
            enable=payload.enable,
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/connectors/{connector_key}/import")
def post_connector_import(connector_key: str, payload: ConnectorImportRequest):
    try:
        return connectors_module.import_records(
            _get_conn(), connector_key, payload.records,
            file_name=payload.file_name, actor=payload.actor,
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/connectors/cabinet_vision_sql/discover")
def post_cv_sql_discovery():
    try:
        return connectors_module.discover_sql(_get_conn())
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/connectors/cabinet_vision_sql/sync")
def post_cv_sql_sync(payload: ConnectorSyncRequest | None = None):
    try:
        return connectors_module.sync_sql(
            _get_conn(), actor=payload.actor if payload else "operator"
        )
    except (RuntimeError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.get("/industrial/snapshot")
def get_industrial_snapshot():
    return industrial_gateway_module.snapshot(_get_conn())


@app.get("/energy/intelligence")
def get_energy_intelligence(hours: int = Query(default=24, ge=1, le=720)):
    return energy_intelligence_module.build(_get_conn(), hours=hours)


@app.put("/industrial/profiles/{profile_key}")
def put_industrial_profile(profile_key: str, payload: IndustrialProfileUpdate):
    try:
        return industrial_gateway_module.update_profile(
            _get_conn(), profile_key, payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/industrial/profiles/{profile_key}/simulate")
def post_industrial_simulation(profile_key: str, payload: IndustrialProbeRequest):
    try:
        return industrial_gateway_module.probe_profile(
            _get_conn(), profile_key, simulate=True, actor=payload.actor
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/industrial/profiles/{profile_key}/probe")
def post_industrial_probe(profile_key: str, payload: IndustrialProbeRequest):
    try:
        return industrial_gateway_module.probe_profile(
            _get_conn(), profile_key, simulate=False, actor=payload.actor
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.post("/industrial/profiles/{profile_key}/mqtt-probe")
def post_industrial_mqtt_probe(profile_key: str, payload: IndustrialMqttProbeRequest):
    try:
        return industrial_gateway_module.probe_mqtt_payload(
            _get_conn(), profile_key, payload.topic, payload.payload,
            actor=payload.actor,
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/industrial/profiles/{profile_key}/approve")
def post_industrial_approval(profile_key: str, payload: IndustrialApprovalRequest):
    try:
        return industrial_gateway_module.approve_run(
            _get_conn(), profile_key, payload.run_id,
            expected_version=payload.expected_version,
            actor=payload.actor, enable=payload.enable,
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/industrial/profiles/{profile_key}/poll")
def post_industrial_poll(profile_key: str, payload: IndustrialProbeRequest):
    try:
        return industrial_gateway_module.poll_profile(
            _get_conn(), profile_key, actor=payload.actor
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.post("/industrial/profiles/{profile_key}/browse")
def post_industrial_browse(profile_key: str, limit: int = Query(default=200, ge=1, le=500)):
    try:
        return industrial_gateway_module.browse_opcua(
            _get_conn(), profile_key, limit=limit
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.get("/industrial/profiles/{profile_key}/telemetry")
def get_industrial_telemetry(profile_key: str,
                             hours: int = Query(default=24, ge=1, le=8760),
                             signal_key: str | None = None):
    try:
        return industrial_gateway_module.telemetry_history(
            _get_conn(), profile_key, hours=hours, signal_key=signal_key
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error


@app.get("/diagnostics")
def get_diagnostics():
    return diagnostics_module.build(
        _get_conn(), CONFIG_PATH,
        mqtt_connected=bool(_mqtt_client and _mqtt_client.is_connected()),
        cv_watcher_running=_cv_observer is not None,
    )


@app.get("/deployment")
def get_deployment():
    return deployment_module.build(CONFIG_PATH)


def _backup_dir() -> Path:
    return Path(os.getenv("HIVE_BACKUP_DIR", str(resilience_module.DEFAULT_BACKUP_DIR)))


@app.get("/resilience")
def get_resilience():
    return resilience_module.snapshot(db_path=DB_PATH, backup_dir=_backup_dir())


@app.post("/resilience/backups")
def post_resilience_backup(request: Request):
    try:
        return resilience_module.create_backup(
            db_path=DB_PATH,
            root=Path(__file__).parent.parent,
            backup_dir=_backup_dir(),
            app_version=APP_VERSION,
            actor=_principal_name(request),
        )
    except (OSError, sqlite3.Error, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.post("/resilience/backups/{filename}/verify")
def post_resilience_verify(filename: str):
    if Path(filename).name != filename or not filename.startswith("hive-backup-"):
        raise HTTPException(400, "Invalid backup filename")
    try:
        return resilience_module.verify_backup(_backup_dir() / filename)
    except (OSError, sqlite3.Error, ValueError, zipfile.BadZipFile) as error:
        raise HTTPException(400, str(error)) from error


@app.get("/config")
def get_config():
    return config_editor_module.load(CONFIG_PATH)


@app.put("/config")
def put_config(payload: SiteConfigUpdate):
    try:
        return config_editor_module.save(
            CONFIG_PATH, payload.model_dump(exclude_none=True)
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/remote-setup/plan/{machine_key}")
def get_remote_setup_plan(machine_key: str):
    try:
        return remote_setup_module.plan(_get_conn(), CONFIG_PATH, machine_key)
    except ValueError as error:
        raise HTTPException(404, str(error)) from error


@app.get("/remote-setup/snapshot")
def get_remote_setup_snapshot():
    return remote_setup_module.snapshot(_get_conn(), CONFIG_PATH)


@app.post("/remote-setup/identity")
def post_remote_setup_identity():
    try:
        return remote_setup_module.generate_identity()
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/remote-setup/test-connection")
def post_remote_connection_test(payload: RemoteConnectionRequest):
    try:
        return remote_setup_module.test_connection(payload.model_dump(exclude_none=True))
    except (TypeError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.post("/remote-setup/scan-host-key")
def post_remote_host_key_scan(payload: RemoteMachineRequest):
    try:
        return remote_setup_module.scan_host_key(
            _get_conn(), CONFIG_PATH, payload.model_dump(exclude_none=True)
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/remote-setup/trust-host")
def post_remote_host_trust(payload: RemoteTrustRequest, request: Request):
    try:
        return remote_setup_module.trust_host(
            _get_conn(), CONFIG_PATH, payload.model_dump(exclude_none=True),
            _principal_name(request),
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.delete("/remote-setup/trust-host/{machine_key}")
def delete_remote_host_trust(machine_key: str, request: Request):
    try:
        return remote_setup_module.forget_host(
            _get_conn(), machine_key, _principal_name(request)
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/remote-setup/authenticate")
def post_remote_authentication(payload: RemoteMachineRequest, request: Request):
    try:
        return remote_setup_module.authenticate(
            _get_conn(), CONFIG_PATH, payload.model_dump(exclude_none=True),
            _principal_name(request),
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/remote-setup/detect-folders")
def post_remote_folder_detection(payload: RemoteMachineRequest, request: Request):
    try:
        return remote_setup_module.detect_folders(
            _get_conn(), CONFIG_PATH, payload.model_dump(exclude_none=True),
            _principal_name(request),
        )
    except (KeyError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.post("/remote-setup/install-agent")
def post_remote_agent_install(payload: RemoteMachineRequest, request: Request):
    try:
        if payload.execute:
            raise ValueError("Use the administrator-only live install endpoint")
        return remote_setup_module.install_agent(
            _get_conn(), CONFIG_PATH, payload.model_dump(exclude_none=True),
            _principal_name(request),
        )
    except (KeyError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.post("/remote-setup/install-agent/live")
def post_remote_agent_live_install(payload: RemoteMachineRequest, request: Request):
    try:
        data = payload.model_dump(exclude_none=True)
        data["execute"] = True
        return remote_setup_module.install_agent(
            _get_conn(), CONFIG_PATH, data, _principal_name(request),
        )
    except (KeyError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.post("/remote-setup/restart-agent")
def post_remote_agent_restart(payload: RemoteMachineRequest, request: Request):
    try:
        return remote_setup_module.restart_agent(
            _get_conn(), CONFIG_PATH, payload.model_dump(exclude_none=True),
            _principal_name(request),
        )
    except (KeyError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.post("/remote-setup/fetch-log")
def post_remote_agent_log(payload: RemoteMachineRequest, request: Request):
    try:
        return remote_setup_module.fetch_log(
            _get_conn(), CONFIG_PATH, payload.model_dump(exclude_none=True),
            _principal_name(request),
        )
    except (KeyError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.get("/operations/summary")
def get_operations_summary():
    return operations_module.summary(_get_conn())


@app.get("/downtime")
def get_downtime(status: Optional[str] = None):
    return operations_module.list_downtime(_get_conn(), status)


@app.post("/downtime")
def post_downtime(payload: DowntimeCreate):
    try:
        return operations_module.create_downtime(
            _get_conn(), payload.model_dump(exclude_none=True)
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/downtime/{downtime_id}/close")
def close_downtime(downtime_id: int, payload: CloseRequest | None = None):
    try:
        body = payload.model_dump(exclude_none=True) if payload else None
        return operations_module.close_downtime(_get_conn(), downtime_id, body)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error


@app.get("/tooling")
def get_tooling_snapshot():
    return tooling_module.snapshot(_get_conn(), commit=True)


@app.get("/tooling/tools/{tool_key}")
def get_tooling_asset(tool_key: str):
    try:
        return tooling_module.get_asset(_get_conn(), tool_key)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error


@app.post("/tooling/tools")
def post_tooling_asset(payload: ToolAssetCreate):
    try:
        return tooling_module.create_asset(_get_conn(), payload.model_dump(exclude_none=True))
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except (sqlite3.IntegrityError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.patch("/tooling/tools/{tool_key}")
def patch_tooling_asset(tool_key: str, payload: ToolAssetUpdate):
    try:
        return tooling_module.update_asset(
            _get_conn(), tool_key, payload.model_dump(exclude_unset=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except (sqlite3.IntegrityError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.post("/tooling/tools/{tool_key}/usage")
def post_tooling_usage(tool_key: str, payload: ToolUsageCreate):
    try:
        return tooling_module.record_usage(
            _get_conn(), tool_key, payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except (sqlite3.IntegrityError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.post("/tooling/tools/{tool_key}/actions")
def post_tooling_action(tool_key: str, payload: ToolActionCreate):
    try:
        return tooling_module.action(_get_conn(), tool_key, payload.model_dump(exclude_none=True))
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except (sqlite3.IntegrityError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.post("/tooling/tools/{tool_key}/service")
def post_tooling_service(tool_key: str, payload: ToolServiceCreate):
    try:
        return tooling_module.record_service(
            _get_conn(), tool_key, payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except (sqlite3.IntegrityError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.put("/tooling/tools/{tool_key}/program-mappings")
def put_tooling_program_mapping(tool_key: str, payload: ToolProgramMappingCreate):
    try:
        return tooling_module.upsert_program_mapping(
            _get_conn(), tool_key, payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except (sqlite3.IntegrityError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.post("/tooling/sync")
def post_tooling_sync():
    try:
        return tooling_module.sync(_get_conn())
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/maintenance/work-orders")
def get_work_orders(status: Optional[str] = None):
    return maintenance_module.list_work_orders(_get_conn(), status)


@app.post("/maintenance/work-orders")
def post_work_order(payload: WorkOrderCreate):
    try:
        created = operations_module.create_work_order(
            _get_conn(), payload.model_dump(exclude_none=True)
        )
        return maintenance_module.get_work_order(_get_conn(), created["id"])
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/maintenance/snapshot")
def get_maintenance_snapshot():
    return maintenance_module.snapshot(_get_conn())


@app.post("/maintenance/sync")
def post_maintenance_sync():
    result = maintenance_module.sync(_get_conn())
    return {**result, "snapshot": maintenance_module.snapshot(_get_conn(), ensure_defaults=False)}


@app.get("/maintenance/plans")
def get_maintenance_plans():
    return maintenance_module.list_plans(_get_conn())


@app.post("/maintenance/plans")
def post_maintenance_plan(payload: MaintenancePlanCreate):
    try:
        return maintenance_module.create_plan(
            _get_conn(), payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.put("/maintenance/plans/{plan_id}")
def put_maintenance_plan(plan_id: int, payload: MaintenancePlanUpdate):
    try:
        return maintenance_module.update_plan(
            _get_conn(), plan_id, payload.model_dump(exclude_unset=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/maintenance/conditions")
def post_maintenance_condition(payload: MaintenanceConditionCreate):
    try:
        return maintenance_module.record_condition_signal(
            _get_conn(), payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/maintenance/spares")
def get_maintenance_spares():
    return maintenance_module.list_spares(_get_conn())


@app.post("/maintenance/spares")
def post_maintenance_spare(payload: SparePartCreate):
    try:
        return maintenance_module.create_spare_part(
            _get_conn(), payload.model_dump(exclude_none=True)
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.put("/maintenance/spares/{part_key}/stock")
def put_maintenance_spare_stock(part_key: str, payload: SpareStockUpdate):
    try:
        return maintenance_module.set_spare_stock(
            _get_conn(), part_key, payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/maintenance/work-orders/{work_order_id}")
def get_maintenance_work_order(work_order_id: int):
    try:
        return maintenance_module.get_work_order(_get_conn(), work_order_id)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error


@app.put("/maintenance/work-orders/{work_order_id}")
def put_maintenance_work_order(work_order_id: int, payload: MaintenanceWorkOrderUpdate):
    try:
        return maintenance_module.update_work_order(
            _get_conn(), work_order_id, payload.model_dump(exclude_unset=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/maintenance/work-orders/{work_order_id}/complete")
def complete_maintenance_work_order(work_order_id: int, payload: MaintenanceCompletion):
    try:
        return maintenance_module.complete_work_order(
            _get_conn(), work_order_id, payload.model_dump(exclude_none=True)
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/quality/checks")
def get_quality_checks(limit: int = Query(100, le=500)):
    return operations_module.list_quality_checks(_get_conn(), limit)


@app.post("/quality/checks")
def post_quality_check(payload: QualityCheckCreate):
    try:
        return operations_module.create_quality_check(
            _get_conn(), payload.model_dump(exclude_none=True)
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/rework")
def get_rework(status: Optional[str] = None):
    return operations_module.list_rework(_get_conn(), status)


@app.post("/rework/{rework_id}/close")
def close_rework(rework_id: int, payload: CloseRequest | None = None):
    try:
        body = payload.model_dump(exclude_none=True) if payload else None
        return operations_module.close_rework(_get_conn(), rework_id, body)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error


@app.get("/barcode/events")
def get_barcode_events(limit: int = Query(100, le=500)):
    return operations_module.list_barcode_events(_get_conn(), limit)


@app.post("/barcode/events")
def post_barcode_event(payload: BarcodeEventCreate):
    return operations_module.create_barcode_event(
        _get_conn(), payload.model_dump(exclude_none=True)
    )


@app.post("/connectors/ottimo/placeholder")
def post_ottimo_placeholder(payload: OttimoPlaceholder):
    normalized = ottimo_connector.parse_placeholder_event(
        payload.model_dump(exclude_none=True)
    )
    validated = BarcodeEventCreate.model_validate(normalized)
    return operations_module.create_barcode_event(
        _get_conn(), validated.model_dump(exclude_none=True)
    )


@app.post("/connectors/cabinet-vision-sql/placeholder")
def post_cv_sql_placeholder(rows: list[CvSqlRow]):
    return cv_sql_connector.upsert_normalized_rows(
        _get_conn(), [row.model_dump(exclude_none=True) for row in rows]
    )


@app.post("/cycle-times/calibrate")
def calibrate_machine(machine_key: str, records: list[dict]):
    """
    Fit cycle time coefficients from timing data.
    Body: list of part dicts with actual_seconds field added.
    Returns fitted coefficients — paste into config/cycle_times.yaml.
    """
    result = cycle_time_module.calibrate(records, machine_key)
    return result


@app.get("/report/shift", response_class=HTMLResponse)
def get_shift_report(date: Optional[str] = None):
    conn   = _get_conn()
    report = shift_report_module.build(conn, date)
    return shift_report_module.render_html(report)


@app.get("/oee")
def get_oee_all(window_hours: int = Query(8, ge=1, le=24)):
    conn = _get_conn()
    results = oee_module.calculate_all(conn, window_hours)
    return [asdict(r) for r in results]


@app.get("/oee/{machine_key}")
def get_oee(machine_key: str, window_hours: int = Query(8, ge=1, le=24)):
    conn = _get_conn()
    row  = conn.execute(
        "SELECT id FROM machines WHERE machine_key=?", (machine_key,)
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Machine '{machine_key}' not found")
    result = oee_module.calculate(conn, row["id"], window_hours)
    return asdict(result)


# ── SSE stream ────────────────────────────────────────────────────────────────

async def _event_generator() -> AsyncGenerator[str, None]:
    q = mqtt_bridge.subscribe_events()

    try:
        # Send current machine state snapshot on connect
        for state in _machine_state.values():
            yield f"data: {json.dumps({**state, '_type': 'snapshot'})}\n\n"

        while True:
            while not q.empty():
                event = q.get_nowait()
                yield f"data: {json.dumps(event)}\n\n"

            yield ": heartbeat\n\n"
            await asyncio.sleep(1)
    finally:
        mqtt_bridge.unsubscribe_events(q)


@app.get("/events/stream")
async def events_stream():
    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",  # disable nginx buffering
            "Access-Control-Allow-Origin": "*",
        },
    )


# ── Simulate endpoint (dev/demo) ──────────────────────────────────────────────

@app.post("/events/simulate")
def simulate_event(machine_key: str, event_type: str,
                   power_w: Optional[float] = None,
                   cnc_file: Optional[str] = None):
    """Inject a fake event — useful for demoing the dashboard without real machines."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT id FROM machines WHERE machine_key=?", (machine_key,)
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Machine '{machine_key}' not found")

    now     = datetime.now(timezone.utc).isoformat()
    payload = {
        "machine_key": machine_key,
        "event_type":  event_type,
        "power_w":     power_w,
        "cnc_file":    cnc_file,
        "ts":          now,
        "source":      "simulate",
    }

    result = event_pipeline.ingest_event(conn, payload)
    if result["status"] == "rejected":
        raise HTTPException(422, result["reason"])
    event = {**result["event"], "event_id": result.get("event_id"),
             "part_id": result.get("part_id")}
    _machine_state[machine_key] = event
    if result["status"] == "accepted":
        mqtt_bridge.publish_event(event)

    return {"ok": True, "status": result["status"], "event": event}


if DASHBOARD_DIST.exists():
    app.mount("/", StaticFiles(directory=DASHBOARD_DIST, html=True), name="dashboard")
