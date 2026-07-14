-- HIVE OS — SQLite schema (migrate to Postgres later by swapping types only)
-- Dimensions in mm (integer), weights/quantities as real

CREATE TABLE IF NOT EXISTS clients (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name        TEXT NOT NULL UNIQUE,   -- e.g. "AA-GBR", "RANJEETH"
    client_id       INTEGER REFERENCES clients(id),
    room_name       TEXT,                   -- e.g. "GBR", "KITCHEN"
    beamsaw_run_id  TEXT,                   -- CncRun number, e.g. "86"
    job_date        TEXT,                   -- ISO date from TXT header
    total_parts     INTEGER,
    source_csv      TEXT,                   -- relative path of source CSV
    source_txt      TEXT,                   -- relative path of source TXT
    imported_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ISA-95-inspired work requests. Imported CV jobs are source definitions;
-- production_orders carry the operator-controlled scheduling and release state.
CREATE TABLE IF NOT EXISTS production_orders (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id              INTEGER NOT NULL UNIQUE REFERENCES jobs(id),
    external_order_id   TEXT,
    status              TEXT NOT NULL DEFAULT 'draft',
    due_at              TEXT,
    priority            INTEGER NOT NULL DEFAULT 50 CHECK(priority BETWEEN 1 AND 100),
    planned_start_at    TEXT,
    release_sequence    INTEGER,
    source              TEXT NOT NULL DEFAULT 'hive',
    notes               TEXT,
    version             INTEGER NOT NULL DEFAULT 1,
    released_by         TEXT,
    released_at         TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS production_order_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    production_order_id INTEGER NOT NULL REFERENCES production_orders(id),
    event_type          TEXT NOT NULL,
    from_status         TEXT,
    to_status           TEXT,
    actor               TEXT NOT NULL,
    notes               TEXT,
    payload_json        TEXT,
    ts                  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assemblies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL REFERENCES jobs(id),
    assembly_name   TEXT NOT NULL,          -- e.g. "GBR-WB1-1100"
    assembly_cv_id  INTEGER,                -- Assembly ID from CV CSV
    UNIQUE(job_id, assembly_name)
);

CREATE TABLE IF NOT EXISTS parts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL REFERENCES jobs(id),
    assembly_id     INTEGER REFERENCES assemblies(id),
    part_cv_id      INTEGER,                -- Part ID from CV CSV (row number)
    part_name       TEXT NOT NULL,          -- e.g. "Fixed Shelf", "Top"
    material        TEXT,                   -- e.g. "HDHMR_18mm_6968 SUD"
    length_mm       REAL,
    width_mm        REAL,
    thickness_mm    REAL,
    qty             INTEGER NOT NULL DEFAULT 1,
    grain           INTEGER,                -- 1 = with grain, 0 = no grain
    eb1             TEXT,                   -- edge banding: front
    eb2             TEXT,                   -- edge banding: back
    eb3             TEXT,                   -- edge banding: left
    eb4             TEXT,                   -- edge banding: right
    cnc_file_back   TEXT,                   -- .xcs reference for back face
    cnc_file_front  TEXT,                   -- .xcs reference for front face
    has_cnc         INTEGER NOT NULL DEFAULT 0,  -- 1 if any CNC file assigned
    beamsaw_seq     INTEGER                 -- sequence position in beam saw run
);

