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

-- Individual cutting-tool lifecycle. Pool quantities remain a commissioning
-- fallback; once assets exist for a pool, verified usable assets are the
-- authoritative planning capacity.
CREATE TABLE IF NOT EXISTS tool_assets (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_key                TEXT NOT NULL UNIQUE,
    pool_id                 INTEGER NOT NULL REFERENCES tool_pools(id),
    name                    TEXT NOT NULL,
    tool_type               TEXT NOT NULL,
    manufacturer            TEXT,
    manufacturer_part_number TEXT,
    serial_number           TEXT,
    external_id             TEXT,
    life_basis              TEXT NOT NULL DEFAULT 'cycles', -- parts | cycles | runtime_minutes
    rated_life              REAL CHECK(rated_life IS NULL OR rated_life > 0),
    warning_remaining       REAL CHECK(warning_remaining IS NULL OR warning_remaining >= 0),
    parts_used              REAL NOT NULL DEFAULT 0 CHECK(parts_used >= 0),
    cycles_used             REAL NOT NULL DEFAULT 0 CHECK(cycles_used >= 0),
    runtime_minutes_used    REAL NOT NULL DEFAULT 0 CHECK(runtime_minutes_used >= 0),
    status                  TEXT NOT NULL DEFAULT 'available',
    machine_id              INTEGER REFERENCES machines(id),
    location                TEXT,
    pocket                  TEXT,
    recondition_count       INTEGER NOT NULL DEFAULT 0 CHECK(recondition_count >= 0),
    recondition_limit       INTEGER CHECK(recondition_limit IS NULL OR recondition_limit >= 0),
    life_started_at         TEXT NOT NULL,
    source                  TEXT NOT NULL DEFAULT 'manual',
    verified                INTEGER NOT NULL DEFAULT 0,
    version                 INTEGER NOT NULL DEFAULT 1,
    created_by              TEXT NOT NULL,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    UNIQUE(pool_id, external_id),
    UNIQUE(manufacturer, serial_number)
);

CREATE TABLE IF NOT EXISTS tool_usage_events (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key               TEXT NOT NULL UNIQUE,
    tool_id                 INTEGER NOT NULL REFERENCES tool_assets(id),
    machine_id              INTEGER REFERENCES machines(id),
    machine_event_id        INTEGER REFERENCES machine_events(id),
    event_type              TEXT NOT NULL,
    delta_parts             REAL NOT NULL DEFAULT 0 CHECK(delta_parts >= 0),
    delta_cycles            REAL NOT NULL DEFAULT 0 CHECK(delta_cycles >= 0),
    delta_runtime_minutes   REAL NOT NULL DEFAULT 0 CHECK(delta_runtime_minutes >= 0),
    condition_percent       REAL CHECK(condition_percent IS NULL OR condition_percent BETWEEN 0 AND 100),
    measured_wear_mm        REAL CHECK(measured_wear_mm IS NULL OR measured_wear_mm >= 0),
    source                  TEXT NOT NULL,
    actor                   TEXT NOT NULL,
    notes                   TEXT,
    occurred_at             TEXT NOT NULL,
    recorded_at             TEXT NOT NULL,
    UNIQUE(tool_id, machine_event_id)
);

CREATE TABLE IF NOT EXISTS tool_program_mappings (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_id                 INTEGER NOT NULL REFERENCES tool_assets(id),
    machine_id              INTEGER NOT NULL REFERENCES machines(id),
    cnc_file                TEXT NOT NULL,
    parts_per_cycle         REAL NOT NULL DEFAULT 1 CHECK(parts_per_cycle >= 0),
    cycles_per_event        REAL NOT NULL DEFAULT 1 CHECK(cycles_per_event > 0),
    source                  TEXT NOT NULL DEFAULT 'manual',
    verified                INTEGER NOT NULL DEFAULT 0,
    created_by              TEXT NOT NULL,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    UNIQUE(tool_id, machine_id, cnc_file)
);

