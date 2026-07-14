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

CREATE TABLE IF NOT EXISTS connector_sync_state (
    connector_key   TEXT PRIMARY KEY,
    status          TEXT NOT NULL DEFAULT 'not_configured',
    last_sync_at    TEXT,
    last_cursor     TEXT,
    last_error      TEXT,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
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
CREATE INDEX IF NOT EXISTS idx_quality_checks_result_ts ON quality_checks(result, ts DESC);
CREATE INDEX IF NOT EXISTS idx_rework_status_created ON rework_tasks(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_barcode_events_ts ON barcode_events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_route_steps_part_status ON part_route_steps(part_id, status, step_index);
CREATE INDEX IF NOT EXISTS idx_route_step_events_step_ts ON route_step_events(route_step_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_route_exceptions_status_ts ON route_exceptions(status, ts DESC);
CREATE INDEX IF NOT EXISTS idx_planning_scenarios_status_created ON planning_scenarios(status, created_at DESC);

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
    ('ottimo_barcode',    'not_configured');