CREATE TABLE IF NOT EXISTS machines (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,       -- e.g. "Gabbiani PT 80"
    machine_key TEXT NOT NULL UNIQUE,       -- slug for MQTT topics, e.g. "gabbiani_pt80"
    type        TEXT,                       -- e.g. "Beam Saw", "CNC Driller"
    brand       TEXT,                       -- e.g. "SCM", "Siemens"
    model       TEXT,
    has_maestro INTEGER NOT NULL DEFAULT 1,
    has_opcua   INTEGER NOT NULL DEFAULT 0,
    active      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS machine_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id      INTEGER NOT NULL REFERENCES machines(id),
    event_type      TEXT NOT NULL,          -- 'power_on','power_off','cycle_start','cycle_end','alarm','idle'
    part_id         INTEGER REFERENCES parts(id),
    cnc_file        TEXT,                   -- which .xcs file triggered this event
    raw_payload     TEXT,                   -- original log line or MQTT JSON
    ts              TEXT NOT NULL,          -- ISO datetime of event
    recorded_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Central ingestion ledger. Raw telemetry passes through this gate before it
-- can affect OEE, constraints, or scheduling decisions.
CREATE TABLE IF NOT EXISTS event_fingerprints (
    fingerprint     TEXT PRIMARY KEY,
    event_id        INTEGER REFERENCES machine_events(id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS event_ingestion_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id      INTEGER REFERENCES machines(id),
    event_id        INTEGER REFERENCES machine_events(id),
    source          TEXT NOT NULL,
    status          TEXT NOT NULL, -- accepted | rejected | duplicate | heartbeat
    reason          TEXT,
    event_type      TEXT,
    event_ts        TEXT,
    received_at     TEXT NOT NULL,
    raw_payload     TEXT
);

CREATE TABLE IF NOT EXISTS agent_status (
    machine_id          INTEGER PRIMARY KEY REFERENCES machines(id),
    source              TEXT NOT NULL,
    last_heartbeat_at   TEXT,
    last_event_at       TEXT,
    last_received_at    TEXT NOT NULL,
    clock_skew_s        REAL,
    raw_payload         TEXT
);

-- Evidence used by the learning and digital-twin layers. Observations are
-- immutable derivatives of raw events; learned models are versioned so a poor
-- candidate can never silently replace the current production model.
CREATE TABLE IF NOT EXISTS cycle_observations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id          INTEGER NOT NULL REFERENCES machines(id),
    part_id             INTEGER REFERENCES parts(id),
    start_event_id      INTEGER REFERENCES machine_events(id),
    end_event_id        INTEGER NOT NULL UNIQUE REFERENCES machine_events(id),
    started_at          TEXT,
    ended_at            TEXT NOT NULL,
    duration_s          REAL,
    duration_source     TEXT NOT NULL, -- event_pair | payload
    validity            TEXT NOT NULL, -- valid | rejected
    rejection_reason    TEXT,
    features_json       TEXT,
    used_for_training   INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cycle_models (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id              INTEGER NOT NULL REFERENCES machines(id),
    version                 INTEGER NOT NULL,
    training_signature      TEXT NOT NULL UNIQUE,
    sample_count            INTEGER NOT NULL,
    train_count             INTEGER NOT NULL,
    validation_count        INTEGER NOT NULL,
    inlier_count            INTEGER NOT NULL,
    coefficients_json       TEXT NOT NULL,
    identified_features_json TEXT NOT NULL,
    mae_s                   REAL,
    mape                    REAL,
    r2                      REAL,
    residual_cv             REAL,
    confidence              TEXT NOT NULL, -- low | medium | high
    status                  TEXT NOT NULL, -- candidate | active | superseded
    reason                  TEXT,
    trained_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS route_observations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    part_id             INTEGER NOT NULL REFERENCES parts(id),
    from_machine_id     INTEGER NOT NULL REFERENCES machines(id),
    to_machine_id       INTEGER NOT NULL REFERENCES machines(id),
    from_event_id       INTEGER NOT NULL REFERENCES machine_events(id),
    to_event_id         INTEGER NOT NULL REFERENCES machine_events(id),
    transfer_s          REAL NOT NULL,
    observed_at         TEXT NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(from_event_id, to_event_id)
);

CREATE TABLE IF NOT EXISTS oee_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id      INTEGER NOT NULL REFERENCES machines(id),
    window_start    TEXT NOT NULL,          -- ISO datetime
    window_end      TEXT NOT NULL,          -- ISO datetime
    planned_time_s  INTEGER,               -- total scheduled seconds
    run_time_s      INTEGER,               -- machine actually running
    idle_time_s     INTEGER,
    down_time_s     INTEGER,
    parts_planned   INTEGER,
    parts_made      INTEGER,
    availability    REAL,                   -- run_time / planned_time
    performance     REAL,                   -- actual rate / ideal rate
    quality         REAL,                   -- good parts / total parts (1.0 until reject tracking added)
    oee             REAL,                   -- availability * performance * quality
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Phase 1 operations layer: downtime, maintenance, quality/rework, barcode,
-- and connector sync state. These are HIVE-native tables; external systems map
-- into them through small adapters.

CREATE TABLE IF NOT EXISTS downtime_reasons (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL UNIQUE,
    label       TEXT NOT NULL,
    category    TEXT NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS downtime_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id      INTEGER REFERENCES machines(id),
    event_id        INTEGER REFERENCES machine_events(id),
    reason_id       INTEGER REFERENCES downtime_reasons(id),
    status          TEXT NOT NULL DEFAULT 'open', -- open | closed
    notes           TEXT,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS maintenance_work_orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id      INTEGER REFERENCES machines(id),
    title           TEXT NOT NULL,
    description     TEXT,
    priority        TEXT NOT NULL DEFAULT 'medium', -- low | medium | high | urgent
    status          TEXT NOT NULL DEFAULT 'open',   -- open | in_progress | done | cancelled
    source          TEXT NOT NULL DEFAULT 'manual',
    due_date        TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at       TEXT
);

-- Preventive maintenance is kept beside the original corrective work-order
-- contract so existing installations migrate without ALTER TABLE operations.
CREATE TABLE IF NOT EXISTS maintenance_plans (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_key                    TEXT NOT NULL UNIQUE,
    machine_id                  INTEGER NOT NULL REFERENCES machines(id),
    title                       TEXT NOT NULL,
    description                 TEXT,
    strategy                    TEXT NOT NULL DEFAULT 'calendar', -- calendar | usage | hybrid | condition
    runtime_basis               TEXT NOT NULL DEFAULT 'powered', -- powered | cycle
    interval_days               REAL CHECK(interval_days IS NULL OR interval_days > 0),
    interval_runtime_h          REAL CHECK(interval_runtime_h IS NULL OR interval_runtime_h > 0),
    interval_cycles             INTEGER CHECK(interval_cycles IS NULL OR interval_cycles > 0),
    warning_days                REAL NOT NULL DEFAULT 7 CHECK(warning_days >= 0),
    warning_runtime_h           REAL NOT NULL DEFAULT 25 CHECK(warning_runtime_h >= 0),
    warning_cycles              INTEGER NOT NULL DEFAULT 100 CHECK(warning_cycles >= 0),
    estimated_duration_min      INTEGER NOT NULL DEFAULT 60 CHECK(estimated_duration_min > 0),
    criticality                 TEXT NOT NULL DEFAULT 'medium', -- low | medium | high | critical
    requires_shutdown           INTEGER NOT NULL DEFAULT 1,
    loto_required               INTEGER NOT NULL DEFAULT 1,
    condition_metric            TEXT,
    condition_operator          TEXT, -- gt | gte | lt | lte
    condition_threshold         REAL,
    active                      INTEGER NOT NULL DEFAULT 1,
    source                      TEXT NOT NULL DEFAULT 'engineering_assumption',
    verified                    INTEGER NOT NULL DEFAULT 0,
    version                     INTEGER NOT NULL DEFAULT 1,
    anchor_at                   TEXT NOT NULL,
    last_completed_at           TEXT,
    last_completed_runtime_h    REAL NOT NULL DEFAULT 0,
    last_completed_cycles       INTEGER NOT NULL DEFAULT 0,
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS maintenance_plan_tasks (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    maintenance_plan_id INTEGER NOT NULL REFERENCES maintenance_plans(id),
    task_key            TEXT NOT NULL,
    sequence            INTEGER NOT NULL CHECK(sequence >= 1),
    title               TEXT NOT NULL,
    instructions        TEXT,
    response_type       TEXT NOT NULL DEFAULT 'check', -- check | pass_fail | number | text
    unit                TEXT,
    required            INTEGER NOT NULL DEFAULT 1,
    active              INTEGER NOT NULL DEFAULT 1,
    updated_at          TEXT NOT NULL,
    UNIQUE(maintenance_plan_id, task_key)
);

CREATE TABLE IF NOT EXISTS spare_parts (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    part_key                TEXT NOT NULL UNIQUE,
    name                    TEXT NOT NULL,
    manufacturer            TEXT,
    manufacturer_part_number TEXT,
    unit                    TEXT NOT NULL DEFAULT 'each',
    criticality             TEXT NOT NULL DEFAULT 'medium',
    reorder_point           REAL NOT NULL DEFAULT 0 CHECK(reorder_point >= 0),
    reorder_qty             REAL NOT NULL DEFAULT 0 CHECK(reorder_qty >= 0),
    lead_time_days          INTEGER CHECK(lead_time_days IS NULL OR lead_time_days >= 0),
    preferred_supplier      TEXT,
    source                  TEXT NOT NULL DEFAULT 'manual',
    verified                INTEGER NOT NULL DEFAULT 0,
    active                  INTEGER NOT NULL DEFAULT 1,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS spare_stock (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    spare_part_id       INTEGER NOT NULL REFERENCES spare_parts(id),
    location            TEXT NOT NULL DEFAULT 'maintenance_store',
    on_hand_qty         REAL NOT NULL DEFAULT 0 CHECK(on_hand_qty >= 0),
    reserved_qty        REAL NOT NULL DEFAULT 0 CHECK(reserved_qty >= 0),
    unit_cost           REAL CHECK(unit_cost IS NULL OR unit_cost >= 0),
    source              TEXT NOT NULL DEFAULT 'manual',
    verified            INTEGER NOT NULL DEFAULT 0,
    updated_at          TEXT NOT NULL,
    UNIQUE(spare_part_id, location)
);

CREATE TABLE IF NOT EXISTS maintenance_plan_spares (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    maintenance_plan_id INTEGER NOT NULL REFERENCES maintenance_plans(id),
    spare_part_id       INTEGER NOT NULL REFERENCES spare_parts(id),
    quantity            REAL NOT NULL CHECK(quantity > 0),
    required            INTEGER NOT NULL DEFAULT 1,
    active              INTEGER NOT NULL DEFAULT 1,
    updated_at          TEXT NOT NULL,
    UNIQUE(maintenance_plan_id, spare_part_id)
);

CREATE TABLE IF NOT EXISTS maintenance_work_order_links (
    work_order_id       INTEGER PRIMARY KEY REFERENCES maintenance_work_orders(id),
    maintenance_plan_id INTEGER NOT NULL REFERENCES maintenance_plans(id),
    trigger_type        TEXT NOT NULL,
    trigger_details_json TEXT,
    generated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS maintenance_condition_signals (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id          INTEGER NOT NULL REFERENCES machines(id),
    maintenance_plan_id INTEGER REFERENCES maintenance_plans(id),
    metric_key          TEXT NOT NULL,
    value               REAL NOT NULL,
    unit                TEXT,
    threshold           REAL,
    comparison          TEXT,
    triggered           INTEGER NOT NULL DEFAULT 0,
    severity            TEXT NOT NULL DEFAULT 'warning',
    status              TEXT NOT NULL DEFAULT 'observed', -- observed | open | acknowledged | cleared
    source              TEXT NOT NULL,
    evidence_type       TEXT,
    evidence_id         INTEGER,
    observed_at         TEXT NOT NULL,
    recorded_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS maintenance_executions (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    work_order_id           INTEGER NOT NULL UNIQUE REFERENCES maintenance_work_orders(id),
    maintenance_plan_id     INTEGER REFERENCES maintenance_plans(id),
    machine_id              INTEGER REFERENCES machines(id),
    outcome                 TEXT NOT NULL, -- completed | follow_up_required
    completed_by            TEXT NOT NULL,
    notes                   TEXT,
    loto_verified           INTEGER NOT NULL DEFAULT 0,
    loto_verified_by        TEXT,
    loto_verified_at        TEXT,
    completed_at            TEXT NOT NULL,
    runtime_h_at_completion REAL,
    cycles_at_completion    INTEGER
);

CREATE TABLE IF NOT EXISTS maintenance_task_results (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    maintenance_execution_id INTEGER NOT NULL REFERENCES maintenance_executions(id),
    maintenance_task_id     INTEGER NOT NULL REFERENCES maintenance_plan_tasks(id),
    result                  TEXT NOT NULL,
    value_text              TEXT,
    value_number            REAL,
    notes                   TEXT,
    UNIQUE(maintenance_execution_id, maintenance_task_id)
);

CREATE TABLE IF NOT EXISTS maintenance_spare_reservations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    work_order_id       INTEGER NOT NULL REFERENCES maintenance_work_orders(id),
    spare_part_id       INTEGER NOT NULL REFERENCES spare_parts(id),
    spare_stock_id      INTEGER REFERENCES spare_stock(id),
    quantity_required   REAL NOT NULL CHECK(quantity_required > 0),
    quantity_reserved   REAL NOT NULL DEFAULT 0 CHECK(quantity_reserved >= 0),
    required            INTEGER NOT NULL DEFAULT 1,
    status              TEXT NOT NULL, -- reserved | shortage | issued | released
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS spare_stock_movements (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    spare_stock_id      INTEGER NOT NULL REFERENCES spare_stock(id),
    work_order_id       INTEGER REFERENCES maintenance_work_orders(id),
    movement_type       TEXT NOT NULL, -- receipt | adjustment | reservation | release | issue
    on_hand_delta       REAL NOT NULL DEFAULT 0,
    reserved_delta      REAL NOT NULL DEFAULT 0,
    actor               TEXT NOT NULL,
    notes               TEXT,
    ts                  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS maintenance_work_order_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    work_order_id       INTEGER NOT NULL REFERENCES maintenance_work_orders(id),
    event_type          TEXT NOT NULL,
    from_status         TEXT,
    to_status           TEXT,
    actor               TEXT NOT NULL,
    details_json        TEXT,
    ts                  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS defect_types (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL UNIQUE,
    label       TEXT NOT NULL,
    process     TEXT NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS quality_checks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER REFERENCES jobs(id),
    part_id         INTEGER REFERENCES parts(id),
    machine_id      INTEGER REFERENCES machines(id),
    defect_type_id  INTEGER REFERENCES defect_types(id),
    result          TEXT NOT NULL, -- pass | fail | rework
    inspector       TEXT,
    notes           TEXT,
    photo_path      TEXT,
    source          TEXT NOT NULL DEFAULT 'manual',
    ts              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rework_tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    quality_check_id INTEGER REFERENCES quality_checks(id),
    job_id          INTEGER REFERENCES jobs(id),
    part_id         INTEGER REFERENCES parts(id),
    assigned_area   TEXT,
    status          TEXT NOT NULL DEFAULT 'open', -- open | in_progress | done | cancelled
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at       TEXT
);

CREATE TABLE IF NOT EXISTS barcode_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    barcode         TEXT NOT NULL,
    job_id          INTEGER REFERENCES jobs(id),
    part_id         INTEGER REFERENCES parts(id),
    station         TEXT,
    event_type      TEXT NOT NULL, -- part_complete | qc_pass | qc_fail | packed | dispatched | unknown
    operator        TEXT,
    source          TEXT NOT NULL DEFAULT 'manual',
    raw_payload     TEXT,
    ts              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS part_route_steps (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    part_id             INTEGER NOT NULL REFERENCES parts(id),
    step_index          INTEGER NOT NULL,
    machine_id          INTEGER NOT NULL REFERENCES machines(id),
    source              TEXT NOT NULL, -- cv_feature | observed | manual
    confidence          TEXT NOT NULL, -- low | medium | high | confirmed
    required            INTEGER NOT NULL DEFAULT 1,
    required_qty        INTEGER NOT NULL DEFAULT 1,
    confirmed_qty       INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'planned', -- planned | started | confirmed | skipped | exception
    confirmed_event_id  INTEGER REFERENCES machine_events(id),
    confirmed_barcode_id INTEGER REFERENCES barcode_events(id),
    confirmed_at        TEXT,
    confirmed_by        TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE(part_id, step_index)
);

CREATE TABLE IF NOT EXISTS route_step_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    route_step_id       INTEGER NOT NULL REFERENCES part_route_steps(id),
    event_type          TEXT NOT NULL,
    from_status         TEXT,
    to_status           TEXT,
    source              TEXT NOT NULL,
    evidence_id         INTEGER,
    actor               TEXT,
    notes               TEXT,
    ts                  TEXT NOT NULL,
    UNIQUE(route_step_id, source, evidence_id, event_type)
);

CREATE TABLE IF NOT EXISTS route_exceptions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    part_id             INTEGER NOT NULL REFERENCES parts(id),
    expected_step_id    INTEGER REFERENCES part_route_steps(id),
    observed_machine_id INTEGER REFERENCES machines(id),
    machine_event_id    INTEGER REFERENCES machine_events(id),
    barcode_event_id    INTEGER REFERENCES barcode_events(id),
    exception_type      TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'open', -- open | accepted | ignored | corrected
    details             TEXT,
    ts                  TEXT NOT NULL,
    resolved_at         TEXT,
    resolved_by         TEXT
);

CREATE TABLE IF NOT EXISTS planning_scenarios (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT,
    created_by          TEXT NOT NULL,
    request_json        TEXT NOT NULL,
    result_json         TEXT NOT NULL,
    readiness_json      TEXT NOT NULL,
    input_signature     TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'draft', -- draft | approved | rejected | expired
    selected_policy     TEXT,
    approved_by         TEXT,
    approved_at         TEXT,
    rejection_reason    TEXT,
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS planning_decisions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id         INTEGER NOT NULL REFERENCES planning_scenarios(id),
    decision            TEXT NOT NULL,
    actor               TEXT NOT NULL,
    selected_policy     TEXT,
    notes               TEXT,
    ts                  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS production_schedule_items (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id         INTEGER NOT NULL REFERENCES planning_scenarios(id),
    production_order_id INTEGER NOT NULL REFERENCES production_orders(id),
    position            INTEGER NOT NULL,
    planned_start_s     REAL,
    planned_end_s       REAL,
    UNIQUE(scenario_id, production_order_id),
    UNIQUE(scenario_id, position)
);

-- ISA-95-inspired resource capability and availability. Defaults are explicit
-- engineering assumptions; verified=1 means a named operator checked them.
CREATE TABLE IF NOT EXISTS material_definitions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    material_key        TEXT NOT NULL UNIQUE,
    name                TEXT NOT NULL,
    sheet_length_mm     REAL NOT NULL DEFAULT 2440,
    sheet_width_mm      REAL NOT NULL DEFAULT 1220,
    yield_factor        REAL NOT NULL DEFAULT 0.82 CHECK(yield_factor > 0 AND yield_factor <= 1),
    source              TEXT NOT NULL DEFAULT 'engineering_assumption',
    verified            INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS material_lots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id         INTEGER NOT NULL REFERENCES material_definitions(id),
    lot_code            TEXT NOT NULL,
    location            TEXT,
    status              TEXT NOT NULL DEFAULT 'available', -- available | hold | consumed
    on_hand_sheets      REAL NOT NULL DEFAULT 0 CHECK(on_hand_sheets >= 0),
    reserved_sheets     REAL NOT NULL DEFAULT 0 CHECK(reserved_sheets >= 0),
    source              TEXT NOT NULL DEFAULT 'manual',
    verified            INTEGER NOT NULL DEFAULT 0,
    updated_at          TEXT NOT NULL,
    UNIQUE(material_id, lot_code)
);

CREATE TABLE IF NOT EXISTS material_requirements (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    production_order_id INTEGER NOT NULL REFERENCES production_orders(id),
    material_id         INTEGER NOT NULL REFERENCES material_definitions(id),
    required_area_m2    REAL NOT NULL DEFAULT 0,
    required_sheets     INTEGER,
    unknown_part_count  INTEGER NOT NULL DEFAULT 0,
    source              TEXT NOT NULL DEFAULT 'cv_dimensions',
    confidence          TEXT NOT NULL DEFAULT 'estimated',
    updated_at          TEXT NOT NULL,
    UNIQUE(production_order_id, material_id)
);

CREATE TABLE IF NOT EXISTS labor_roles (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    role_key            TEXT NOT NULL UNIQUE,
    name                TEXT NOT NULL,
    headcount           INTEGER NOT NULL DEFAULT 1 CHECK(headcount >= 0),
    source              TEXT NOT NULL DEFAULT 'engineering_assumption',
    verified            INTEGER NOT NULL DEFAULT 0,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_pools (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    pool_key            TEXT NOT NULL UNIQUE,
    name                TEXT NOT NULL,
    total_qty           INTEGER NOT NULL DEFAULT 1 CHECK(total_qty >= 0),
    available_qty       INTEGER NOT NULL DEFAULT 1 CHECK(available_qty >= 0),
    source              TEXT NOT NULL DEFAULT 'engineering_assumption',
    verified            INTEGER NOT NULL DEFAULT 0,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS machine_resource_profiles (
    machine_id          INTEGER PRIMARY KEY REFERENCES machines(id),
    labor_role_id       INTEGER REFERENCES labor_roles(id),
    labor_qty           INTEGER NOT NULL DEFAULT 1 CHECK(labor_qty >= 0),
    tool_pool_id        INTEGER REFERENCES tool_pools(id),
    tool_qty            INTEGER NOT NULL DEFAULT 1 CHECK(tool_qty >= 0),
    machine_capacity    INTEGER NOT NULL DEFAULT 1 CHECK(machine_capacity >= 1),
    source              TEXT NOT NULL DEFAULT 'engineering_assumption',
    verified            INTEGER NOT NULL DEFAULT 0,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS work_calendar_windows (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_type       TEXT NOT NULL, -- factory | machine | labor_role | tool_pool
    resource_key        TEXT NOT NULL,
    weekday             INTEGER NOT NULL CHECK(weekday BETWEEN 0 AND 6),
    start_time          TEXT NOT NULL,
    end_time            TEXT NOT NULL,
    capacity            INTEGER NOT NULL DEFAULT 1 CHECK(capacity >= 1),
    timezone            TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    source              TEXT NOT NULL DEFAULT 'engineering_assumption',
    verified            INTEGER NOT NULL DEFAULT 0,
    active              INTEGER NOT NULL DEFAULT 1,
    updated_at          TEXT NOT NULL,
    UNIQUE(resource_type, resource_key, weekday, start_time, end_time)
);

CREATE TABLE IF NOT EXISTS resource_unavailability (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_type       TEXT NOT NULL,
    resource_key        TEXT NOT NULL,
    starts_at           TEXT NOT NULL,
    ends_at             TEXT NOT NULL,
    reason              TEXT NOT NULL,
    source              TEXT NOT NULL DEFAULT 'manual',
    work_order_id       INTEGER REFERENCES maintenance_work_orders(id),
    created_by          TEXT NOT NULL,
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wip_buffers (
    machine_id          INTEGER PRIMARY KEY REFERENCES machines(id),
    capacity_qty        INTEGER NOT NULL DEFAULT 50 CHECK(capacity_qty >= 1),
    current_qty         INTEGER NOT NULL DEFAULT 0 CHECK(current_qty >= 0),
    source              TEXT NOT NULL DEFAULT 'engineering_assumption',
    verified            INTEGER NOT NULL DEFAULT 0,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS material_reservations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id         INTEGER NOT NULL REFERENCES planning_scenarios(id),
    production_order_id INTEGER NOT NULL REFERENCES production_orders(id),
    material_lot_id     INTEGER NOT NULL REFERENCES material_lots(id),
    quantity_sheets     REAL NOT NULL CHECK(quantity_sheets > 0),
    status              TEXT NOT NULL DEFAULT 'committed', -- committed | consumed | released
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE(scenario_id, production_order_id, material_lot_id)
);

CREATE TABLE IF NOT EXISTS resource_change_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_type       TEXT NOT NULL,
    resource_key        TEXT NOT NULL,
    event_type          TEXT NOT NULL,
    actor               TEXT NOT NULL,
    payload_json        TEXT,
    ts                  TEXT NOT NULL
);

-- ISA-95-style station job orders generated from an approved HIVE schedule.
-- The route remains the work definition; execution_jobs hold dispatch state
-- and actual quantities for one route step.
CREATE TABLE IF NOT EXISTS execution_jobs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id         INTEGER NOT NULL REFERENCES planning_scenarios(id),
    schedule_item_id    INTEGER NOT NULL REFERENCES production_schedule_items(id),
    production_order_id INTEGER NOT NULL REFERENCES production_orders(id),
    route_step_id       INTEGER NOT NULL UNIQUE REFERENCES part_route_steps(id),
    machine_id          INTEGER NOT NULL REFERENCES machines(id),
    dispatch_sequence   INTEGER NOT NULL,
    state               TEXT NOT NULL DEFAULT 'queued',
    resume_state        TEXT,
    required_qty        INTEGER NOT NULL CHECK(required_qty >= 1),
    in_process_qty      INTEGER NOT NULL DEFAULT 0 CHECK(in_process_qty >= 0),
    completed_qty       INTEGER NOT NULL DEFAULT 0 CHECK(completed_qty >= 0),
    scrap_qty           INTEGER NOT NULL DEFAULT 0 CHECK(scrap_qty >= 0),
    assigned_operator   TEXT,
    version             INTEGER NOT NULL DEFAULT 1,
    dispatched_at       TEXT,
    acknowledged_at     TEXT,
    started_at          TEXT,
    completed_at        TEXT,
    held_reason         TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_job_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_job_id    INTEGER NOT NULL REFERENCES execution_jobs(id),
    event_type          TEXT NOT NULL,
    from_state          TEXT,
    to_state            TEXT,
    quantity            INTEGER,
    good_qty            INTEGER,
    scrap_qty           INTEGER,
    source              TEXT NOT NULL,
    evidence_type       TEXT,
    evidence_id         INTEGER,
    actor               TEXT NOT NULL,
    notes               TEXT,
    idempotency_key     TEXT UNIQUE,
    ts                  TEXT NOT NULL
);

-- EPCIS-inspired physical truth ledger. Intentional schedule/dispatch state is
-- kept above; this table records what object was observed, where, and in what
-- resulting disposition.
CREATE TABLE IF NOT EXISTS traceability_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    object_type         TEXT NOT NULL,
    object_key          TEXT NOT NULL,
    production_order_id INTEGER REFERENCES production_orders(id),
    part_id             INTEGER REFERENCES parts(id),
    material_lot_id     INTEGER REFERENCES material_lots(id),
    execution_job_id    INTEGER REFERENCES execution_jobs(id),
    event_type          TEXT NOT NULL,
    action              TEXT NOT NULL DEFAULT 'observe',
    quantity            REAL NOT NULL DEFAULT 1,
    uom                 TEXT NOT NULL DEFAULT 'each',
    read_point          TEXT,
    business_location   TEXT,
    disposition         TEXT,
    source              TEXT NOT NULL,
    evidence_type       TEXT,
    evidence_id         INTEGER,
    actor               TEXT,
    idempotency_key     TEXT UNIQUE,
    event_time          TEXT NOT NULL,
    recorded_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_exceptions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_job_id    INTEGER REFERENCES execution_jobs(id),
    production_order_id INTEGER REFERENCES production_orders(id),
    part_id             INTEGER REFERENCES parts(id),
    machine_id          INTEGER REFERENCES machines(id),
    exception_type      TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'open',
    details             TEXT NOT NULL,
    source              TEXT NOT NULL,
    evidence_type       TEXT,
    evidence_id         INTEGER,
    occurred_at         TEXT NOT NULL,
    resolved_at         TEXT,
    resolved_by         TEXT,
    resolution_notes    TEXT,
    UNIQUE(source, evidence_type, evidence_id, exception_type)
);

-- One row per physical copy of an imported Cabinet Vision part. HIVE unit
-- identifiers are intentionally private until HAEEV has a licensed GS1 prefix;
-- globally standard identifiers can be attached through aliases later.
CREATE TABLE IF NOT EXISTS trace_units (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_key            TEXT NOT NULL UNIQUE,
    qr_payload          TEXT NOT NULL UNIQUE,
    production_order_id INTEGER NOT NULL REFERENCES production_orders(id),
    part_id             INTEGER NOT NULL REFERENCES parts(id),
    ordinal             INTEGER NOT NULL CHECK(ordinal >= 1),
    status              TEXT NOT NULL DEFAULT 'planned',
    current_machine_id  INTEGER REFERENCES machines(id),
    version             INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE(part_id, ordinal)
);

CREATE TABLE IF NOT EXISTS unit_identifier_aliases (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_id             INTEGER NOT NULL REFERENCES trace_units(id),
    scheme              TEXT NOT NULL,
    value               TEXT NOT NULL UNIQUE,
    active              INTEGER NOT NULL DEFAULT 1,
    source              TEXT NOT NULL DEFAULT 'hive',
    created_by          TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    UNIQUE(unit_id, scheme, value)
);

-- Raw scans remain in barcode_events. This table records how each raw value
-- was interpreted so mappings can be audited and corrected independently.
CREATE TABLE IF NOT EXISTS barcode_event_resolutions (
    barcode_event_id    INTEGER PRIMARY KEY REFERENCES barcode_events(id),
    unit_id             INTEGER REFERENCES trace_units(id),
    identifier_scheme   TEXT,
    status              TEXT NOT NULL, -- resolved | applied | duplicate | legacy | unknown | conflict
    details             TEXT,
    resolved_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS unit_route_progress (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_id             INTEGER NOT NULL REFERENCES trace_units(id),
    route_step_id       INTEGER NOT NULL REFERENCES part_route_steps(id),
    state               TEXT NOT NULL DEFAULT 'planned', -- planned | started | completed
    started_barcode_id  INTEGER REFERENCES barcode_events(id),
    completed_barcode_id INTEGER REFERENCES barcode_events(id),
    started_at          TEXT,
    completed_at        TEXT,
    updated_at          TEXT NOT NULL,
    UNIQUE(unit_id, route_step_id)
);

CREATE TABLE IF NOT EXISTS label_print_jobs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    production_order_id INTEGER NOT NULL REFERENCES production_orders(id),
    template_key        TEXT NOT NULL DEFAULT 'part_100x50',
    printer_key         TEXT,
    status              TEXT NOT NULL DEFAULT 'ready', -- ready | printed | cancelled
    unit_count          INTEGER NOT NULL,
    requested_by        TEXT NOT NULL,
    notes               TEXT,
    created_at          TEXT NOT NULL,
    printed_at          TEXT,
    printed_by          TEXT
);

CREATE TABLE IF NOT EXISTS label_print_items (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    print_job_id        INTEGER NOT NULL REFERENCES label_print_jobs(id),
    unit_id             INTEGER NOT NULL REFERENCES trace_units(id),
    position            INTEGER NOT NULL,
    printed_count       INTEGER NOT NULL DEFAULT 0,
    last_printed_at     TEXT,
    UNIQUE(print_job_id, unit_id)
);

CREATE TABLE IF NOT EXISTS connector_sync_state (
    connector_key   TEXT PRIMARY KEY,
    status          TEXT NOT NULL DEFAULT 'not_configured',
    last_sync_at    TEXT,
    last_cursor     TEXT,
    last_error      TEXT,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Factory-specific formats are commissioned at this boundary. Profiles contain
-- no credentials: credential_env names an environment variable held by the OS.
CREATE TABLE IF NOT EXISTS connector_profiles (
    connector_key       TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    record_type         TEXT NOT NULL,
    transport           TEXT NOT NULL,
    enabled             INTEGER NOT NULL DEFAULT 0,
    verified            INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'commissioning_required',
    credential_env      TEXT,
    settings_json       TEXT NOT NULL DEFAULT '{}',
    active_mapping_id   INTEGER,
    version             INTEGER NOT NULL DEFAULT 1,
    last_test_at        TEXT,
    last_error          TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS connector_mapping_versions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    connector_key       TEXT NOT NULL REFERENCES connector_profiles(connector_key),
    version             INTEGER NOT NULL,
    status              TEXT NOT NULL DEFAULT 'approved',
    mapping_json        TEXT NOT NULL,
    source_columns_json TEXT NOT NULL DEFAULT '[]',
    sample_sha256       TEXT NOT NULL,
    coverage            REAL NOT NULL,
    approved_by         TEXT NOT NULL,
    approved_at         TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    UNIQUE(connector_key, version)
);

CREATE TABLE IF NOT EXISTS connector_commissioning_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    connector_key       TEXT NOT NULL REFERENCES connector_profiles(connector_key),
    scope_key           TEXT,
    mapping_version_id  INTEGER REFERENCES connector_mapping_versions(id),
    mode                TEXT NOT NULL,
    source_sha256       TEXT NOT NULL,
    file_name           TEXT,
    status              TEXT NOT NULL,
    records_seen        INTEGER NOT NULL DEFAULT 0,
    records_accepted    INTEGER NOT NULL DEFAULT 0,
    records_rejected    INTEGER NOT NULL DEFAULT 0,
    records_imported    INTEGER NOT NULL DEFAULT 0,
    records_duplicate   INTEGER NOT NULL DEFAULT 0,
    summary_json        TEXT NOT NULL DEFAULT '{}',
    actor               TEXT NOT NULL,
    started_at          TEXT NOT NULL,
    completed_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS connector_run_issues (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              INTEGER NOT NULL REFERENCES connector_commissioning_runs(id),
    record_index        INTEGER,
    field_key           TEXT,
    code                TEXT NOT NULL,
    severity            TEXT NOT NULL,
    detail              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS connector_import_batches (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    connector_key       TEXT NOT NULL REFERENCES connector_profiles(connector_key),
    source_sha256       TEXT NOT NULL,
    mapping_version_id  INTEGER NOT NULL REFERENCES connector_mapping_versions(id),
    run_id              INTEGER NOT NULL REFERENCES connector_commissioning_runs(id),
    imported_at         TEXT NOT NULL,
    UNIQUE(connector_key, source_sha256)
);

CREATE INDEX IF NOT EXISTS idx_parts_job ON parts(job_id);
CREATE INDEX IF NOT EXISTS idx_production_orders_status_due ON production_orders(status, due_at, priority DESC);
CREATE INDEX IF NOT EXISTS idx_production_order_events_order_ts ON production_order_events(production_order_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_parts_cnc_back ON parts(cnc_file_back);
CREATE INDEX IF NOT EXISTS idx_parts_cnc_front ON parts(cnc_file_front);
CREATE INDEX IF NOT EXISTS idx_machine_events_machine_ts ON machine_events(machine_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_machine_events_part_ts ON machine_events(part_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_machine_events_type_ts ON machine_events(event_type, ts DESC);
CREATE INDEX IF NOT EXISTS idx_event_ingestion_machine_received ON event_ingestion_log(machine_id, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_event_ingestion_status_received ON event_ingestion_log(status, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_cycle_observations_machine_valid ON cycle_observations(machine_id, validity, ended_at DESC);
CREATE INDEX IF NOT EXISTS idx_cycle_models_machine_status ON cycle_models(machine_id, status, trained_at DESC);
CREATE INDEX IF NOT EXISTS idx_route_observations_part_time ON route_observations(part_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_route_observations_edge ON route_observations(from_machine_id, to_machine_id);
CREATE INDEX IF NOT EXISTS idx_oee_snapshots_machine_window ON oee_snapshots(machine_id, window_end DESC);
CREATE INDEX IF NOT EXISTS idx_downtime_status_started ON downtime_events(status, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_work_orders_status_created ON maintenance_work_orders(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_maintenance_plans_machine_active ON maintenance_plans(machine_id, active, verified);
CREATE INDEX IF NOT EXISTS idx_maintenance_tasks_plan_active ON maintenance_plan_tasks(maintenance_plan_id, active, sequence);
CREATE INDEX IF NOT EXISTS idx_maintenance_links_plan ON maintenance_work_order_links(maintenance_plan_id, generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_maintenance_signals_plan_status ON maintenance_condition_signals(maintenance_plan_id, status, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_maintenance_events_order_ts ON maintenance_work_order_events(work_order_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_spare_stock_part_location ON spare_stock(spare_part_id, location);
CREATE INDEX IF NOT EXISTS idx_spare_reservations_order_status ON maintenance_spare_reservations(work_order_id, status);
CREATE INDEX IF NOT EXISTS idx_spare_movements_stock_ts ON spare_stock_movements(spare_stock_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_quality_checks_result_ts ON quality_checks(result, ts DESC);
CREATE INDEX IF NOT EXISTS idx_rework_status_created ON rework_tasks(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_barcode_events_ts ON barcode_events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_route_steps_part_status ON part_route_steps(part_id, status, step_index);
CREATE INDEX IF NOT EXISTS idx_route_step_events_step_ts ON route_step_events(route_step_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_route_exceptions_status_ts ON route_exceptions(status, ts DESC);
CREATE INDEX IF NOT EXISTS idx_planning_scenarios_status_created ON planning_scenarios(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_material_requirements_order ON material_requirements(production_order_id);
CREATE INDEX IF NOT EXISTS idx_material_lots_material_status ON material_lots(material_id, status);
CREATE INDEX IF NOT EXISTS idx_material_reservations_status ON material_reservations(status, production_order_id);
CREATE INDEX IF NOT EXISTS idx_calendar_resource_day ON work_calendar_windows(resource_type, resource_key, weekday);
CREATE INDEX IF NOT EXISTS idx_resource_unavailability_window ON resource_unavailability(resource_type, resource_key, starts_at, ends_at);
CREATE INDEX IF NOT EXISTS idx_resource_change_events_key_ts ON resource_change_events(resource_type, resource_key, ts DESC);
CREATE INDEX IF NOT EXISTS idx_execution_jobs_machine_state_sequence ON execution_jobs(machine_id, state, dispatch_sequence);
CREATE INDEX IF NOT EXISTS idx_execution_jobs_order_state ON execution_jobs(production_order_id, state);
CREATE INDEX IF NOT EXISTS idx_execution_events_job_ts ON execution_job_events(execution_job_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_execution_events_evidence ON execution_job_events(evidence_type, evidence_id);
CREATE INDEX IF NOT EXISTS idx_traceability_object_time ON traceability_events(object_type, object_key, event_time DESC);
CREATE INDEX IF NOT EXISTS idx_traceability_part_time ON traceability_events(part_id, event_time DESC);
CREATE INDEX IF NOT EXISTS idx_execution_exceptions_status_time ON execution_exceptions(status, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_trace_units_order_status ON trace_units(production_order_id, status);
CREATE INDEX IF NOT EXISTS idx_trace_units_part_ordinal ON trace_units(part_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_unit_aliases_unit_active ON unit_identifier_aliases(unit_id, active);
CREATE INDEX IF NOT EXISTS idx_barcode_resolutions_status ON barcode_event_resolutions(status, resolved_at DESC);
CREATE INDEX IF NOT EXISTS idx_unit_progress_step_state ON unit_route_progress(route_step_id, state);
CREATE INDEX IF NOT EXISTS idx_label_jobs_order_created ON label_print_jobs(production_order_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_label_items_unit_printed ON label_print_items(unit_id, printed_count);
CREATE INDEX IF NOT EXISTS idx_connector_mappings_key_status ON connector_mapping_versions(connector_key, status, version DESC);
CREATE INDEX IF NOT EXISTS idx_connector_runs_key_time ON connector_commissioning_runs(connector_key, completed_at DESC);
CREATE INDEX IF NOT EXISTS idx_connector_issues_run ON connector_run_issues(run_id, severity);

-- Seed the 14 in-scope HAEEV machines (aluminium pair excluded, compressors/dust collectors as utility)
INSERT OR IGNORE INTO machines (name, machine_key, type, brand, model, has_maestro, has_opcua, active) VALUES
    ('Stefani KD',      'stefani_kd',       'Edge Bander Thrufeed',  'SCM', 'Stefani KD',    1, 0, 1),
    ('Action E',        'action_e',         'Boxing',                'SCM', 'Action E',      1, 0, 1),
    ('Gabbiani PT 80',  'gabbiani_pt80',    'Beam Saw',              'SCM', 'Gabbiani PT 80',1, 0, 1),
    ('Morbidelli CX100','morbidelli_cx100', 'CNC Driller',           'SCM', 'Morbidelli CX100',1,0,1),
    ('Morbidelli N100', 'morbidelli_n100',  'Flat Bed Router',       'SCM', 'Morbidelli N100',1,0,1),
    ('Nova SI 400',     'nova_si400',       'Panel Saw',             'SCM', 'Nova SI 400',   1, 0, 1),
    ('Sergiani GS 120', 'sergiani_gs120',   'Hot Press',             'Sergiani','GS 120',    0, 1, 1),
    ('DMC60 RCS 135',   'dmc60_rcs135',     'Calibration Sander',   'SCM', 'DMC60 RCS 135', 1, 0, 1),
    ('DMC90 XRT 135',   'dmc90_xrt135',     'Finishing Sander',     'SCM', 'DMC90 XRT 135', 1, 0, 1),
    ('Superfici',       'superfici',        'Paint Line',            'Superfici',NULL,        1, 0, 1),
    ('Varie Osama',     'varie_osama',      'Glueing Line',          'Osama',NULL,            1, 0, 1),
    ('Elgi 1',          'elgi_1',           'Compressor',            'Elgi', NULL,            0, 0, 1),
    ('Elgi 2',          'elgi_2',           'Compressor',            'Elgi', NULL,            0, 0, 1),
    ('Aarco 1',         'aarco_1',          'Dust Collector',        'Aarco',NULL,            0, 0, 1),
    ('Aarco 2',         'aarco_2',          'Dust Collector',        'Aarco',NULL,            0, 0, 1);

INSERT OR IGNORE INTO downtime_reasons (code, label, category) VALUES
    ('setup',            'Setup / changeover',       'planned'),
    ('breakdown',        'Machine breakdown',        'maintenance'),
    ('waiting_material', 'Waiting for material',     'flow'),
    ('tool_change',      'Tool change / sharpening', 'maintenance'),
    ('no_operator',      'No operator available',    'labor'),
    ('quality_issue',    'Quality issue / rework',   'quality'),
    ('no_job',           'No job queued',            'planning'),
    ('unknown',          'Unknown',                  'unknown');

INSERT OR IGNORE INTO defect_types (code, label, process) VALUES
    ('edge_band',        'Edge banding defect',      'edge_banding'),
    ('drilling',         'Drilling / boring defect', 'cnc'),
    ('cut_size',         'Wrong size / cutting',     'cutting'),
    ('sanding',          'Sanding defect',           'sanding'),
    ('paint',            'Paint / finishing defect', 'finishing'),
    ('material_damage',  'Material damage',          'material'),
    ('missing_part',     'Missing part',             'packing'),
    ('other',            'Other defect',             'unknown');

INSERT OR IGNORE INTO connector_sync_state (connector_key, status) VALUES
    ('cabinet_vision_sql', 'not_configured'),
    ('ottimo_barcode',    'not_configured'),
    ('maestro_logs',      'not_configured');

INSERT OR IGNORE INTO connector_profiles
    (connector_key,name,record_type,transport,created_at,updated_at) VALUES
    ('cabinet_vision_sql','Cabinet Vision SQL','job_part_row','sql_server',datetime('now'),datetime('now')),
    ('ottimo_barcode','Ottimo barcode','barcode_event','file_or_api',datetime('now'),datetime('now')),
    ('maestro_logs','SCM Maestro logs','machine_log','file_tail',datetime('now'),datetime('now'));