CREATE TABLE IF NOT EXISTS tool_service_records (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_id                 INTEGER NOT NULL REFERENCES tool_assets(id),
    action                  TEXT NOT NULL, -- inspect | recondition | replace | retire
    end_reason              TEXT NOT NULL, -- scheduled | worn | quality | broken | other
    prior_life_value        REAL NOT NULL CHECK(prior_life_value >= 0),
    prior_parts             REAL NOT NULL CHECK(prior_parts >= 0),
    prior_cycles            REAL NOT NULL CHECK(prior_cycles >= 0),
    prior_runtime_minutes   REAL NOT NULL CHECK(prior_runtime_minutes >= 0),
    condition_percent       REAL CHECK(condition_percent IS NULL OR condition_percent BETWEEN 0 AND 100),
    measured_wear_mm        REAL CHECK(measured_wear_mm IS NULL OR measured_wear_mm >= 0),
    cost                    REAL CHECK(cost IS NULL OR cost >= 0),
    provider                TEXT,
    actor                   TEXT NOT NULL,
    notes                   TEXT,
    performed_at            TEXT NOT NULL,
    created_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_work_order_links (
    tool_id                 INTEGER NOT NULL REFERENCES tool_assets(id),
    work_order_id           INTEGER NOT NULL UNIQUE REFERENCES maintenance_work_orders(id),
    trigger_status          TEXT NOT NULL,
    trigger_life_value      REAL,
    created_at              TEXT NOT NULL,
    PRIMARY KEY(tool_id, work_order_id)
);

CREATE TABLE IF NOT EXISTS tool_quality_links (
    quality_check_id        INTEGER PRIMARY KEY REFERENCES quality_checks(id),
    tool_id                 INTEGER NOT NULL REFERENCES tool_assets(id),
    attribution             TEXT NOT NULL,
    created_at              TEXT NOT NULL
);

-- Approved SSH identities for machine-PC commissioning. Host public keys and
-- fingerprints are configuration evidence, not credentials. The HIVE private
-- deployment key remains on disk with OS ACLs and is never stored in SQLite.
CREATE TABLE IF NOT EXISTS remote_setup_hosts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id          INTEGER NOT NULL UNIQUE REFERENCES machines(id),
    host                TEXT NOT NULL,
    port                INTEGER NOT NULL DEFAULT 22 CHECK(port BETWEEN 1 AND 65535),
    username            TEXT NOT NULL,
    host_key_type       TEXT NOT NULL,
    host_key_sha256     TEXT NOT NULL,
    known_hosts_line    TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'trusted',
    trusted_by          TEXT NOT NULL,
    trusted_at          TEXT NOT NULL,
    last_connected_at   TEXT,
    last_error          TEXT,
    version             INTEGER NOT NULL DEFAULT 1,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS remote_setup_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id          INTEGER NOT NULL REFERENCES machines(id),
    action              TEXT NOT NULL,
    mode                TEXT NOT NULL,
    status              TEXT NOT NULL,
    host                TEXT NOT NULL,
    port                INTEGER NOT NULL,
    username            TEXT,
    command_summary     TEXT NOT NULL,
    exit_code           INTEGER,
    stdout_tail         TEXT,
    stderr_tail         TEXT,
    actor               TEXT NOT NULL,
    started_at          TEXT NOT NULL,
    completed_at        TEXT
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

-- Warehouse components that are consumed by production but are not sheet stock.
-- Cabinet Vision edge fields seed edge_band definitions and requirements;
-- hardware/consumable requirements remain manual until a real BOM is connected.
CREATE TABLE IF NOT EXISTS inventory_items (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    item_key            TEXT NOT NULL UNIQUE,
    name                TEXT NOT NULL,
    category            TEXT NOT NULL, -- edge_band | hardware | consumable | packaging
    uom                 TEXT NOT NULL, -- m | each | kg | l
    usage_factor        REAL NOT NULL DEFAULT 1 CHECK(usage_factor >= 1),
    reorder_point       REAL NOT NULL DEFAULT 0 CHECK(reorder_point >= 0),
    safety_stock        REAL NOT NULL DEFAULT 0 CHECK(safety_stock >= 0),
    order_multiple      REAL NOT NULL DEFAULT 1 CHECK(order_multiple > 0),
    lead_time_days      INTEGER NOT NULL DEFAULT 0 CHECK(lead_time_days >= 0),
    unit_cost           REAL CHECK(unit_cost IS NULL OR unit_cost >= 0),
    preferred_supplier  TEXT,
    source              TEXT NOT NULL DEFAULT 'manual',
    verified            INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory_lots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id             INTEGER NOT NULL REFERENCES inventory_items(id),
    lot_code            TEXT NOT NULL,
    location            TEXT,
    status              TEXT NOT NULL DEFAULT 'available', -- available | hold | depleted
    on_hand_qty         REAL NOT NULL DEFAULT 0 CHECK(on_hand_qty >= 0),
    reserved_qty        REAL NOT NULL DEFAULT 0 CHECK(reserved_qty >= 0),
    source              TEXT NOT NULL DEFAULT 'manual',
    verified            INTEGER NOT NULL DEFAULT 0,
    version             INTEGER NOT NULL DEFAULT 1,
    received_at         TEXT,
    updated_at          TEXT NOT NULL,
    UNIQUE(item_id, lot_code)
);

CREATE TABLE IF NOT EXISTS component_requirements (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    production_order_id INTEGER NOT NULL REFERENCES production_orders(id),
    item_id             INTEGER NOT NULL REFERENCES inventory_items(id),
    required_qty        REAL NOT NULL CHECK(required_qty >= 0),
    source              TEXT NOT NULL, -- cv_edges | manual_bom | connector
    confidence          TEXT NOT NULL DEFAULT 'estimated',
    notes               TEXT,
    updated_at          TEXT NOT NULL,
    UNIQUE(production_order_id, item_id)
);

CREATE TABLE IF NOT EXISTS component_reservations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id         INTEGER NOT NULL REFERENCES planning_scenarios(id),
    production_order_id INTEGER NOT NULL REFERENCES production_orders(id),
    inventory_lot_id    INTEGER NOT NULL REFERENCES inventory_lots(id),
    quantity            REAL NOT NULL CHECK(quantity > 0),
    status              TEXT NOT NULL DEFAULT 'committed', -- committed | consumed | released
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE(scenario_id, production_order_id, inventory_lot_id)
);

-- A remnant is a uniquely measured rectangular panel left after cutting. HIVE
-- credits only one verified remnant against one physical part instance.
CREATE TABLE IF NOT EXISTS material_remnants (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    remnant_key         TEXT NOT NULL UNIQUE,
    material_id         INTEGER NOT NULL REFERENCES material_definitions(id),
    source_material_lot_id INTEGER REFERENCES material_lots(id),
    length_mm           REAL NOT NULL CHECK(length_mm > 0),
    width_mm            REAL NOT NULL CHECK(width_mm > 0),
    thickness_mm        REAL CHECK(thickness_mm IS NULL OR thickness_mm > 0),
    grain_direction     TEXT NOT NULL DEFAULT 'length', -- length | none
    usable_area_m2      REAL NOT NULL CHECK(usable_area_m2 > 0),
    location            TEXT,
    status              TEXT NOT NULL DEFAULT 'available', -- available | reserved | consumed | hold | scrapped
    source              TEXT NOT NULL DEFAULT 'manual_measurement',
    verified            INTEGER NOT NULL DEFAULT 0,
    version             INTEGER NOT NULL DEFAULT 1,
    created_by          TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS remnant_reservations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id         INTEGER NOT NULL REFERENCES planning_scenarios(id),
    production_order_id INTEGER NOT NULL REFERENCES production_orders(id),
    remnant_id          INTEGER NOT NULL REFERENCES material_remnants(id),
    part_id             INTEGER NOT NULL REFERENCES parts(id),
    instance_ordinal    INTEGER NOT NULL CHECK(instance_ordinal >= 1),
    credited_area_m2    REAL NOT NULL CHECK(credited_area_m2 > 0),
    status              TEXT NOT NULL DEFAULT 'committed', -- committed | consumed | released
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE(scenario_id, remnant_id),
    UNIQUE(scenario_id, part_id, instance_ordinal)
);

