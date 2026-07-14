# HIVE OS Warehouse Intelligence

HIVE treats warehouse truth as a production constraint, not a spreadsheet beside
the schedule. Sheet lots, edge rolls, hardware, consumables, and measured panel
remnants are versioned, located, reserved, issued, released, and audited.

## Evidence Boundary

HIVE can derive sheet material, panel dimensions, quantity, gross part area, and
edge-band metres from the current Cabinet Vision cut-list. It cannot currently
derive hinges, runners, handles, screws, glue, packaging, or other hardware
because those fields are not present in the commissioned export. Those
requirements stay manual until a real Cabinet Vision report, SQL view, or ERP
BOM is approved. HIVE does not seed guessed hardware quantities.

## Material Model

The warehouse uses the ISA-95 separation between a material definition and the
physical lots that carry quantity, status, and storage location:

| HIVE object | Meaning |
|---|---|
| `material_definition` | Board identity, standard sheet size, and commissioned nesting yield |
| `material_lot` | Located sheet balance and committed sheet reservations |
| `inventory_item` | Edge band, hardware, consumable, or packaging definition |
| `inventory_lot` | Located component balance with count verification and optimistic version |
| `material_remnant` | One measured rectangular offcut with material, dimensions, grain, location, and status |
| `inventory_movement` | Immutable receipt, adjustment, reservation, release, issue, create, or scrap evidence |

Schedule approval allocates verified stock FIFO by lot. Completion issues the
allocation; cancellation or schedule replacement releases it. Every balance or
reservation change writes the movement ledger.

## Derived Edge Demand

For a part with quantity `q`:

```text
EB1 metres = length_mm / 1000 * q
EB2 metres = length_mm / 1000 * q
EB3 metres = width_mm  / 1000 * q
EB4 metres = width_mm  / 1000 * q
required metres = summed metres * commissioned usage factor
```

The provisional usage factor is `1.05` for trim and handling allowance. It is an
explicit commissioning value, not learned truth. Values that resemble shifted
CNC filenames are quarantined as source issues instead of becoming edge demand.

## Usable Remnants

The offsite policy is intentionally conservative:

1. A remnant must be physically measured and operator-verified.
2. The provisional usable threshold is both dimensions at least 150 mm and area
   at least 0.05 m2. Smaller pieces enter `hold`, not available stock.
3. A grain-sensitive part can use only the recorded length orientation. A
   grain-free part may rotate 90 degrees.
4. One remnant can credit one physical part instance only.
5. Parts are considered largest first; HIVE chooses the smallest fitting
   remnant to preserve larger offcuts.
6. Only the matched part area reduces estimated new-sheet demand. Unused area
   inside that remnant is not counted as additional yield.

This is a safe allocation heuristic, not a replacement for the beam-saw nesting
engine. Multi-part remnant nesting should be enabled only after a real optimizer
export exposes cutting patterns and remnant geometry.

## Shortage and Reorder Logic

```text
free stock = available on hand - committed reservations
scope shortage = max(0, required scope - free stock - reservations held for scope)
target stock = max(reorder point, open demand + safety stock)
suggested order = round_up(max(0, target stock - free stock), order multiple)
```

Cost is shown only when unit cost is commissioned. Supplier and lead time remain
blank rather than inferred. Suggestions are advisory and never create or send a
purchase order.

## On-Site Commissioning

1. Open **Planning > Resources > Sheets** and count every board material by lot
   and storage location. Confirm actual sheet dimensions and nesting yield.
2. Open **Components**. Resolve source issues, count each edge roll, and enter
   supplier, lead time, reorder point, safety stock, and order multiple.
3. Add hardware and consumables from an approved BOM source. Until that source
   exists, enter requirements against a production order manually.
4. Open **Remnants**. Label, measure, locate, and verify each usable rectangle.
   Measure `length` along grain for grain-sensitive boards.
5. Generate a planning scenario. Confirm gross sheets, remnant credit, net
   sheets, component allocations, and purchase suggestions.
6. Approve one test schedule, then cancel it and verify every reservation is
   released. Approve again, complete the test order, and verify issues and
   remnant consumption in the movement ledger.

## API

| Method | Route | Purpose |
|---|---|---|
| GET | `/inventory/snapshot` | Component demand, lots, remnants, shortages, suggestions, and source issues |
| GET | `/inventory/movements` | Immutable warehouse movement evidence |
| PUT | `/inventory/items/{key}` | Commission item policy and supplier values |
| PUT | `/inventory/items/{key}/lots/{lot}` | Record a receipt or verified physical balance |
| PUT | `/inventory/orders/{id}/requirements/{key}` | Enter a manual BOM requirement |
| POST | `/inventory/remnants` | Record a measured remnant |
| PATCH | `/inventory/remnants/{key}` | Release, hold, verify, or scrap an unreserved remnant |

Primary references:

- [OPC UA ISA-95 material information model](https://reference.opcfoundation.org/ISA-95/v100/docs/8.4)
- [GS1 EPCIS 2.0.1](https://ref.gs1.org/standards/epcis/2.0.1/)
- [GS1 EPCIS TransformationEvent](https://ref.gs1.org/epcis/TransformationEvent)
- [Cutting stock problem with usable leftovers review](https://doi.org/10.1016/j.ejor.2025.03.014)
- [Multi-stage two-dimensional cutting stock with usable leftovers](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5270710)
