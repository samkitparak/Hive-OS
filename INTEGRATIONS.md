# HIVE OS Integration Roadmap

## Highest-Value Factory Integrations

| Integration | Value | Likely interface |
|---|---|---|
| Cabinet Vision SQL Server | Live jobs, revisions, materials, due dates | Read-only SQL |
| Maestro machine software | Cycles, programs, alarms, part completion | Logs / local agent |
| Ottimo barcode system | Serialized WIP, finished goods, packing, dispatch readiness | Logs / API / scanner events |
| Energy meters | Machine state, power cost, compressor efficiency | Modbus TCP |
| Sergiani Siemens controller | Press cycle, recipe, temperature, alarms | OPC-UA / Modbus TCP |
| ERP/accounting | Orders, purchase needs, inventory, costing | API / database export |
| Maintenance system | Work orders, spares, preventive maintenance | API / HIVE module |
| Quality inspections | Rejects, rework, defect Pareto | Tablet forms / barcode |
| Tooling management | Tool life, sharpening, replacement prediction | Maestro data / operator scans |
| Environmental sensors | Dust, temperature, humidity, VOC, air pressure | MQTT / Modbus |
| Warehouse and sheet inventory | Material availability and remnants | Barcode / RFID / CV data |
| Attendance and labor | Staffing versus output and skill coverage | Attendance API / CSV |
| Cameras | Safety, queue counts, manual-process cycle detection | Edge CV events |
| AMRs and forklifts | Material movement and dispatching | Fleet API / MQTT |

## Recommended Sequence

1. Cabinet Vision exports and Maestro event reliability
2. HIVE unit labels, then Ottimo alias and station-event mapping
3. Energy meters and utility-cost allocation
4. Inventory/remnant tracking and purchase alerts
5. Maintenance work orders and downtime reasons
6. Direct Cabinet Vision SQL integration
7. Environmental, camera, and material-movement automation

## Design Rule

Prefer read-only integrations first. HIVE OS should observe, reconcile, and
recommend before it is allowed to write schedules or control equipment.

## Phase 1 Placeholder Strategy

The first operations integrations are implemented as replaceable adapters. HIVE
OS already owns the normalized workflows for downtime, maintenance, quality,
rework, barcode scans, Cabinet Vision job/part imports, and Ottimo scanner
events.

Real formats should replace only:

| System | Replacement function | Stable downstream contract |
|---|---|---|
| Ottimo | `src/ottimo_connector.py::parse_placeholder_event` | Normalized barcode event |
| Cabinet Vision SQL | `src/cv_sql_connector.py::normalize_placeholder_rows` | Normalized job/part rows |

See `PHASE1_PLACEHOLDERS.md` for payload examples and exact normalized fields.
