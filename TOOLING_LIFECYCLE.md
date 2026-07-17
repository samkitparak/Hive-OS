# Tooling Lifecycle

HIVE tracks physical blades, cutters, drills, belts, and other serviceable
tooling as individual assets. The generic tool-pool quantity remains available
during commissioning. As soon as a pool has registered assets, its verified
usable asset count becomes the authoritative planning capacity.

## Evidence model

Each tool has a stable HIVE key, pool, type, optional OEM identity, life basis,
rated limit, warning threshold, assignment, service count, and verification
state. Supported life bases are parts, cycles, and runtime minutes.

Usage is append-only and requires an idempotency key. HIVE accepts manual or
scanner evidence and can import `cycle_end` events when a named tool has a
verified exact mapping to that machine and CNC file. It normalizes path styles
and case but does not infer usage for unmapped programs.

Failed or rework quality checks are linked to tooling only when exactly one
verified assigned tool is active on the inspected machine. Three attributed
failures in one tool life make that tool service due. Ambiguous multi-tool
conditions remain unattributed.

## Life decisions

The configured rated life is an explicit OEM or site value. HIVE calculates
remaining life from current counters and excludes tools that are service due,
expired, broken, in service, or retired from planning capacity.

Completed service records preserve the prior counters and end reason. After at
least five `worn` or `quality` outcomes exist for the same tool type and life
basis, HIVE exposes the empirical 20th percentile as a conservative local life
estimate. The effective display limit is the lower of that estimate and the
rated limit. The estimate is advisory and never changes master data.

This structure follows the separation of identity, lifecycle status,
reconditioning count, and parts/minutes life in the
[MTConnect Cutting Tools specification](https://docs.mtconnect.org/MTC_Part4_1_CuttingTools_1_4_0.pdf).
Its evidence-first prediction boundary follows NIST work on
[operation-level tool condition prediction](https://www.nist.gov/publications/data-processing-pipeline-prediction-milling-machine-tool-condition-raw-sensor-data)
and [condition-monitoring policy evaluation](https://www.nist.gov/services-resources/software/simprocesd).
The program/tool mapping supports the same operational goal as SCM's
[Maestro tool-management software](https://www.scmgroup.com/hu/scmwood/products/maestro-digital-systems/software.c102273/cnc-machining-centers-for-panel-and-solid-wood.102275):
matching available tooling to the programs that require it.

## Service workflow

1. Register and physically label the tool with its `HIVE:T:<tool_key>` value.
2. Enter the OEM/site life basis and limit, then verify the record.
3. Install or allocate it to a machine and pocket.
4. Record usage directly or approve exact CNC program mappings.
5. Run tooling sync; thresholded tools create one open maintenance work order.
6. Inspect, recondition, replace, or retire the tool with a named outcome.
7. Reconditioning resets current counters, increments its recondition count,
   and closes the linked tool-lifecycle work order.

## On-site commissioning

- Import the real tool crib list and attach printed HIVE labels.
- Confirm OEM part numbers, serials, rated limits, and reconditioning limits.
- Map only programs observed on the actual machine; leave uncertain mappings
  unverified.
- Compare the first imported usage totals with Maestro and physical counters.
- Record measured wear and quality outcomes during each service event.
- Review local life estimates only after the five-outcome evidence gate opens.
