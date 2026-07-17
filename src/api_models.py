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


class MaintenanceTaskInput(RequestModel):
    task_key: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$")
    title: str = Field(min_length=1)
    instructions: Optional[str] = None
    response_type: Literal["check", "pass_fail", "number", "text"] = "check"
    unit: Optional[str] = None
    required: bool = True


class MaintenancePlanSpareInput(RequestModel):
    part_key: str = Field(min_length=1)
    quantity: float = Field(gt=0)
    required: bool = True


class MaintenancePlanCreate(RequestModel):
    plan_key: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$")
    machine_key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: Optional[str] = None
    strategy: Literal["calendar", "usage", "hybrid", "condition"] = "calendar"
    runtime_basis: Literal["powered", "cycle"] = "powered"
    interval_days: Optional[float] = Field(default=None, gt=0)
    interval_runtime_h: Optional[float] = Field(default=None, gt=0)
    interval_cycles: Optional[int] = Field(default=None, gt=0)
    warning_days: float = Field(default=7, ge=0)
    warning_runtime_h: float = Field(default=25, ge=0)
    warning_cycles: int = Field(default=100, ge=0)
    estimated_duration_min: int = Field(default=60, gt=0)
    criticality: Literal["low", "medium", "high", "critical"] = "medium"
    requires_shutdown: bool = True
    loto_required: bool = True
    condition_metric: Optional[str] = None
    condition_operator: Optional[Literal["gt", "gte", "lt", "lte"]] = None
    condition_threshold: Optional[float] = None
    active: bool = True
    verified: bool = False
    anchor_at: Optional[str] = None
    source: str = "manual"
    tasks: list[MaintenanceTaskInput] = Field(default_factory=list)
    spares: list[MaintenancePlanSpareInput] = Field(default_factory=list)


class MaintenancePlanUpdate(RequestModel):
    expected_version: Optional[int] = Field(default=None, ge=1)
    title: Optional[str] = Field(default=None, min_length=1)
    description: Optional[str] = None
    strategy: Optional[Literal["calendar", "usage", "hybrid", "condition"]] = None
    runtime_basis: Optional[Literal["powered", "cycle"]] = None
    interval_days: Optional[float] = Field(default=None, gt=0)
    interval_runtime_h: Optional[float] = Field(default=None, gt=0)
    interval_cycles: Optional[int] = Field(default=None, gt=0)
    warning_days: Optional[float] = Field(default=None, ge=0)
    warning_runtime_h: Optional[float] = Field(default=None, ge=0)
    warning_cycles: Optional[int] = Field(default=None, ge=0)
    estimated_duration_min: Optional[int] = Field(default=None, gt=0)
    criticality: Optional[Literal["low", "medium", "high", "critical"]] = None
    requires_shutdown: Optional[bool] = None
    loto_required: Optional[bool] = None
    condition_metric: Optional[str] = None
    condition_operator: Optional[Literal["gt", "gte", "lt", "lte"]] = None
    condition_threshold: Optional[float] = None
    active: Optional[bool] = None
    verified: Optional[bool] = None
    anchor_at: Optional[str] = None
    actor: str = Field(default="operator", min_length=1)
    tasks: Optional[list[MaintenanceTaskInput]] = None
    spares: Optional[list[MaintenancePlanSpareInput]] = None


class MaintenanceConditionCreate(RequestModel):
    machine_key: str = Field(min_length=1)
    metric_key: str = Field(min_length=1)
    value: float
    unit: Optional[str] = None
    severity: Literal["info", "warning", "critical"] = "warning"
    source: str = Field(default="manual", min_length=1)
    evidence_type: Optional[str] = None
    evidence_id: Optional[int] = None
    observed_at: Optional[str] = None


class SparePartCreate(RequestModel):
    part_key: str = Field(min_length=1, pattern=r"^[a-zA-Z0-9_.-]+$")
    name: str = Field(min_length=1)
    manufacturer: Optional[str] = None
    manufacturer_part_number: Optional[str] = None
    unit: str = Field(default="each", min_length=1)
    criticality: Literal["low", "medium", "high", "critical"] = "medium"
    reorder_point: float = Field(default=0, ge=0)
    reorder_qty: float = Field(default=0, ge=0)
    lead_time_days: Optional[int] = Field(default=None, ge=0)
    preferred_supplier: Optional[str] = None
    source: str = "manual"
    verified: bool = False


