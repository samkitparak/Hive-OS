# HIVE OS Optimization Model

## Purpose

HIVE does not treat a busy machine as a bottleneck by default. It first checks
whether the telemetry is trustworthy, then combines production state, active
periods, waiting work, downstream idle behavior, and failure evidence.

The model is deliberately explainable. Every recommendation carries its source
evidence, while estimated gains remain hidden until real cycle times and stable
factory telemetry are available.

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

The production twin uses SimPy discrete-event resources to reproduce machine
queues, part flow, transfers, and beam-saw material changeovers. It can compare
the current sequencer with FIFO, earliest-date, shortest-processing-time, and
material-batching policies. Results remain commissioning what-if scenarios
until every simulated operation has a cycle model and at least 80% of selected
part routes have direct historical evidence.

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
