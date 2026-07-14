# HIVE OS Preventive Maintenance Control

## Purpose

HIVE turns machine-specific maintenance plans, observed usage, condition
readings, inspections, and spare balances into auditable work orders. It keeps
unverified offsite assumptions separate from commissioned factory schedules.

The model is informed by:

- [ISO 55001:2024 asset-management systems](https://www.iso.org/standard/83054.html)
- [ISO 17359:2018 condition-monitoring programmes](https://www.iso.org/standard/71194.html)
- [ISO 13374-1 condition-monitoring data processing](https://www.iso.org/standard/21832.html)
- [IEC 60300-3-11 reliability-centred maintenance](https://webstore.iec.ch/en/publication/1296)
- [OPC UA Machinery preventive-maintenance model](https://reference.opcfoundation.org/specs/OPC-40001-1/5.6)
- [OPC UA Machine Tools operation and power-on counters](https://reference.opcfoundation.org/specs/OPC-40501-1)

These references guide the data model. They do not replace OEM instructions,
Indian law, the factory's risk assessment, or a competent safety professional.

## Safety Boundary

HIVE is observation-first. It does not command a PLC, defeat an interlock,
isolate energy, energize equipment, or determine that a machine is physically
safe. For a plan marked `loto_required`, HIVE only records:

- that the site's hazardous-energy isolation procedure was reported complete;
- the named authorized person who verified it; and
- the completion time and inspection evidence.

Work on machinery must follow the current site procedure, OEM documentation,
and applicable Indian requirements. The dashboard cannot substitute for locks,
tags, testing for zero energy, permits, or physical supervision.

## Trigger Model

| Trigger | Evidence | Result |
|---|---|---|
| Calendar | Plan anchor or last completion | Due after configured days |
| Powered runtime | Machine on/idle event timeline | Due after powered hours |
| Active runtime | Valid cycle observations | Due after processing hours |
| Cycle count | Valid `cycle_end` events | Due after configured cycles |
| Condition | Named metric, threshold, comparator | Due while a matching signal is open |

Hybrid plans can combine trigger families; the first due dimension opens the
work order. Missing usage evidence is shown as `awaiting_evidence`. Runtime gaps
are capped at 16 hours and marked estimated so a missing power-off event cannot
silently create unlimited runtime.

## Commissioning Rule

Startup creates one machine-specific baseline inspection for each active
machine. These plans are `source=engineering_assumption` and `verified=0`.
They may be edited and inspected offsite, but they cannot generate work orders
or block production.

A maintenance planner must compare each plan with the exact machine model's OEM
manual, applicable service bulletins, site risk assessment, and actual duty.
Verification resets usage and calendar baselines to the verification time unless
a known historical anchor is supplied.

No spare SKU is invented offsite. Manufacturer part numbers, quantities,
suppliers, lead times, storage locations, and physical counts are commissioned
from factory evidence.

## Work-Order Lifecycle

1. A verified due plan creates one idempotent linked work order.
2. Required spares are reserved by location; shortages remain visible.
3. Scheduling writes a machine `resource_unavailability` window.
4. The digital twin subtracts that window from available machine capacity.
5. Completion requires every mandatory checklist response.
6. Plans requiring LOTO also require a named authorized verifier.
7. Reserved spares are issued and stock movements are audited.
8. A failed inspection completes the preventive visit and opens a separate
   high-priority corrective work order.
9. Successful evidence advances the plan baseline and clears active condition
   signals for that plan.

Required spare shortages prevent completion. Cancellation releases reservations
and removes the associated future capacity outage.

## On-Site Inputs

For every active machine, capture:

1. Exact manufacturer, model, serial number, and OEM manual revision.
2. OEM calendar, powered-hour, active-hour, and cycle-based tasks.
3. Safe shutdown and site hazardous-energy isolation requirements.
4. Checklist acceptance criteria, units, and required measurements.
5. Condition metric source, unit, normal range, alarm threshold, and sensor ID.
6. Expected duration, criticality, labor skill, and preferred maintenance window.
7. Required spares with manufacturer part numbers and quantities.
8. Physical stock by location, reorder point, supplier, and lead time.

## API Surface

| Method | Path | Purpose |
|---|---|---|
| GET | `/maintenance/snapshot` | Plans, work orders, spares, reliability, readiness |
| POST | `/maintenance/sync` | Re-evaluate verified triggers and reservations |
| GET/POST | `/maintenance/plans` | List or create plans |
| PUT | `/maintenance/plans/{id}` | Versioned plan configuration and verification |
| POST | `/maintenance/conditions` | Record normalized condition evidence |
| GET/POST | `/maintenance/spares` | List or create spare catalog entries |
| PUT | `/maintenance/spares/{key}/stock` | Set and audit a location balance |
| GET/PUT | `/maintenance/work-orders/{id}` | Detail, status, and capacity window |
| POST | `/maintenance/work-orders/{id}/complete` | Checklist, LOTO, spares, completion |
