# HIVE OS Changeover Intelligence

## Purpose

HIVE models setup as a directional transition between process-specific families,
not as one factory-wide delay. Changing a beam saw from material A to B can take
a different amount of time than B to A, and a CNC program-class change is not the
same operation as an edge-band material change.

The feature is advisory. It changes simulation, forecast, planning, and recovery
estimates inside HIVE; it never writes a recipe or command to a machine controller.

## Setup Families

Families use only fields HIVE currently owns and can explain:

| Process | Family basis |
|---|---|
| Gabbiani and Nova saws | Board material code |
| Morbidelli CNCs | Front/back face count and groove-program class |
| Stefani edge bander | Set of edge-band materials |
| Sergiani press and Osama glue spreader | Material and thickness recipe proxy |
| DMC sanders | Panel thickness |

Superfici is deliberately excluded until a real finish recipe, color, or coating
field is commissioned. HIVE does not infer those properties from unrelated part
fields.

## Estimate Priority

For each `machine + from family + to family` transition, HIVE selects:

1. zero seconds when the setup family does not change;
2. the P90 of an active directional evidence model;
3. a named, verified machine fallback;
4. a visible broad engineering assumption for commissioning comparisons only.

The nine setup-capable machines receive broad offsite defaults. These defaults
are not production truth. A selected planning scope with multiple families on a
machine is approvable only when that machine has a verified fallback or every
required directional transition has an active learned model.

## Evidence And Promotion

Observations require a timezone-aware timestamp, duration, source, machine,
source family, target family, and named actor. First-good-piece confirmation is
recorded separately so timing and quality evidence are not conflated.

- Medium confidence: at least 5 observations across 2 dates, with
  `MAD / median <= 0.5`.
- High confidence: at least 15 observations across 3 dates, at least 80% with
  first-good-piece confirmation, and `MAD / median <= 0.3`.
- Low-confidence candidates remain inactive.
- Excluding an invalid observation immediately retrains the transition and can
  retract an active model.

The model uses P90 rather than the mean to avoid optimistic schedule promises.
Evidence fingerprints make retries idempotent. Automatic extraction accepts only
closed downtime explicitly classified as setup/changeover with identifiable
before and after parts; ordinary idle gaps are never relabeled as setup.

## Planning Behavior

The digital twin maintains the last setup family independently for every
setup-capable machine. It reports setup count, setup seconds, machine-level setup
totals, and whether each duration came from learned evidence, a verified standard,
or an assumption.

The `setup_aware` policy greedily minimizes modeled machine transitions inside
due-date priority. Recovery evaluates the same policy while retaining dispatched,
in-progress, held, and freeze-horizon jobs at their exact baseline positions.

## Site Workflow

1. Open **Planning > Resources > Setups**.
2. Review the family basis for each machine with the production and machine leads.
3. Time representative directional changes through the first good piece.
4. Enter observations with local timestamp, source, actor, and quality confirmation.
5. Verify a conservative fallback for every setup-sensitive machine before early use.
6. Use **Sync evidence** only after setup downtime classification is operational.
7. Compare FIFO/current and setup-aware scenarios; confirm reported transitions on the floor.
8. Exclude mistimed or misclassified evidence with a reason and repeat the comparison.

## API

| Method | Path | Behavior |
|---|---|---|
| GET | `/api/changeovers` | Standards, scoped families, active models, evidence, and readiness |
| PUT | `/api/changeovers/machines/{key}/standard` | Version and verify a conservative machine fallback |
| POST | `/api/changeovers/observations` | Record repeat-safe directional timing evidence |
| POST | `/api/changeovers/observations/{id}/exclude` | Exclude invalid evidence and retrain |
| POST | `/api/changeovers/sync` | Import explicit setup downtime and refresh models |

## Research Basis

Sequence-dependent setup time is an established flexible job-shop constraint,
and setup-oriented dispatch rules can reduce tardiness and flow time in dynamic
shops. HIVE applies that finding conservatively by requiring local evidence before
its estimates can support approval.

- [Flexible job shop scheduling with sequence-dependent setup time and tardiness](https://www.tandfonline.com/doi/abs/10.1080/00207543.2012.746480)
- [Setup-oriented dispatching rules for dynamic job shops](https://www.sciencedirect.com/science/article/pii/S0736584507000567)
- [Tabu search for flexible job shops with sequence-dependent setup times](https://www.sciencedirect.com/science/article/pii/S037722171730752X)
- [Open study of setup-aware dynamic flexible flow lines](https://link.springer.com/article/10.1007/s40092-017-0185-y)
