# HIVE OS — India On-Site Checklist

Everything in this file needs to be done or verified when Samkit arrives at HAEEV factory.
Each item has the file to edit and exactly what to do.

---

## Network Setup (Day 1 — before anything runs)

- [ ] Assign static IPs to all machine PCs on factory LAN
- [ ] Note down the central broker PC IP (this is what everything talks to)
- [ ] Update `config/machines.yaml` → `mqtt.broker_host` with real broker IP
- [ ] Confirm factory LAN allows Modbus TCP (port 502) and mutual-TLS MQTT (port 8883)

## Remote Machine Bootstrap (Day 1)

Dashboard -> **Setup** -> **Remote Agent Setup**

- [ ] Copy the installer-created `HIVE Machine Bootstrap` folder to an approved USB
- [ ] On each Maestro PC, run `enable-hive-ssh.ps1` once as Administrator
- [ ] Confirm the OpenSSH firewall rule is limited to `LocalSubnet`
- [ ] Enter the static IP and existing local Administrator username in HIVE
- [ ] Scan the host key and compare its SHA-256 fingerprint with the machine screen
- [ ] Approve only the matching fingerprint; never approve from the scan alone
- [ ] Authenticate and confirm the returned Windows context reports `is_admin: true`
- [ ] Run live folder detection and save the confirmed log/CNC paths
- [ ] Install the agent centrally, fetch its log, and confirm its MQTT heartbeat
- [ ] Revoke trust and investigate any unexpected host-key change

See `REMOTE_COMMISSIONING.md` for the exact secure workflow.

## Identity and Access (Day 1)

- [ ] On the central PC, use the installer-generated token to create the first administrator
- [ ] Create a second administrator for recovery and one named account per person; never share operator accounts
- [ ] Assign the smallest suitable role: supervisor, planner, maintenance, quality, operator, or viewer
- [ ] Create a separate integration key for each machine PC that submits HTTP evidence; record only its prefix in the commissioning log
- [ ] Revoke and replace any integration key exposed during setup
- [ ] Keep dashboard/API access on central-PC localhost until an approved HTTPS reverse proxy is commissioned
- [ ] Verify a viewer cannot mutate records, an operator cannot approve planning, and a machine key cannot read `/api/machines`
- [ ] Confirm audit events show the authenticated person's display name even when a client submits a different actor value
- [ ] Store administrator credentials in the approved site password manager and test second-admin recovery

See `ACCESS_CONTROL.md` for the complete role and transport contract.

## Virtual Factory Prior Replacement (Day 1-3)

Dashboard -> **Commission** -> **Virtual lab**

- [ ] Run and retain the shipped reference model before changing any assumptions
- [ ] Open **Field evidence**, download the evidence pack, and verify its SHA-256 header is recorded in the commissioning log
- [ ] Keep the pack on the approved offline commissioning laptop/USB as the blank fallback capture path
- [ ] Work down **Measure first on site** in displayed priority order
- [ ] Create one study per high-priority machine and record at least 20 cycles split across two product/program strata, two dates, and two observers unless capture is automated
- [ ] Measure load, queue, operation, unload, setup/changeover, blocked, starved, and first-good-piece time separately
- [ ] Use stable source IDs for CSV rows; preview before apply and replay once to confirm duplicate suppression
- [ ] Review every flagged outlier; exclude only with a written reason and retain the raw observation
- [ ] Confirm actual product-family shares and every route, including loops, rework, manual fallback, and outsourced steps
- [ ] Confirm parallel capacity and staffing for the hot press and boxing cells
- [ ] Submit review only after all six credibility gates pass; record named approval/rejection notes
- [ ] If approved, manually update only `config/virtual_factory.yaml`, preserving `status: assumption_only`, then rerun and retain the new SHA-256 fingerprint
- [ ] Treat intervention uplift as a trial-screening result; do not use it as a production forecast or schedule approval
- [ ] Continue real cycle-model, route, resource, and forecast commissioning independently

See `VIRTUAL_FACTORY_COMMISSIONING.md` for each prior, exact measurement, and
the boundary between this lab and production truth.
See `COMMISSIONING_EVIDENCE.md` for field definitions, CSV rules, and review gates.

---

## Energy Meters — Compressors + Dust Collectors

Dashboard → **Commission** → **Industrial I/O**

