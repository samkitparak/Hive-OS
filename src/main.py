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
import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

import db as db_module
import mqtt_bridge
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
import learning as learning_module
import routing as routing_module
import commissioning as commissioning_module
import event_pipeline
import optimization as optimization_module
import planning as planning_module
import production_control as production_control_module
import resources as resources_module
import execution as execution_module
import identity as identity_module
import diagnostics as diagnostics_module
import deployment as deployment_module
import config_editor as config_editor_module
import remote_setup as remote_setup_module
import operations as operations_module
import maintenance as maintenance_module
import connectors as connectors_module
import industrial_gateway as industrial_gateway_module
import energy_intelligence as energy_intelligence_module
import ottimo_connector
import cv_sql_connector
from api_models import (
    BarcodeEventCreate,
    CloseRequest,
    CommissioningLogRequest,
    ConnectorAnalyzeRequest,
    ConnectorApprovalRequest,
    ConnectorImportRequest,
    ConnectorProfileUpdate,
    ConnectorSyncRequest,
    IndustrialApprovalRequest,
    IndustrialMqttProbeRequest,
    IndustrialProbeRequest,
    IndustrialProfileUpdate,
    DigitalTwinRequest,
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
    ProductionOrderUpdate,
    CvSqlRow,
    DowntimeCreate,
    OttimoPlaceholder,
    QualityCheckCreate,
    RemoteConnectionRequest,
    RemoteMachineRequest,
    ResourceUnavailabilityCreate,
    RouteExceptionDecision,
    SiteConfigUpdate,
    SparePartCreate,
    SpareStockUpdate,
    ToolPoolUpdate,
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
APP_VERSION = "0.9.0"


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

# ── App lifecycle ─────────────────────────────────────────────────────────────

_mqtt_client = None
_conn        = None
_cv_observer = None
_event_watch_task = None
_learning_watch_task = None
_industrial_watch_task = None
_route_conn_override = None
_route_connections = threading.local()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _mqtt_client, _conn, _cv_observer, _event_watch_task, _learning_watch_task, _industrial_watch_task
    _conn = init_db(DB_PATH, check_same_thread=False)
    production_control_module.sync_all(_conn)
    resources_module.sync_defaults(_conn)
    identity_module.sync_controlled_orders(_conn)
    execution_module.sync(_conn)
    maintenance_module.sync_defaults(_conn)
    maintenance_module.sync(_conn)
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
    yield
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
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)
app.add_middleware(ApiPrefixMiddleware)


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
        "current": vars(report.current) if report.current else None,
        "candidate": vars(report.candidate) if report.candidate else None,
        "machines": [vars(machine) for machine in report.machines],
    }


@app.get("/data-quality")
def get_data_quality(window_hours: int = Query(8, ge=1, le=168)):
    return data_quality_module.build(_get_conn(), window_hours)


@app.get("/optimization")
def get_optimization(window_hours: int = Query(8, ge=1, le=24)):
    return optimization_module.build(_get_conn(), window_hours)


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
def post_commissioning_log_analysis(payload: CommissioningLogRequest):
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


@app.get("/config")
def get_config():
    return config_editor_module.load(CONFIG_PATH)


@app.put("/config")
def put_config(payload: SiteConfigUpdate):
    return config_editor_module.save(
        CONFIG_PATH, payload.model_dump(exclude_none=True)
    )


@app.get("/remote-setup/plan/{machine_key}")
def get_remote_setup_plan(machine_key: str):
    try:
        return remote_setup_module.plan(CONFIG_PATH, machine_key)
    except ValueError as error:
        raise HTTPException(404, str(error)) from error


@app.post("/remote-setup/test-connection")
def post_remote_connection_test(payload: RemoteConnectionRequest):
    try:
        return remote_setup_module.test_connection(payload.model_dump(exclude_none=True))
    except (TypeError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.post("/remote-setup/detect-folders")
def post_remote_folder_detection(payload: RemoteMachineRequest):
    try:
        return remote_setup_module.detect_folders(
            CONFIG_PATH, payload.model_dump(exclude_none=True)
        )
    except (KeyError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.post("/remote-setup/install-agent")
def post_remote_agent_install(payload: RemoteMachineRequest):
    try:
        return remote_setup_module.install_agent(
            CONFIG_PATH, payload.model_dump(exclude_none=True)
        )
    except (KeyError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.post("/remote-setup/restart-agent")
def post_remote_agent_restart(payload: RemoteMachineRequest):
    try:
        return remote_setup_module.restart_agent(payload.model_dump(exclude_none=True))
    except KeyError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/remote-setup/fetch-log")
def post_remote_agent_log(payload: RemoteMachineRequest):
    try:
        return remote_setup_module.fetch_log(payload.model_dump(exclude_none=True))
    except KeyError as error:
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