CREATE TABLE IF NOT EXISTS inventory_movements (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    object_type         TEXT NOT NULL, -- component_lot | sheet_lot | remnant
    object_key          TEXT NOT NULL,
    movement_type       TEXT NOT NULL, -- receipt | adjustment | reservation | release | issue | create | scrap
    quantity            REAL NOT NULL,
    uom                 TEXT NOT NULL,
    balance_after       REAL,
    production_order_id INTEGER REFERENCES production_orders(id),
    scenario_id         INTEGER REFERENCES planning_scenarios(id),
    source              TEXT NOT NULL,
    actor               TEXT NOT NULL,
    idempotency_key     TEXT UNIQUE,
    notes               TEXT,
    ts                  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory_sync_issues (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    production_order_id INTEGER REFERENCES production_orders(id),
    part_id             INTEGER REFERENCES parts(id),
    source_field        TEXT NOT NULL,
    raw_value           TEXT,
    issue_code          TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'open',
    updated_at          TEXT NOT NULL,
    UNIQUE(production_order_id, part_id, source_field, issue_code)
);

-- Vendor-neutral procurement master data. HIVE item/material keys remain the
-- internal identity; supplier SKUs, GTINs, packs, and purchase units are
-- versioned boundary mappings.
CREATE TABLE IF NOT EXISTS procurement_suppliers (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_key        TEXT NOT NULL UNIQUE,
    name                TEXT NOT NULL,
    legal_name          TEXT,
    currency            TEXT NOT NULL DEFAULT 'INR',
    lead_time_days      INTEGER NOT NULL DEFAULT 0 CHECK(lead_time_days >= 0),
    gln                 TEXT,
    tax_id              TEXT,
    email               TEXT,
    external_system     TEXT,
    source              TEXT NOT NULL DEFAULT 'manual',
    active              INTEGER NOT NULL DEFAULT 1,
    verified            INTEGER NOT NULL DEFAULT 0,
    version             INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS procurement_item_mappings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id         INTEGER NOT NULL REFERENCES procurement_suppliers(id),
    object_type         TEXT NOT NULL, -- component | sheet
    object_key          TEXT NOT NULL,
    supplier_sku        TEXT NOT NULL,
    gtin                TEXT,
    purchase_uom        TEXT NOT NULL,
    conversion_factor   REAL NOT NULL DEFAULT 1 CHECK(conversion_factor > 0),
    order_multiple      REAL NOT NULL DEFAULT 1 CHECK(order_multiple > 0),
    min_order_qty       REAL NOT NULL DEFAULT 0 CHECK(min_order_qty >= 0),
    unit_price          REAL CHECK(unit_price IS NULL OR unit_price >= 0),
    currency            TEXT NOT NULL DEFAULT 'INR',
    preferred           INTEGER NOT NULL DEFAULT 0,
    source              TEXT NOT NULL DEFAULT 'manual',
    verified            INTEGER NOT NULL DEFAULT 0,
    version             INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE(supplier_id, object_type, object_key)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_procurement_preferred_mapping
    ON procurement_item_mappings(object_type, object_key) WHERE preferred=1;

CREATE TABLE IF NOT EXISTS purchase_orders (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    po_number           TEXT NOT NULL UNIQUE,
    supplier_id         INTEGER NOT NULL REFERENCES procurement_suppliers(id),
    status              TEXT NOT NULL DEFAULT 'draft',
    currency            TEXT NOT NULL DEFAULT 'INR',
    expected_at         TEXT,
    external_id         TEXT,
    source              TEXT NOT NULL DEFAULT 'shortage_recommendation',
    notes               TEXT,
    version             INTEGER NOT NULL DEFAULT 1,
    created_by          TEXT NOT NULL,
    approved_by         TEXT,
    approved_at         TEXT,
    queued_at           TEXT,
    sent_at             TEXT,
    closed_at           TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE(supplier_id, external_id)
);

CREATE TABLE IF NOT EXISTS purchase_order_lines (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_order_id   INTEGER NOT NULL REFERENCES purchase_orders(id),
    line_number         INTEGER NOT NULL CHECK(line_number >= 1),
    mapping_id          INTEGER REFERENCES procurement_item_mappings(id),
    object_type         TEXT NOT NULL, -- component | sheet
    object_key          TEXT NOT NULL,
    item_name           TEXT NOT NULL,
    supplier_sku        TEXT NOT NULL,
    internal_uom        TEXT NOT NULL,
    purchase_uom        TEXT NOT NULL,
    conversion_factor   REAL NOT NULL CHECK(conversion_factor > 0),
    ordered_qty         REAL NOT NULL CHECK(ordered_qty > 0),
    received_qty        REAL NOT NULL DEFAULT 0 CHECK(received_qty >= 0),
    rejected_qty        REAL NOT NULL DEFAULT 0 CHECK(rejected_qty >= 0),
    unit_price          REAL CHECK(unit_price IS NULL OR unit_price >= 0),
    currency            TEXT NOT NULL,
    need_by_at          TEXT,
    status              TEXT NOT NULL DEFAULT 'open',
    notes               TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE(purchase_order_id, line_number),
    UNIQUE(purchase_order_id, object_type, object_key)
);

CREATE TABLE IF NOT EXISTS goods_receipts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_key         TEXT NOT NULL UNIQUE,
    purchase_order_id   INTEGER NOT NULL REFERENCES purchase_orders(id),
    supplier_id         INTEGER NOT NULL REFERENCES procurement_suppliers(id),
    external_receipt_id TEXT,
    source_hash         TEXT,
    received_at         TEXT NOT NULL,
    location            TEXT,
    status              TEXT NOT NULL DEFAULT 'posted',
    source              TEXT NOT NULL DEFAULT 'manual',
    verified            INTEGER NOT NULL DEFAULT 0,
    actor               TEXT NOT NULL,
    notes               TEXT,
    created_at          TEXT NOT NULL,
    UNIQUE(supplier_id, external_receipt_id)
);

CREATE TABLE IF NOT EXISTS goods_receipt_lines (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    goods_receipt_id    INTEGER NOT NULL REFERENCES goods_receipts(id),
    purchase_order_line_id INTEGER NOT NULL REFERENCES purchase_order_lines(id),
    lot_code            TEXT NOT NULL,
    accepted_qty        REAL NOT NULL DEFAULT 0 CHECK(accepted_qty >= 0),
    rejected_qty        REAL NOT NULL DEFAULT 0 CHECK(rejected_qty >= 0),
    purchase_uom        TEXT NOT NULL,
    conversion_factor   REAL NOT NULL CHECK(conversion_factor > 0),
    accepted_internal_qty REAL NOT NULL DEFAULT 0 CHECK(accepted_internal_qty >= 0),
    rejection_reason    TEXT,
    location            TEXT,
    created_at          TEXT NOT NULL,
    UNIQUE(goods_receipt_id, purchase_order_line_id)
);

CREATE TABLE IF NOT EXISTS procurement_outbox (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    document_type       TEXT NOT NULL,
    object_type         TEXT NOT NULL,
    object_key          TEXT NOT NULL,
    payload_json        TEXT NOT NULL,
    payload_sha256      TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending', -- pending | delivered | failed
    attempts            INTEGER NOT NULL DEFAULT 0,
    external_id         TEXT,
    last_error          TEXT,
    created_at          TEXT NOT NULL,
    delivered_at        TEXT,
    updated_at          TEXT NOT NULL,
    UNIQUE(document_type, object_type, object_key)
);

CREATE TABLE IF NOT EXISTS procurement_exchange_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    direction           TEXT NOT NULL, -- import | export
    document_type       TEXT NOT NULL,
    source_sha256       TEXT NOT NULL,
    file_name           TEXT,
    mode                TEXT NOT NULL,
    status              TEXT NOT NULL,
    records_seen        INTEGER NOT NULL DEFAULT 0,
    records_accepted    INTEGER NOT NULL DEFAULT 0,
    records_rejected    INTEGER NOT NULL DEFAULT 0,
    records_imported    INTEGER NOT NULL DEFAULT 0,
    summary_json        TEXT NOT NULL DEFAULT '{}',
    actor               TEXT NOT NULL,
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS procurement_exchange_issues (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              INTEGER NOT NULL REFERENCES procurement_exchange_runs(id),
    record_index        INTEGER,
    field_key           TEXT,
    code                TEXT NOT NULL,
    detail              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS procurement_import_batches (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    document_type       TEXT NOT NULL,
    source_sha256       TEXT NOT NULL,
    run_id              INTEGER NOT NULL REFERENCES procurement_exchange_runs(id),
    imported_at         TEXT NOT NULL,
    UNIQUE(document_type, source_sha256)
);

CREATE INDEX IF NOT EXISTS idx_po_status ON purchase_orders(status, expected_at);
CREATE INDEX IF NOT EXISTS idx_po_line_object ON purchase_order_lines(object_type, object_key, status);
CREATE INDEX IF NOT EXISTS idx_receipt_po ON goods_receipts(purchase_order_id, received_at);
CREATE INDEX IF NOT EXISTS idx_procurement_outbox_status ON procurement_outbox(status, id);

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

-- Read-only industrial I/O commissioning. Draft settings can be edited, but
-- only immutable approved contracts are used by the live poller.
CREATE TABLE IF NOT EXISTS industrial_profiles (
    profile_key         TEXT PRIMARY KEY,
    machine_id          INTEGER REFERENCES machines(id),
    name                TEXT NOT NULL,
    protocol            TEXT NOT NULL, -- modbus_tcp | opcua | mqtt_json
    template_key        TEXT,
    endpoint            TEXT,
    credential_env      TEXT,
    poll_interval_s     REAL NOT NULL DEFAULT 15,
    settings_json       TEXT NOT NULL DEFAULT '{}',
    enabled             INTEGER NOT NULL DEFAULT 0,
    verified            INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'site_configuration_required',
    active_contract_id  INTEGER,
    version             INTEGER NOT NULL DEFAULT 1,
    last_probe_at       TEXT,
    last_poll_at        TEXT,
    last_success_at     TEXT,
    last_error          TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS industrial_contract_versions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_key         TEXT NOT NULL REFERENCES industrial_profiles(profile_key),
    version             INTEGER NOT NULL,
    protocol            TEXT NOT NULL,
    endpoint             TEXT NOT NULL,
    signals_json        TEXT NOT NULL,
    settings_json       TEXT NOT NULL,
    evidence_sha256     TEXT NOT NULL,
    approved_by         TEXT NOT NULL,
    approved_at         TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    UNIQUE(profile_key, version)
);

CREATE TABLE IF NOT EXISTS industrial_commissioning_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_key         TEXT NOT NULL REFERENCES industrial_profiles(profile_key),
    contract_id         INTEGER REFERENCES industrial_contract_versions(id),
    mode                TEXT NOT NULL, -- simulate | probe | poll | mqtt
    status              TEXT NOT NULL,
    evidence_sha256     TEXT NOT NULL,
    signals_seen        INTEGER NOT NULL DEFAULT 0,
    signals_good        INTEGER NOT NULL DEFAULT 0,
    summary_json        TEXT NOT NULL DEFAULT '{}',
    actor               TEXT NOT NULL,
    started_at          TEXT NOT NULL,
    completed_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS telemetry_samples (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_key         TEXT NOT NULL REFERENCES industrial_profiles(profile_key),
    machine_id          INTEGER REFERENCES machines(id),
    signal_key          TEXT NOT NULL,
    value_num           REAL,
    value_text          TEXT,
    unit                TEXT,
    quality             TEXT NOT NULL, -- good | uncertain | bad
    source_ts           TEXT NOT NULL,
    received_at         TEXT NOT NULL,
    fingerprint         TEXT NOT NULL UNIQUE,
    contract_id         INTEGER NOT NULL REFERENCES industrial_contract_versions(id)
);

CREATE TABLE IF NOT EXISTS telemetry_latest (
    profile_key         TEXT NOT NULL REFERENCES industrial_profiles(profile_key),
    signal_key          TEXT NOT NULL,
    machine_id          INTEGER REFERENCES machines(id),
    value_num           REAL,
    value_text          TEXT,
    unit                TEXT,
    quality             TEXT NOT NULL,
    source_ts           TEXT NOT NULL,
    received_at         TEXT NOT NULL,
    contract_id         INTEGER NOT NULL REFERENCES industrial_contract_versions(id),
    PRIMARY KEY(profile_key, signal_key)
);

CREATE TABLE IF NOT EXISTS telemetry_hourly (
    profile_key         TEXT NOT NULL REFERENCES industrial_profiles(profile_key),
    signal_key          TEXT NOT NULL,
    hour_ts             TEXT NOT NULL,
    unit                TEXT,
    sample_count        INTEGER NOT NULL,
    good_count          INTEGER NOT NULL,
    min_value           REAL,
    max_value           REAL,
    avg_value           REAL,
    first_value         REAL,
    last_value          REAL,
    PRIMARY KEY(profile_key, signal_key, hour_ts)
);

CREATE TABLE IF NOT EXISTS industrial_profile_state (
    profile_key         TEXT PRIMARY KEY REFERENCES industrial_profiles(profile_key),
    current_state       TEXT NOT NULL DEFAULT 'unknown',
    pending_state       TEXT,
    pending_count       INTEGER NOT NULL DEFAULT 0,
    last_power_w        REAL,
    last_transition_at  TEXT,
    updated_at          TEXT NOT NULL
);

-- Human-approved continuous-improvement loop. Recommendations are stable,
-- experiments freeze their baseline before implementation, and every state
-- change is retained as immutable evidence.
CREATE TABLE IF NOT EXISTS improvement_recommendations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_key  TEXT NOT NULL UNIQUE,
    category            TEXT NOT NULL,
    title               TEXT NOT NULL,
    action              TEXT NOT NULL,
    target_type         TEXT NOT NULL DEFAULT 'factory',
    target_key          TEXT NOT NULL DEFAULT 'factory',
    cause_code          TEXT NOT NULL DEFAULT 'unclassified',
    confidence          TEXT NOT NULL,
    metric_hint         TEXT,
    target_direction    TEXT,
    evidence_json       TEXT NOT NULL DEFAULT '[]',
    source_window_start TEXT,
    source_window_end   TEXT,
    source_generated_at TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'proposed',
    owner               TEXT,
    resolution_notes    TEXT,
    version             INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS improvement_experiments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_id   INTEGER NOT NULL REFERENCES improvement_recommendations(id),
    status              TEXT NOT NULL DEFAULT 'accepted',
    owner               TEXT NOT NULL,
    hypothesis          TEXT NOT NULL,
    primary_metric      TEXT NOT NULL,
    target_direction    TEXT NOT NULL,
    target_delta_pct    REAL NOT NULL,
    baseline_hours      INTEGER NOT NULL,
    evaluation_hours    INTEGER NOT NULL,
    min_samples         INTEGER NOT NULL,
    design_type         TEXT NOT NULL DEFAULT 'before_after',
    confounders_json    TEXT NOT NULL DEFAULT '[]',
    baseline_start      TEXT,
    baseline_end        TEXT,
    implemented_at      TEXT,
    evaluation_due_at   TEXT,
    baseline_json       TEXT,
    evaluation_json     TEXT,
    guardrails_json     TEXT,
    outcome             TEXT,
    effect_pct          REAL,
    ci_lower_pct        REAL,
    ci_upper_pct        REAL,
    notes               TEXT,
    version             INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS improvement_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_id   INTEGER NOT NULL REFERENCES improvement_recommendations(id),
    experiment_id       INTEGER REFERENCES improvement_experiments(id),
    event_type          TEXT NOT NULL,
    from_status         TEXT,
    to_status           TEXT,
    actor               TEXT NOT NULL,
    notes               TEXT,
    payload_json        TEXT NOT NULL DEFAULT '{}',
    ts                  TEXT NOT NULL
);

-- Incident diagnosis is separate from optimization: correlations remain
-- hypotheses until a named operator confirms a cause. Re-analysis appends a
-- new hypothesis version rather than overwriting prior evidence.
CREATE TABLE IF NOT EXISTS diagnostic_cases (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    case_key            TEXT NOT NULL UNIQUE,
    incident_type       TEXT NOT NULL, -- alarm | downtime | quality
    source_type         TEXT NOT NULL,
    source_id           INTEGER NOT NULL,
    machine_id          INTEGER REFERENCES machines(id),
    part_id             INTEGER REFERENCES parts(id),
    occurred_at         TEXT NOT NULL,
    ended_at            TEXT,
    severity            TEXT NOT NULL,
    symptom_code        TEXT NOT NULL,
    symptom_label       TEXT NOT NULL,
    source_json         TEXT NOT NULL DEFAULT '{}',
    features_json       TEXT NOT NULL DEFAULT '{}',
    status              TEXT NOT NULL DEFAULT 'open', -- open | confirmed | dismissed
    top_hypothesis_code TEXT,
    confidence          TEXT NOT NULL DEFAULT 'low',
    actual_cause_code   TEXT,
    corrective_action   TEXT,
    resolution_notes    TEXT,
    analysis_version    INTEGER NOT NULL DEFAULT 0,
    version             INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE(source_type, source_id)
);

CREATE TABLE IF NOT EXISTS diagnostic_hypotheses (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id             INTEGER NOT NULL REFERENCES diagnostic_cases(id),
    analysis_version    INTEGER NOT NULL,
    cause_code          TEXT NOT NULL,
    rank                INTEGER NOT NULL,
    evidence_score      REAL NOT NULL,
    prior_score         REAL NOT NULL,
    evidence_json       TEXT NOT NULL DEFAULT '[]',
    contradictions_json TEXT NOT NULL DEFAULT '[]',
    data_gaps_json      TEXT NOT NULL DEFAULT '[]',
    created_at          TEXT NOT NULL,
    UNIQUE(case_id, analysis_version, cause_code)
);

CREATE TABLE IF NOT EXISTS diagnostic_case_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id             INTEGER NOT NULL REFERENCES diagnostic_cases(id),
    event_type          TEXT NOT NULL,
    from_status         TEXT,
    to_status           TEXT,
    actor               TEXT NOT NULL,
    notes               TEXT,
    payload_json        TEXT NOT NULL DEFAULT '{}',
    ts                  TEXT NOT NULL
);

