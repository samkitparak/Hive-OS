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
| Notifications and incident systems | Owned abnormal-condition response and escalation | Commissioned CloudEvents webhook |

## Recommended Sequence

1. Cabinet Vision exports and Maestro event reliability
2. HIVE unit labels, then Ottimo alias and station-event mapping
3. Energy meters and utility-cost allocation
4. Inventory/remnant tracking, supplier commissioning, purchase orders, and receipts
5. Commission OEM maintenance plans, condition thresholds, and spare catalogs
6. Direct Cabinet Vision SQL integration
7. Environmental, camera, and material-movement automation

## Design Rule

Prefer read-only integrations first. HIVE OS should observe, reconcile, and
recommend before it is allowed to write schedules or control equipment.

## Commissioned Connector Strategy

HIVE owns normalized workflows for downtime, maintenance, quality, rework,
barcode scans, Cabinet Vision job/part imports, and Ottimo scanner events. Real
vendor formats are now learned through versioned connector mappings rather than
source-code replacements.

Production formats enter through:

| System | Commissioning route | Stable downstream contract |
|---|---|---|
| Ottimo | `/connectors/ottimo_barcode/analyze` then approve/import | Normalized barcode event |
| Cabinet Vision SQL | read-only view discovery then analyze/approve/sync | Normalized job/part rows |
| SCM Maestro | per-machine log evidence analyze/approve/replay | Normalized machine event |

See `CONNECTOR_COMMISSIONING.md` for credentials, evidence, mappings, and exact on-site steps.
Modbus TCP, OPC-UA, and MQTT telemetry use the same evidence-first pattern via
**Commission > Industrial I/O**. HIVE now stores immutable read-only signal
contracts, normalized samples, latest values, hourly rollups, and debounced
machine-state transitions. See `INDUSTRIAL_TELEMETRY.md`.
Legacy placeholder endpoints remain available for demo/test compatibility only.
See `MAINTENANCE_CONTROL.md` for preventive trigger and evidence contracts.

## Procurement and ERP Boundary

HIVE now owns canonical supplier-item mappings, inbound-aware shortages,
purchase-order approval, accepted/rejected receipts, and supplier performance
evidence. Day one uses validated CSV import/export. A real ERP connector reads
the canonical outbox, translates it to the vendor contract, and positively
acknowledges delivery; queueing alone never marks a PO sent.

See `PROCUREMENT_INTEGRATION.md` for the exact master data, lifecycle, CSV, and
adapter contracts.

## Alert Delivery Boundary

HIVE rationalizes actionable conditions internally, then sends a vendor-neutral
CloudEvents webhook to a site-approved gateway. The gateway owns Teams, Slack,
email, SMS, WhatsApp, or incident-platform credentials and channel routing.
HIVE stores only the HTTPS endpoint and optional HMAC environment-variable name.
Simulation, live verification, enabling, and automatic dispatch are separate
named-operator decisions. See `ALARM_MANAGEMENT.md`.
