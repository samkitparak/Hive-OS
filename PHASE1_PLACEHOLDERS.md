# Phase 1 Placeholder Integrations

Phase 1 gives HIVE OS real product surfaces before the exact factory formats are
known. The database, API, dashboard, and tests use HIVE-native normalized data.
When the real systems are available, replace only the adapter functions listed
below.

## What Exists Now

| Area | HIVE table/API | Placeholder source |
|---|---|---|
| Downtime reasons and events | `/downtime` | Manual/demo downtime events |
| Maintenance work orders | `/maintenance/work-orders` | Manual/HIVE-generated work orders |
| Quality checks | `/quality/checks` | Manual checks or barcode QC events |
| Rework tasks | `/rework` | Auto-created from failed quality checks |
| Barcode events | `/barcode/events` | Normalized scanner events |
| Ottimo | `/connectors/ottimo/placeholder` | Demo scanner payloads |
| Cabinet Vision SQL | `/connectors/cabinet-vision-sql/placeholder` | Demo SQL-like job/part rows |

## Replacement Points

### Ottimo

Replace `parse_placeholder_event()` in `src/ottimo_connector.py`.

Current demo payload:

```json
{
  "barcode": "AA-GBR|Fixed Shelf",
  "event": "QC_FAIL",
  "station": "packing",
  "operator": "Amit",
  "ts": "2026-06-05T10:30:00+00:00"
}
```

Required normalized output:

```json
{
  "barcode": "AA-GBR|Fixed Shelf",
  "job_name": "AA-GBR",
  "part_name": "Fixed Shelf",
  "station": "packing",
  "event_type": "qc_fail",
  "operator": "Amit",
  "source": "ottimo",
  "raw_payload": {},
  "ts": "2026-06-05T10:30:00+00:00"
}
```

Supported HIVE event types today:

| HIVE event type | Effect |
|---|---|
| `part_complete` | Stores the scan |
| `qc_pass` | Stores the scan and creates a passing quality check |
| `qc_fail` | Stores the scan, creates a failing quality check, and opens rework |
| `packed` | Stores the scan |
| `dispatched` | Stores the scan |

### Cabinet Vision SQL Server

Replace `normalize_placeholder_rows()` in `src/cv_sql_connector.py` with a real
read-only SQL Server query/mapping.

Current demo row:

```json
{
  "job_name": "AA-GBR",
  "client_name": "Ahuja Residence",
  "room_name": "Guest Bedroom",
  "part_name": "Fixed Shelf",
  "material": "18mm ply",
  "length_mm": 760,
  "width_mm": 320,
  "thickness_mm": 18,
  "qty": 1,
  "cnc_file_back": "AA-GBR-FS.cix",
  "has_cnc": true
}
```

Required normalized fields:

```text
job_name, client_name, room_name, job_date, part_name, material, length_mm,
width_mm, thickness_mm, qty, cnc_file_back, cnc_file_front, has_cnc
```

The adapter should continue returning those fields. `upsert_normalized_rows()`
then handles HIVE inserts/updates for `clients`, `jobs`, `parts`, and
`connector_sync_state`.

## Phase 1 Implementation Logic

1. External or demo payload arrives at a placeholder endpoint.
2. Connector adapter maps the payload into HIVE-normalized data.
3. `operations.py` writes the normalized record into HIVE-native tables.
4. Route and operation scans reconcile through `execution.py`, which advances
   station quantities and writes traceability evidence when an approved schedule
   exists. Otherwise the scan remains valid commissioning route evidence.
5. Related workflow records are created automatically where appropriate.
6. Dashboard queries the same HIVE APIs regardless of whether data came from a
   placeholder, manual entry, or the final factory integration.

This keeps the user experience stable while the integration details evolve.
