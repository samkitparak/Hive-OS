# HIVE OS

[![CI](https://github.com/samkitparak/Hive-OS/actions/workflows/ci.yml/badge.svg)](https://github.com/samkitparak/Hive-OS/actions/workflows/ci.yml)

Manufacturing operations OS for HAEEV, a custom woodworking factory in India.

HIVE OS is observation-first: local agents read machine logs and telemetry, but
the system does not write commands to PLCs or machine controllers. Its write
operations are limited to the HIVE database and site configuration.

---

## What it does

- **Live machine dashboard** — 15 machines across 7 process areas (Cutting, CNC, Edge Banding, Pressing, Sanding, Finishing, Utilities). Real-time state (RUNNING / IDLE / OFF / ALARM), power draw, current CNC program, OEE bars.
- **Job progress** — pulls cut lists from Cabinet Vision exports, tracks parts completed per job via Maestro cycle events. Shows done/total, progress bar, ETA, on-time status.
- **OEE** — Availability × Performance × Quality per machine, updated every shift.
- **Daily score + streak** — gamified production score (0–100) combining OEE and on-time job completion. Streak tracks consecutive days beating the 7-day rolling average.
- **Explainable optimization engine** — ranks dynamic constraints using active periods, queue depth, inferred downstream starvation, alarms, and a separate telemetry-confidence gate.
- **Closed-loop improvement learning** — turns priorities into owned experiments with frozen baselines, minimum sample gates, confidence intervals, guardrails, immutable outcomes, and conservative advisory promotion.
- **Evidence-backed root-cause diagnostics** — correlates alarms, downtime, quality, programs, maintenance, spares, and utility telemetry; preserves ranked alternatives and learns local priors only from named operator confirmations.
- **Connector commissioning** — browse for real Cabinet Vision, Ottimo, or Maestro evidence; map and validate it; explicitly approve a version; then enable repeat-safe imports.
- **Data trust layer** — normalizes India-local timestamps, suppresses duplicate MQTT delivery, isolates heartbeats, audits rejected events, and scores each machine's evidence quality.
- **Automatic cycle learning** — pairs validated part cycles, robustly fits versioned nonnegative models, and protects active models from weak candidates.
- **Production digital twin** — compares dispatch policies with finite machine, labor, tooling, calendar, maintenance, material, and WIP capacity.
- **Production control** — explicit work releases, contractual due times, quantity-aware routes, exception reconciliation, and audited human schedule approval.
- **Factory resource control** — sheet-stock estimates and reservations, labor/tool pools, machine profiles, shift calendars, planned outages, and finite WIP buffers.
- **Station execution control** — approved schedule dispatch, acknowledgements, partial quantities, holds, machine/scanner actuals, WIP movement, and traceability.
- **Serialized unit identity** — one auditable identity per physical part, alias-safe scanner resolution, duplicate suppression, browser labels, and native Zebra ZPL.
- **Preventive maintenance control** — commissioned calendar/usage/condition plans, machine-specific inspections, named LOTO evidence, maintenance-aware schedules, and audited spare reservations.
- **Factory integration boundary** — versioned mappings, read-only SQL discovery, credential references, sample fingerprints, issue audits, and idempotent import batches.
- **Industrial telemetry gateway** — commissioned Modbus TCP, OPC-UA, and MQTT signals; read-only probes; immutable contracts; debounced machine state; raw/latest/hourly telemetry; and offsite simulation.
- **Warehouse intelligence** — derived edge demand, component lots, usable-remnant allocation, schedule reservations, immutable movements, and evidence-labeled purchase suggestions.
- **Procurement and ERP exchange** — verified supplier/SKU mappings, inbound-aware order suggestions, controlled PO approval, CSV/outbox adapters, idempotent receipts, and supplier evidence.
- **Live event stream** — SSE feed of all machine events (cycle start/end, alarms, power changes) in real time.

---

## Architecture

```
Cabinet Vision (office PC)
  └── exports CSV cut lists → src/ingest.py → SQLite DB

Machine floor
  ├── Maestro log files → src/maestro_agent.py → MQTT
  └── Meters / PLCs / sensors → src/industrial_gateway.py → normalized telemetry

MQTT broker
  └── src/mqtt_bridge.py → SQLite DB + per-client event broadcast

FastAPI backend (src/main.py)
  ├── REST + SSE under /api
  └── serves the built React dashboard (dashboard/dist)
```

---

## Stack

| Layer | Tech |
|---|---|
| Database | SQLite (WAL mode) |
| Backend | Python 3.12, FastAPI, paho-mqtt, pymodbus, asyncua |
| Machine agents | paho-mqtt, pymodbus, PyYAML, watchdog |
| Dashboard | React, Vite, TanStack Query |
| Tests | pytest |

---

## Project structure

```
hive-os/
├── config/
│   ├── machines.yaml        # machine IPs, MQTT broker, Maestro log paths
│   └── cycle_times.yaml     # ideal cycle time per machine (fill in on-site)
├── src/
│   ├── schema.sql            # SQLite schema + machine seed data
│   ├── db.py                 # DB connection helpers
│   ├── cv_parser.py          # Cabinet Vision CSV parser
│   ├── beamsaw_parser.py     # Beam saw TXT parser (UTF-16 LE)
│   ├── ingest.py             # batch ingest walker
│   ├── energy_agent.py       # Modbus TCP energy meter poller
│   ├── industrial_gateway.py # commissioned Modbus/OPC-UA/MQTT telemetry
│   ├── inventory.py          # component, remnant, shortage, and movement truth
│   ├── procurement.py        # supplier mappings, purchase orders, receipts, ERP outbox
│   ├── maestro_agent.py      # Maestro log file watcher
│   ├── mqtt_bridge.py        # MQTT subscriber → DB + event broadcast
│   ├── oee.py                # OEE calculator
│   ├── progress.py           # job progress tracker
│   ├── score.py              # daily score + streak
│   ├── bottleneck.py         # current factory constraint detector
│   ├── event_pipeline.py     # validation, timestamps, deduplication, audit
│   ├── data_quality.py       # per-machine telemetry confidence
│   ├── commissioning.py      # offline Maestro evidence analysis + replay
│   ├── optimization.py       # explainable, confidence-gated priorities
│   ├── improvement.py        # recommendation lifecycle, experiments, outcome learning
│   ├── root_cause.py         # incident evidence, hypotheses, confirmation learning
│   ├── learning.py           # automatic cycle observations + model validation
│   ├── routing.py            # observed same-part process transitions
│   ├── digital_twin.py       # SimPy schedule-policy comparison
│   ├── production_control.py  # order lifecycle + planned/observed route control
│   ├── planning.py            # persisted scenarios + approval ledger
│   ├── resources.py           # stock, labor, tooling, calendars, WIP, reservations
│   ├── execution.py           # station dispatch, actuals, WIP flow, traceability
│   ├── identity.py            # physical units, scanner aliases, QR/ZPL labels
│   ├── maintenance.py         # preventive plans, inspections, spares, reliability
│   ├── operations.py         # downtime, manual work, quality/rework, barcode
│   ├── connectors.py         # versioned connector commissioning + imports
│   ├── cv_sql_connector.py   # Cabinet Vision SQL placeholder adapter
│   ├── ottimo_connector.py   # Ottimo placeholder barcode adapter
│   └── main.py               # FastAPI app (REST + SSE)
├── dashboard/                # React + Vite frontend
├── deploy/windows/           # one-click central and machine-agent installers
├── tests/                    # pytest suite
├── DEPLOYMENT.md             # Windows installation and diagnostics guide
├── INTEGRATIONS.md           # integration roadmap
├── PHASE1_PLACEHOLDERS.md    # placeholder contracts and replacement points
├── RESOURCE_MODEL.md         # finite-capacity resource model and site workflow
├── EXECUTION_CONTROL.md      # station state machine and evidence contracts
├── IDENTITY_AND_LABELS.md    # serialized units, scan resolution, print outputs
├── MAINTENANCE_CONTROL.md    # preventive triggers, safety boundary, spares
├── CONNECTOR_COMMISSIONING.md # CV SQL, Ottimo, and Maestro site workflow
├── INDUSTRIAL_TELEMETRY.md    # industrial I/O contracts and site workflow
├── WAREHOUSE_INTELLIGENCE.md  # stock, remnants, BOM boundary, and purchasing logic
├── PROCUREMENT_INTEGRATION.md # supplier, PO, receipt, and ERP adapter contract
├── IMPROVEMENT_LEARNING.md   # recommendation experiments and promotion guardrails
├── ROOT_CAUSE_DIAGNOSTICS.md  # diagnostic evidence, decisions, and learning contract
└── INDIA_CHECKLIST.md        # on-site configuration checklist
```

---

## Running locally

**Backend:**
```bash
cd hive-os
pip install -r requirements.txt
PYTHONPATH=src uvicorn src.main:app --port 8000 --reload
```

**Dashboard:**
```bash
cd hive-os/dashboard
npm install
npm run dev
# → http://localhost:5173
```

For a production-style local run, build the dashboard first and then start only
FastAPI. The combined app is available at `http://localhost:8000`:

```bash
cd dashboard && npm ci && npm run build && cd ..
PYTHONPATH=src uvicorn src.main:app --port 8000
```

**Ingest sample data:**
```bash
cd hive-os
PYTHONPATH=src python src/ingest.py data/
```

**Tests:**
```bash
cd hive-os
python -m pytest -v
```

**Demo mode:** hit the **▶ Demo Mode** button in the dashboard to fire simulated machine events without any real hardware.

---

## Configuration (before going live)

All TODOs are in two files:

**`config/machines.yaml`**
- MQTT broker IP (currently `127.0.0.1`)
- Modbus IPs for energy meters (Elgi compressors, Aarco dust collectors)
- Maestro log file paths per machine
- Cabinet Vision watch folder path

**`config/cycle_times.yaml`**
- Ideal cycle time in seconds per machine
- Used for ETA calculation and OEE Performance metric
- All set to `0` until timed on-site

See `INDIA_CHECKLIST.md` for the full on-site setup sequence.
See `DEPLOYMENT.md` for Windows one-click installation and diagnostics.

The Windows installer limits dashboard/API and MQTT firewall rules to the local
subnet. Remote setup probes are also restricted to private LAN addresses.

---

## Machine agents

**Industrial I/O** (meters, controllers, and sensors):
```bash
# Start HIVE, then use Commission > Industrial I/O
PYTHONPATH=src uvicorn src.main:app --port 8000
```

The legacy `energy_agent.py` remains available for compatibility. New meter
installations use versioned gateway profiles and do not assume a register map.

**Maestro agent** (all SCM machines):
```bash
# Simulate
python src/maestro_agent.py --machine morbidelli_cx100 --simulate

# Real hardware (after setting log paths in machines.yaml)
python src/maestro_agent.py --machine morbidelli_cx100
```

The parser supports the simulated format plus conservative aliases for common
machine, cycle, program, alarm, and part-completion terms. Use **Commission** in
the dashboard to test a real log before importing its history. Unrecognized
keywords are surfaced for the final site-specific mapping.

---

## API endpoints

The dashboard uses the `/api` prefix (for example `/api/machines`). Direct
unprefixed routes remain available for compatibility and local tooling.

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Lightweight service health check |
| GET | `/machines` | All machines + current state |
| GET | `/machines/{key}` | Single machine + last 20 events |
| GET | `/jobs` | All jobs (most recent first) |
| GET | `/jobs/active` | Jobs in progress today with part counts + ETA |
| GET | `/jobs/{job_name}/parts` | All parts for a job |
| GET | `/jobs/{job_name}/progress` | Progress for a specific job |
| GET | `/oee` | OEE for all machines (last 8h) |
| GET | `/oee/{machine_key}` | OEE for one machine |
| GET | `/score/daily` | Today's score, streak, 7-day average |
| GET | `/sequence` | Priority-ranked production queue |
| GET | `/bottlenecks` | Current constraint ranking and recommendation |
| GET | `/data-quality` | Telemetry confidence, cycle integrity, part links, and clock drift |
| GET | `/optimization` | Confidence-gated factory priorities and constraint persistence |
| GET | `/improvements` | Recommendation lifecycle, experiments, outcomes, and learned advisories |
| POST | `/improvements/sync` | Materialize current optimization priorities without GET-side writes |
| GET | `/improvements/recommendations/{id}` | One recommendation's experiment and immutable event history |
| POST | `/improvements/recommendations/{id}/action` | Accept, reject, implement, evaluate, complete, or cancel an action |
| GET | `/root-causes` | Diagnostic cases, ranked hypotheses, evidence, gaps, and local-learning status |
| POST | `/root-causes/sync` | Explicitly materialize and reanalyze recent alarms, downtime, and quality incidents |
| GET | `/root-causes/{id}` | One diagnostic case with current hypotheses and immutable event history |
| POST | `/root-causes/{id}/decision` | Confirm, dismiss, or reopen a case with optimistic version protection |
| GET | `/learning/status` | Cycle observations and candidate/active model evidence |
| POST | `/learning/refresh` | Derive observations, train candidates, and refresh route evidence |
| GET | `/routing/graph` | Observed part-flow edges with support and confidence |
| GET | `/digital-twin/readiness` | Cycle-model and observed-route coverage gate |
| POST | `/digital-twin/compare` | Compare deterministic or seeded schedule scenarios |
| GET | `/production/readiness` | Day-one work, route, model, exception, and schedule gates |
| GET | `/production/orders` | Controlled production orders and route coverage |
| PUT | `/production/orders/{id}` | Versioned due-date, priority, and lifecycle update |
| GET | `/production/routes/{job_name}` | Planned route steps and confirmation quantities |
| PUT | `/production/routes/parts/{part_id}` | Replace an off-line part route with operator confirmation |
| GET | `/production/route-exceptions` | Unexpected or out-of-sequence floor evidence |
| GET/POST | `/planning/scenarios` | List or generate persisted twin comparisons |
| POST | `/planning/scenarios/{id}/decision` | Approve or reject a non-stale ready scenario |
| GET | `/planning/active-schedule` | Current approved dispatch sequence |
| GET | `/resources/snapshot` | Material, labor, tooling, calendar, WIP, and readiness snapshot |
| PUT | `/resources/materials/{key}` | Verify sheet definition and update lot stock |
| PUT | `/resources/labor/{key}` | Verify labor-role headcount |
| PUT | `/resources/tooling/{key}` | Verify total and available tool-pool capacity |
| PUT | `/resources/machines/{key}` | Verify machine labor/tool requirements and capacity |
| PUT | `/resources/calendar/factory` | Replace and verify the recurring factory calendar |
| PUT | `/resources/wip/{machine_key}` | Verify input-buffer capacity and current WIP |
| POST/DELETE | `/resources/unavailability` | Add or remove planned resource outages |
| GET/POST | `/execution/snapshot`, `/execution/sync` | Station dispatch state and schedule materialization |
| GET | `/execution/jobs` | Filter station work by machine and lifecycle state |
| POST | `/execution/jobs/{id}/action` | Dispatch, acknowledge, start, complete, hold, resume, or cancel |
| GET | `/execution/events` | Immutable station execution history |
| GET | `/execution/exceptions` | Review actual-vs-control deviations |
| POST | `/execution/exceptions/{id}/resolve` | Correct, accept, or ignore a reviewed deviation |
| GET | `/traceability/events` | Query physical-flow evidence by object or part |
| GET | `/identity/snapshot` | Unitization, print queue, and scanner-resolution state |
| POST | `/identity/orders/{id}/materialize` | Create missing physical unit identities idempotently |
| GET | `/identity/orders/{id}/units` | List serialized units for an order |
| GET | `/identity/units/{unit_key}` | Unit aliases, route progress, and traceability |
| GET | `/identity/resolve` | Diagnose a scanned identifier without writing an event |
| POST | `/identity/units/{unit_key}/aliases` | Attach Ottimo, ERP, supplier, or licensed GS1 aliases |
| GET/POST | `/labels/jobs` | List or create print-ready label sets |
| GET | `/labels/jobs/{id}/print`, `/zpl` | Browser-print or Zebra-native label output |
| POST | `/labels/jobs/{id}/printed` | Confirm physical label printing |
| POST | `/commissioning/log/analyze` | Dry-run or import a validated Maestro log sample |
| GET | `/connectors/snapshot` | Connector profiles, mappings, evidence, and status |
| PUT | `/connectors/{key}` | Configure or enable a connector without storing secrets |
| POST | `/connectors/{key}/analyze` | Analyze a sample and suggest/validate its mapping |
| POST | `/connectors/{key}/approve` | Approve one passing evidence run as a mapping version |
| POST | `/connectors/{key}/import` | Import an approved row batch idempotently |
| POST | `/connectors/cabinet_vision_sql/discover` | Read-only SQL view metadata test |
| POST | `/connectors/cabinet_vision_sql/sync` | Import from the approved read-only SQL view |
| GET | `/industrial/snapshot` | Industrial profiles, contracts, latest telemetry, and fleet energy summary |
| PUT | `/industrial/profiles/{key}` | Save a read-only endpoint and draft signal map |
| POST | `/industrial/profiles/{key}/simulate` | Exercise decoding and downstream logic without hardware |
| POST | `/industrial/profiles/{key}/probe` | Run a real read-only Modbus or OPC-UA probe |
| POST | `/industrial/profiles/{key}/mqtt-probe` | Validate a real MQTT topic and JSON sample |
| POST | `/industrial/profiles/{key}/approve` | Approve one passing real probe as an immutable contract |
| POST | `/industrial/profiles/{key}/poll` | Run one approved device poll immediately |
| POST | `/industrial/profiles/{key}/browse` | Browse OPC-UA nodes without writing |
| GET | `/industrial/profiles/{key}/telemetry` | Query hourly telemetry evidence |
| GET | `/energy/intelligence` | Gap-aware energy, idle waste, load factor, cost, and power-factor analysis |
| GET | `/inventory/snapshot` | Warehouse demand, component lots, remnants, shortages, and purchase suggestions |
| GET | `/inventory/movements` | Immutable warehouse movement ledger |
| PUT | `/inventory/items/{key}` | Commission an edge, hardware, consumable, or packaging item |
| PUT | `/inventory/items/{key}/lots/{lot}` | Record a receipt or physical balance |
| PUT | `/inventory/orders/{id}/requirements/{key}` | Record a manual BOM requirement |
| POST | `/inventory/remnants` | Record a measured panel remnant |
| PATCH | `/inventory/remnants/{key}` | Change an unreserved remnant disposition |
| GET | `/procurement/snapshot` | Supplier mappings, needs, POs, receipts, metrics, and ERP outbox |
| PUT | `/procurement/suppliers/{key}` | Create or version a supplier |
| PUT | `/procurement/suppliers/{key}/mappings/{type}/{item}` | Map a HIVE item to a supplier SKU and purchase unit |
| POST | `/procurement/orders` | Create a manual draft purchase order |
| POST | `/procurement/orders/draft-recommendations` | Draft mapped uncovered requirements by supplier |
| POST | `/procurement/orders/{id}/action` | Approve, queue, or cancel a purchase order |
| GET | `/procurement/orders/{id}/export.csv` | Export a purchase order CSV |
| POST | `/procurement/receipts` | Post an idempotent accepted/rejected goods receipt |
| POST | `/procurement/imports/csv` | Validate or apply supplier catalog and receipt CSV files |
| GET | `/diagnostics` | Service and machine-agent connection health |
| GET | `/deployment` | Windows install package readiness and commands |
| GET | `/config` | Current editable site setup configuration |
| PUT | `/config` | Save editable site setup configuration with backup |
| GET | `/remote-setup/plan/{machine_key}` | Dry-run remote agent deployment plan |
| POST | `/remote-setup/test-connection` | Probe an SSH TCP port without authentication |
| POST | `/remote-setup/detect-folders` | Preview remote Maestro folder discovery |
| POST | `/remote-setup/install-agent` | Preview a remote machine-agent install |
| POST | `/remote-setup/restart-agent` | Preview a remote agent restart |
| POST | `/remote-setup/fetch-log` | Preview retrieval of the remote agent log |
| GET | `/operations/summary` | Downtime, work-order, rework, defect, and scan summary |
| GET | `/downtime` | Downtime events, optionally filtered by status |
| POST | `/downtime` | Create a downtime event |
| POST | `/downtime/{id}/close` | Close a downtime event |
| GET | `/maintenance/work-orders` | Maintenance work orders, optionally filtered by status |
| POST | `/maintenance/work-orders` | Create a maintenance work order |
| GET | `/maintenance/snapshot` | Preventive plans, work orders, spares, and readiness |
| POST | `/maintenance/sync` | Evaluate verified plan and condition triggers |
| GET/POST | `/maintenance/plans` | List or create preventive plans |
| PUT | `/maintenance/plans/{id}` | Configure and verify a preventive plan |
| POST | `/maintenance/conditions` | Record a normalized condition reading |
| GET/POST | `/maintenance/spares` | List or create spare catalog entries |
| PUT | `/maintenance/spares/{key}/stock` | Set and audit spare stock by location |
| GET/PUT | `/maintenance/work-orders/{id}` | Read, schedule, start, or cancel work |
| POST | `/maintenance/work-orders/{id}/complete` | Complete checklist, LOTO, and spare issue |
| GET | `/quality/checks` | Recent quality checks |
| POST | `/quality/checks` | Create a quality check; failures create rework |
| GET | `/rework` | Rework tasks, optionally filtered by status |
| POST | `/rework/{id}/close` | Close a rework task |
| GET | `/barcode/events` | Recent barcode events |
| POST | `/barcode/events` | Create a normalized barcode event |
| POST | `/connectors/ottimo/placeholder` | Demo Ottimo barcode payload adapter |
| POST | `/connectors/cabinet-vision-sql/placeholder` | Demo Cabinet Vision SQL row adapter |
| GET | `/report/shift` | Printable shift report |
| GET | `/events/stream` | SSE live event stream |
| POST | `/events/simulate` | Inject a fake event (demo/dev) |

See [OPTIMIZATION_MODEL.md](OPTIMIZATION_MODEL.md) for the evidence model,
research basis, assumptions, confidence gate, and learning stages.
See [IMPROVEMENT_LEARNING.md](IMPROVEMENT_LEARNING.md) for experiment metrics,
outcome rules, guardrails, and the operator workflow.
See [PRODUCTION_CONTROL.md](PRODUCTION_CONTROL.md) for order lifecycle, route
reconciliation, schedule approval, and the day-one operator workflow.
See [RESOURCE_MODEL.md](RESOURCE_MODEL.md) for stock reservations, finite
capacity, calendars, maintenance, WIP, assumptions, and site verification.
See [EXECUTION_CONTROL.md](EXECUTION_CONTROL.md) for station dispatch, actual
quantity, scanner/machine reconciliation, WIP movement, and traceability.
See [IDENTITY_AND_LABELS.md](IDENTITY_AND_LABELS.md) for physical unit IDs,
scanner alias rules, duplicate protection, QR labels, and Zebra output.

---

## After India data collection

Once weeks of real OEE data exist:
- Promote automatically learned cycle models into real Performance OEE
- Validate bottleneck scoring weights against real queues and operator observations
- Dynamic job resequencing based on live machine state
- AMR routing
- ERP integration
