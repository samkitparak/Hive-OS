# HIVE OS Virtual Factory Commissioning

## Purpose

The virtual commissioning lab answers three offsite questions before HAEEV
telemetry exists:

1. Which process is most likely to constrain the reference product mix?
2. Which uncertain machine measurement can change that answer the most?
3. Which proposed improvement is worth a controlled on-site trial?

It does not predict the live factory. Every result is `assumption_only`, carries
the SHA-256 fingerprint of `config/virtual_factory.yaml`, and is permanently
`production_eligible: false`.

## Isolation Contract

A lab run may append one JSON result to `virtual_factory_runs`. It cannot write
machine events, cycle observations/models, route observations, resource values,
production forecasts, planning scenarios, execution jobs, or controller output.
Changing the prior file makes the latest result stale. There is no API that
promotes a lab prior into a production model.

This separation follows NIST's emphasis that manufacturing digital-twin
credibility depends on context, verification, validation, and uncertainty
quantification rather than model detail alone: [Digital Twins for Advanced
Manufacturing](https://www.nist.gov/programs-projects/digital-twins-advanced-manufacturing)
and [Credibility Consideration for Digital Twins in
Manufacturing](https://www.nist.gov/publications/credibility-consideration-digital-twins-manufacturing).

## Reference Model

The shipped workload contains 180 units over a nine-hour reference shift and
five explicit product families: standard carcass, routed, painted, pressed, and
manual/rework. All units enter at time zero. This is a backlog stress test, not
an order-arrival forecast.

Each route step requests a finite-capacity SimPy resource. For simulation run
`r`, effective step occupancy is:

```text
sampled triangular cycle time
× family duration scale
× intervention scale
÷ sampled effective availability
```

Availability is sampled once per machine per run and represents aggregated
uptime loss. It is not an explicit failure/repair process. Cycle duration is
sampled per operation. Seeded runs are reproducible.

The broad envelopes use official vendor capabilities only as anchors; feed or
axis speed is not treated as a real cycle time. Sources include SCM's official
[Gabbiani PT](https://www.scmgroup.com/en/scmwood/products/beam-saws.c907/automatic-single-blade-beam-saws.912/gabbiani-pt.699),
[Morbidelli CX100](https://www.scmgroup.com/en/scmwood/products/boring-machines.c880/boring-solutions.883/morbidelli-cx100.547),
[Morbidelli N100](https://www.scmgroup.com/products/docs/morbidelli%20n100_apr18_Ing.pdf),
[Stefani KD](https://www.scmgroup.com/en/superfici/products/edge-banders-squaring-edge-banders.c863/single-sided-automatic-edge-banders.865/stefani-kd.42159),
[DMC SD 60](https://www.scmgroup.com/en/scmwood/products/wide-belt-sanders.c869/automatic-sanding-and-calibrating-machines.870/dmc-sd-60.702),
and [DMC SD 90](https://www.scmgroup.com/en_CA/scmwood/products/wide-belt-sanders.c869/automatic-wide-belt-sanders.870/dmc-sd-90.816)
technical pages. Unknown handling, setup, program complexity, passes, recipes,
batching, staffing, and rework are intentionally represented by wide ranges.

## Outputs

- **Baseline bands:** P10/P50/P90 throughput, shift completion, and makespan.
- **Constraint probability:** fraction of runs where a machine has the highest
  finite-capacity utilization. This is conditional on the reference backlog.
- **Measurement priority:** throughput impact from a common-seed -20%/+20%
  machine-cycle perturbation multiplied by that prior's relative width.
- **Intervention screening:** conditional change in P50 throughput and makespan
  after applying one named duration reduction. It is not expected realized gain.

NIST's uncertainty guidance motivates propagating input uncertainty and using
sensitivity coefficients to choose measurements: [uncertainty
propagation](https://www.itl.nist.gov/div898/handbook/mpc/section5/mpc55.htm),
[sensitivity coefficients](https://www.itl.nist.gov/div898/handbook/mpc/section5/mpc56.htm),
and [manufacturing performance under uncertainty](https://www.nist.gov/publications/performance-evaluation-manufacturing-process-under-uncertainty-using-bayesian-networks).

## On-Site Workflow

1. Open **Commission -> Virtual lab**, run the shipped priors, and retain its
   fingerprint as the offsite baseline.
2. Follow the displayed measurement list in priority order. The exact signal
   set for every machine is stored beside its prior in
   `config/virtual_factory.yaml`.
3. Capture at least 20 representative observations per important
   product/program family. Separate queue, setup, load, operation, blocked,
   starved, unload, rework, and first-good-piece time.
4. Confirm product mix, routes, repeat passes, parallel positions, staffing,
   buffers, planned breaks, and material release behavior.
5. Narrow the prior file only from named evidence, retain `assumption_only`, and
   rerun. The old result remains immutable and the changed fingerprint is clear.
6. Use screened interventions to choose controlled trials. Validate trial
   outcomes through HIVE's improvement-learning workflow.
7. Commission production cycle models, observed routes, resources, forecasts,
   and schedules through their existing evidence gates. The lab never replaces
   those steps.

## API

- `GET /api/commissioning-lab`: current assumptions, latest result, history,
  and staleness.
- `GET /api/commissioning-lab/history`: immutable run metadata and results.
- `POST /api/commissioning-lab/run`: `samples` from 10 to 100 and a reproducible
  nonnegative integer `seed`; requires commission or optimize permission.
