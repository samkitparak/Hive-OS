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

CREATE TABLE IF NOT EXISTS connector_sync_state (
    connector_key   TEXT PRIMARY KEY,
    status          TEXT NOT NULL DEFAULT 'not_configured',
    last_sync_at    TEXT,
    last_cursor     TEXT,
    last_error      TEXT,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_parts_job ON parts(job_id);
CREATE INDEX IF NOT EXISTS idx_parts_cnc_back ON parts(cnc_file_back);
CREATE INDEX IF NOT EXISTS idx_parts_cnc_front ON parts(cnc_file_front);
CREATE INDEX IF NOT EXISTS idx_machine_events_machine_ts ON machine_events(machine_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_machine_events_part_ts ON machine_events(part_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_machine_events_type_ts ON machine_events(event_type, ts DESC);
CREATE INDEX IF NOT EXISTS idx_oee_snapshots_machine_window ON oee_snapshots(machine_id, window_end DESC);
CREATE INDEX IF NOT EXISTS idx_downtime_status_started ON downtime_events(status, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_work_orders_status_created ON maintenance_work_orders(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_quality_checks_result_ts ON quality_checks(result, ts DESC);
CREATE INDEX IF NOT EXISTS idx_rework_status_created ON rework_tasks(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_barcode_events_ts ON barcode_events(ts DESC);

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