-- Rationalized operator alarms are derived from actionable HIVE conditions.
-- Source events remain immutable; this layer owns acknowledgement, escalation,
-- resolution, and commissioned external delivery.
CREATE TABLE IF NOT EXISTS alert_instances (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_key           TEXT NOT NULL UNIQUE,
    rule_key            TEXT NOT NULL,
    source_type         TEXT NOT NULL,
    source_id           TEXT NOT NULL,
    machine_id          INTEGER REFERENCES machines(id),
    domain              TEXT NOT NULL,
    severity            TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'open', -- open | acknowledged | snoozed | resolved
    title               TEXT NOT NULL,
    detail              TEXT NOT NULL,
    required_action     TEXT NOT NULL,
    consequence         TEXT NOT NULL,
    owner_role          TEXT NOT NULL,
    owner               TEXT,
    evidence_token      TEXT NOT NULL,
    evidence_json       TEXT NOT NULL DEFAULT '{}',
    occurred_at         TEXT NOT NULL,
    first_seen_at       TEXT NOT NULL,
    last_seen_at        TEXT NOT NULL,
    response_due_at     TEXT NOT NULL,
    occurrence_count    INTEGER NOT NULL DEFAULT 1,
    escalation_level    INTEGER NOT NULL DEFAULT 0,
    escalated_at        TEXT,
    acknowledged_at     TEXT,
    acknowledged_by     TEXT,
    snoozed_until       TEXT,
    resolved_at         TEXT,
    resolved_by         TEXT,
    resolution_notes    TEXT,
    version             INTEGER NOT NULL DEFAULT 1,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id            INTEGER NOT NULL REFERENCES alert_instances(id),
    event_type          TEXT NOT NULL,
    from_status         TEXT,
    to_status           TEXT NOT NULL,
    actor               TEXT NOT NULL,
    notes               TEXT,
    payload_json        TEXT NOT NULL DEFAULT '{}',
    ts                  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_destinations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    destination_key     TEXT NOT NULL UNIQUE,
    name                TEXT NOT NULL,
    channel             TEXT NOT NULL DEFAULT 'webhook',
    endpoint            TEXT NOT NULL,
    secret_env          TEXT,
    min_severity        TEXT NOT NULL DEFAULT 'warning',
    enabled             INTEGER NOT NULL DEFAULT 0,
    verified_at         TEXT,
    last_tested_at      TEXT,
    last_error          TEXT,
    version             INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_deliveries (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id            INTEGER REFERENCES alert_instances(id),
    alert_event_id      INTEGER REFERENCES alert_events(id),
    destination_id      INTEGER NOT NULL REFERENCES alert_destinations(id),
    delivery_key        TEXT NOT NULL UNIQUE,
    event_type          TEXT NOT NULL,
    payload_json        TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending', -- pending | delivered | failed
    attempts            INTEGER NOT NULL DEFAULT 0,
    next_attempt_at     TEXT,
    response_code       INTEGER,
    response_body       TEXT,
    last_error          TEXT,
    created_at          TEXT NOT NULL,
    delivered_at        TEXT,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_runtime_settings (
    id                  INTEGER PRIMARY KEY CHECK(id=1),
    auto_sync           INTEGER NOT NULL DEFAULT 0,
    auto_dispatch       INTEGER NOT NULL DEFAULT 0,
    interval_seconds    INTEGER NOT NULL DEFAULT 60,
    version             INTEGER NOT NULL DEFAULT 1,
    updated_by          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_admin_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type         TEXT NOT NULL,
    target_key          TEXT NOT NULL,
    event_type          TEXT NOT NULL,
    actor               TEXT NOT NULL,
    payload_json        TEXT NOT NULL DEFAULT '{}',
    ts                  TEXT NOT NULL
);

-- Local-first access control. Browser session and API-key secrets are stored as
-- one-way hashes; human passwords use Argon2id strings with embedded parameters.
CREATE TABLE IF NOT EXISTS auth_users (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    username            TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name        TEXT NOT NULL,
    password_hash       TEXT NOT NULL,
    role                TEXT NOT NULL,
    active              INTEGER NOT NULL DEFAULT 1,
    failed_logins       INTEGER NOT NULL DEFAULT 0,
    locked_until        TEXT,
    last_login_at       TEXT,
    password_changed_at TEXT NOT NULL,
    created_by          TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    version             INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL REFERENCES auth_users(id),
    token_hash          TEXT NOT NULL UNIQUE,
    csrf_token          TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    expires_at          TEXT NOT NULL,
    revoked_at          TEXT,
    client_ip           TEXT,
    user_agent          TEXT
);

CREATE TABLE IF NOT EXISTS auth_api_keys (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL UNIQUE,
    key_prefix          TEXT NOT NULL,
    token_hash          TEXT NOT NULL UNIQUE,
    permissions_json    TEXT NOT NULL DEFAULT '[]',
    active              INTEGER NOT NULL DEFAULT 1,
    expires_at          TEXT,
    last_used_at        TEXT,
    created_by          TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    revoked_at          TEXT,
    version             INTEGER NOT NULL DEFAULT 1
);

-- Machine MQTT identities. Private client keys are generated directly into a
-- one-time enrollment bundle and are never persisted in the database.
CREATE TABLE IF NOT EXISTS mqtt_enrollments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id          INTEGER NOT NULL REFERENCES machines(id),
    common_name         TEXT NOT NULL,
    certificate_serial  TEXT NOT NULL UNIQUE,
    certificate_sha256  TEXT NOT NULL UNIQUE,
    certificate_pem     TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'active',
    issued_by           TEXT NOT NULL,
    issued_at           TEXT NOT NULL,
    expires_at          TEXT NOT NULL,
    revoked_by          TEXT,
    revoked_at          TEXT,
    revocation_reason   TEXT,
    bundle_downloaded_at TEXT,
    version             INTEGER NOT NULL DEFAULT 1
);

-- Immutable probabilistic forecasts. The input signature makes stale results
-- detectable; completed production-order events later provide calibration truth.
CREATE TABLE IF NOT EXISTS production_forecasts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    input_signature     TEXT NOT NULL,
    policy              TEXT NOT NULL,
    sample_count        INTEGER NOT NULL,
    seed                INTEGER NOT NULL,
    status              TEXT NOT NULL,
    request_json        TEXT NOT NULL,
    result_json         TEXT NOT NULL,
    generated_at        TEXT NOT NULL
);

