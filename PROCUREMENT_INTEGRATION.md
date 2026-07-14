# HIVE OS Procurement and ERP Exchange

## Purpose

HIVE converts verified warehouse shortages into controlled purchase orders and
verified receipts. It does not guess vendor identifiers, claim that an export
reached an ERP, or add rejected material to available stock.

The canonical workflow is:

```text
production demand + stock policy - usable stock - open inbound
  -> uncovered requirement
  -> verified supplier/item mapping
  -> draft PO
  -> named approval
  -> CSV export or ERP outbox
  -> goods receipt
  -> inventory lot + immutable movement
  -> supplier performance evidence
```

## Master Data

Supplier records carry a stable HIVE key, currency, lead time, optional GLN and
ERP identity, and an explicit verified flag. Item mappings preserve the HIVE
component or sheet key while adding the supplier SKU, optional GTIN, purchase
unit, internal-units-per-purchase-unit conversion, pack multiple, MOQ, price,
and currency.

Only one mapping may be preferred for a HIVE item. Draft orders may expose an
unverified mapping for review, but approval requires both the supplier and every
line mapping to be verified.

## Quantity Logic

```text
uncovered = max(0, reorder target - remaining open inbound)
raw purchase units = max(uncovered / conversion factor, MOQ)
purchase quantity = round_up(raw purchase units, order multiple)
```

Remaining inbound excludes quantities already accepted or rejected. This keeps
an open PO from creating duplicate recommendations while allowing rejected
material to reappear as a shortage.

## Purchase Order Lifecycle

| State | Meaning |
|---|---|
| `draft` | Editable HIVE intent; not approved or sent |
| `approved` | Named operator approved verified master data |
| `queued` | Canonical document exists in the adapter outbox |
| `sent` | Adapter positively acknowledged external delivery |
| `partially_received` | At least one line has open quantity |
| `received` | All ordered quantity accepted |
| `received_with_exceptions` | All lines closed with rejected quantity |
| `cancelled` | Closed before receiving stock |

Queueing does not claim delivery. An external worker must acknowledge the outbox
record before HIVE marks the order `sent`. The outbox uses deterministic payload
hashes and one current order document per PO.

## Receipt Rules

- `receipt_key` is an idempotency key; replay returns the existing receipt.
- A receipt can target only an approved, queued, sent, or partially received PO.
- Accepted plus rejected quantity cannot exceed the open PO line quantity.
- Rejected quantity requires a reason and never enters inventory.
- Accepted quantity is converted into HIVE internal units and posted to a
  component or sheet lot with an immutable `receipt` movement.
- The operator must explicitly verify the physical count before the resulting
  lot is trusted for production allocation.

## Day-One CSV Bridge

Use **Planning > Resources > Procurement > CSV exchange**. Always run
`validate` before `apply`. Applied file fingerprints are replay-protected.

Supplier catalog columns:

```csv
supplier_key,supplier_name,object_type,object_key,supplier_sku,purchase_uom,conversion_factor,order_multiple,min_order_qty,unit_price,currency,lead_time_days,gln,gtin,preferred
```

Required fields are supplier key/name, `component` or `sheet`, the existing HIVE
object key, supplier SKU, and valid positive conversion/multiple values. Use
**Approve master data** only after reconciling the file with the supplier or ERP.

Goods receipt columns:

```csv
receipt_key,po_number,line_number,lot_code,accepted_qty,rejected_qty,rejection_reason,location,received_at,external_receipt_id,verified
```

The PO must already be receivable. A failed row prevents the batch from being
ready to apply.

## ERP Adapter Contract

`POST /procurement/orders/{id}/action` with `queue` writes a vendor-neutral JSON
document using Order concepts from OASIS UBL. It is intentionally not described
as UBL XML conformance. A real adapter translates that document to the target
ERP API, EDI, database staging table, or file format, then calls:

```http
POST /api/procurement/outbox/{id}/ack
```

with `success`, the external ID, or an error. Secrets belong in the adapter's
environment or credential store, never in HIVE supplier records.

OASIS UBL separates Order and Receipt Advice and supports received, rejected,
and shortage quantities. GS1 identifies traded items with GTIN and parties or
locations with GLN; EPCIS receiving evidence links what, when, where, and the
governing purchase order. HIVE keeps those identifiers optional until licensed
and confirmed site values exist.

Primary references:

- [OASIS Universal Business Language 2.3](https://docs.oasis-open.org/ubl/UBL-2.3.html)
- [OASIS UBL Order 2.3](https://docs.oasis-open.org/ubl/os-UBL-2.3/mod/summary/reports/UBL-Order-2.3.html)
- [OASIS UBL Receipt Advice 2.3](https://docs.oasis-open.org/ubl/os-UBL-2.3/mod/summary/reports/UBL-ReceiptAdvice-2.3.html)
- [GS1 GLN data model](https://www.gs1.org/standards/gln-data-model-solution-standard/current-standard)
- [GS1 EPCIS and CBV implementation guideline](https://ref.gs1.org/guidelines/epcis-cbv/2.0.0/)

## Learning Boundary

Supplier metrics are evidence, not automatic master-data edits. HIVE records
observed lead time, on-time rate, and rejection rate. It offers a lead-time
recommendation only after at least five receipts and a meaningful difference
from the commissioned value. A person remains responsible for changing the
planning lead time or supplier preference.

## API

| Method | Route | Purpose |
|---|---|---|
| GET | `/procurement/snapshot` | Suppliers, mappings, needs, POs, receipts, outbox, metrics |
| PUT | `/procurement/suppliers/{key}` | Create or version supplier master data |
| PUT | `/procurement/suppliers/{key}/mappings/{type}/{item}` | Map a HIVE item to a supplier SKU |
| POST | `/procurement/orders` | Create a manual draft PO |
| POST | `/procurement/orders/draft-recommendations` | Group mapped needs into supplier drafts |
| POST | `/procurement/orders/{id}/action` | Approve, queue, or cancel a PO |
| GET | `/procurement/orders/{id}/export.csv` | Export a PO for a manual ERP bridge |
| GET | `/procurement/outbox` | Read canonical adapter documents |
| POST | `/procurement/outbox/{id}/ack` | Record adapter delivery success or failure |
| POST | `/procurement/receipts` | Post an idempotent accepted/rejected receipt |
| POST | `/procurement/imports/csv` | Validate or apply a catalog/receipt file |
