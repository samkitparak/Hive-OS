# HIVE OS Schedule Recovery

## Purpose

HIVE closes the gap between an approved plan and current factory state without
silently destabilizing production. It detects a meaningful deviation, simulates
the remaining work, and drafts a replacement sequence only when the measured
benefit clears declared recovery and stability thresholds.

Recovery is advisory and approval-controlled. It never starts, stops, releases,
resumes, or writes commands to a machine. Approval changes the HIVE dispatch
sequence; existing station holds remain held.

## Trigger Logic

Recovery monitoring starts after a schedule is approved. HIVE detects:

- open machine downtime associated with unfinished station work;
- dispatched work placed on hold;
- an order beyond planned completion plus the configured grace period;
- a ready, released, or in-progress order absent from the active schedule;
- an unresolved station execution exception; and
- a decision-ready forecast with at least 50% late probability.

Each trigger has a stable evidence key and the trigger set has a deterministic
signature. Automatic analysis is rate-limited for unchanged evidence. Detection
is read-only and does not replace the active schedule.

## Residual Simulation

The recovery twin starts at the assessment timestamp and simulates only
unfinished work. Completed operations and completed units are removed. For an
operation already in progress, HIVE subtracts a conservative share of elapsed
time from its modeled remaining duration; it does not claim unobserved units as
complete.

The current active order is the baseline. Newly ready unscheduled orders are
appended before candidate policies are generated. HIVE evaluates current, FIFO,
earliest due date (EDD), shortest processing time (SPT), and material batch.

## Freeze Horizon And Stability

The following jobs keep their exact baseline positions:

- jobs in dispatched, acknowledged, running, or held station state;
- production orders already in progress; and
- the first `freeze_horizon_jobs` positions, currently two.

Policies reorder only the other positions. Every result reports moved jobs,
moved-job share, total and maximum position shift, a normalized stability score,
and whether every frozen position was preserved.

The recommender first minimizes late jobs, then stability-penalized tardiness,
then makespan and setup time. A non-current policy is actionable only if it is
feasible, preserves frozen positions, and satisfies at least one material-benefit
rule:

- reduces the number of late jobs;
- recovers at least 900 seconds of total tardiness; or
- when no baseline job is late, recovers at least 600 seconds of makespan.

Unless late-job count improves, no more than 50% of jobs may move. Each position
of movement adds a 120-second stability penalty during candidate ranking. These
defaults live under `rescheduling` in `config/simulation.yaml` and require site
validation before they are changed.

## Approval And Audit

An actionable assessment creates a normal draft planning scenario linked to the
active schedule. The input signature includes orders, routes, models, resources,
events, inventory, maintenance, and execution state. Any relevant change makes
the assessment stale and blocks approval.

A named planner selects a feasible policy and approves or rejects it. Approval
uses the existing planning ledger, activates the selected sequence, and relinks
station execution. The recovery assessment records the actor, decision, policy,
timestamp, and notes. Rejection leaves the prior schedule active. A newer
actionable analysis expires older draft recovery scenarios.

## API

| Method | Path | Behavior |
|---|---|---|
| GET | `/api/recovery` | Current triggers, latest assessment, staleness, and guardrail |
| GET | `/api/recovery/history` | Recent immutable recovery evidence and decisions |
| POST | `/api/recovery/analyze` | Run residual deterministic policy comparison |
| POST | `/api/recovery/{id}/decision` | Approve or reject a linked non-stale scenario |

The planning dashboard exposes the same evidence: trigger details, candidate
lateness, recovered tardiness, moved jobs, stability, frozen-position status,
and named decision controls. Diagnostics and the optimization feed show when a
review is needed. An actionable assessment creates the rationalized
`schedule_recovery_review` alert.

## Commissioning

1. Approve a production-ready schedule with at least four representative jobs.
2. Hold one dispatched job and verify detection changes no schedule or station state.
3. Analyze and reconcile residual quantities with the physical floor.
4. Verify all dispatched and horizon jobs retain their exact positions.
5. Compare HIVE's recommendation with a planner's manual recovery decision.
6. Change one order or resource and confirm the prior evidence becomes stale.
7. Approve one recovery and confirm held work remains held while later dispatch relinks.
8. Repeat across several real deviations before changing benefit or stability thresholds.

## Research Basis

The structure follows NIST's reactive scheduling pattern: monitor shop state,
recognize when the current plan is unacceptable, and generate a schedule from
current loads, resources, buffers, and materials. It also follows dynamic
scheduling research that treats efficiency and schedule stability as simultaneous
objectives rather than resequencing the entire shop after every event.

- [NIST reactive scheduling architecture](https://tsapps.nist.gov/publication/get_pdf.cfm?pub_id=821463)
- [NIST digital thread for manufacturing roadmap](https://nvlpubs.nist.gov/nistpubs/gcr/2024/NIST.GCR.24-057.pdf)
- [Efficiency and stability in rescheduling](https://doi.org/10.1016/j.cie.2003.09.007)
- [Stable reactive scheduling approach](https://doi.org/10.1016/j.cie.2016.06.018)