-- Event-driven rolling-horizon recovery analyses. The linked planning scenario
-- remains the approval authority; this table preserves why replanning started
-- and the stability/benefit evidence shown to the planner.
CREATE TABLE IF NOT EXISTS schedule_recovery_assessments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    active_scenario_id  INTEGER REFERENCES planning_scenarios(id),
    planning_scenario_id INTEGER REFERENCES planning_scenarios(id),
    input_signature     TEXT NOT NULL,
    trigger_signature   TEXT NOT NULL,
    status              TEXT NOT NULL,
    triggers_json       TEXT NOT NULL,
    result_json         TEXT NOT NULL,
    created_by          TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    decision            TEXT,
    selected_policy     TEXT,
    decided_by          TEXT,
    decided_at          TEXT,
    notes               TEXT
);

-- Assumption-only commissioning analyses are isolated from operational truth.
-- A lab run may only append its immutable result here; it has no foreign keys
-- into production models, routes, schedules, forecasts, or machine events.
CREATE TABLE IF NOT EXISTS virtual_factory_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    assumptions_sha256  TEXT NOT NULL,
    assumptions_version TEXT NOT NULL,
    sample_count        INTEGER NOT NULL,
    seed                INTEGER NOT NULL,
    actor               TEXT NOT NULL,
    result_json         TEXT NOT NULL,
    created_at          TEXT NOT NULL
);

