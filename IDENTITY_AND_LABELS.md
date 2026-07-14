# HIVE Unit Identity and Labels

HIVE materializes one `trace_unit` for every physical copy represented by a
Cabinet Vision part row. A part with `qty=4` therefore receives four stable unit
keys, four labels, and four independent route histories.

## Identifier Policy

The current primary identifier is private to HAEEV:

```text
unit key:  HU-<16 RFC 4648 base32 characters>
QR value:  HIVE:U:<unit-key>
```

The value is short, ASCII-only, and works with offline keyboard-wedge scanners.
It is not presented as a GS1 identifier. GS1 requires an allocated Company
Prefix before a company creates globally unique GS1 identification keys. Once
HAEEV has that prefix, the licensed SGTIN, GTIN+serial Digital Link, or an Ottimo
identifier can be added as an alias without changing the HIVE unit key or its
history.

Standards used for the boundary:

- [GS1 identification keys](https://www.gs1.org/standards/id-keys) and the
  [GS1 Company Prefix](https://www.gs1.org/standards/id-keys/company-prefix)
  define when a globally shared identifier may be issued.
- [GS1 Digital Link URI Syntax 1.6.0](https://ref.gs1.org/standards/digital-link/uri-syntax/)
  defines GTIN, lot, and serial representation after licensed keys exist.
- [GS1 EPCIS 2.0.1](https://ref.gs1.org/standards/epcis/2.0.1/) informs the
  object, event time, read point, business location, and disposition ledger.
- Zebra's official [`^BQ` command](https://docs.zebra.com/content/tcm/us/en/printers/software/zpl-pg/zpl-commands/%5Ebq.html)
  defines native Model 2 QR output in ZPL.

## Data Model

| Table | Purpose |
|---|---|
| `trace_units` | Stable physical unit, order/part link, ordinal, lifecycle state, current machine |
| `unit_identifier_aliases` | HIVE, Ottimo, ERP, future GS1, or supplier identifiers mapped to one unit |
| `barcode_event_resolutions` | Auditable result of resolving every raw scanner event |
| `unit_route_progress` | Per-unit start/completion state for each planned route step |
| `label_print_jobs` | Audited label set requested for a production order |
| `label_print_items` | Ordered units and print confirmations within a label set |

Raw scanner text always remains in `barcode_events`. Resolution is separate so
a bad alias or payload mismatch can be corrected without rewriting evidence.

## Scan Rules

1. Resolve the exact scanned value against active aliases or a HIVE unit key.
2. If resolved, derive the job and part from the unit; typed context is optional.
3. If typed job/part context conflicts, retain the raw scan as `conflict` and do
   not advance execution.
4. Apply the station event through the same execution state machine used by
   manual and machine actuals.
5. Record per-unit route progress and physical traceability in one transaction.
6. Ignore a repeated start/completion for the same unit and route step, retain
   the scan as `duplicate`, and do not increment aggregate quantity.
7. Unknown and legacy codes remain queryable for adapter commissioning.

One serialized scan represents one physical unit. A future batch alias must
carry an explicit quantity instead of silently changing that rule.

## Label Outputs

The built-in `part_100x50` template is 100 x 50 mm and contains:

- HIVE and HAEEV identity
- production job and part
- assembly, dimensions, material, and Cabinet Vision part reference
- unit ordinal, total quantity, and human-readable unit key
- Model 2 QR encoding the exact HIVE payload

Two renderers use the same print job and unit data:

- Browser print/SVG for normal office or thermal printer drivers.
- Native Zebra ZPL (`^BQN,2,5` with automatic input) for direct spool or file
  transfer once the printer model and network address are known.

HIVE never marks a label printed merely because it was rendered or downloaded.
The operator confirms printing, which increments the unit's audited print count.

## API

| Method | Route | Purpose |
|---|---|---|
| GET | `/identity/snapshot` | Unitization, print queue, and scan-resolution readiness |
| POST | `/identity/orders/{id}/materialize` | Idempotently create missing physical units |
| GET | `/identity/orders/{id}/units` | List the order's physical units |
| GET | `/identity/units/{unit_key}` | Unit, aliases, route progress, and traceability |
| GET | `/identity/resolve?value=...` | Read-only scanner/alias diagnostic |
| POST | `/identity/units/{unit_key}/aliases` | Attach an Ottimo, ERP, supplier, or licensed GS1 alias |
| GET/POST | `/labels/jobs` | List or create print-ready label sets |
| GET | `/labels/jobs/{id}/print` | 100 x 50 mm browser print view |
| GET | `/labels/jobs/{id}/zpl` | Download native Zebra commands |
| GET | `/labels/units/{unit_key}/svg` | Render one unit label as SVG |
| POST | `/labels/jobs/{id}/printed` | Confirm physical printing |

## Factory Commissioning

1. Confirm the label stock size and printer DPI (203 or 300 DPI).
2. Print the same test set through browser and ZPL paths; retain the clearer path.
3. Scan every test label with the actual Ottimo/keyboard-wedge hardware.
4. Confirm scanners send the complete value followed by Enter and do not alter
   case, colon, or hyphen characters.
5. Map each station/read point to the canonical HIVE machine key.
6. Run one unit through all required route steps and verify duplicate scans do
   not change quantities.
7. Obtain the real Ottimo identifier format and attach it as an alias rather
   than parsing job and part names from display text.
8. Obtain a GS1 India Company Prefix only if labels must be globally exchanged;
   keep HIVE private identifiers for internal work-in-process otherwise.

Printer IP, DPI, darkness, media calibration, and Ottimo payload examples are
the only site values still required for direct unattended printing.