- [ ] Record exact meter manufacturer, model, firmware, wiring mode, IP, port, and unit ID for Elgi x2 and Aarco x2
- [ ] Obtain the manufacturer register list; use the seeded SDM630 map only if the installed model is confirmed SDM630
- [ ] Run `deploy/windows/test-industrial-network.ps1` on the central HIVE PC
- [ ] Enter each endpoint and exact zero-based signal addresses, then save
- [ ] Run **Simulate** to verify the HIVE software path
- [ ] Run **Probe device** and compare voltage, current, power, energy, power factor, and frequency with the meter display
- [ ] Observe power while off, unloaded, and loaded; set idle/on thresholds between those bands
- [ ] Approve and enable only after the real probe passes
- [ ] Poll twice and confirm latest telemetry, a debounced state transition, and an hourly rollup

---

## Warehouse — Sheets, Edge, Hardware, Remnants

Dashboard → **Planning** → **Resources**

- [ ] Count sheet stock by exact Cabinet Vision material code, lot, and location
- [ ] Confirm physical sheet dimensions and compare estimated sheets with one approved CV nest
- [ ] Resolve every component source issue before trusting edge demand
- [ ] Count each edge-band roll in metres and enter location, supplier, lead time, reorder point, safety stock, and order multiple
- [ ] Obtain a real Cabinet Vision/ERP hardware BOM; until then, enter hardware requirements manually per order
- [ ] Label, measure, locate, and verify usable remnants; record length along grain
- [ ] Approve then cancel a test schedule and confirm all sheet, component, and remnant reservations release
- [ ] Complete the test order and confirm the movement ledger issues stock and consumes allocated remnants

---

## Procurement and ERP Exchange

Dashboard -> **Planning** -> **Resources** -> **Procurement**

- [ ] Export the supplier/item catalog from the current ERP or approved purchasing sheet
- [ ] Confirm each supplier legal identity, stable key, currency, lead time, and optional GLN
- [ ] Map every current sheet and component shortage to the exact supplier SKU and purchase unit
- [ ] Verify conversion factors, pack multiples, MOQ, and prices against a current supplier document
- [ ] Validate the supplier catalog CSV before applying it; reconcile accepted and rejected row counts
- [ ] Draft shortages and confirm existing open inbound quantity is not ordered twice
- [ ] Approve and export one test PO; confirm queueing alone does not mark it sent
- [ ] Decide the real ERP adapter target: API, EDI, staging table, or watched file folder
- [ ] Store ERP credentials outside HIVE master data and test positive and failed outbox acknowledgments
- [ ] Post a partial receipt and confirm only accepted quantity enters its physical lot
- [ ] Post a rejected quantity with a reason and confirm it returns to uncovered demand
- [ ] Replay the receipt and confirm no second stock movement is created
- [ ] Reconcile observed lead time, on-time rate, and rejection rate after at least five receipts

See `PROCUREMENT_INTEGRATION.md` for CSV columns and the adapter contract.

---

## Maestro Log Watcher — SCM CNC Machines

Primary workflow: dashboard → **Commission**

**This is the main on-site task. Takes ~30 minutes once you have log access.**