-- Guided field studies are evidence for reviewing engineering priors only.
-- They are deliberately separate from cycle_observations/cycle_models, which
-- are derived from validated production events and own production readiness.
CREATE TABLE IF NOT EXISTS commissioning_evidence_studies (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    study_key           TEXT NOT NULL UNIQUE,
    machine_id          INTEGER NOT NULL REFERENCES machines(id),
    title               TEXT NOT NULL,
    goal                TEXT NOT NULL,
    method_version      TEXT NOT NULL,
    assumptions_sha256  TEXT NOT NULL,
    status              TEXT NOT NULL,
    target_samples      INTEGER NOT NULL,
    target_strata       INTEGER NOT NULL,
    version             INTEGER NOT NULL DEFAULT 1,
    created_by          TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    started_at          TEXT,
    submitted_at        TEXT,
    decided_by          TEXT,
    decided_at          TEXT,
    decision_notes      TEXT
);

CREATE TABLE IF NOT EXISTS commissioning_evidence_observations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    study_id            INTEGER NOT NULL REFERENCES commissioning_evidence_studies(id),
    source_record_id    TEXT NOT NULL,
    source_sha256       TEXT NOT NULL,
    measured_at         TEXT NOT NULL,
    shift_key           TEXT,
    measurement_method  TEXT NOT NULL,
    observer            TEXT NOT NULL,
    product_family      TEXT NOT NULL,
    program_key         TEXT,
    unit_count          INTEGER NOT NULL,
    operator_count      INTEGER NOT NULL,
    queue_s             REAL NOT NULL,
    setup_s             REAL NOT NULL,
    load_s              REAL NOT NULL,
    process_s           REAL NOT NULL,
    blocked_s           REAL NOT NULL,
    starved_s           REAL NOT NULL,
    unload_s            REAL NOT NULL,
    quality_s           REAL NOT NULL,
    rework_s            REAL NOT NULL,
    total_s             REAL NOT NULL,
    good_units          INTEGER,
    reject_units        INTEGER NOT NULL,
    notes               TEXT,
    validity            TEXT NOT NULL DEFAULT 'accepted',
    exclusion_reason    TEXT,
    raw_payload_json    TEXT NOT NULL,
    created_by          TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    UNIQUE(study_id, source_record_id)
);

