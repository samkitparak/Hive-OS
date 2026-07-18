# WIP and Flow Intelligence

HIVE OS 0.32 adds a revisioned measurement layer for work in process, queue
age, operation flow time, and recurring flow pressure. It is designed to begin
collecting useful history as soon as the central PC is installed while keeping
schedule intent separate from physical factory evidence.

## Semantic boundary

HIVE reports five populations separately:

| Population | Meaning | Evidence |
|---|---|---|
| Released queue | First operation is released but has not started | Approved schedule and release |
| Ready WIP | A downstream predecessor completed and the next operation is waiting | Route and predecessor completion |
| In-process WIP | Quantity is currently started at a station | Execution start and quantity state |
| Held WIP | Started or downstream material is formally held | Execution hold state |
| Blocked demand | Released operation cannot become available | Route predecessor or control state |

Released queue is demand, not physical WIP. A system transition may establish
controlled WIP, but only a machine event or barcode event counts as physical
evidence. This prevents an approved plan from manufacturing inventory in the
database before anything moves on the floor.

## Measurement pipeline

1. The central service samples execution state every five minutes.
2. One `flow_samples` row is stored per UTC five-minute bucket. Repeated calls
   in the same bucket are idempotent.
3. `flow_machine_samples` stores station stocks, queue-age percentiles, buffer
   reconciliation, evidence confidence, and flow pressure.
4. After a calendar shift ends, HIVE combines samples, execution transitions,
   physical completion sources, and the production-loss ledger.
5. The completed shift is stored in `flow_shift_snapshots` with an evidence
   hash. Late evidence creates a new revision and supersedes the previous
   current revision without deleting it.

GET endpoints remain read-only. Automatic or operator-triggered sync is the
only path that records samples or closes shifts.

## Time calculations

For operation `i`:

```text
ready_at(i) = production_order.released_at                    when i = 1
ready_at(i) = predecessor_execution_job.completed_at          when i > 1

queue_time(i)           = started_at(i) - ready_at(i)
process_time(i)         = completed_at(i) - started_at(i)
operation_flow_time(i)  = completed_at(i) - ready_at(i)
release_elapsed(i)      = completed_at(i) - order.released_at
```

HIVE computes quantity-weighted P50 and P90 values. Missing timestamps remain
missing and reduce timestamp coverage; they are never replaced with zero.

## Sampled WIP

WIP is a stock and cannot be reconstructed reliably by counting completion
events. A completed shift therefore uses the five-minute samples:

```text
station_wip(t) = ready_wip(t) + in_process_wip(t) + held_wip(t)
average_station_wip = mean(station_wip(t_1) ... station_wip(t_n))
sample_coverage = captured_shift_buckets / expected_shift_buckets
```

The interval is fixed and visible in the API. Raw samples are retained for 180
days; revisioned shift closes preserve the aggregate evidence beyond that.

## Buffer reconciliation

Execution updates a downstream input buffer when a predecessor completes and
decrements it when work starts. For each station:

```text
buffer_difference = recorded_input_buffer - downstream_ready_wip
```

A buffer is reconciled only when it is site-verified, sourced from execution,
and the difference is zero. An unverified engineering capacity can inform a
low-confidence pressure score but cannot make flow decision-ready.

## Flow pressure

Flow pressure is a bounded corroborating score, not an independent bottleneck
declaration:

```text
35%  downstream ready WIP / verified buffer capacity
25%  queue P90 / elapsed shift time
15%  positive arrivals-versus-completions imbalance
15%  held WIP / controlled WIP
10%  corroboration by an open repeated constraint episode
```

Each component is capped before weighting. A station becomes a
`constraint_candidate` only at a score of at least 60 with high evidence
confidence. Capacity changes must still consider the production-loss ledger,
upstream starvation, demand, maintenance, labor, tooling, and the existing
repeated-sample constraint detector.

## Shift-close gate

A completed shift is flow-decision-ready only when all are true:

- the factory calendar is verified;
- five-minute sample coverage is at least 90%;
- at least one unit completed;
- at least 90% of completed quantity came from machine or barcode evidence;
- at least 90% of completed quantity has ready, start, and completion times.

Production-loss OEE has its own independent gate. A shift may be trustworthy
for flow but not OEE, or vice versa, and HIVE reports both states.

## Historical learning

Recurring flow pressure requires one station to rank first in at least three
decision-ready shifts and at least 30% of trusted shift closes.

HIVE creates exploratory three-sigma baselines after 20 decision-ready shifts,
but never labels them control limits automatically. A control-limit decision
requires an engineering review of independence and stationarity.

Little's Law is evaluated per station as:

```text
estimated_flow_time = average_wip / average_throughput
```

The estimate stays hidden until at least 30 decision-ready shifts exist and
both shift WIP and throughput have coefficients of variation no greater than
0.25. Even then the API labels it decision support, not causal proof.

## Research basis

- [ISO 22400-1](https://www.iso.org/standard/56847.html) defines an
  industry-neutral framework for manufacturing KPI construction and use.
- [NIST Technical Note 1890](https://www.nist.gov/publications/inventory-and-flow-time-us-manufacturing-industry)
  treats inventory and flow time as measurable manufacturing waste exposure.
- [NISTIR 7180](https://nvlpubs.nist.gov/nistpubs/Legacy/IR/nistir7180.pdf)
  identifies queue length, throughput rate, and elapsed time as complementary
  performance measures and warns that queueing analysis assumes steady state.
- [NIST manufacturing analytical services](https://tsapps.nist.gov/publication/get_pdf.cfm?pub_id=920909)
  shows the relationship between throughput, WIP, utilization, variability,
  and queue time and applies Little's Law as `WIP = throughput * cycle time`.
- [NIST process stability guidance](https://www.itl.nist.gov/div898/handbook/ppc/section4/ppc45.htm)
  requires constant mean and variance over time and recommends more than 100
  independent samples before claiming process stability.

## API

| Method | Path | Behavior |
|---|---|---|
| GET | `/flow-intelligence?days=90` | Current flow, sampling health, shift history, baselines, recurrence, and gated Little's Law |
| POST | `/flow-intelligence/sync` | Capture the current bucket and close due shifts |
| POST | `/flow-intelligence/sync` with `local_date` | Recalculate one completed shift and revision it only if evidence changed |

## Factory commissioning

1. Verify the factory calendar and time zone.
2. Approve a production schedule so station execution jobs exist.
3. Connect machine events or use serialized barcode scans for operation starts
   and completions.
4. Verify physical input-buffer capacities and reconcile recorded quantities at
   each production station.
5. Run one known route through at least two operations and confirm released
   queue, downstream WIP, start, completion, and buffer decrement.
6. Leave the central PC running through a complete shift and verify at least
   90% sample coverage.
7. Review the first shift close with the supervisor before using flow pressure
   in dispatch or capacity decisions.
