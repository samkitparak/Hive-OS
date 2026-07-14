"""Validated API request models for HIVE OS write endpoints."""

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DowntimeCreate(RequestModel):
    machine_key: str = Field(min_length=1)
    reason_code: Optional[str] = None
    status: Literal["open", "closed"] = "open"
    notes: Optional[str] = None
    started_at: Optional[str] = None


class CloseRequest(RequestModel):
    notes: Optional[str] = None
    ended_at: Optional[str] = None
    closed_at: Optional[str] = None


class WorkOrderCreate(RequestModel):
    title: str = Field(min_length=1)
    machine_key: Optional[str] = None
    description: Optional[str] = None
    priority: Literal["low", "medium", "high", "urgent"] = "medium"
    status: Literal["open", "in_progress", "done", "cancelled"] = "open"
    source: str = "manual"
    due_date: Optional[str] = None


class QualityCheckCreate(RequestModel):
    result: Literal["pass", "fail", "rework"]
    job_name: Optional[str] = None
    part_id: Optional[int] = None
    part_name: Optional[str] = None
    machine_key: Optional[str] = None
    defect_code: Optional[str] = None
    assigned_area: Optional[str] = None
    inspector: Optional[str] = None
    notes: Optional[str] = None
    photo_path: Optional[str] = None
    source: str = "manual"
    ts: Optional[str] = None


class BarcodeEventCreate(RequestModel):
    barcode: str = Field(min_length=1)
    job_name: Optional[str] = None
    part_id: Optional[int] = None
    part_name: Optional[str] = None
    station: Optional[str] = None
    event_type: Literal[
        "route_arrival", "operation_start", "operation_complete", "part_complete",
        "qc_pass", "qc_fail", "packed", "dispatched", "unknown"
    ] = "unknown"
    operator: Optional[str] = None
    source: str = "manual"
    raw_payload: Any = None
    ts: Optional[str] = None
    notes: Optional[str] = None


class OttimoPlaceholder(RequestModel):
    barcode: str = Field(min_length=1)
    event: Optional[str] = None
    event_type: Optional[str] = None
    job_name: Optional[str] = None
    part_name: Optional[str] = None
    station: Optional[str] = None
    operator: Optional[str] = None
    ts: Optional[str] = None
    notes: Optional[str] = None


class CvSqlRow(BaseModel):
    model_config = ConfigDict(extra="allow")

    job_name: Optional[str] = None
    client_name: Optional[str] = None
    room_name: Optional[str] = None
    job_date: Optional[str] = None
    part_name: Optional[str] = None
    material: Optional[str] = None
    length_mm: Optional[float] = None
    width_mm: Optional[float] = None
    thickness_mm: Optional[float] = None
    qty: int = Field(default=1, ge=1)
    cnc_file_back: Optional[str] = None
    cnc_file_front: Optional[str] = None
    has_cnc: Optional[bool] = None


class RemoteMachineRequest(RequestModel):
    machine_key: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$")
    host: Optional[str] = None
    log_folder: Optional[str] = None
    username: Optional[str] = None
    port: int = Field(default=22, ge=1, le=65535)


class RemoteConnectionRequest(RequestModel):
    host: str = Field(min_length=1)
    port: int = Field(default=22, ge=1, le=65535)
    machine_key: Optional[str] = None
    log_folder: Optional[str] = None
    username: Optional[str] = None


class MqttConfig(RequestModel):
    broker_host: str = Field(min_length=1)
    broker_port: int = Field(ge=1, le=65535)
    keepalive: int = Field(default=60, ge=5, le=3600)
    topic_prefix: str = Field(default="hive/machines", min_length=1)


class EnergyDefaults(RequestModel):
    on_threshold_w: float = Field(ge=0)
    idle_threshold_w: float = Field(ge=0)
    poll_interval_s: float = Field(default=5, gt=0)


class EnergyMeterConfig(RequestModel):
    machine_key: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$")
    label: str = Field(min_length=1)
    modbus_host: str = Field(min_length=1)
    modbus_port: int = Field(default=502, ge=1, le=65535)
    unit_id: int = Field(default=1, ge=0, le=247)
    on_threshold_w: Optional[float] = Field(default=None, ge=0)
    idle_threshold_w: Optional[float] = Field(default=None, ge=0)


class MaestroAgentConfig(RequestModel):
    machine_key: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$")
    label: str = Field(min_length=1)
    host: str = Field(min_length=1)
    log_folder: str = Field(min_length=1)
    cnc_folder: Optional[str] = None


class SiteConfigUpdate(RequestModel):
    mqtt: MqttConfig
    cv_watch_folder: str = Field(min_length=1)
    energy_defaults: EnergyDefaults
    energy_meters: list[EnergyMeterConfig]
    maestro_agents: list[MaestroAgentConfig]


class CommissioningLogRequest(RequestModel):
    machine_key: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$")
    log_text: str = Field(min_length=1, max_length=5_000_000)
    persist: bool = False
    site_timezone: str = Field(default="Asia/Kolkata", min_length=1, max_length=64)