CREATE TABLE IF NOT EXISTS commissioning_evidence_analyses (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    study_id            INTEGER NOT NULL REFERENCES commissioning_evidence_studies(id),
    input_signature     TEXT NOT NULL,
    assumptions_sha256  TEXT NOT NULL,
    sample_count        INTEGER NOT NULL,
    result_json         TEXT NOT NULL,
    created_by          TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    UNIQUE(study_id, input_signature)
);

CREATE TABLE IF NOT EXISTS commissioning_evidence_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    study_id            INTEGER NOT NULL REFERENCES commissioning_evidence_studies(id),
    event_type          TEXT NOT NULL,
    actor               TEXT NOT NULL,
    from_status         TEXT,
    to_status           TEXT,
    details_json        TEXT NOT NULL DEFAULT '{}',
    ts                  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type          TEXT NOT NULL,
    actor_user_id       INTEGER REFERENCES auth_users(id),
    actor_name          TEXT NOT NULL,
    target_type         TEXT,
    target_key          TEXT,
    success             INTEGER NOT NULL,
    client_ip           TEXT,
    user_agent          TEXT,
    details_json        TEXT NOT NULL DEFAULT '{}',
    ts                  TEXT NOT NULL
);

INSERT OR IGNORE INTO alert_runtime_settings
    (id,auto_sync,auto_dispatch,interval_seconds,updated_by,updated_at)