class SpareStockUpdate(RequestModel):
    on_hand_qty: float = Field(ge=0)
    location: str = Field(default="maintenance_store", min_length=1)
    unit_cost: Optional[float] = Field(default=None, ge=0)
    verified: bool = False
    actor: str = Field(default="operator", min_length=1)
    notes: Optional[str] = None


class MaintenanceWorkOrderUpdate(RequestModel):
    status: Optional[Literal["open", "in_progress", "cancelled"]] = None
    scheduled_start_at: Optional[str] = None
    scheduled_end_at: Optional[str] = None
    actor: str = Field(default="operator", min_length=1)


class MaintenanceTaskResult(RequestModel):
    task_id: int = Field(ge=1)
    result: str = Field(min_length=1)
    value_text: Optional[str] = None
    value_number: Optional[float] = None
    notes: Optional[str] = None


class MaintenanceCompletion(RequestModel):
    completed_by: str = Field(min_length=1)
    completed_at: Optional[str] = None
    notes: Optional[str] = None
    loto_verified: bool = False
    loto_verified_by: Optional[str] = None
    task_results: list[MaintenanceTaskResult] = Field(default_factory=list)


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


class ConnectorProfileUpdate(RequestModel):
    expected_version: Optional[int] = Field(default=None, ge=1)
    enabled: Optional[bool] = None
    credential_env: Optional[str] = Field(default=None, max_length=128)
    settings: Optional[dict[str, Any]] = None
    actor: str = Field(default="operator", min_length=1)


class ConnectorAnalyzeRequest(RequestModel):
    records: list[dict[str, Any]] = Field(default_factory=list, max_length=10000)
    mapping: Optional[dict[str, Any]] = None
    log_text: Optional[str] = Field(default=None, max_length=5_000_000)
    file_name: Optional[str] = Field(default=None, max_length=255)
    scope_key: Optional[str] = Field(default=None, max_length=128)
    actor: str = Field(default="operator", min_length=1)


class ConnectorApprovalRequest(RequestModel):
    run_id: int = Field(ge=1)
    expected_version: int = Field(ge=1)
    actor: str = Field(default="operator", min_length=1)
    enable: bool = False


class ConnectorImportRequest(RequestModel):
    records: list[dict[str, Any]] = Field(min_length=1, max_length=10000)
    file_name: Optional[str] = Field(default=None, max_length=255)
    actor: str = Field(default="operator", min_length=1)


class ConnectorSyncRequest(RequestModel):
    actor: str = Field(default="operator", min_length=1)


class IndustrialProfileUpdate(RequestModel):
    expected_version: Optional[int] = Field(default=None, ge=1)
    protocol: Optional[Literal["modbus_tcp", "opcua", "mqtt_json"]] = None
    endpoint: Optional[str] = Field(default=None, max_length=512)
    credential_env: Optional[str] = Field(default=None, max_length=128)
    poll_interval_s: Optional[float] = Field(default=None, ge=1, le=3600)
    settings: Optional[dict[str, Any]] = None
    enabled: Optional[bool] = None
    actor: str = Field(default="operator", min_length=1)


class IndustrialProbeRequest(RequestModel):
    actor: str = Field(default="operator", min_length=1)


class IndustrialMqttProbeRequest(RequestModel):
    topic: str = Field(min_length=1, max_length=256)
    payload: dict[str, Any]
    actor: str = Field(default="operator", min_length=1)


class IndustrialApprovalRequest(RequestModel):
    run_id: int = Field(ge=1)
    expected_version: int = Field(ge=1)
    actor: str = Field(default="operator", min_length=1)
    enable: bool = True


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


class MqttTlsConfig(RequestModel):
    enabled: bool = False
    ca_cert: Optional[str] = None
    client_cert: Optional[str] = None
    client_key: Optional[str] = None


class MqttConfig(RequestModel):
    broker_host: str = Field(min_length=1)
    broker_port: int = Field(ge=1, le=65535)
    keepalive: int = Field(default=60, ge=5, le=3600)
    topic_prefix: str = Field(default="hive/machines", min_length=1)
    require_tls: bool = False
    tls: Optional[MqttTlsConfig] = None


class MqttEnrollmentCreate(RequestModel):
    machine_key: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$")
    validity_days: int = Field(default=397, ge=30, le=825)


class MqttEnrollmentRevoke(RequestModel):
    reason: Optional[str] = Field(default=None, max_length=500)


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


