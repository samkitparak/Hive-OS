# HIVE OS Root-Cause Diagnostics

## Purpose

HIVE ranks plausible causes for alarms, recorded downtime, and failed or rework
quality checks. It does not claim that temporal correlation proves causation.
Every case exposes alternative hypotheses, source evidence, contradictions, and
missing data so a named operator can confirm, dismiss, or later reopen it.

The diagnostic layer is advisory. It writes only to the HIVE database, never to
a PLC, machine controller, approved schedule, or maintenance work order.

## Evidence Contract

An explicit **Sync incidents** action scans a configurable lookback window and
creates one stable case for each source record:

| Incident | Stable source | Core evidence |
|---|---|---|
| Alarm | `machine_events.id` | Alarm code/message, event sequence, cycle interruption, program and part |
| Downtime | `downtime_events.id` | Controlled reason, duration, machine, nearby event/program/part context |
| Quality | `quality_checks.id` | Result, defect type/process, machine, part, material, program, repeated defects |

Every incident is enriched with open high-priority maintenance work, verified
overdue plans, triggered condition thresholds, required-spare shortages, nearby
program changes, and commissioned power/voltage/current/frequency telemetry.
Utility anomalies require at least eight baseline samples and use a robust
median/MAD deviation threshold of 3.5. If evidence is unavailable, the gap is
stored and shown rather than replaced by an assumption.

GET endpoints never create or reanalyze cases. Repeated explicit syncs retain
the same case identity and append a new `analysis_version` with a new set of
hypotheses.

## Ranking

HIVE begins with engineering priors by incident type, then adds only explicit
evidence weights. Examples include a controlled `waiting_material` downtime
reason, an OEM alarm code, an interrupted cycle, a newly observed CNC program,
repeated defects, overdue verified maintenance, a triggered condition signal,
or a robust utility anomaly. Contradictory evidence applies a visible penalty.

The five highest scores are retained. Confidence is conservative:

- **High:** top score at least 0.70, margin at least 0.15, and one strong direct signal.
- **Medium:** top score at least 0.42 with a margin of at least 0.06.
- **Low:** all other cases.

Scores rank hypotheses inside one case. They are not calibrated probabilities
and should not be compared as failure rates between machines.

## Decisions And Learning

Confirmation requires a real reviewer name and a classified cause other than
`unknown`. Dismissal requires a reason. Every decision uses optimistic version
protection and appends an immutable event with actor, state transition, note,
and decision payload.

Local empirical priors remain disabled until five cases of the same incident
type have named confirmations. Once active, HIVE applies Laplace smoothing over
the full cause catalog and blends 30% local evidence with 70% engineering prior.
This prevents a short run of early incidents from erasing engineering context.
Reopening a case removes its confirmation from the next learned-prior count.

Only confirmed causes can replace a generic downtime category or defect code in
optimization recommendations. Open and dismissed cases cannot influence the
recommendation identity or improvement-learning cause label.

## API

| Method | Path | Behavior |
|---|---|---|
| GET | `/api/root-causes` | Read all cases, summaries, cause catalog, and learning state |
| GET | `/api/root-causes?status=open` | Read one status without database writes |
| POST | `/api/root-causes/sync` | Discover and analyze incidents in `lookback_days` |
| GET | `/api/root-causes/{id}` | Read one case and its decision history |
| POST | `/api/root-causes/{id}/decision` | `confirm`, `dismiss`, or `reopen` with `expected_version` |

Example confirmation:

```json
{
  "action": "confirm",
  "expected_version": 2,
  "actual_cause_code": "tooling_condition",
  "corrective_action": "Replace and measure the drill before the next batch",
  "notes": "Wear confirmed during supervised inspection",
  "actor": "Shift Lead Name"
}
```

## Research Basis

The contract follows condition-monitoring guidance that diagnostics should
identify symptoms, failure modes, confidence, root cause, and corrective action,
while preserving uncertainty and alternatives. Event-log research also supports
enriching raw events with process, equipment, and external context before
classification and keeping explicitly defined hypotheses separate from causal
claims.

- [ISO 13379-1:2025 condition monitoring and diagnostics](https://www.iso.org/standard/88027.html)
- [NIST standards related to prognostics and health management](https://www.nist.gov/publications/standards-related-prognostics-and-health-management-phm-manufacturing)
- [Root Cause Analysis in Process Mining with Probabilistic Temporal Logic](https://link.springer.com/chapter/10.1007/978-3-030-98581-3_6)
- [Root Cause Analysis with Enriched Process Logs](https://publications.rwth-aachen.de/record/714336)
- [Groot: event-graph root cause analysis](https://arxiv.org/abs/2108.00344)
- [NIST review of industrial condition-monitoring technologies](https://www.nist.gov/publications/comprehensive-evaluations-condition-monitoring-based-technologies-industrial)

## Site Commissioning Boundary

Offsite tests use controlled reason codes and simulated event/telemetry records.
At HAEEV, alarm semantics, signal units, controller clocks, OEM maintenance
failure modes, and actual corrective findings must be commissioned from real
equipment evidence. Until then, rankings remain review prompts rather than
validated machine-specific diagnosis.