- [ ] Sit at any SCM machine PC (Morbidelli CX100 or N100 is best)
- [ ] Navigate to Maestro log folder — likely `C:\SCM\Maestro\Logs\` or `C:\Program Files\SCM Group\Maestro\Logs\`
- [ ] Open the most recent `.log` file, copy 20-30 lines, note the format
- [ ] Select the captured file in **Commission** and run dry-run analysis
- [ ] Confirm at least 70% recognition, three complete cycle pairs, ordered timestamps, and CNC identities where available
- [ ] If checks fail, use the displayed unknown samples and candidate keywords to add the site-specific aliases
- [ ] Import validated history only after all required checks pass
- [ ] **Ottimo barcode scan:** scan one finished part at packing while the log file is open — note the exact log line that appears (likely `PART_COMPLETE`, `SCAN_OUT`, `QC_OK` or similar). Add that event to `MAESTRO_EVENTS` as `part_complete` — this is what powers the finished goods count in the shift report.
- [ ] For each machine PC, note the actual log folder path and CNC program folder path
- [ ] Update `config/machines.yaml` → `maestro_agents` section with real paths and IPs for:
  - [ ] Stefani KD (Edge Bander)
  - [ ] Action E (Boxing)
  - [ ] Gabbiani PT 80 (Beam Saw)
  - [ ] Morbidelli CX100 (CNC Driller) ← priority, has .xcs file access
  - [ ] Morbidelli N100 (Flat Bed Router) ← priority
  - [ ] Nova SI 400 (Panel Saw)
  - [ ] DMC60 RCS 135 (Calibration Sander)
  - [ ] DMC90 XRT 135 (Finishing Sander)
  - [ ] Superfici (Paint Line)
  - [ ] Varie Osama (Glueing Line)

---

## Sergiani GS 120 — Hot Press

- [ ] Record Siemens CPU/HMI model, firmware, IP, licensed communication features, and integrator contact
- [ ] Confirm OPC-UA server availability and endpoint with Sergiani or the controls integrator; do not assume it is enabled
- [ ] Create a read-only OPC-UA role and trust a unique HIVE application certificate
- [ ] Put `SignAndEncrypt` certificate/user material in a Windows machine environment variable
- [ ] In **Commission > Industrial I/O**, save the endpoint, credential variable name, and matching security policy
- [ ] Browse nodes and map only confirmed running, alarm, recipe/program, cycle counter, temperature, and pressure nodes
- [ ] Probe and compare every value with the HMI before approval
- [ ] If OPC-UA is unavailable, use documented read-only Modbus registers; if neither works, use an energy meter plus operator scans

---

## Cabinet Vision — Live Folder Watch

File to edit: `config/machines.yaml` → `cv_watch_folder`

- [ ] Find the Cabinet Vision export folder on the office PC — where it drops new CSVs when a job is sent. Usually something like `C:\CabinetVision\Export` or wherever the operator clicks "Export" to.
- [ ] Update `cv_watch_folder` in `config/machines.yaml` with the real path
- [ ] Confirm the office PC is reachable from the broker PC on the factory LAN (or run the backend on the same machine as CV)
- [ ] Restart the backend — the watcher starts automatically and new jobs will appear in the dashboard within seconds of being exported from CV. No manual ingest needed from this point on.

> The auto-watcher (`src/cv_watcher.py`) debounces rapid file writes so even if CV drops multiple files at once, only one ingest runs. Already-ingested jobs are skipped so re-exporting the same job is safe.

### Cabinet Vision SQL commissioning

Dashboard → **Commission** → **Data connectors**

- [ ] Confirm the exact Cabinet Vision version and SQL Server instance with the reseller or site DBA
- [ ] Create an approved read-only view with one row per part; do not guess internal table names
- [ ] Grant the HIVE service identity `SELECT` only on that view
- [ ] Set `HIVE_CV_SQL_CONNECTION` as a Windows machine environment variable and restart HIVE
- [ ] Save the credential variable name and view name, then run **Test metadata**
- [ ] Analyze 10-100 representative rows, verify every mapping, approve, and enable
- [ ] Run one SQL sync and reconcile job, part, quantity, material, and CNC program counts

### Ottimo connector commissioning

- [ ] Export representative scan events for completion, QC pass/fail, packing, and dispatch
- [ ] Map barcode, station, timestamp, operator, external event ID, and every event value
- [ ] Approve only after all sample rows pass; import once and reconcile with Ottimo totals
- [ ] Repeat a batch and confirm HIVE reports it as duplicate without writing again

---

## Production Planning — Before First Live Job

Dashboard → **Planning**

- [ ] Cancel historical/demo jobs that should not enter the live queue
- [ ] Set a timezone-aware due time and priority for each live job
- [ ] Review the generated Gabbiani → Morbidelli → Stefani route per part
- [ ] Add press, sanding, paint, boxing, or alternate-machine steps where the product requires them
- [ ] Move complete orders to `ready`, then have the supervisor move them to `released`
- [ ] Confirm the readiness strip shows work, due dates, and routes passing
- [ ] Run schedule comparison; leave it commissioning-only until models and route evidence pass
- [ ] Resolve every route exception produced by the first physical job

### Factory resources

Planning → **Resources**

- [ ] Count on-hand sheets by exact Cabinet Vision material code and storage location
- [ ] Confirm sheet length/width for every material; replace the 2440 × 1220 mm assumption where needed
- [ ] Compare HIVE's 82% nesting-yield estimate against one real Cabinet Vision nest
- [ ] Enter available qualified headcount for cutting, CNC, edge banding, pressing, finishing, and packing
- [ ] Count available machine toolsets and note tooling out for sharpening/service
- [ ] Verify each machine's labor role, tool pool, and simultaneous-operation capacity
- [ ] Confirm the Monday-Saturday 09:00-18:00 Asia/Kolkata calendar and adjust shifts/breaks
- [ ] Add known preventive-maintenance windows before schedule comparison
- [ ] Measure safe input WIP capacity and current WIP at each downstream machine
- [ ] Verify each resource row only after the physical value is checked
- [ ] Confirm material reservations appear after schedule approval and release after cancellation
- [ ] Verify each scanner's station key and whether one scan represents one unit or a batch
- [ ] Name every physical read point, input buffer, hold area, packing area, and dispatch location
- [ ] Execute one test part through every routed station and compare HIVE actuals with machine logs
- [ ] Test duplicate, out-of-sequence, scrap, hold/resume, and completion-without-start scans
- [ ] Confirm live WIP counts match each configured input buffer before approving production
- [ ] Keep all machine command output disabled until vendor safety interlocks are independently commissioned

### Unit labels and scanners

Operations -> **Unit identity and labels**

- [ ] Record thermal/office label printer make, model, IP/USB connection, and DPI
- [ ] Confirm available label stock; the current template is 100 x 50 mm
- [ ] Print one test set through browser output and one through Zebra ZPL where supported
- [ ] Scan `HIVE:U:HU-...` using every physical scanner and confirm the full value arrives unchanged
- [ ] Confirm keyboard-wedge scanners append Enter and do not add a hidden prefix/suffix
- [ ] Attach one real Ottimo code as an alias to a HIVE unit and scan both values
- [ ] Scan the same unit twice at one station and confirm the second event is marked duplicate
- [ ] Scan a unit with deliberately wrong typed job context and confirm it is retained as conflict
- [ ] Verify unit status changes through started, completed, packed, and dispatched
- [ ] Confirm browser and ZPL labels remain readable after application to dusty sheet material
- [ ] Decide whether HAEEV needs globally shared GS1 IDs; obtain a GS1 India Company Prefix before assigning any

---

## Automatic Cycle Time Learning (After Telemetry Is Stable)

HIVE pairs linked cycle starts/ends and trains candidate models automatically.
No spreadsheet is required when machine events identify the part reliably.

- [ ] Collect at least 20 varied, linked cycles per modeled feature set
- [ ] Open **Planning** and verify cycle-model coverage in commissioning readiness
- [ ] Check rejected observations and fix clocks/part links rather than hand-editing data
- [ ] Confirm a candidate reaches medium/high validation confidence before it activates
- [ ] Use stopwatch records only as an independent validation sample or manual fallback

**Priority order for calibration** (most impact first):
- [ ] `morbidelli_cx100` — CNC Driller (most variable, grooves vs no grooves)
- [ ] `stefani_kd` — Edge Bander (4-edge parts take 4× as long as 1-edge)
- [ ] `gabbiani_pt80` — Beam Saw (length is the main driver)
- [ ] `dmc60_rcs135` — Calibration Sander
- [ ] `dmc90_xrt135` — Finishing Sander
- [ ] `superfici` — Paint Line
- [ ] `sergiani_gs120` — Hot Press
- [ ] `morbidelli_n100` — Flat Bed Router
- [ ] `nova_si400` — Panel Saw
- [ ] `varie_osama` — Glueing Line

**What to note per machine:**
- Does panel size obviously affect cycle time? (yes → length/area coefficients matter)
- Do grooved parts take noticeably longer on CNC? (yes → groove_coeff matters)
- Does flipping for front face add significant time? (yes → face_coeff matters)

---

## Predictive Production Forecast (After Routes And Resources Pass)

Dashboard → **Production forecast**

- [ ] Set contractual timezone-aware due times on every ready or released order
- [ ] Confirm every selected route and cycle model passes the digital-twin gate
- [ ] Verify material, component, labor, tooling, shift, outage, and WIP inputs against the floor
- [ ] Run the default 50-replication forecast twice and confirm the same seed is reproducible
- [ ] Compare the likely forecast constraint with the planner and shift supervisor's expectation
- [ ] Review all orders with at least 20% late probability before the next dispatch decision
- [ ] Treat forecast alerts as recovery reviews, not automatic schedule changes
- [ ] Confirm a production or resource edit immediately marks the prior forecast stale
- [ ] Complete orders through normal machine/scanner evidence so actual timestamps remain trustworthy
- [ ] After five outcomes, review P80/P95 coverage, P50 error, and late-risk Brier score
- [ ] Classify every major miss as model, route, resource, calendar, disturbance, or source-data error
- [ ] Stop using forecast recommendations if calibration enters `drift` until assumptions are corrected

See `PREDICTIVE_CONTROL.md` for the uncertainty model, credibility gates, and calibration rules.

---

## Schedule Recovery (After One Approved Test Schedule)

Dashboard → **Planning** → **Schedule recovery**

- [ ] Approve a representative four-or-more-order schedule with trustworthy routes, models, due times, and resources
- [ ] Hold one dispatched station job and confirm HIVE reports the deviation without changing the approved sequence
- [ ] Run **Analyze** and confirm completed operations are absent from residual simulation
- [ ] Confirm dispatched, acknowledged, running, held, in-progress, and the first two horizon jobs remain in their exact positions
- [ ] Compare current, FIFO, EDD, SPT, and material-batch results with the production planner
- [ ] Verify a proposal appears only when configured lateness or makespan recovery is material
- [ ] Edit an order or resource after analysis and confirm approval is blocked as stale
- [ ] Approve one recovery with a named planner and verify the execution queue relinks without releasing held work
- [ ] Reject one recovery and confirm the current approved schedule remains active
- [ ] Tune thresholds only through a recorded management-of-change review after observing real planner decisions

See `SCHEDULE_RECOVERY.md` for trigger logic, stability scoring, thresholds, and the audited decision flow.

---

## Improvement Experiments (After One Stable Shift)

Dashboard → **Review actions**

- [ ] Synchronize priorities only after the shift's telemetry and downtime classifications are reviewed
- [ ] Assign one named owner and write a falsifiable hypothesis before accepting a measured action
- [ ] Choose the machine scope and primary metric; do not use factory-wide scope when machine evidence exists
- [ ] Use a baseline and evaluation window covering comparable shifts, product mix, staffing, and planned breaks
- [ ] Record known confounders such as product family, material, tool change, operator, maintenance, or power interruption
- [ ] Confirm the baseline meets the minimum sample gate before marking the change implemented
- [ ] Make only one material process change per experiment where practical
- [ ] Wait for the fixed evaluation window; do not force an early result
- [ ] Review the 90% interval and every throughput, downtime, or quality guardrail before accepting the outcome
- [ ] Treat `promising` and `inconclusive` as reasons to repeat, not proof of improvement
- [ ] Reconcile the immutable event history with the shift supervisor's notes
- [ ] Keep promoted patterns advisory until a separate safety and production-control review approves any broader automation

See `IMPROVEMENT_LEARNING.md` for metric definitions and outcome rules.

---

## Root-Cause Diagnostics (After Incident Evidence Is Stable)

Dashboard → **Root-cause diagnostics**

- [ ] Obtain OEM alarm-code lists for each exact machine/controller model and map codes to verified failure modes
- [ ] Confirm machine and HIVE clocks agree before interpreting event order around a failure
- [ ] Classify every test downtime event with the closest controlled reason instead of leaving it unknown
- [ ] Link CNC program and physical part identity through one alarm, downtime, and failed quality example
- [ ] Verify maintenance plans, open work orders, condition thresholds, and required spare shortages reflect the real machine state
- [ ] Validate voltage, current, power, and frequency units against the meter display before using utility anomalies
- [ ] Synchronize incidents and compare the top five hypotheses with the shift supervisor's evidence
- [ ] Record contradictions and data gaps before confirming a cause
- [ ] Require the reviewing operator's real name, selected actual cause, corrective action, and review note
- [ ] Dismiss duplicate or invalid source incidents with a reason; reopen them if later evidence changes the decision
- [ ] Reconcile confirmed causes with maintenance and quality records after each shift
- [ ] Treat local priors as active only after five confirmed cases of the same incident type
- [ ] Confirm a reviewed downtime or quality cause appears in `/api/optimization` before starting its improvement experiment
- [ ] Keep diagnosis advisory; do not connect it directly to PLC writes or automatic schedule changes

See `ROOT_CAUSE_DIAGNOSTICS.md` for evidence weights, learning thresholds, and API behavior.

---

## Alert Management and Escalation

Dashboard → **Alerts**

- [ ] Rationalize each OEM alarm code: required operator action, consequence, priority, owner, and duplicate/deadband behavior
- [ ] Confirm the 14 HIVE rule classes create an alert only when a person must respond
- [ ] Assign primary and backup roles for every shift and approve initial response times
- [ ] Run repeated condition synchronization against one shift of evidence and remove nuisance or chattering conditions
- [ ] Verify acknowledgment, snooze expiry, manual resolution, source-clear resolution, recurrence, and escalation history
- [ ] Approve the site webhook gateway URL; do not target arbitrary public URLs
- [ ] Put the signing secret in a Windows machine environment variable and record only its name in HIVE
- [ ] Save the destination disabled, run local simulation, then run one explicit live test
- [ ] Verify the receiver rejects a bad signature and deduplicates the same `X-HIVE-Delivery` value
- [ ] Enable the verified destination and dispatch pending current state once
- [ ] Enable automatic sync for one supervised shift before enabling automatic dispatch
- [ ] Run an alarm-flood drill and confirm the dashboard remains usable and operators know the escalation route
- [ ] Review alert counts, response-overdue counts, failed deliveries, and rationalization changes after each commissioning shift

See `ALARM_MANAGEMENT.md` for the rule catalog, lifecycle, security, and delivery contract.

---

## Preventive Maintenance and Spares

Dashboard → **Operations** → **Preventive maintenance**

- [ ] Record exact manufacturer, model, serial number, and OEM manual revision for every machine
- [ ] Replace each unverified 30-day baseline with the current OEM schedule and site risk assessment
- [ ] Separate calendar, powered-hour, active-cycle-hour, cycle-count, and condition triggers where the OEM does
- [ ] Confirm safe shutdown requirements and whether the site procedure requires hazardous-energy isolation
- [ ] Name the roles authorized to apply and independently verify the site's LOTO procedure
- [ ] Verify every checklist instruction and acceptance criterion against the exact machine model
- [ ] Record expected duration and preferred service window for each plan
- [ ] Confirm maintenance windows appear as machine outages in Planning before schedule approval
- [ ] Capture condition metric source, sensor ID, unit, normal range, threshold, and comparator
- [ ] Add real manufacturer spare part numbers; do not commission generic or guessed SKUs
- [ ] Count each spare by storage location and record reorder point, quantity, supplier, and lead time
- [ ] Test one spare reservation, cancellation release, issue, and stock-movement audit
- [ ] Complete one supervised inspection and confirm required checklist and named LOTO evidence are enforced
- [ ] Fail one non-safety test checklist item and confirm a corrective follow-up work order opens
- [ ] Review MTBF/MTTR only after breakdown reasons and machine-state evidence are reliable

---

## Database

- [ ] Decide: keep SQLite or migrate to Postgres (Postgres recommended if >1 user hitting the dashboard)
- [ ] If Postgres: update `DB_PATH` in `src/db.py` to a Postgres connection string

---

## Dashboard

- [ ] Open dashboard on a screen in the factory — confirm it's visible from the floor
- [ ] Set browser to full-screen / kiosk mode on the display PC

---

## Smoke Test Sequence (run in this order)

1. `python src/ingest.py /path/to/cv/export/folder` — confirm jobs load
2. `python src/energy_agent.py` — confirm compressor/dust collector events flowing in MQTT
3. `python src/maestro_agent.py` — confirm at least one SCM machine reporting events
4. `PYTHONPATH=src uvicorn src.main:app --port 8000` — confirm backend starts, OEE numbers appear
5. Open dashboard — confirm all machines show live state
6. Open **Commission** — analyze one real log and confirm telemetry confidence begins increasing
7. Open `/api/optimization` — confirm low-confidence data is gated and no unsupported gain estimate is shown
8. Open **Planning** — set one real due time, verify routes, and release only the test job
9. Scan/start/complete one routed part — confirm quantity advances and no route exception remains
10. Open **Root-cause diagnostics**, synchronize incidents, and review one case without confirming unsupported evidence
11. Open **Alerts**, synchronize conditions with a named operator, and verify automatic sync and dispatch are still off

---

*Last updated: 2026-07-14*
*Update this file whenever a new component is built.*
