# HIVE OS — India On-Site Checklist

Everything in this file needs to be done or verified when Samkit arrives at HAEEV factory.
Each item has the file to edit and exactly what to do.

---

## Network Setup (Day 1 — before anything runs)

- [ ] Assign static IPs to all machine PCs on factory LAN
- [ ] Note down the central broker PC IP (this is what everything talks to)
- [ ] Update `config/machines.yaml` → `mqtt.broker_host` with real broker IP
- [ ] Confirm factory LAN allows Modbus TCP (port 502) and MQTT (port 1883)

---

## Energy Meters — Compressors + Dust Collectors

File to edit: `config/machines.yaml` → `energy_meters` section

- [ ] Install Modbus TCP energy meters (e.g. Eastron SDM120) on Elgi x2, Aarco x2 panels
- [ ] Replace `modbus_host` for `elgi_1` with real IP
- [ ] Replace `modbus_host` for `elgi_2` with real IP
- [ ] Replace `modbus_host` for `aarco_1` with real IP
- [ ] Replace `modbus_host` for `aarco_2` with real IP
- [ ] Verify thresholds by watching live power readings: `python src/energy_agent.py --simulate` → replace with real reader and observe actual W values at idle vs loaded
- [ ] Tune `on_threshold_w` and `idle_threshold_w` per machine based on observed readings

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

- [ ] Check what the Siemens controller actually exposes: open the controller panel, look for an IP address or network settings menu
- [ ] Try connecting with a Modbus TCP scanner (free tool: Modbus Poll) — if registers respond, note the address map
- [ ] Try OPC-UA browser (free tool: UaExpert) — if tags are visible, note the node paths
- [ ] Update `config/machines.yaml` with real connection details once protocol is confirmed
- [ ] If neither works: fall back to current clamp (same as compressors)

---

## Cabinet Vision — Live Folder Watch

File to edit: `config/machines.yaml` → `cv_watch_folder`

- [ ] Find the Cabinet Vision export folder on the office PC — where it drops new CSVs when a job is sent. Usually something like `C:\CabinetVision\Export` or wherever the operator clicks "Export" to.
- [ ] Update `cv_watch_folder` in `config/machines.yaml` with the real path
- [ ] Confirm the office PC is reachable from the broker PC on the factory LAN (or run the backend on the same machine as CV)
- [ ] Restart the backend — the watcher starts automatically and new jobs will appear in the dashboard within seconds of being exported from CV. No manual ingest needed from this point on.

> The auto-watcher (`src/cv_watcher.py`) debounces rapid file writes so even if CV drops multiple files at once, only one ingest runs. Already-ingested jobs are skipped so re-exporting the same job is safe.

---

## Cycle Time Calibration (Tier 2 — do after system is stable)

File to edit: `config/cycle_times.yaml`

**Takes ~1 hour per machine. Do this after the system has been running for a few days.**

For each machine, time 20 parts with a stopwatch — vary the sizes and operation types.
Record in a spreadsheet: `length_mm, width_mm, eb1/2/3/4, cnc_file_back, cnc_file_front, actual_seconds`

Then send the table to Claude and run:
```
POST /cycle-times/calibrate?machine_key=gabbiani_pt80
Body: [list of timed parts]
```
Paste the returned coefficients into `config/cycle_times.yaml` for that machine.
Once calibrated, job ETAs, the sequencer, and bottleneck detection all get real numbers.

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

---

*Last updated: 2026-06-04*
*Update this file whenever a new component is built.*
