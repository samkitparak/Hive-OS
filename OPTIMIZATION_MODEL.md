# HIVE OS Optimization Model

## Purpose

HIVE does not treat a busy machine as a bottleneck by default. It first checks
whether the telemetry is trustworthy, then combines production state, active
periods, waiting work, downstream idle behavior, and failure evidence.

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

- [Real-Time Data-Driven Average Active Period Method](https://www.iieta.org/journals/ijdne/paper/10.2495/DNE-V11-N3-428-437)
- [Throughput bottleneck detection systematic review](https://www.tandfonline.com/doi/full/10.1080/21693277.2023.2283031)
- [Data-driven analysis of dynamic bottlenecks in order-based value streams](https://publica.fraunhofer.de/entities/publication/f0ac655c-7644-473f-810a-286a1f711425)
- [Analysis and Visualization of Production Bottlenecks in Industrial IoT](https://www.mdpi.com/2076-3417/13/6/3525)

These sources also emphasize two constraints HIVE now enforces: preprocessing
machine states is essential, and bottlenecks can shift over time. Therefore a
single utilization number cannot justify a scheduling change.

## Current Evidence Model

For each machine and analysis window, HIVE calculates:

| Signal | Meaning |
|---|---|
| Active share | Fraction of the window spent in cycle or failure-active states |
| Average active period | Mean uninterrupted active duration |
| Longest active period | Strong evidence of a momentary constraint |
| Queue depth | Planned applicable parts minus linked completed parts |
| Downstream inferred starvation | Idle share among configured downstream processes |
| Alarm pressure | Relative alarm count in the window |
| Throughput | Completed cycles per hour |

The provisional constraint score is:

```text
0.25 active share
+ 0.20 normalized average active period
+ 0.30 normalized queue depth
+ 0.15 downstream inferred starvation
+ 0.10 normalized alarm pressure
```

This weighting is an initial engineering prior, not a learned truth. HIVE will
retain the component metrics so factory observations can validate and refit the
weights instead of hiding assumptions inside a black box.

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
6. **Closed-loop planning**: recommend release and sequence changes with human approval.

PLC writes and autonomous machine control are outside the present safety
boundary. HIVE observes, reconciles, and recommends first.

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
finite input buffers, part flow, transfers, and beam-saw material changeovers. It can compare
the current sequencer with FIFO, earliest-date, shortest-processing-time, and
material-batching policies. Results remain commissioning what-if scenarios
until every simulated operation has a cycle model and at least 80% of selected
part routes have direct historical evidence.

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
