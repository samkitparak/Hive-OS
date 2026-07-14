# Phase 1 Placeholder Integrations

Phase 1 gave HIVE real product surfaces before the exact factory formats were
known. These endpoints remain for demos and compatibility, but production data
now uses the versioned workflow in `CONNECTOR_COMMISSIONING.md`. No source-code
replacement is required for ordinary field or event-value differences.

## What Exists Now

| Area | HIVE table/API | Placeholder source |
|---|---|---|
| Downtime reasons and events | `/downtime` | Manual/demo downtime events |
| Maintenance work orders | `/maintenance/work-orders` | Manual/HIVE-generated work orders |
| Quality checks | `/quality/checks` | Manual checks or barcode QC events |
| Rework tasks | `/rework` | Auto-created from failed quality checks |
| Barcode events | `/barcode/events` | Normalized scanner events |
| Unit labels | `/labels/jobs` | HIVE QR/SVG and Zebra ZPL output |
| Ottimo | `/connectors/ottimo/placeholder` | Demo scanner payloads |
| Cabinet Vision SQL | `/connectors/cabinet-vision-sql/placeholder` | Demo SQL-like job/part rows |

## Legacy Adapter Points

### Ottimo

`parse_placeholder_event()` in `src/ottimo_connector.py` serves only the legacy
demo endpoint. Production formats are mapped in the commissioning UI and do not
require changes to this function.

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
| `part_complete` | Completes one serialized unit at the resolved station |
| `qc_pass` | Updates unit disposition and creates a passing quality check |
| `qc_fail` | Marks the unit non-conforming, creates a failing check, and opens rework |
| `packed` | Marks the resolved unit packed |
| `dispatched` | Marks the resolved unit dispatched |

### Cabinet Vision SQL Server

`normalize_placeholder_rows()` in `src/cv_sql_connector.py` remains the shared
normalized upsert boundary for the legacy demo endpoint. Production SQL source
and field mappings are configured in the commissioning UI.

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
3. `identity.py` resolves exact HIVE or external aliases and derives job/part
   context for serialized labels; legacy display-text codes remain supported.
4. `operations.py` preserves the raw scan and its resolution result.
5. Route and operation scans reconcile through `execution.py`, which advances
   station quantities and writes traceability evidence when an approved schedule
   exists. Otherwise the scan remains valid commissioning route evidence.
6. Duplicate unit/station events are retained but cannot double-count quantity.
7. Related workflow records are created automatically where appropriate.
8. Dashboard queries the same HIVE APIs regardless of whether data came from a
   placeholder, manual entry, or the final factory integration.

This keeps the user experience stable while the integration details evolve.