VALUES (1,0,0,60,'schema','1970-01-01T00:00:00+00:00');

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
CREATE INDEX IF NOT EXISTS idx_inventory_lots_item_status ON inventory_lots(item_id, status);
CREATE INDEX IF NOT EXISTS idx_component_requirements_order ON component_requirements(production_order_id, item_id);
CREATE INDEX IF NOT EXISTS idx_component_reservations_status ON component_reservations(status, production_order_id);
CREATE INDEX IF NOT EXISTS idx_remnants_material_status ON material_remnants(material_id, status, verified);
CREATE INDEX IF NOT EXISTS idx_remnant_reservations_status ON remnant_reservations(status, production_order_id);
CREATE INDEX IF NOT EXISTS idx_inventory_movements_object_ts ON inventory_movements(object_type, object_key, ts DESC);
CREATE INDEX IF NOT EXISTS idx_inventory_issues_status ON inventory_sync_issues(status, production_order_id);
CREATE INDEX IF NOT EXISTS idx_calendar_resource_day ON work_calendar_windows(resource_type, resource_key, weekday);
CREATE INDEX IF NOT EXISTS idx_tool_assets_pool_status ON tool_assets(pool_id, status, verified);
CREATE INDEX IF NOT EXISTS idx_tool_assets_machine_status ON tool_assets(machine_id, status);
CREATE INDEX IF NOT EXISTS idx_tool_usage_tool_time ON tool_usage_events(tool_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_tool_usage_machine_event ON tool_usage_events(machine_event_id);
CREATE INDEX IF NOT EXISTS idx_tool_program_machine_file ON tool_program_mappings(machine_id, cnc_file, verified);
CREATE INDEX IF NOT EXISTS idx_tool_service_tool_time ON tool_service_records(tool_id, performed_at DESC);
CREATE INDEX IF NOT EXISTS idx_remote_setup_runs_machine_time ON remote_setup_runs(machine_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_remote_setup_runs_status_time ON remote_setup_runs(status, started_at DESC);
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
CREATE INDEX IF NOT EXISTS idx_industrial_profiles_machine ON industrial_profiles(machine_id, protocol);
CREATE INDEX IF NOT EXISTS idx_industrial_runs_profile_time ON industrial_commissioning_runs(profile_key, completed_at DESC);
CREATE INDEX IF NOT EXISTS idx_telemetry_profile_signal_ts ON telemetry_samples(profile_key, signal_key, source_ts DESC);
CREATE INDEX IF NOT EXISTS idx_telemetry_machine_ts ON telemetry_samples(machine_id, source_ts DESC);
CREATE INDEX IF NOT EXISTS idx_improvement_recommendations_status ON improvement_recommendations(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_improvement_experiments_recommendation ON improvement_experiments(recommendation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_improvement_experiments_outcome ON improvement_experiments(outcome, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_improvement_events_recommendation_ts ON improvement_events(recommendation_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_diagnostic_cases_status_time ON diagnostic_cases(status, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_diagnostic_cases_machine_time ON diagnostic_cases(machine_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_diagnostic_hypotheses_case_version ON diagnostic_hypotheses(case_id, analysis_version DESC, rank);
CREATE INDEX IF NOT EXISTS idx_diagnostic_case_events_case_ts ON diagnostic_case_events(case_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_alert_instances_status_severity ON alert_instances(status, severity, first_seen_at);
CREATE INDEX IF NOT EXISTS idx_alert_instances_source ON alert_instances(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_alert_events_alert_ts ON alert_events(alert_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_alert_deliveries_status_due ON alert_deliveries(status, next_attempt_at, id);
CREATE INDEX IF NOT EXISTS idx_alert_admin_events_target_ts ON alert_admin_events(target_type, target_key, ts DESC);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_token ON auth_sessions(token_hash, revoked_at, expires_at);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id, revoked_at);
CREATE INDEX IF NOT EXISTS idx_auth_api_keys_token ON auth_api_keys(token_hash, active);
CREATE INDEX IF NOT EXISTS idx_auth_events_time ON auth_events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_auth_events_actor ON auth_events(actor_user_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_mqtt_enrollments_machine_status ON mqtt_enrollments(machine_id, status, expires_at);
CREATE INDEX IF NOT EXISTS idx_production_forecasts_generated ON production_forecasts(generated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_production_forecasts_signature ON production_forecasts(input_signature, policy, sample_count);
CREATE INDEX IF NOT EXISTS idx_schedule_recovery_created ON schedule_recovery_assessments(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_schedule_recovery_trigger ON schedule_recovery_assessments(trigger_signature, input_signature);
CREATE INDEX IF NOT EXISTS idx_virtual_factory_runs_created ON virtual_factory_runs(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_commissioning_studies_machine_status ON commissioning_evidence_studies(machine_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_commissioning_observations_study_time ON commissioning_evidence_observations(study_id, measured_at, id);
CREATE INDEX IF NOT EXISTS idx_commissioning_analyses_study_time ON commissioning_evidence_analyses(study_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_commissioning_events_study_time ON commissioning_evidence_events(study_id, ts, id);

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
