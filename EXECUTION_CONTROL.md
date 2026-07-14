# HIVE OS Execution Control

HIVE execution control turns an approved finite-capacity schedule into auditable
station work. It does not write commands to a machine controller. A human,
scanner, or connected machine agent supplies execution evidence; HIVE validates
that evidence, advances quantities, moves WIP, and records deviations.

## Research basis

- [OPC UA for ISA-95 Job Control overview](https://reference.opcfoundation.org/ISA95JOBCONTROL/v200/docs/4)
  defines a Job Order as a request for a unit of work and a Job Response as the
  report of actual work, equipment, personnel, and material.
- [ISA95JobOrderDataType](https://reference.opcfoundation.org/ISA95JOBCONTROL/v100/docs/6.3.2)
  carries a job ID, work master, proposed start/end, and resource requirements.
- [GS1 EPCIS 2.0](https://ref.gs1.org/standards/epcis/2.0.1/) models traceability
  with event time, read point, business location, business step, and resulting
  disposition.
- [OPC UA ISA-95 material model](https://reference.opcfoundation.org/specs/OPC-10030/4.2)
  separates material definitions from actual lots so consumed material remains
  traceable.

HIVE follows those boundaries without claiming formal certification.

## Object model

| HIVE object | Meaning |
|---|---|
| `production_orders` | ISA-95-like work request controlled by the planner |
| `production_schedule_items` | Approved order sequence from the digital twin |
| `part_route_steps` | Work definition: required station sequence and quantity |
| `execution_jobs` | Station Job Order generated for one required route step |
| `execution_job_events` | Immutable Job Response/action history |
| `traceability_events` | Physical object, location, business step, and disposition |
| `execution_exceptions` | Actual-vs-control deviations requiring disposition |

An execution job is unique per route step. Repeated API calls use an optional
idempotency key, and every mutation uses optimistic version checking.

## State machine

```text
queued -> available -> dispatched -> acknowledged -> running -> completed
              |             |             |             |
              +-----------> held <---------+-------------+
                               |
                             resume
```

- `queued`: order is not released, a predecessor is incomplete, a route
  exception is open, or the machine has open downtime.
- `available`: all route and release prerequisites pass.
- `dispatched`: a supervisor sent the work to the station.
- `acknowledged`: an operator accepted responsibility.
- `running`: one or more units are physically in process.
- `held`: work is intentionally paused with a reason and resumable state.
- `completed`: the required good quantity is complete. Scrap never satisfies
  required good quantity.

Before a manual start, HIVE rechecks machine slots, shared labor, shared tools,
the verified calendar, planned unavailability, open downtime, route sequence,
and order release. Machine/scanner actuals are never discarded: evidence that
bypasses a gate is recorded with an execution exception.

## Quantity and WIP logic

1. `start(quantity)` adds units to `in_process_qty`.
2. Starting a downstream operation removes that quantity from its input WIP
   buffer. An underflow is recorded if physical evidence exceeds recorded WIP.
3. `complete(good_qty, scrap_qty)` removes units from in-process quantity.
4. Good units increment the next station's input WIP buffer.
5. Buffer overflow is retained as physical truth and opens an exception; it is
   not silently capped.
6. The final confirmed route step completes the production order and consumes
   its committed sheet reservation.

## Evidence adapters

Machine `cycle_start` and `cycle_end` events are reconciled into execution start
and completion actuals when the event identifies a part. Barcode events use the
same path:

| Normalized scan | Execution/trace result |
|---|---|
| `route_arrival`, `operation_start` | Start one unit |
| `operation_complete`, `part_complete` | Complete one good unit; implicit start if necessary |
| `qc_pass`, `qc_fail` | Conforming/non-conforming disposition |
| `packed` | Packed disposition |
| `dispatched` | Dispatched disposition |

The one-unit barcode assumption is explicit and replaceable when Ottimo's real
payload reveals a quantity or serial identity.

## API

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/execution/snapshot` | Station queues, summary, exceptions, and recent actuals |
| POST | `/execution/sync` | Materialize station jobs from the approved schedule |
| GET | `/execution/jobs` | Filterable station-job list |
| POST | `/execution/jobs/{id}/action` | Dispatch, acknowledge, start, complete, hold, resume, cancel |
| GET | `/execution/events` | Immutable execution event history |
| GET | `/execution/exceptions` | Actual-vs-control deviations |
| POST | `/execution/exceptions/{id}/resolve` | Correct, accept, or ignore a reviewed deviation |
| GET | `/traceability/events` | Query physical-flow events by object or part |

## Onsite verification

Before live dispatch:

1. Verify every scanner's station key and whether one scan means one unit.
2. Define physical read points and buffer/location names.
3. Verify the operator acknowledgement workflow at each station.
4. Run one routed test part and compare HIVE starts/completions to machine logs.
5. Test duplicate scans, out-of-sequence scans, scrap, hold/resume, and downtime.
6. Confirm WIP buffer counts physically before enabling schedule approval.
7. Keep machine command output disabled until vendor protocols and safety
   interlocks are commissioned independently.
