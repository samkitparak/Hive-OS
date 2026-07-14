# HIVE OS Production Control

## Operating Model

HIVE separates imported product data from executable production control:

| HIVE object | Manufacturing meaning |
|---|---|
| Cabinet Vision `job` | Production request / product definition |
| `production_order` | Operator-controlled work request |
| `part_route_step` | Machine-level job order / work master step |
| `planning_scenario` | Finite-capacity schedule proposal |
| `planning_decision` | Human approval, rejection, or expiry evidence |
| `production_schedule_item` | Approved dispatch position |

This follows the ISA-95 distinction between a work schedule, work requests, and
machine/line job orders. A source import date is never treated as a due date.
Operators must set a timezone-aware `due_at` value before an order can become
`ready` or `released`. Material, labor, tooling, calendar, WIP, and required
machine profiles must also be verified and feasible.

Primary references:

- [ISA-95 enterprise-control integration](https://www.isa.org/standards-and-publications/isa-standards/isa-95-standard)
- [OPC UA ISA-95 job-control overview](https://reference.opcfoundation.org/ISA95JOBCONTROL/v200/docs/4)
- [NIST human/machine teaming for manufacturing digital twins](https://www.nist.gov/programs-projects/humanmachine-teaming-manufacturing-digital-twins)
- [NIST human-in-the-loop digital-twin architecture](https://www.nist.gov/publications/conceptual-architecture-digital-twins-human-loop-based-smart-manufacturing)

## Order Lifecycle

```text
draft -> ready -> released -> in_progress -> completed
   |       |          |             |
   +---- cancelled    +---- hold ---+
```

- `draft`: imported but not authorized for production.
- `ready`: due time and complete part routes are present.
- `released`: supervisor has made the work available to the floor.
- `in_progress`: machine or barcode evidence touched a required route step.
- `hold`: work is intentionally paused; route edits are allowed only off-line.
- `completed`: every required route step reached its required physical quantity.
- `cancelled`: removed from active production without deleting history.

Updates use optimistic versions. A stale browser receives HTTP `409` instead of
overwriting another operator's decision. Every update and lifecycle transition
is appended to `production_order_events` with actor, time, and payload.

## Route Reconciliation

Initial routes are generated conservatively from CV features:

1. Gabbiani beam saw.
2. Morbidelli CX100 when a CNC program exists.
3. Stefani KD when at least one edge is banded.

Observed multi-machine routes or operator-confirmed routes carry stronger
confidence. A route step stores required and confirmed quantity, so a CV row
with `qty=10` needs ten unique completion events rather than one.

Machine `cycle_start`/`cycle_end` events and barcode
`operation_start`/`operation_complete` events reconcile automatically. Evidence
from the wrong machine or wrong sequence creates a route exception; it does not
silently rewrite the process plan.

## Schedule Approval

The digital twin ranks policies by:

1. Total tardiness against explicit production-order due times.
2. Number of late jobs.
3. Makespan.
4. Material-changeover time.

A scenario cannot be approved unless every operation has a cycle model and at
least 80% of routes have observed or operator-confirmed evidence. Approval also
requires a feasible finite-capacity resource simulation and checks a factory-input
signature. Changes to orders, routes, models, resources, stock, calendars,
maintenance, WIP, or evidence expire the old scenario and require a fresh comparison.

Approved scenarios reserve verified material lots. Completion consumes those
reservations and cancellation releases them. A replacement schedule must retain
all released and in-progress work.

Schedule approval atomically reserves sheet lots, component lots, and verified
remnants. Every reservation, release, issue, physical adjustment, and remnant
disposition is written to the warehouse movement ledger.

## Station Execution

An approved scenario is materialized into one `execution_job` per required part
route step. Work remains queued until its production order is released and its
predecessor is complete. Dispatch, acknowledgement, start, partial completion,
scrap, hold, resume, and cancellation are versioned in an immutable event
ledger. Machine and barcode evidence use the same state machine as manual
actions, while violations are retained as execution exceptions.

Good completion moves WIP to the next station buffer. Starting the next step
removes it. Only final good route completion settles the order and consumes its
material reservation. See [EXECUTION_CONTROL.md](EXECUTION_CONTROL.md).

The approved sequence remains advisory: HIVE releases and tracks work but does
not write to PLCs or bypass machine safety controls.

## Day-One Workflow

1. Import or watch the Cabinet Vision export folder.
2. Open **Planning** and cancel historical/example jobs.
3. Set due times and priorities for live jobs.
4. Review generated part routes; correct product-specific press/sanding/finish steps.
5. Verify materials, labor, tooling, machine profiles, shift calendar, and WIP buffers.
6. Move complete orders to `ready` and run schedule comparisons.
7. Approve only when HIVE marks the scenario production-ready, then release floor work.
8. Open **Operations**, dispatch the first available station job, and record acknowledgement.
9. Create the order's serialized label set and print one QR label per physical unit.
10. Commission machine logs and barcode stations against the same station job.
11. Resolve route and execution exceptions as the first real jobs cross the floor.
