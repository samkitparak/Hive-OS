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
from fastapi.responses import HTMLResponse, StreamingResponse
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
import diagnostics as diagnostics_module
import deployment as deployment_module
import config_editor as config_editor_module
import remote_setup as remote_setup_module
import operations as operations_module
import ottimo_connector
import cv_sql_connector
from api_models import (
    BarcodeEventCreate,
    CloseRequest,
    CvSqlRow,
    DowntimeCreate,
    OttimoPlaceholder,
    QualityCheckCreate,
    RemoteConnectionRequest,
    RemoteMachineRequest,
    SiteConfigUpdate,
    WorkOrderCreate,
)
from db import DB_PATH, init_db

log = logging.getLogger("main")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s")

CONFIG_PATH = Path(__file__).parent.parent / "config" / "machines.yaml"
DASHBOARD_DIST = Path(__file__).parent.parent / "dashboard" / "dist"
APP_VERSION = "0.2.0"


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
_route_conn_override = None
_route_connections = threading.local()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _mqtt_client, _conn, _cv_observer, _event_watch_task
    _conn = init_db(DB_PATH, check_same_thread=False)
    try:
        _mqtt_client = mqtt_bridge.start(_conn, CONFIG_PATH)
        log.info("MQTT bridge started")
    except Exception as e:
        log.warning("MQTT bridge failed to start (no broker?): %s", e)
    _cv_observer = cv_watcher.start(_conn, CONFIG_PATH)
    _event_watch_task = asyncio.create_task(_watch_events())
    yield
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
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
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
                  j.total_parts, c.name as client_name
           FROM jobs j LEFT JOIN clients c ON j.client_id=c.id
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
        "machines": [vars(machine) for machine in report.machines],
    }


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
    return operations_module.list_work_orders(_get_conn(), status)


@app.post("/maintenance/work-orders")
def post_work_order(payload: WorkOrderCreate):
    try:
        return operations_module.create_work_order(
            _get_conn(), payload.model_dump(exclude_none=True)
        )
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
    row  = conn.execute(
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

    part_id = None
    if cnc_file:
        stem = cnc_file.replace(".xcs", "")
        r    = conn.execute(
            "SELECT id FROM parts WHERE cnc_file_back=? OR cnc_file_front=? LIMIT 1",
            (stem, stem)
        ).fetchone()
        part_id = r["id"] if r else None

    conn.execute(
        """INSERT INTO machine_events
           (machine_id, event_type, part_id, cnc_file, raw_payload, ts)
           VALUES (?,?,?,?,?,?)""",
        (row["id"], event_type, part_id, cnc_file, json.dumps(payload), now)
    )
    conn.commit()

    _machine_state[machine_key] = payload
    mqtt_bridge.publish_event(payload)

    return {"ok": True, "event": payload}


if DASHBOARD_DIST.exists():
    app.mount("/", StaticFiles(directory=DASHBOARD_DIST, html=True), name="dashboard")
