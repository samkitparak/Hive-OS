# Production Economics and Value Assurance

HIVE OS 0.34 converts operational evidence into conservative financial claims.
It is not a replacement for the accounting system, and it does not infer profit
from generic machine utilization. It preserves enough provenance for finance and
operations to reproduce every current value from source evidence and approved
rates.

## The three ledgers

HIVE reports three values separately. They must not be added into a single
"total savings" number.

1. **Direct cost exposure** is measured waste with a cash-like basis, such as
   idle electricity under an approved tariff or an attributed failed unit under
   an approved internal-failure rate.
2. **Constraint capacity opportunity** is contribution that might be recovered
   by protecting the confirmed system constraint. It is not booked savings and
   is never calculated for arbitrary non-constraint machine time.
3. **Measured improvement benefit** is benefit observed during a controlled
   HIVE improvement experiment. It becomes sustained only after later windows
   pass the original target and guardrails and receive named operating-condition
   adjustment reviews.

This separation prevents three common errors:

- treating all downtime as lost sales;
- adding overlapping machine-minute losses several times;
- annualizing a short before/after result before it persists.

## Method

`src/economics.py` uses method version `production-economics-mv-v1`.

### Idle energy exposure

For each approved industrial profile:

```text
idle energy exposure = integrated idle-band kWh * approved tariff per kWh
```

The value is decision-ready only when:

- the economics policy is site-verified;
- the industrial profile and immutable signal contract are approved;
- the tariff is present in that approved profile;
- at least 80% of the observed power span has contiguous good evidence.

Intervals longer than three polls, with a minimum gap threshold of 60 seconds,
remain excluded by `energy_intelligence.py`. Cumulative meter delta remains the
preferred total-energy source, while idle energy is integrated from the power
signal and approved idle threshold.

### Internal failure and rework exposure

```text
internal failure exposure = attributed failed units * approved failure cost/unit
rework exposure = attributed rework units * approved rework cost/unit
```

Every counted disposition must resolve to a physical part and a named inspector.
Anonymous or unlinked checks remain previews. This is the internal-failure part
of the prevention-appraisal-failure quality-cost model; external warranty,
return, complaint, prevention, and appraisal costs are not invented from the
current factory records.

### Constraint capacity opportunity

```text
constraint opportunity = recoverable constraint minutes * value per constraint minute
```

The minute value can be commissioned directly. If it is absent, HIVE may derive
it from a verified throughput contribution rate and observed constraint
throughput:

```text
derived minute value = contribution per good unit * good units/hour / 60
```

The opportunity is decision-ready only when:

- a repeated medium/high-confidence constraint episode is open;
- released demand exists at that machine;
- its loss waterfall is decision-ready and exactly reconciled;
- the economics policy and selected rate are verified in the reporting currency.

Only classified breakdown, setup, material-starvation, tooling, staffing,
quality-stop, minor-stop, speed, and quality-equivalent losses are eligible.
Unknown telemetry, no-demand time, planned stops, and unclassified time are
excluded.

### Measured experiment benefit

HIVE reads the frozen baseline and evaluation produced by
`improvement.py`. A claim requires a `validated` outcome and a positive lower
bound from its deterministic 90% bootstrap interval.

Operational quantities are calculated as follows:

```text
throughput units = max(0, evaluation units/hour - baseline units/hour)
                   * evaluation hours

avoided downtime minutes = max(0, baseline min/hour - evaluation min/hour)
                           * evaluation hours

avoided failures = max(0, baseline defect rate - evaluation defect rate)
                   * evaluation disposition count

freed constraint minutes = max(0, baseline cycle seconds - evaluation cycle seconds)
                           * evaluation cycle count / 60
```

Throughput units use the approved contribution per good unit. Avoided downtime
and cycle time use the constraint-minute value and require an overlapping
confirmed historical constraint episode. Avoided failures use the approved
internal-failure rate and complete physical-part identity.

The resulting amount covers only the measured evaluation window. HIVE does not
multiply it by weeks or years.

### Persistence and adjustments

After the initial evaluation, HIVE creates complete non-overlapping persistence
windows. The default is 30 days, with two passing windows required.

Each window rechecks:

- minimum sample count;
- original target delta;
- the experiment's quality, throughput, or downtime guardrails;
- a named routine/non-routine adjustment review.

The savings convention is:

```text
adjusted window benefit = raw baseline-vs-performance benefit
                          + routine/non-routine adjustment
```

An adjustment of zero must still be explicitly recorded and verified. This
means a reviewer confirmed that product mix, scheduled hours, demand, asset
condition, and other material operating conditions did not require a change.
The claim becomes `sustained` only after the configured number of verified
windows passes and the latest reviewed window still passes.

## Rates

The current catalog is deliberately small:

| Key | Unit | Meaning |
|---|---|---|
| `throughput_contribution_per_unit` | currency/good unit | Selling price less truly variable cost for one additional good unit |
| `constraint_minute_value` | currency/constraint minute | Contribution protected by one productive minute at a confirmed constraint |
| `internal_failure_cost_per_unit` | currency/failed unit | Material and processing cost of one internal rejection |
| `rework_cost_per_unit` | currency/reworked unit | Incremental labor, material, and utility cost of one reworked unit |

Rates can be factory-wide or machine-specific. Machine rates override factory
rates. Every edit creates a new version, deactivates the prior version, and
records the named actor. A draft rate can render a preview but cannot enter a
decision-ready or measured total.

Do not put allocated rent, depreciation, or fixed salaries into throughput
contribution. Do not use sales price as contribution. Finance should document
the accounting source outside HIVE and place only the approved resulting rate
in HIVE.

## Statuses

| Status | Meaning |
|---|---|
| `waiting_for_evidence` | No current cost exposure or completed experiment exists |
| `commissioning_required` | A numerical preview exists but policy, rate, identity, constraint, or attribution is incomplete |
| `measuring` | At least one direct exposure or capacity opportunity is decision-ready |
| `verified_value` | At least one experiment benefit is measured or sustained |

Claim statuses are more specific:

- `preview_only`: amount may be visible, but at least one gate is open;
- `decision_ready`: direct exposure or constraint opportunity passed its gates;
- `measured`: a validated experiment passed initial financial gates;
- `sustained`: enough later reviewed windows passed;
- `no_measured_value`: the completed experiment was ineffective or inconclusive.

## Storage

- `economics_settings`: singleton measurement policy and worker health;
- `economics_rates`: versioned factory or machine finance rates;
- `economics_rate_events`: immutable rate commissioning history;
- `economics_reviews`: idempotent review history and input signature;
- `economics_claims`: one provenance-rich claim inside each review;
- `economics_adjustments`: versioned named persistence-window adjustments.

GET requests do not create reviews. The background worker or explicit sync
creates an immutable review at most once for the configured time bucket and
input signature.

## API

```text
GET  /api/economics
POST /api/economics/sync
PUT  /api/economics/settings
PUT  /api/economics/rates/{rate_key}
PUT  /api/economics/experiments/{experiment_id}/adjustments
```

Writes require `optimize`, `supervise`, or administrator authority. Authenticated
requests bind actor fields to the logged-in identity. Optimistic versions reject
stale policy, rate, and adjustment writes.

## On-site commissioning

1. Finance selects the reporting currency and verifies the policy.
2. Finance and operations calculate contribution from actual selling price and
   truly variable cost, not generic accounting allocation.
3. Quality reviews recent attributed scrap and rework to establish conservative
   failure rates.
4. Electrical profiles receive the current tariff before their contracts are
   approved.
5. HIVE collects enough production evidence to confirm recurring constraints
   and decision-ready loss waterfalls.
6. Improvement owners use frozen baselines and record confounders before
   implementation.
7. Finance reviews each due persistence window, including explicit zero
   adjustments.
8. Only `measured` and `sustained` claims are used in benefits reporting;
   opportunity remains operational prioritization.

## Research basis

- The U.S. Department of Energy's [FEMP Measurement and Verification Guidelines 5.0](https://www.energy.gov/cmei/femp/articles/mv-guidelines-measurement-and-verification-performance-based-contracts-version-0)
  defines baseline-versus-performance savings, routine and non-routine
  adjustments, and verification effort proportional to value and risk.
- [ISO 50015:2014](https://www.iso.org/standard/60043.html) establishes general
  principles for measurement and verification of organizational energy
  performance.
- The NIST [Manufacturing Cost Guide](https://doi.org/10.6028/NIST.AMS.200-7)
  provides standardized manufacturing-cost categories and reproducible cost
  estimation guidance.
- Omachonu, Suthummanon, and Einspruch's
  [manufacturing quality-cost study](https://doi.org/10.1108/02656710410522720)
  uses prevention, appraisal, internal-failure, and external-failure categories.
- Gardiner and Blackstone compare standard costing with capacity-sensitive
  Theory of Constraints decisions, including contribution per constraint
  minute: [International Journal of Purchasing and Materials Management](https://doi.org/10.1111/j.1745-493X.1991.tb00539.x).
- Atwater and Chakravorty's drum-buffer-rope study explains why the system's
  primary constraint, rather than every local resource, governs capacity
  decisions: [Production and Operations Management](https://doi.org/10.1111/j.1937-5956.2002.tb00495.x).

These sources inform the method. They do not verify HAEEV's rates, product mix,
tariffs, or operating conditions; those remain site evidence.
