# HIVE OS Optimization Model

## Purpose

HIVE does not treat a busy machine as a bottleneck by default. It first proves
that released work is routed to that machine, separates ready demand from work
waiting on a predecessor, checks machine-state quality, and records repeated
evidence before optimization may act on the result.

The model is deliberately explainable. Every recommendation carries its source
evidence, while estimated gains remain hidden until real cycle times and stable
factory telemetry are available.

Supply recommendations use a separate deterministic evidence path. HIVE can
surface unmapped shortages with high confidence because they come from explicit
demand, stock, reservations, and open inbound quantities. Lead-time risks remain
medium confidence because projected arrival uses commissioned supplier lead time,
not a guaranteed delivery date. No estimated financial or throughput gain is
shown for either recommendation.

## Research Basis

The active-period family of methods identifies a constraint from the duration a
resource remains active, because the resource with long uninterrupted active
periods is more likely to be making surrounding processes wait. It can operate
from timestamped machine states without requiring a complete simulation model.
Blocking/starvation methods add route and buffer evidence so an inactive machine
is not confused with the process that is actually limiting system throughput.

- [Real-Time Data-Driven Average Active Period Method](https://www.iieta.org/journals/ijdne/paper/10.2495/DNE-V11-N3-428-437)
- [Original shifting bottleneck detection paper](https://informs-sim.org/wsc02papers/145.pdf)
- [Data-driven shifting bottleneck algorithm](https://www.tandfonline.com/doi/full/10.1080/23311916.2016.1239516)
- [Data-driven blockage and starvation method](https://www.tandfonline.com/doi/full/10.1080/00207540701881860)
- [Throughput bottleneck detection systematic review](https://www.tandfonline.com/doi/full/10.1080/21693277.2023.2283031)
- [Data-driven analysis of dynamic bottlenecks in order-based value streams](https://publica.fraunhofer.de/entities/publication/f0ac655c-7644-473f-810a-286a1f711425)
- [Analysis and Visualization of Production Bottlenecks in Industrial IoT](https://www.mdpi.com/2076-3417/13/6/3525)

These sources also emphasize two constraints HIVE now enforces: preprocessing
machine states is essential, and bottlenecks can shift over time. Therefore a
single utilization number cannot justify a scheduling change.

## Constraint State Model

Released `production_orders`, `part_route_steps`, and `execution_jobs` define
demand. An observed part on an unrelated machine never creates queue demand.
Execution state is authoritative when station jobs exist; otherwise HIVE uses
the remaining quantities on the released planned route.

| Signal | Meaning |
|---|---|
| Routed demand | Required quantity minus confirmed/completed quantity for released work |
| Ready demand | Work whose predecessor is complete and which can be dispatched or is running |
| Starved demand | Work waiting on an incomplete predecessor |
| Active periods | Bounded cycle-start/cycle-end running intervals; an alarm does not start a run |
| Reliability loss | Alarm evidence plus overlapping classified downtime |
| Blocking | Every observed successor input buffer is commissioned, verified, and full |
| Throughput | Completed cycles per hour in the analysis window |

Each machine receives one mutually exclusive operating state:

| State | Decision meaning |
|---|---|
| `capacity_constraint` | Ready released demand exists and the machine has sustained high activity |
| `reliability_constraint` | Released demand overlaps an alarm or recorded downtime |
| `starved` | Demand exists but predecessors have not produced ready work |
| `blocked` | Verified successor buffers are full |
| `flow_or_staffing` | Ready demand exists but activity is too low for a capacity conclusion |
| `demand_absent` | No released route demand exists, regardless of observed activity |
| `insufficient_data` | Demand exists but machine-state evidence does not pass minimum coverage |

Only `capacity_constraint` and `reliability_constraint` are eligible to become
the current factory constraint. Starvation and blocking are still actionable,
but increasing the affected machine's capacity would address the wrong resource.

The ranking score combines active share (30%), normalized average active period
(20%), normalized ready demand (25%), normalized recorded downtime (15%), and a
10% eligibility term. This is a transparent prioritization prior, not a learned
causal model. Every component is persisted so factory evidence can replace it.

## Snapshots And Episodes

`GET /bottlenecks` is read-only. `POST /constraints/sync` writes a manual
immutable factory snapshot and one per-machine evidence record. The 0.27 runtime
also appends a due snapshot automatically every five minutes by default. It uses
a dedicated database connection and can be disabled or changed between five and
sixty minutes through versioned supervisor settings.

A qualified machine first enters `observing`; a second qualified sample opens
the episode. No second sample inside five minutes advances an episode, even when
new events arrive, so persistence in time remains mandatory.

When a repeatedly observed constraint moves, the prior open episode closes with
`constraint_migrated`. Two qualified misses close an open episode with
`evidence_cleared`. Optimization recommendations require a matching open episode,
so one alarm or one noisy analysis window cannot change factory priorities.

Each snapshot carries `constraint-intelligence-v2` and a SHA-256 fingerprint of
the decision inputs. The stored report contains supporting and counter-evidence,
the demand source, route confidence, and the applicable guardrail.

Every new snapshot also records its factory shift from the active recurring work
calendar, including timezone, local date, source, and verification state.
Overnight windows retain the date on which the shift began. Samples outside a
window are labeled off-shift; missing or assumed calendars stay visibly
unverified. `GET /constraints/timeline` groups samples by this context and keeps
episode migration and duration history.

Runtime health, failures, settings changes, and retention are persisted. Three
consecutive failures or a stale successful sample create a rationalized
site-engineer alert. Snapshot detail defaults to 90-day retention, while episode
summaries and their boundary evidence remain protected. Automatic sampling is
analytical only: it cannot dispatch work, approve a schedule, acknowledge an
alert, or write to a controller.

## Quantified Opportunity Gate

HIVE exposes downtime minutes directly because they are measured records. It
converts those minutes to exposed units only when the machine has an active
medium/high-confidence cycle model and valid cycle observations. The estimate is
`overlapping downtime seconds / median validated cycle seconds`; it is an upper
exposure bound, not promised additional output. No model means no unit estimate.

## Factory Commissioning

1. Confirm machine passports and read-only telemetry timestamps.
2. Import or approve part routes and release production orders.
3. Approve a schedule so execution jobs become the authoritative demand source.
4. Verify WIP buffer capacities before HIVE can classify blocking.
5. Record downtime and causes; train cycle models from linked complete cycles.
6. Verify the factory calendar, confirm automatic sampling health, and review episode movement by shift.
7. Accept a constraint action into the improvement ledger and validate its measured effect.

## Telemetry Confidence Gate

Recommendations are gated by a separate confidence score:

```text
0.20 ingestion acceptance
+ 0.25 temporal coverage
+ 0.25 cycle start/end integrity
+ 0.15 part identity link rate
+ 0.15 machine clock agreement
```

Duplicate MQTT deliveries are suppressed, malformed events are audited, naive
machine timestamps are interpreted in `Asia/Kolkata`, and heartbeats are stored
outside production history. A low-confidence machine can still display raw
evidence, but HIVE will not recommend a schedule change from it.

## Learning Stages

1. **Commissioning**: validate one real Maestro log and establish stable events.
2. **Learning**: collect complete shifts, classify downtime, and link programs to parts.
3. **Calibrated**: fit machine cycle-time coefficients from observed part features.
4. **Diagnostic**: rank persistent constraints and their dominant causes.
5. **Predictive**: forecast constraint movement and late-job risk from repeated shifts.
6. **Closed-loop planning**: detect deviations, resimulate residual work, and recommend stable sequence changes with human approval.

PLC writes and autonomous machine control are outside the present safety
boundary. HIVE observes, reconciles, and recommends first.

## Pre-Evidence Virtual Commissioning

Before site telemetry exists, `commissioning_lab.py` exercises the expected
factory flow with explicit broad triangular priors. It reports constraint
frequency, output bands, local cycle-time sensitivity, measurement priority,
and conditional intervention uplift. These results are a commissioning aid,
not an earlier maturity stage of the production optimizer.

The lab has its own config fingerprint and append-only table. It cannot create
or activate a cycle model, route, resource value, forecast, schedule, execution
job, or machine event. Production optimization continues to show commissioning
status until its existing real-evidence gates pass. See
`VIRTUAL_FACTORY_COMMISSIONING.md`.

## Predictive Constraint And Delivery Risk

Once the digital-twin readiness gates pass, HIVE repeats the selected policy
with seeded stochastic cycle durations. Bottleneck frequency is the share of
feasible runs in which a machine has the highest utilization; tied peaks share
that run's credit. Relative severity compares each machine's utilization with
that run's peak. Order completion and
tardiness are retained as P10/P50/P80/P95 distributions rather than one falsely
precise ETA.

Every forecast carries the complete factory-input signature. Any order, route,
model, event, stock, labor, tooling, calendar, availability, WIP, or execution
change makes it stale. Completed orders calibrate P80/P95 coverage, P50 error,
and late-risk Brier score. Calibration drift removes effective decision-ready
status until the model assumptions are reviewed.

See `PREDICTIVE_CONTROL.md` for the algorithm, operating gates, research basis,
API, and site workflow.

## Rolling-Horizon Schedule Recovery

Once a schedule is approved, HIVE monitors downtime, held station work,
execution exceptions, unscheduled ready work, schedule overruns, and credible
forecast late risk. A trigger starts a residual simulation: completed operations
are removed and elapsed work is conservatively credited to operations already in
process. Dispatched, acknowledged, running, held, and in-progress work is fixed
in place, as are the first configured horizon positions.

Current, FIFO, EDD, SPT, and material-batch orders are compared only inside the
remaining movable positions. HIVE reports moved-job share, total position shift,
and frozen-position preservation. A candidate is actionable only when it reduces
late jobs, clears a declared tardiness or makespan threshold, and respects the
stability limit. It becomes a draft planning scenario; no station state changes
until a named planner approves non-stale evidence.

See `SCHEDULE_RECOVERY.md` for the full trigger, stability, approval, and
commissioning contract.

## Closed-Loop Improvement Evidence

Optimization output is read-only until an operator explicitly synchronizes it
into the improvement ledger. A deterministic identity based on category,
target, cause, and action keeps the same priority recognizable across
refreshes. Accepting a measurable action declares its owner, hypothesis,
primary metric, direction, target effect, baseline window, evaluation window,
and minimum sample count before implementation.

Implementation freezes the preceding baseline. Evaluation uses the fixed
post-intervention window, a deterministic bootstrap 90% interval, and a
metric-specific safety guardrail. HIVE labels the outcome `validated` only when
the target is met, the interval excludes zero, and no guardrail fails.
`promising` means the point target was met but uncertainty remains;
`ineffective` means a sufficiently powered result missed the target or failed a
guardrail; `inconclusive` means the evidence was insufficient.

Repeated outcomes remain advisory. A recommendation pattern is promoted only
after at least three decisive outcomes, at least 70% are validated, and the
implementations span two dates. Promotion changes no schedule and writes no
machine command.

## Confirmed Root-Cause Feedback

Root-cause analysis is a separate evidence path from optimization. HIVE creates
diagnostic cases only during an explicit incident synchronization and keeps up
to five alternatives with their priors, supporting evidence, contradictions,
and missing data. A correlation never silently becomes a cause.

When a named operator confirms a classified cause for the downtime or quality
record that generated a recommendation, optimization uses that confirmed cause
code in the recommendation identity and displays the confirmation as evidence.
Open and dismissed hypotheses do not alter optimization. This keeps improvement
experiments tied to reviewed factory truth while preserving all earlier
analysis versions.

See `ROOT_CAUSE_DIAGNOSTICS.md` for the ranking and learning contract.

This design follows NIST guidance to verify measurement capability and process
stability, declare experimental factors and responses in advance, and compare
current behavior with a historical baseline. It also preserves a digital thread
from source evidence through intervention and result:

- [NIST process improvement](https://www.itl.nist.gov/div898/handbook/pri/pri.htm)
- [NIST experimental design](https://itl.nist.gov/div898/handbook/pri/section1/pri11.htm)
- [NIST process control techniques](https://www.itl.nist.gov/div898/handbook/pmc/section1/pmc12.htm)
- [Bayesian structural time-series intervention analysis](https://arxiv.org/abs/1506.00356)
- [NIST digital thread for manufacturing and inspection](https://www.nist.gov/publications/testing-digital-thread-support-model-based-manufacturing-and-inspection)

## Automatic Cycle Learning

HIVE derives an immutable cycle observation only when a `cycle_end` can be
paired with the nearest unused `cycle_start` on the same machine. The pair must
have a real part link, matching part identity, and a duration between one second
and four hours. Payload durations are accepted only as a labeled fallback.

Each machine model uses the same explainable CV features as the manual model.
Training is chronological: the newest 20% is held out, outliers are filtered by
median absolute deviation, and a small nonnegative least-squares active-set
search prevents impossible negative time coefficients. Constant features are
excluded as unidentifiable. A candidate needs enough samples and medium or high
validation confidence before it can become active; it replaces an existing
active model only when confidence improves or same-tier error improves by 5%.

## Routes And Digital Twin

Route edges are created only from a completed cycle followed by the next cycle
start for the same part on a different machine. Edge support, unique parts,
median transfer time, outgoing probability, and confidence remain visible.
They describe observed transitions, not automatically inferred complete routes.

The production twin uses SimPy discrete-event resources to reproduce finite
machine, shared labor, shared tooling, recurring shifts, planned maintenance,
finite input buffers, part flow, transfers, and sequence-dependent setup state on
each setup-capable machine. Setup duration comes from a learned directional P90,
a verified machine fallback, or a visibly production-ineligible commissioning
assumption. It can compare the current sequencer with FIFO, earliest-date,
shortest-processing-time, material-batching, and setup-aware policies. Results
remain commissioning what-if scenarios until every simulated operation has a
cycle model and at least 80% of selected part routes have direct historical
evidence. A multi-family planning scope also requires a verified fallback or
learned coverage for every required directional transition before approval.

Verified rectangular remnants are allocated before new sheets using a
largest-part/smallest-fitting-remnant heuristic. One remnant credits one physical
part only; HIVE does not claim multi-part nesting without a commissioned cutting
pattern export.

Live planning uses only explicit production-order `due_at` timestamps. Cabinet
Vision `job_date` remains source metadata and cannot create a false overdue job.
Policies are ranked by total tardiness, late-job count, makespan, and setup time.
Every scenario is persisted and must pass order, resource, route, model,
feasibility, and stale-input checks before a named operator can approve it.
Scenario results expose capacity wait, calendar wait, blocked parts, and
machine/labor/tool utilization rather than treating unfinished work as complete.

This structure follows NIST guidance that production simulation starts with
explicit requirements and validated input data, and research that combines
live physical state with discrete-event models before using the model for
scheduling decisions:

- [NIST production-system discrete-event simulation requirements](https://nvlpubs.nist.gov/nistpubs/Legacy/IR/nistir6154.pdf)
- [NIST data-driven dispatching-rule identification](https://tsapps.nist.gov/publication/get_pdf.cfm?pub_id=927446)
- [NIST simulation-integrated production planning framework](https://tsapps.nist.gov/publication/get_pdf.cfm?pub_id=921140)
- [Open discrete-event simulator for dynamic flexible job shops](https://www.sciencedirect.com/science/article/pii/S1569190X24000625)
- [Flexible job shop scheduling with sequence-dependent setup time](https://www.tandfonline.com/doi/abs/10.1080/00207543.2012.746480)

## Factory Assumptions To Verify

- Machine PCs use local India time and may not have synchronized clocks.
- SCM machine state is available in local Maestro logs or adjacent event files.
- `.xcs` or `.ard` identity can connect machine cycles to Cabinet Vision parts.
- Gabbiani/Nova feed CNC and edge-banding work; downstream routes can vary by part.
- Morbidelli CX100 and N100 are parallel for some work but not interchangeable for all work.
- Finishing, pressing, and gluing routes are product-dependent rather than universal.
- MQTT and the HIVE API remain on the factory LAN.

Route assumptions must eventually come from Cabinet Vision operations or
observed part transitions. Until then, downstream starvation is explicitly
labeled **inferred**.
