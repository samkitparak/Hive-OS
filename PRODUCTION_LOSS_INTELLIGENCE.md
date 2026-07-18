# HIVE OS Production Loss Intelligence

## Purpose

The production-loss engine turns raw state events, reviewed downtime, planned
unavailability, calibrated cycle models, and quality disposition into a
shift-scoped loss waterfall. It is read-only and recomputable from retained
evidence. It does not approve schedules, alter orders, or write to equipment.

The implementation follows the ISO 22400 principle that manufacturing KPIs need
defined formulas, elements, time behavior, units, and user context. OEE is
treated as a hierarchy of availability, performance, and quality rather than a
single unexplained score. This is consistent with NIST's manufacturing KPI
relationship work and its guidance that measurement capability and uncertainty
must be understood before analytics are used for control.

Primary references:

- [ISO 22400-1 KPI concepts and terminology](https://www.iso.org/standard/56847.html)
- [ISO 22400-2 KPI definitions and formulas](https://www.iso.org/standard/54497.html)
- [NIST hierarchical manufacturing KPI relationships](https://www.nist.gov/publications/hierarchical-structure-key-performance-indicators-operation-improvement-production)
- [NIST smart-manufacturing performance measurement](https://www.nist.gov/programs-projects/operations-driven-performance-measurement-smart-manufacturing-systems)
- [NIST monitoring, diagnostics, and prognostics validation](https://www.nist.gov/programs-projects/monitoring-diagnostics-and-prognostics-manufacturing-operations)
- [NIST measurement-process characterization](https://www.itl.nist.gov/div898/handbook/mpc/section1/mpc11.htm)

## Time Boundary

HIVE selects the active recurring factory-calendar window. Outside a shift it
selects the latest completed window. `date=YYYY-MM-DD` recalculates all windows
anchored to that local factory date, including overnight shifts. If no calendar
exists, the API exposes a visibly unverified rolling eight-hour fallback.

Planned factory or machine unavailability is recorded inside scheduled time but
excluded before the OEE denominator is formed:

```text
scheduled time - planned stop = planned production time
```

## Timeline Ledger

Each scheduled second belongs to exactly one timeline category. A recent state
before shift start may be carried in for at most twelve hours; older state is
unknown rather than assumed. Explicit planned-unavailability and reviewed
downtime intervals override raw state for the same second.

```text
scheduled time
  = planned stop
  + running
  + classified availability losses
  + telemetry unknown
```

Reviewed reasons map to breakdown, setup/adjustment, material starvation,
tooling, staffing, quality stop, or no-demand loss. Unreviewed idle and downtime
stay unclassified. Stops of five minutes or less are minor stops; the threshold
is a commissioning default, not a machine fact.

## Output Waterfall

Speed and quality are equivalent output-time losses inside running time and are
not added to timeline downtime. Performance requires every completed cycle to
link to a part and an active medium/high-confidence model. Quality requires one
complete pass/fail/rework disposition for every completed cycle.

```text
planned production time
  = availability loss
  + telemetry unknown
  + speed loss
  + quality loss
  + fully productive time
```

The second identity is reported as reconciled only when every required factor is
available. Missing cycle or quality evidence remains unquantified; HIVE never
substitutes `1.0` for a missing factor.

## Decision Gate

A machine's OEE is decision-ready only when all of these pass:

1. The factory calendar is site-verified.
2. State evidence classifies at least 90% of planned production time.
3. A medium/high-confidence active cycle model covers all completed cycles.
4. Quality disposition is complete for all completed cycles.
5. The timeline and output waterfall reconcile.

Factory OEE includes only decision-ready machines and reports the denominator.
Machine minutes in the Pareto are additive equipment exposure, not elapsed
factory delay or promised recovered output.

## API

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/production-losses` | Current or latest completed factory-shift waterfall |
| `GET` | `/production-losses?date=YYYY-MM-DD` | Recalculate a retained local shift date |
| `GET` | `/production-losses?machine_key=...` | Restrict the report to one production machine |

## Factory Commissioning

1. Confirm the recurring factory calendar and planned exceptions.
2. Verify continuous state transitions and clock agreement for one pilot machine.
3. Review the longest unknown idle and assign the real reason on the floor.
4. Link completed cycles to parts and promote a validated cycle model.
5. Capture complete quality disposition for the validation shift.
6. Compare HIVE's waterfall with the supervisor's shift record and correct the
   source contract before using OEE or the loss Pareto for improvement work.