class ForecastRefreshRequest(RequestModel):
    job_names: Optional[list[str]] = None
    policy: Literal["current", "fifo", "edd", "spt", "material_batch"] = "current"
    samples: int = Field(default=50, ge=20, le=200)
    seed: int = Field(default=1, ge=0, le=2_147_483_447)
    force: bool = False


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


class RecoveryAnalyzeRequest(RequestModel):
    actor: str = Field(default="planner", min_length=1)
    force: bool = False


class RecoveryDecision(RequestModel):
    decision: Literal["approve", "reject"]
    actor: str = Field(min_length=1)
    selected_policy: Optional[Literal[
        "current", "fifo", "edd", "spt", "material_batch"
    ]] = None
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


class IdentityMaterializeRequest(RequestModel):
    actor: str = Field(default="operator", min_length=1)


class UnitAliasCreate(RequestModel):
    scheme: str = Field(min_length=1, max_length=40, pattern=r"^[a-zA-Z0-9_-]+$")
    value: str = Field(min_length=1, max_length=500)
    actor: str = Field(min_length=1)
    source: str = Field(default="manual", min_length=1, max_length=80)


class LabelJobCreate(RequestModel):
    order_id: int = Field(ge=1)
    requested_by: str = Field(default="operator", min_length=1)
    only_unprinted: bool = True
    part_ids: Optional[list[int]] = None
    template_key: Literal["part_100x50"] = "part_100x50"
    printer_key: Optional[str] = Field(default=None, max_length=100)
    notes: Optional[str] = None


class LabelPrintConfirmation(RequestModel):
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
    notes: Optional[str] = None


class InventoryItemUpdate(RequestModel):
    name: str = Field(min_length=1, max_length=200)
    category: Literal["edge_band", "hardware", "consumable", "packaging"]
    uom: Literal["m", "each", "kg", "l"]
    usage_factor: float = Field(default=1, ge=1, le=5)
    reorder_point: float = Field(default=0, ge=0)
    safety_stock: float = Field(default=0, ge=0)
    order_multiple: float = Field(default=1, gt=0)
    lead_time_days: int = Field(default=0, ge=0, le=3650)
    unit_cost: Optional[float] = Field(default=None, ge=0)
    preferred_supplier: Optional[str] = Field(default=None, max_length=200)
    verified: bool = False
    actor: str = Field(default="operator", min_length=1)


class InventoryLotBalanceUpdate(RequestModel):
    on_hand_qty: float = Field(ge=0)
    location: Optional[str] = Field(default=None, max_length=200)
    verified: bool = False
    expected_version: Optional[int] = Field(default=None, ge=1)
    movement_type: Literal["receipt", "adjustment"] = "adjustment"
    received_at: Optional[str] = None
    idempotency_key: Optional[str] = Field(default=None, max_length=200)
    notes: Optional[str] = None
    actor: str = Field(default="operator", min_length=1)


class InventoryRequirementUpdate(RequestModel):
    required_qty: float = Field(ge=0)
    verified: bool = False
    notes: Optional[str] = None
    actor: str = Field(default="operator", min_length=1)


class RemnantCreate(RequestModel):
    material_key: str = Field(min_length=1)
    remnant_key: Optional[str] = Field(default=None, min_length=1, max_length=100)
    source_material_lot_id: Optional[int] = Field(default=None, ge=1)
    length_mm: float = Field(gt=0)
    width_mm: float = Field(gt=0)
    thickness_mm: Optional[float] = Field(default=None, gt=0)
    grain_direction: Literal["length", "none"] = "length"
    location: Optional[str] = Field(default=None, max_length=200)
    verified: bool = False
    actor: str = Field(default="operator", min_length=1)


class RemnantUpdate(RequestModel):
    expected_version: int = Field(ge=1)
    status: Literal["available", "hold", "scrapped"]
    location: Optional[str] = Field(default=None, max_length=200)
    verified: bool = False
    notes: Optional[str] = None
    actor: str = Field(default="operator", min_length=1)


class ProcurementSupplierUpdate(RequestModel):
    name: str = Field(min_length=1, max_length=200)
    legal_name: Optional[str] = Field(default=None, max_length=240)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    lead_time_days: int = Field(default=0, ge=0, le=3650)
    gln: Optional[str] = Field(default=None, max_length=13)
    tax_id: Optional[str] = Field(default=None, max_length=100)
    email: Optional[str] = Field(default=None, max_length=240)
    external_system: Optional[str] = Field(default=None, max_length=100)
    active: bool = True
    verified: bool = False
    expected_version: Optional[int] = Field(default=None, ge=1)
    actor: str = Field(default="operator", min_length=1)


