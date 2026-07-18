# HIVE OS Commissioning Evidence

## Purpose

HIVE OS 0.24 turns the virtual factory's measurement priorities into a guided,
offline-capable process-characterization program. It supports stopwatch, video,
machine-log, controller-counter, and operator-scan evidence while keeping that
evidence outside production learning.

The workflow follows NIST's production process characterization sequence:
define the goal, model the process, define a sampling plan, collect data,
analyze relationships and variance, then check stability and assumptions. See
[NIST Production Process Characterization](https://www.itl.nist.gov/div898/handbook/ppc/ppc.htm).
The study records method, observer, strata, date, and exclusive time segments
because repeatability, reproducibility, stability, and uncertainty are separate
measurement properties. See [NIST Measurement Process
Characterization](https://www.nist.gov/publications/nistsematech-engineering-statistics-handbook-chapter-2-measurement-process).

## Non-Production Contract

Commissioning evidence is stored only in:

- `commissioning_evidence_studies`
- `commissioning_evidence_observations`
- `commissioning_evidence_analyses`
- `commissioning_evidence_events`

It never creates `machine_events`, `cycle_observations`, `cycle_models`, route
observations, resource truth, forecasts, planning scenarios, execution jobs, or
controller commands. A proposal approved in this workflow means only that a
named person accepts it for manual review of `config/virtual_factory.yaml`.
Every response and proposal remains `production_eligible: false`.

Production models still require validated production event pairs, part links,
holdout validation, route evidence, resources, and their existing readiness
gates. There is no promotion API between these systems.

## Offline Evidence Pack

Use **Commission -> Field evidence -> Evidence pack** or:

```text
GET /api/commissioning-evidence/pack
```

The ZIP contains:

- a ranked `machine-protocols.csv`;
- one blank CSV template and one JSON protocol per machine;
- the exact prior ranges, measurement instruction, and assumption SHA-256;
- `manifest.json` with a SHA-256 and byte count for every declared file;
- `SHA256SUMS` and an offline field guide.

The HTTP response also carries `X-HIVE-Pack-SHA256` and
`X-HIVE-Assumptions-SHA256`. The pack contains no credentials or production
data and can be carried to a disconnected machine area.

## Observation Contract

One row represents one exclusive timed observation. Durations are seconds.

| Field | Meaning |
|---|---|
| `source_record_id` | Stable ID within one study; repeat imports are duplicates |
| `measured_at` | ISO-8601 timestamp with timezone |
| `shift_key` | Named shift or observation window |
| `measurement_method` | stopwatch, video_review, machine_log, controller_counter, or operator_scan |
| `observer` | Person or system responsible for the measurement |
| `product_family`, `program_key` | Sampling strata and recipe/program context |
| `unit_count`, `operator_count` | Batch denominator and labor context |
| `queue_s` | Waiting before the resource is acquired |
| `setup_s` | Recipe, tool, fixture, color, or batch setup |
| `load_s`, `unload_s` | Physical transfer while the resource is occupied |
| `process_s` | Productive machine/process time; required and positive |
| `blocked_s` | Resource occupied because downstream cannot receive output |
| `starved_s` | Resource available but waiting for input |
| `quality_s`, `rework_s` | In-cycle verification and correction time |
| `total_s` | Optional elapsed total; defaults to the segment sum |
| `good_units`, `reject_units` | Optional first-pass quality evidence |

Segments must not overlap. `total_s` cannot be smaller than their sum, but may
be larger when unclassified elapsed time remains. Unknown CSV columns are
rejected so spelling errors cannot silently lose evidence. Imports are previewed
before an atomic apply. Reusing a source ID with identical data is a duplicate;
reusing it with different data is a conflict.

Rows are immutable except for explicit exclusion. Exclusion requires a reason,
retains the original values, reopens the study, increments its version, and
adds an audit event. Modified-Z outlier detection flags unusual occupancy at
`|z| > 3.5` but never removes it automatically.

## Analysis

The cycle prior is compared with occupied resource time per unit:

```text
(setup + load + process + blocked + unload + quality + rework) / unit_count
```

Queue and starvation remain separate flow losses and do not inflate resource
occupancy. HIVE reports min, P10, median, mean, P90, max, and median absolute
deviation for occupancy and total flow time, plus product-family groups,
segment medians, quality, outliers, and prior-range coverage.

Median uncertainty uses 500 deterministic bootstrap resamples and a 90 percent
interval. NIST describes bootstrap resampling for statistics such as the median
and notes that 500 resamples is usually sufficient for this purpose: [NIST
Bootstrap Plot](https://www.itl.nist.gov/div898/handbook/eda/section3/bootplot.htm).
The interval is reproducible from the immutable input signature.

## Credibility Gates

A proposal is review-ready only when all six checks pass:

1. Accepted observations meet the study target, default 20.
2. Product/program strata meet the target, default two.
3. Evidence spans at least two calendar dates.
4. Evidence has two observers or at least 80 percent automated capture.
5. The 90 percent bootstrap interval width is at most 30 percent of the median.
6. First-half versus second-half median drift is at most 20 percent.

The proposal uses a conservative triangle:

```text
min  = minimum(observed P10, bootstrap lower median)
mode = observed median
max  = maximum(observed P90, bootstrap upper median)
```

It does not propose availability. Cycle observations do not measure planned
time, failures, repairs, breaks, or uptime. Availability needs a separate
observation window. Reporting the method and uncertainty with the result follows
NIST's guidance that a measured quantity must remain tied to its method and
uncertainty: [NIST TN 1297 Appendix
D4](https://www.nist.gov/pml/nist-technical-note-1297/nist-tn-1297-appendix-d4-measurand-defined-measurement-method).

The KPI names and contexts are kept explicit rather than collapsed into one
score, consistent with the current [ISO 22400-1 manufacturing KPI framework](https://www.iso.org/standard/56847.html),
which was reviewed and confirmed in 2025.

## Study Lifecycle

```text
draft -> collecting -> review_ready -> proposal_approved | proposal_rejected
                    \-> collecting when evidence is added or excluded
any non-archived state -> archived
```

All transitions use optimistic `expected_version` checks and named actor audit.
Submitting review persists an immutable analysis only after the six gates pass
against the current assumption fingerprint. Approval or rejection requires the
same current evidence and never changes a prior file automatically.

## API

- `GET /api/commissioning-evidence`
- `GET /api/commissioning-evidence/pack`
- `POST /api/commissioning-evidence/studies`
- `GET /api/commissioning-evidence/studies/{id}`
- `POST /api/commissioning-evidence/studies/{id}/observations`
- `POST /api/commissioning-evidence/studies/{id}/import`
- `POST /api/commissioning-evidence/studies/{id}/analyze`
- `POST /api/commissioning-evidence/studies/{id}/action`
- `POST /api/commissioning-evidence/studies/{id}/observations/{observation_id}/exclude`

Read access requires `view`. Mutations require `commission` or `optimize`.
