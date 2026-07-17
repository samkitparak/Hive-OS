# HIVE OS Predictive Production Control

## Purpose

HIVE forecasts where the production constraint is likely to move and which
committed orders are at risk before the risk becomes current downtime or a late
delivery. Forecasts remain advisory: they create evidence, recommendations, and
reviewable alerts but never release work or write to a machine.

## Forecast Method

Each forecast freezes the current production orders, routes, active cycle
models, stock, labor, tooling, calendars, planned outages, WIP buffers, and
approved resource capacities into an input signature. It then runs 20 to 200
seeded SimPy replications. The default is 50.

Operation duration in each replication is sampled from the active cycle model's
mean and residual coefficient of variation. Durations stay positive and the
coefficient of variation is capped at 0.5. This captures measured process-time
variation without pretending that HIVE already knows uncommissioned breakdown,
absence, or supply distributions.

For every machine HIVE reports:

- bottleneck probability: share of feasible runs where it has the highest
  utilization, with equal peaks sharing that run's credit;
- relative severity: its utilization relative to each run's peak utilization;
- mean and P90 utilization;
- P80 simulated resource wait.

For every controlled order HIVE reports P10, P50, P80, and P95 completion,
late probability, expected tardiness, and P80 tardiness. Factory KPIs retain the
same percentile bands for makespan, throughput, late-order count, and total
tardiness.

## Credibility Gates

A forecast is decision-ready only when:

1. every selected operation has an active cycle model;
2. at least 80% of routes are observed or operator-confirmed;
3. production orders, due times, materials, components, labor, tooling,
   calendars, WIP, and availability pass the twin's existing readiness gates;
4. at least 95% of ensemble runs finish feasibly; and
5. at least 20 runs complete.

An immutable input signature marks a forecast stale as soon as production or
resource truth changes. HIVE automatically generates a new default forecast
when the factory becomes decision-ready and the signature changes. Manual
refresh remains available to planners and supervisors.

## Forecast Calibration

The latest forecast made before each completed production order is compared
with its actual completion event. HIVE measures:

- P80 and P95 interval coverage;
- P50 mean absolute completion error;
- Brier score for the predicted late/not-late probability.

Calibration begins as `collecting`, can become `credible`, remains `monitor`
when evidence is mixed, and becomes `drift` when interval coverage or delivery
risk accuracy materially degrades. Drift disables decision-ready output and
requires review of cycle models, routes, calendars, resource capacity, and
unmodeled disturbances. HIVE never silently retunes operational inputs from a
forecast miss.

This follows NIST guidance that manufacturing digital twins need verification,
validation, uncertainty quantification, traceable results, and continuous
forecast-gap monitoring:

- [NIST Digital Twins for Advanced Manufacturing](https://www.nist.gov/programs-projects/digital-twins-advanced-manufacturing)
- [NIST credibility considerations for manufacturing digital twins](https://www.nist.gov/publications/credibility-consideration-digital-twins-manufacturing)
- [NIST/Winter Simulation Conference manufacturing digital-twin lifecycle](https://tsapps.nist.gov/publication/get_pdf.cfm?pub_id=936954)
- [Dynamic bottleneck frequency and severity research](https://arxiv.org/abs/2306.16120)
- [Monte Carlo scheduling with probabilistic durations](https://arxiv.org/abs/1110.2732)

## API And Operator Flow

| Method | Path | Behavior |
|---|---|---|
| GET | `/api/forecast` | Latest forecast, staleness, effective readiness, and calibration |
| GET | `/api/forecast/history` | Immutable recent forecast snapshots |
| POST | `/api/forecast/refresh` | Seeded refresh with policy, sample count, and optional order scope |

The dashboard forecast band shows the likely future constraint, P50/P80
completion, late-order risk, feasibility, and calibration. A decision-ready
order with at least 50% late probability becomes a rationalized planning alert.
The optimization feed also exposes at-risk orders and forecast constraints as
reviewable actions.

## Site Commissioning

1. Finish route, cycle-model, resource, and contractual due-time commissioning.
2. Run 50 replications and compare the likely constraint with the planner's
   current expectation.
3. Review P50/P80 completion against the approved schedule before release.
4. Record actual completion through normal route evidence; do not backfill
   timestamps to improve calibration.
5. After five completed-order outcomes, inspect coverage, error, and Brier score.
6. Classify forecast misses as model, route, resource, calendar, disturbance,
   or source-data gaps before changing an input.
