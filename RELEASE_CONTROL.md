# Adaptive Order Release Control

HIVE OS 0.33 adds an evidence-gated pre-shop pool between an approved schedule
and the factory floor. It answers a recurring operating question: **which ready
order should enter production now, and which should remain outside the system?**

The release worker is advisory. It may create `release`, `expedite`, or `hold`
recommendations, but only a named user with planning, optimization, or
supervision permission can approve one. It never writes to a PLC or machine
controller.

## Why release control exists

Releasing every scheduled order immediately turns planning demand into shop
floor queues. That can increase waiting, obscure the active constraint, consume
floor space, and make priorities unstable. Workload Control instead retains
orders in a pre-shop pool and admits work when its routed load fits established
station norms.

This is especially relevant to HAEEV's high-variety, make-to-order flow: two
orders with the same part count can impose very different cutting, CNC, edge,
pressing, sanding, and finishing workloads.

## Decision boundary

The existing production states remain authoritative:

- `draft`: work definition is incomplete.
- `ready`: eligible pre-shop work, but not authorized on the floor.
- `released`: approved to enter production.
- `in_progress`: physical execution evidence exists.
- `hold`: intentionally stopped; once-released work still counts as floor load.
- `completed` or `cancelled`: no remaining release load.

An approved planning scenario establishes sequence intent and creates queued
station jobs. Release control does not replace the schedule. It controls entry
to that schedule's execution layer.

## Corrected workload

For order `j`, operation `o`, station `m`, and route position `r`:

```text
operation load = remaining quantity * estimated seconds per unit
corrected station contribution = operation load / r
station released load = sum(corrected contributions not yet completed)
projected station ratio = (released load + candidate contribution) / workload norm
```

Dividing an operation by route position is the corrected-load convention used
by LUMS COR: a later operation is less likely to arrive immediately than the
same amount of direct first-operation load. A released order contributes until
that station operation is completed.

Processing time comes from, in order:

1. an active learned HIVE cycle model;
2. a manually calibrated cycle model;
3. a verified station fallback in the release norm.

Missing processing time remains missing. HIVE does not substitute an OEM feed
speed or an unverified average into a production release decision.

## Planned release date

```text
planned release date = contractual due time
                     - total estimated processing time
                     - route length * queue allowance
```

The queue allowance is a site policy, not a universal constant. The offsite
default is an explicit engineering assumption and cannot authorize release
until a named operator verifies it.

## Review algorithm

Each periodic review performs the following steps:

1. Load the current approved schedule and its `ready` pre-shop orders.
2. Reconstruct incomplete corrected load from every once-released order.
3. Calculate each candidate's planned release date and station contributions.
4. Consider overdue planned-release dates first.
5. When several orders are urgent, prefer the candidate with the lower maximum
   projected station ratio before falling back to time and schedule position.
6. Select an order only when every routed station remains within its norm.
7. During high shop load, hold orders beyond the configured work-ahead window.
8. Limit approvals per review; after one approval, remaining recommendations
   become stale and a new review is required.
9. Optionally identify a supervised starvation override when a candidate's
   first station has zero direct load. This requires explicit override
   confirmation and never happens automatically.

The adaptive work-ahead behavior is intentionally asymmetric: under low load,
HIVE may pull future work forward to smooth demand; under high load, it limits
the candidate pool to avoid early inventory and improve load balancing.

## Evidence gates

A `release` or `expedite` recommendation is decision-ready only when all of the
following are true:

- one production schedule is currently approved;
- the order is still `ready` and its version has not changed;
- contractual due time exists;
- every required route operation has processing-time evidence;
- every routed station has a verified workload norm;
- the release policy is site-verified;
- material, components, machine profiles, labor, tooling, calendar, WIP,
  availability, and changeover checks remain production-ready;
- current released load is measurable for every affected station;
- the review's full factory signature is still current.

Before commissioning, HIVE still displays numerical previews. They are marked
`commissioning_only_preview`, have `evidence_ready=false`, and the API rejects
approval.

## Immutable review history

`release_control_reviews` stores each five-minute decision context with its
factory input signature and method version. Recommendations preserve the order
version, planned release date, corrected station loads, evidence gaps, and
reason code. `release_control_actions` records the named approval or dismissal.

Any change to schedule, order, routes, cycle models, execution, resources,
policy, norms, flow samples, constraint evidence, or downtime changes the input
signature. An old open recommendation then becomes stale rather than silently
changing meaning.

## Configuration

The dashboard's **Adaptive release** band contains:

- worker and policy state;
- pre-shop and on-floor order counts;
- current corrected workload;
- ranked release, expedite, and hold recommendations;
- projected load by station;
- policy controls and station norms;
- named approve and dismiss actions.

Offsite defaults are intentionally unverified:

| Setting | Initial assumption | Site decision |
|---|---:|---|
| Review interval | 5 minutes | Match supervisor operating cadence |
| Overload threshold | 85% of any station norm | Tune after stable shifts |
| Work-ahead window | 72 hours | Match floor and finished-goods space |
| Queue allowance | 4 hours per operation | Calibrate from queue-time evidence |
| Expedite threshold | 8 hours past planned release | Match delivery escalation policy |
| Releases per review | 1 | Increase only after controlled trials |
| Station workload norm | 240 corrected minutes | Tune individually by station |

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/release-control` | Policy, norms, runtime, current review, and history |
| `POST` | `/release-control/sync` | Create an idempotent review for the current interval |
| `PUT` | `/release-control/settings` | Versioned release-policy update |
| `PUT` | `/release-control/norms/{machine_key}` | Versioned station-norm update |
| `POST` | `/release-control/recommendations/{id}/action` | Named approve or dismiss decision |

## On-site commissioning

1. Approve a representative production schedule without releasing every order.
2. Verify routes, due times, resources, and cycle models.
3. Observe queue and flow behavior while release control remains in preview.
4. Set each station norm conservatively and record the physical rationale.
5. Verify the policy and norms with the production supervisor.
6. Approve one recommendation at a time for at least several shifts.
7. Compare tardiness, shop-floor flow time, queue P90, throughput, and WIP before
   and after the controlled-release trial.
8. Change norms through an HIVE improvement experiment, not by undocumented
   tuning.

## Research basis

- Haeussler, Neuner, and Thürer describe LUMS COR's periodic selection,
  corrected load, continuous starvation trigger, planned-release-date formula,
  and adaptive time-limit results in an open-access simulation study:
  [Balancing earliness and tardiness within workload control order release](https://link.springer.com/article/10.1007/s10696-021-09440-9).
- Thürer et al. separate release into sequencing and selection decisions and
  report that load balancing should be used among urgent orders rather than as
  the sole sequencing rule:
  [Concerning Workload Control and Order Release](https://doi.org/10.1111/poms.12304).
- Thürer, Stevenson, and Silva review three decades of theory, simulation, and
  implementation evidence while emphasizing the need for real-world validation:
  [Three decades of workload control research](https://eprints.lancs.ac.uk/id/eprint/45658/).
- Spearman, Woodruff, and Hopp introduced CONWIP as a pull alternative that
  regulates work in process:
  [CONWIP: A Pull Alternative to Kanban](https://doi.org/10.1080/00207549008942761).
- Thürer, Stevenson, and Land show that input control through order release and
  output control through capacity adjustment play complementary roles:
  [On the integration of input and output control](https://doi.org/10.1016/j.ijpe.2016.01.005).

These studies guide HIVE's structure, not its final parameter values. HAEEV's
norms and allowances must be learned through verified factory evidence and
controlled experiments.
