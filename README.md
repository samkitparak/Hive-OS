# HIVE OS

Agentic manufacturing OS for HAEEV, a custom woodworking factory in India.

Tier 1 (this repo): read-only observation layer. Machines don't know HIVE OS exists — it watches, measures, and displays. No control, no changes to existing workflows.

---

## What it does

- **Live machine dashboard** — 15 machines across 7 process areas (Cutting, CNC, Edge Banding, Pressing, Sanding, Finishing, Utilities). Real-time state (RUNNING / IDLE / OFF / ALARM), power draw, current CNC program, OEE bars.
- **Job progress** — pulls cut lists from Cabinet Vision exports, tracks parts completed per job via Maestro cycle events. Shows done/total, progress bar, ETA, on-time status.
- **OEE** — Availability × Performance × Quality per machine, updated every shift.
- **Daily score + streak** — gamified production score (0–100) combining OEE and on-time job completion. Streak tracks consecutive days beating the 7-day rolling average.
- **Live event stream** — SSE feed of all machine events (cycle start/end, alarms, power changes) in real time.

---

## Architecture

```
Cabinet Vision (office PC)
  └── exports CSV cut lists → src/ingest.py → SQLite DB

Machine floor
  ├── Maestro log files → src/maestro_agent.py → MQTT
  └── Energy meters (Modbus TCP) → src/energy_agent.py → MQTT

MQTT broker
  └── src/mqtt_bridge.py → SQLite DB + SSE queue

FastAPI backend (src/main.py)
  └── REST + SSE → React dashboard (dashboard/)
```

---

## Stack

| Layer | Tech |
|---|---|
| Database | SQLite (WAL mode) |
| Backend | Python 3.12, FastAPI, paho-mqtt, pymodbus |
| Machine agents | paho-mqtt, pymodbus, PyYAML, watchdog |
| Dashboard | React, Vite, TanStack Query |
| Tests | pytest (87 tests) |

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
│   ├── maestro_agent.py      # Maestro log file watcher
│   ├── mqtt_bridge.py        # MQTT subscriber → DB + SSE queue
│   ├── oee.py                # OEE calculator
│   ├── progress.py           # job progress tracker
│   ├── score.py              # daily score + streak
│   └── main.py               # FastAPI app (REST + SSE)
├── dashboard/                # React + Vite frontend
├── tests/                    # pytest suite
└── INDIA_CHECKLIST.md        # on-site configuration checklist
```

---

## Running locally

**Backend:**
```bash
cd hive-os
pip install fastapi uvicorn paho-mqtt pymodbus pyyaml
PYTHONPATH=src uvicorn src.main:app --port 8000 --reload
```

**Dashboard:**
```bash
cd hive-os/dashboard
npm install
npm run dev
# → http://localhost:5173
```

**Ingest sample data:**
```bash
cd hive-os
PYTHONPATH=src python src/ingest.py data/
```

**Tests:**
```bash
cd hive-os
PYTHONPATH=src python -m pytest tests/ -v
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

---

## Machine agents

**Energy agent** (compressors + dust collectors):
```bash
# Simulate
python src/energy_agent.py --simulate

# Real hardware (after setting IPs in machines.yaml)
python src/energy_agent.py
```

**Maestro agent** (all SCM machines):
```bash
# Simulate
python src/maestro_agent.py --machine morbidelli_cx100 --simulate

# Real hardware (after setting log paths in machines.yaml)
python src/maestro_agent.py --machine morbidelli_cx100
```

> **Note:** The Maestro log regex in `maestro_agent.py` uses a simulated format. Update `MAESTRO_LOG_PATTERN` and `MAESTRO_EVENTS` after reading 20–30 real log lines on-site (~30 min task).

---

## API endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/machines` | All machines + current state |
| GET | `/machines/{key}` | Single machine + last 20 events |
| GET | `/jobs` | All jobs (most recent first) |
| GET | `/jobs/active` | Jobs in progress today with part counts + ETA |
| GET | `/jobs/{job_name}/parts` | All parts for a job |
| GET | `/jobs/{job_name}/progress` | Progress for a specific job |
| GET | `/oee` | OEE for all machines (last 8h) |
| GET | `/oee/{machine_key}` | OEE for one machine |
| GET | `/score/daily` | Today's score, streak, 7-day average |
| GET | `/events/stream` | SSE live event stream |
| POST | `/events/simulate` | Inject a fake event (demo/dev) |

---

## Tier 2 (after India data collection)

Once weeks of real OEE data exist:
- Bottleneck detection across machines
- Real Performance OEE (requires cycle times from `cycle_times.yaml`)
- Job resequencing based on machine state
- AMR routing
- ERP integration
