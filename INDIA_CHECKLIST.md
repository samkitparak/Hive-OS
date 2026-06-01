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

File to edit: `src/maestro_agent.py` → `MAESTRO_LOG_PATTERN` and `_parse_log_line()`

**This is the main on-site task. Takes ~30 minutes once you have log access.**

- [ ] Sit at any SCM machine PC (Morbidelli CX100 or N100 is best)
- [ ] Navigate to Maestro log folder — likely `C:\SCM\Maestro\Logs\` or `C:\Program Files\SCM Group\Maestro\Logs\`
- [ ] Open the most recent `.log` file, copy 20-30 lines, note the format
- [ ] Update `_parse_log_line()` in `src/maestro_agent.py` with real regex matching actual log line format
- [ ] Update `MAESTRO_EVENTS` dict with real event keyword strings from the log
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

- [ ] Find the Cabinet Vision export folder on the office PC — where it drops new CSVs when a job is sent
- [ ] Update `cv_watch_folder` path in config
- [ ] Confirm the office PC IP is reachable from broker PC, or run CV connector on the same machine

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

---

*Last updated: 2026-05-31*
*Update this file whenever a new component is built.*
