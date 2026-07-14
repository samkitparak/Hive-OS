# HIVE OS Improvement Learning

## Safety Boundary

The improvement layer is an evidence and approval system. It does not write to
PLCs, machine controllers, approved schedules, production-order state, or
resource reservations. Synchronizing priorities is explicit; all GET endpoints
remain read-only.

## Lifecycle

```text
optimization evidence
  -> proposed
  -> accepted or rejected
  -> evaluating after implementation
  -> validated | promising | ineffective | inconclusive
  -> optional repeat
```

Unmeasured commissioning and master-data actions can be accepted, owned, and
completed without pretending they are statistical experiments. Measurable
actions create a new experiment each time they are accepted, preserving repeat
trials under the same stable recommendation identity.

Every transition records actor, time, prior state, next state, notes, and a JSON
evidence payload in `improvement_events`. Recommendation and experiment versions
prevent stale browser actions from overwriting newer operator decisions.

## Metrics

| Metric | Sample | Default direction | Typical action |
|---|---|---|---|
| Throughput per hour | Completed cycles in each hour bucket | Increase | Constraint protection |
| Downtime minutes per hour | Interval overlap in each hour bucket | Decrease | Reliability, setup, staffing, or material flow |
| Defect rate | Pass/fail result for each quality check | Decrease | Defect containment |
| Median cycle time | Valid linked cycle observation | Decrease | Setup or method improvement |

Zero-throughput and zero-downtime hours remain samples because removing them
would bias the comparison. Defect and cycle-time metrics require actual quality
checks or valid linked observations; HIVE does not invent empty samples.

## Baseline And Evaluation

Accepting an experiment declares:

- named owner and hypothesis
- target machine or factory
- primary response metric and direction
- minimum target change in percent
- baseline and evaluation durations
- minimum sample count
- known confounders

Implementation is blocked until the preceding baseline contains enough samples.
At implementation, HIVE freezes the baseline window and its summary. The result
uses the fixed window from implementation to the declared due time, even if the
operator evaluates it later.

HIVE resamples baseline and evaluation observations 1,000 times with a stable
seed and reports a 90% interval for direction-adjusted relative effect. A zero
baseline cannot produce a relative effect and is `inconclusive` rather than an
inflated success.

## Outcome Rules

| Outcome | Rule |
|---|---|
| `validated` | Minimum samples met, target met, lower 90% bound above zero, no failed guardrail |
| `promising` | Minimum samples and target met, but the interval still includes zero |
| `ineffective` | Powered comparison misses target or a safety guardrail fails |
| `inconclusive` | Minimum evidence is missing or relative effect is undefined |

Throughput experiments guard defect rate and downtime. Downtime and quality
experiments guard throughput. Cycle-time experiments guard defect rate. Missing
guardrail samples are displayed as unavailable and never silently treated as
measured evidence.

## Promotion

A repeated recommendation becomes a reusable advisory only when it has at least
three decisive outcomes, at least 70% are `validated`, and implementations span
at least two dates. `promising` does not count as validation. Promotion remains
visible guidance only and cannot trigger control changes.

## On-Site Workflow

1. Review telemetry quality and downtime/quality classification for the shift.
2. Open **Review actions** and synchronize current priorities.
3. Accept one action, name its owner, and set the experiment contract.
4. Implement only after the baseline gate passes.
5. Record confounders in the shift notes while the window runs.
6. Evaluate after the due time and inspect effect, interval, and guardrails.
7. Repeat on a comparable date before relying on a promising result.

The method is grounded in [NIST process improvement](https://www.itl.nist.gov/div898/handbook/pri/pri.htm),
[NIST experimental design](https://itl.nist.gov/div898/handbook/pri/section1/pri11.htm),
and [NIST control-chart principles](https://www.itl.nist.gov/div898/handbook/pmc/section3/pmc31.htm).