class ProcurementMappingUpdate(RequestModel):
    supplier_sku: str = Field(min_length=1, max_length=200)
    gtin: Optional[str] = Field(default=None, max_length=32)
    purchase_uom: str = Field(min_length=1, max_length=30)
    conversion_factor: float = Field(default=1, gt=0)
    order_multiple: float = Field(default=1, gt=0)
    min_order_qty: float = Field(default=0, ge=0)
    unit_price: Optional[float] = Field(default=None, ge=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    preferred: bool = False
    verified: bool = False
    expected_version: Optional[int] = Field(default=None, ge=1)
    source: str = Field(default="manual", min_length=1, max_length=60)
    actor: str = Field(default="operator", min_length=1)


class PurchaseOrderLineCreate(RequestModel):
    object_type: Literal["component", "sheet"]
    object_key: str = Field(min_length=1, max_length=100)
    ordered_qty: float = Field(gt=0)
    unit_price: Optional[float] = Field(default=None, ge=0)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    need_by_at: Optional[str] = None
    notes: Optional[str] = None


class PurchaseOrderCreate(RequestModel):
    supplier_key: str = Field(min_length=1, max_length=80)
    po_number: Optional[str] = Field(default=None, min_length=1, max_length=100)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    expected_at: Optional[str] = None
    external_id: Optional[str] = Field(default=None, max_length=200)
    source: str = Field(default="manual", min_length=1, max_length=60)
    notes: Optional[str] = None
    actor: str = Field(default="planner", min_length=1)
    lines: list[PurchaseOrderLineCreate] = Field(min_length=1)


class ProcurementDraftRequest(RequestModel):
    supplier_key: Optional[str] = Field(default=None, max_length=80)
    object_keys: list[str] = Field(default_factory=list)
    notes: Optional[str] = None
    actor: str = Field(default="planner", min_length=1)


class PurchaseOrderAction(RequestModel):
    action: Literal["approve", "queue", "cancel"]
    expected_version: Optional[int] = Field(default=None, ge=1)
    actor: str = Field(default="planner", min_length=1)
    notes: Optional[str] = None


class GoodsReceiptLineCreate(RequestModel):
    po_line_id: Optional[int] = Field(default=None, ge=1)
    line_number: Optional[int] = Field(default=None, ge=1)
    lot_code: Optional[str] = Field(default=None, max_length=100)
    accepted_qty: float = Field(default=0, ge=0)
    rejected_qty: float = Field(default=0, ge=0)
    rejection_reason: Optional[str] = Field(default=None, max_length=500)
    location: Optional[str] = Field(default=None, max_length=200)


class GoodsReceiptCreate(RequestModel):
    receipt_key: str = Field(min_length=1, max_length=100)
    purchase_order_id: int = Field(ge=1)
    external_receipt_id: Optional[str] = Field(default=None, max_length=200)
    received_at: Optional[str] = None
    location: Optional[str] = Field(default=None, max_length=200)
    verified: bool = False
    source: str = Field(default="manual", min_length=1, max_length=60)
    notes: Optional[str] = None
    actor: str = Field(default="receiver", min_length=1)
    lines: list[GoodsReceiptLineCreate] = Field(min_length=1)


class ProcurementOutboxAck(RequestModel):
    success: bool
    external_id: Optional[str] = Field(default=None, max_length=200)
    error: Optional[str] = Field(default=None, max_length=1000)
    actor: str = Field(default="erp-worker", min_length=1)


class ProcurementCsvImport(RequestModel):
    document_type: Literal["supplier_catalog", "goods_receipt"]
    mode: Literal["validate", "apply"] = "validate"
    csv_text: str = Field(min_length=1)
    file_name: Optional[str] = Field(default=None, max_length=240)
    approve_master_data: bool = False
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


class ImprovementSyncRequest(RequestModel):
    window_hours: int = Field(default=8, ge=1, le=24)
    actor: str = Field(default="operator", min_length=1)


class ImprovementAction(RequestModel):
    action: Literal["accept", "reject", "implement", "evaluate", "complete", "cancel"]
    expected_version: Optional[int] = Field(default=None, ge=1)
    actor: str = Field(default="operator", min_length=1)
    owner: Optional[str] = Field(default=None, min_length=1, max_length=120)
    notes: Optional[str] = Field(default=None, max_length=2000)
    hypothesis: Optional[str] = Field(default=None, min_length=1, max_length=1000)
    primary_metric: Optional[Literal[
        "throughput_per_hour", "downtime_minutes_per_hour", "defect_rate", "median_cycle_time_s"
    ]] = None
    target_direction: Optional[Literal["increase", "decrease"]] = None
    target_delta_pct: float = Field(default=5, ge=0, le=1000)
    baseline_hours: int = Field(default=8, ge=1, le=720)
    evaluation_hours: int = Field(default=8, ge=1, le=720)
    min_samples: int = Field(default=4, ge=2, le=10000)
    confounders: list[str] = Field(default_factory=list, max_length=50)


class RootCauseSyncRequest(RequestModel):
    lookback_days: int = Field(default=30, ge=1, le=3650)
    actor: str = Field(default="operator", min_length=1)


class RootCauseDecision(RequestModel):
    action: Literal["confirm", "dismiss", "reopen"]
    expected_version: Optional[int] = Field(default=None, ge=1)
    actual_cause_code: Optional[str] = Field(default=None, min_length=1, max_length=80)
    corrective_action: Optional[str] = Field(default=None, max_length=2000)
    notes: Optional[str] = Field(default=None, max_length=2000)
    actor: str = Field(min_length=1, max_length=120)


class AlertSyncRequest(RequestModel):
    actor: str = Field(min_length=1, max_length=120)


class AlertAction(RequestModel):
    action: Literal["acknowledge", "snooze", "resolve", "reopen"]
    expected_version: Optional[int] = Field(default=None, ge=1)
    actor: str = Field(min_length=1, max_length=120)
    owner: Optional[str] = Field(default=None, min_length=1, max_length=120)
    notes: Optional[str] = Field(default=None, max_length=2000)
    snooze_minutes: Optional[int] = Field(default=None, ge=5, le=1440)


class AlertDestinationUpsert(RequestModel):
    name: str = Field(min_length=1, max_length=120)
    channel: Literal["webhook"] = "webhook"
    endpoint: str = Field(min_length=8, max_length=2000)
    secret_env: Optional[str] = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$", max_length=120)
    min_severity: Literal["info", "warning", "critical"] = "warning"
    enabled: bool = False
    expected_version: Optional[int] = Field(default=None, ge=1)
    actor: str = Field(min_length=1, max_length=120)


class AlertDestinationTest(RequestModel):
    live: bool = False
    actor: str = Field(min_length=1, max_length=120)


class AlertDispatchRequest(RequestModel):
    limit: int = Field(default=50, ge=1, le=500)
    actor: str = Field(min_length=1, max_length=120)


class AlertSettingsUpdate(RequestModel):
    auto_sync: bool
    auto_dispatch: bool
    interval_seconds: int = Field(default=60, ge=15, le=3600)
    expected_version: Optional[int] = Field(default=None, ge=1)
    actor: str = Field(min_length=1, max_length=120)


class AuthBootstrap(RequestModel):
    bootstrap_token: str = Field(min_length=20, max_length=200)
    username: str = Field(min_length=3, max_length=40)
    display_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=15, max_length=128)


class AuthLogin(RequestModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=128)


class AuthUserCreate(RequestModel):
    username: str = Field(min_length=3, max_length=40)
    display_name: str = Field(min_length=2, max_length=120)
    role: Literal["admin", "supervisor", "planner", "maintenance", "quality", "operator", "viewer"]
    password: str = Field(min_length=15, max_length=128)


class AuthUserUpdate(RequestModel):
    display_name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    role: Optional[Literal["admin", "supervisor", "planner", "maintenance", "quality", "operator", "viewer"]] = None
    active: Optional[bool] = None
    expected_version: Optional[int] = Field(default=None, ge=1)


class AuthPasswordReset(RequestModel):
    password: str = Field(min_length=15, max_length=128)


class AuthPasswordChange(RequestModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=15, max_length=128)


class AuthApiKeyCreate(RequestModel):
    name: str = Field(min_length=2, max_length=120)
    permissions: list[Literal["integration"]]
    expires_at: Optional[str] = None