class DigitalTwinRequest(RequestModel):
    job_names: Optional[list[str]] = None
    policies: Optional[list[str]] = None
    stochastic: bool = False
    seed: int = Field(default=1, ge=0, le=2_147_483_647)


class ProductionOrderUpdate(RequestModel):
    expected_version: Optional[int] = Field(default=None, ge=1)
    status: Optional[Literal[
        "draft", "ready", "released", "in_progress", "hold", "completed", "cancelled"
    ]] = None
    due_at: Optional[str] = None
    priority: Optional[int] = Field(default=None, ge=1, le=100)
    planned_start_at: Optional[str] = None
    external_order_id: Optional[str] = None
    notes: Optional[str] = None
    actor: str = Field(default="operator", min_length=1)


class PartRouteUpdate(RequestModel):
    machine_keys: list[str] = Field(min_length=1)
    actor: str = Field(default="operator", min_length=1)
    notes: Optional[str] = None


class RouteExceptionDecision(RequestModel):
    status: Literal["accepted", "ignored", "corrected"]
    actor: str = Field(min_length=1)
    notes: Optional[str] = None


class PlanningScenarioCreate(RequestModel):
    name: Optional[str] = None
    created_by: str = Field(default="operator", min_length=1)
    job_names: Optional[list[str]] = None
    policies: Optional[list[Literal["current", "fifo", "edd", "spt", "material_batch"]]] = None
    stochastic: bool = False
    seed: int = Field(default=1, ge=0, le=2_147_483_647)


class PlanningDecision(RequestModel):
    decision: Literal["approve", "reject"]
    actor: str = Field(min_length=1)
    selected_policy: Optional[Literal["current", "fifo", "edd", "spt", "material_batch"]] = None
    notes: Optional[str] = None


class ExecutionActionRequest(RequestModel):
    action: Literal["dispatch", "acknowledge", "start", "complete", "hold", "resume", "cancel"]
    expected_version: Optional[int] = Field(default=None, ge=1)
    quantity: Optional[int] = Field(default=None, ge=1)
    good_qty: Optional[int] = Field(default=None, ge=0)
    scrap_qty: Optional[int] = Field(default=None, ge=0)
    assigned_operator: Optional[str] = None
    actor: str = Field(default="operator", min_length=1)
    notes: Optional[str] = None
    idempotency_key: Optional[str] = Field(default=None, min_length=1, max_length=200)


class ExecutionExceptionDecision(RequestModel):
    status: Literal["accepted", "corrected", "ignored"]
    actor: str = Field(min_length=1)
    notes: Optional[str] = None


class MaterialStockUpdate(RequestModel):
    on_hand_sheets: float = Field(ge=0)
    lot_code: str = Field(default="MANUAL-BALANCE", min_length=1)
    location: Optional[str] = None
    sheet_length_mm: Optional[float] = Field(default=None, gt=0)
    sheet_width_mm: Optional[float] = Field(default=None, gt=0)
    yield_factor: Optional[float] = Field(default=None, gt=0, le=1)
    verified: bool = False
    actor: str = Field(default="operator", min_length=1)


class LaborRoleUpdate(RequestModel):
    headcount: int = Field(ge=0)
    verified: bool = False
    actor: str = Field(default="operator", min_length=1)


class ToolPoolUpdate(RequestModel):
    total_qty: int = Field(ge=0)
    available_qty: int = Field(ge=0)
    verified: bool = False
    actor: str = Field(default="operator", min_length=1)


class MachineResourceProfileUpdate(RequestModel):
    role_key: str = Field(min_length=1)
    labor_qty: int = Field(ge=0)
    pool_key: str = Field(min_length=1)
    tool_qty: int = Field(ge=0)
    machine_capacity: int = Field(ge=1)
    verified: bool = False
    actor: str = Field(default="operator", min_length=1)


class FactoryCalendarUpdate(RequestModel):
    weekdays: list[int] = Field(min_length=1)
    start_time: str = Field(min_length=4)
    end_time: str = Field(min_length=4)
    timezone: str = Field(default="Asia/Kolkata", min_length=1)
    capacity: int = Field(default=1, ge=1)
    verified: bool = False
    actor: str = Field(default="operator", min_length=1)


class WipBufferUpdate(RequestModel):
    capacity_qty: int = Field(ge=1)
    current_qty: int = Field(ge=0)
    verified: bool = False
    actor: str = Field(default="operator", min_length=1)


class ResourceUnavailabilityCreate(RequestModel):
    resource_type: Literal["factory", "machine", "labor_role", "tool_pool"] = "machine"
    resource_key: str = Field(min_length=1)
    starts_at: str = Field(min_length=1)
    ends_at: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    source: str = "manual"
    work_order_id: Optional[int] = Field(default=None, ge=1)
    actor: str = Field(default="operator", min_length=1)
