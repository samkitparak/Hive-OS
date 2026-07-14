# HIVE OS Factory Resource Model

## Purpose

Machine order alone is not an executable production schedule. HIVE also models
the material, personnel, tooling, calendar, maintenance, and WIP capacity needed
to carry each routed part through the factory.

The model follows the ISA-95 separation of personnel, equipment, physical
assets, and material information. These are independent capabilities with their
own availability and verification evidence:

- [OPC UA ISA-95 common resource models](https://reference.opcfoundation.org/specs/OPC-10030/1)
- [OPC UA ISA-95 personnel roles and qualifications](https://reference.opcfoundation.org/specs/OPC-10030/8.1)
- [OPC UA ISA-95 equipment and material capability facets](https://reference.opcfoundation.org/specs/OPC-10030/10.2)
- [NIST finite-buffer and maintenance-aware manufacturing simulation](https://www.nist.gov/services-resources/software/simantha-simulation-manufacturing)
- [NIST simulation-based finite-capacity shop-floor control](https://www.nist.gov/publications/simulation-based-shop-floor-control-formal-model-model-generation)

## Resource Objects

| HIVE object | Meaning |
|---|---|
| `material_definition` | Board type plus sheet dimensions and nesting yield |
| `material_lot` | Located, status-controlled sheet stock |
| `material_requirement` | Estimated sheets required by one production order |
| `material_reservation` | Stock committed by an approved scenario |
| `labor_role` | Shared qualified headcount pool |
| `tool_pool` | Shared available tooling capacity |
| `machine_resource_profile` | Labor, tooling, and parallel capacity for a machine |
| `work_calendar_window` | Recurring resource availability in a named timezone |
| `resource_unavailability` | Planned maintenance or other finite outage |
| `wip_buffer` | Finite input capacity and current occupancy |
| `resource_change_event` | Named, timestamped audit record for every operator edit |

## Material Logic

Cabinet Vision part dimensions are converted to net square metres by material.
Estimated sheets are:

```text
ceil(net part area / (sheet area * nesting yield))
```

This estimate is deliberately conservative and cannot replace a real Cabinet
Vision nesting result. Missing part dimensions make the requirement unresolved.
Only verified, available lots count as stock.

Schedule approval commits lot-level reservations. Completion consumes the
reserved sheets; cancellation releases them. A replacement schedule cannot
discard released or in-progress work, so its material cannot be silently freed.

## Capacity Logic

Each simulated operation acquires, in order:

1. Required labor-role capacity.
2. Required tool-pool capacity.
3. Machine capacity.
4. A continuous common calendar window long enough for setup and processing.
5. Its token from the destination machine's finite input buffer.

The previous operation places that token in the destination buffer after
transfer. A full buffer therefore blocks upstream flow. Planned maintenance is
subtracted from recurring calendar windows. Open downtime removes the affected
machine for the simulation horizon.

Scenarios report machine, labor, and tooling utilization; capacity wait;
calendar wait; completed and blocked parts; and explicit blocked reasons. An
incomplete scenario is never eligible for recommendation.

## Offsite Assumptions

HIVE seeds the following values as `engineering_assumption`, `verified=0`:

| Assumption | Initial value |
|---|---|
| Board size | 2440 x 1220 mm |
| Nesting yield | 82% |
| Factory calendar | Monday-Saturday, 09:00-18:00, Asia/Kolkata |
| Machine capacity | One simultaneous operation |
| Process labor | One operator per pool; two for shared finishing |
| Tooling | One available process toolset |
| Machine input buffer | 50 parts |

These values make offsite what-if simulation possible. They do not satisfy the
production gate. A named operator must verify the values in **Planning >
Resources** before an order can move to `ready`.

## Approval Gates

For every selected route, HIVE requires:

- Verified material definition, dimensions, yield, and enough verified stock.
- Verified machine resource profile.
- Verified non-zero labor and tooling pools.
- Verified recurring factory calendar.
- Verified non-full downstream WIP buffers.
- No open downtime on a required machine.
- Ready/released order state, contractual due time, calibrated cycle models,
  route evidence, and a feasible simulation.

Any stock, profile, calendar, outage, WIP, order, route, model, or telemetry
change alters the factory signature and expires a stale scenario before approval.

## On-Site Sequence

1. Count sheets by Cabinet Vision material code and record storage locations.
2. Confirm actual sheet dimensions and compare HIVE estimates with one CV nest.
3. Enter qualified headcount available to each process pool.
4. Confirm shared toolsets, spares, and tooling currently out for service.
5. Confirm the normal weekly shift and breaks.
6. Record planned maintenance windows.
7. Measure safe input WIP capacity at each downstream machine.
8. Verify each machine profile, then verify the corresponding resource row.
9. Move one test order to `ready`; HIVE lists every remaining failed gate.
10. Compare and approve only a feasible, non-stale scenario.
