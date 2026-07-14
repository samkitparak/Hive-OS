import { useMemo, useState } from "react";
import {
  AlertTriangle, CheckCircle2, Download, PackageCheck, Plus, Save, Send,
  ShoppingCart, Upload, XCircle,
} from "lucide-react";
import { purchaseOrderExportUrl } from "./api";

const line = { borderTop: "1px solid #263244", padding: "11px 0" };
const label = { color: "#6b7280", fontSize: 9, fontWeight: 800, textTransform: "uppercase" };
const input = { background: "#0d1117", border: "1px solid #374151", borderRadius: 5,
  color: "#e5e7eb", padding: "7px 8px", fontSize: 11, minWidth: 0, width: "100%" };
const button = { background: "#1f2937", border: "1px solid #374151", color: "#e5e7eb",
  padding: "7px 10px", borderRadius: 5, fontSize: 11, fontWeight: 700, cursor: "pointer",
  display: "inline-flex", gap: 6, alignItems: "center", justifyContent: "center" };
const primary = { ...button, background: "#1d4ed8", borderColor: "#3b82f6" };

function NumberField({ title, value, onChange, min = 0, step = "any" }) {
  return <div><div style={label}>{title}</div><input type="number" min={min} step={step}
    value={value} onChange={event => onChange(event.target.value)} style={input} /></div>;
}

function Stat({ title, value, tone = "#e5e7eb" }) {
  return <div style={{ minWidth: 100 }}><div style={label}>{title}</div>
    <div style={{ color: tone, fontSize: 16, fontWeight: 800, marginTop: 3 }}>{value}</div></div>;
}

function MappingRow({ item, suppliers, actor, onSave }) {
  const current = item.mapping;
  const [supplierKey, setSupplierKey] = useState(current?.supplier_key ?? suppliers[0]?.supplier_key ?? "");
  const [sku, setSku] = useState(current?.supplier_sku ?? "");
  const [purchaseUom, setPurchaseUom] = useState(current?.purchase_uom ?? item.internal_uom);
  const [conversion, setConversion] = useState(current?.conversion_factor ?? 1);
  const [multiple, setMultiple] = useState(current?.order_multiple ?? 1);
  const [minimum, setMinimum] = useState(current?.min_order_qty ?? 0);
  const [price, setPrice] = useState(current?.unit_price ?? "");
  const [verified, setVerified] = useState(Boolean(current?.verified));
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      await onSave({ supplierKey, objectType: item.object_type, objectKey: item.object_key,
        payload: { supplier_sku: sku, purchase_uom: purchaseUom,
          conversion_factor: Number(conversion), order_multiple: Number(multiple),
          min_order_qty: Number(minimum), unit_price: price === "" ? null : Number(price),
          currency: suppliers.find(supplier => supplier.supplier_key === supplierKey)?.currency ?? "INR",
          preferred: true, verified, expected_version: current?.supplier_key === supplierKey ? current.version : undefined,
          actor } });
    } finally { setBusy(false); }
  };
  const tone = item.at_risk ? "#f87171" : item.status === "mapping_required" ? "#fbbf24" : "#22c55e";
  return <div style={line}>
    <div style={{ display: "flex", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
      <div><div style={{ fontSize: 11, fontWeight: 800 }}>{item.name}</div>
        <div style={{ color: "#6b7280", fontSize: 9, marginTop: 3 }}>{item.object_type} / {item.object_key}</div></div>
      <div style={{ color: tone, fontSize: 10, fontWeight: 800 }}>
        {item.uncovered_qty} {item.internal_uom} uncovered
        {item.open_inbound_qty > 0 ? ` / ${item.open_inbound_qty} inbound` : ""}
        {item.at_risk ? " / late at current lead time" : ""}
      </div>
    </div>
    <div className="procurement-mapping-grid" style={{ display: "grid",
      gridTemplateColumns: "minmax(120px,1.1fr) minmax(110px,1fr) 90px 80px 70px 70px 90px 92px auto",
      gap: 7, alignItems: "end", marginTop: 9 }}>
      <div><div style={label}>Supplier</div><select value={supplierKey} onChange={event => setSupplierKey(event.target.value)} style={input}>
        {!suppliers.length && <option value="">Add a supplier first</option>}
        {suppliers.filter(supplier => supplier.active).map(supplier => <option key={supplier.supplier_key} value={supplier.supplier_key}>{supplier.name}</option>)}
      </select></div>
      <div><div style={label}>Supplier SKU</div><input value={sku} onChange={event => setSku(event.target.value)} style={input} /></div>
      <div><div style={label}>Buy unit</div><input value={purchaseUom} onChange={event => setPurchaseUom(event.target.value)} style={input} /></div>
      <NumberField title={`Per ${purchaseUom || "unit"}`} value={conversion} onChange={setConversion} min="0.000001" />
      <NumberField title="Multiple" value={multiple} onChange={setMultiple} min="0.000001" />
      <NumberField title="MOQ" value={minimum} onChange={setMinimum} />
      <NumberField title="Unit price" value={price} onChange={setPrice} />
      <label style={{ ...button, background: "transparent", border: 0, justifyContent: "flex-start", paddingLeft: 0 }}>
        <input type="checkbox" checked={verified} onChange={event => setVerified(event.target.checked)} /> Verified
      </label>
      <button disabled={busy || !supplierKey || !sku || !purchaseUom} onClick={save} title="Save supplier mapping" style={primary}><Save size={13} /> Save</button>
    </div>
    <div style={{ color: "#6b7280", fontSize: 9, marginTop: 6 }}>
      Suggested purchase: {item.recommended_purchase_qty ?? "mapping needed"} {current?.purchase_uom ?? "supplier units"}
      {item.need_by_at ? ` / needed ${new Date(item.need_by_at).toLocaleDateString()}` : ""}
    </div>
  </div>;
}

function SupplierEditor({ supplier, actor, onSave }) {
  const [key, setKey] = useState(supplier?.supplier_key ?? "");
  const [name, setName] = useState(supplier?.name ?? "");
  const [currency, setCurrency] = useState(supplier?.currency ?? "INR");
  const [lead, setLead] = useState(supplier?.lead_time_days ?? 0);
  const [email, setEmail] = useState(supplier?.email ?? "");
  const [gln, setGln] = useState(supplier?.gln ?? "");
  const [verified, setVerified] = useState(Boolean(supplier?.verified));
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try { await onSave({ key, payload: { name, currency: currency.toUpperCase(), lead_time_days: Number(lead),
      email: email || null, gln: gln || null, active: true, verified,
      expected_version: supplier?.version, actor } }); }
    finally { setBusy(false); }
  };
  return <div style={line}>
    <div className="procurement-supplier-grid" style={{ display: "grid",
      gridTemplateColumns: "110px minmax(140px,1.2fr) 70px 80px minmax(140px,1fr) 120px 90px auto",
      gap: 7, alignItems: "end" }}>
      <div><div style={label}>Key</div><input disabled={Boolean(supplier)} value={key} onChange={event => setKey(event.target.value.toUpperCase())} style={input} /></div>
      <div><div style={label}>Name</div><input value={name} onChange={event => setName(event.target.value)} style={input} /></div>
      <div><div style={label}>Currency</div><input maxLength="3" value={currency} onChange={event => setCurrency(event.target.value)} style={input} /></div>
      <NumberField title="Lead days" value={lead} onChange={setLead} step="1" />
      <div><div style={label}>Email</div><input type="email" value={email} onChange={event => setEmail(event.target.value)} style={input} /></div>
      <div><div style={label}>GLN</div><input maxLength="13" value={gln} onChange={event => setGln(event.target.value)} style={input} /></div>
      <label style={{ ...button, background: "transparent", border: 0, justifyContent: "flex-start", paddingLeft: 0 }}>
        <input type="checkbox" checked={verified} onChange={event => setVerified(event.target.checked)} /> Verified
      </label>
      <button disabled={busy || !key || !name || currency.length !== 3} onClick={save} style={primary}><Save size={13} /> Save</button>
    </div>
    {supplier?.metrics?.receipt_count > 0 && <div style={{ color: "#9ca3af", fontSize: 9, marginTop: 7 }}>
      {supplier.metrics.receipt_count} receipts / {Math.round((supplier.metrics.on_time_rate ?? 0) * 100)}% on time /
      {Math.round((supplier.metrics.rejection_rate ?? 0) * 100)}% rejected
      {supplier.metrics.observed_lead_time_days != null ? ` / ${supplier.metrics.observed_lead_time_days}d observed lead` : ""}
    </div>}
  </div>;
}

function OrderList({ orders, actor, onAction }) {
  const action = (order, name) => onAction({ id: order.id,
    payload: { action: name, expected_version: order.version, actor } });
  if (!orders.length) return <div style={{ color: "#6b7280", fontSize: 10, padding: 12 }}>No purchase orders yet.</div>;
  return <div>{orders.map(order => {
    const tone = ["approved", "sent", "received"].includes(order.status) ? "#22c55e"
      : order.status.includes("exception") ? "#f87171" : "#fbbf24";
    return <div key={`${order.id}-${order.version}`} style={line}>
      <div className="procurement-order-row" style={{ display: "grid", gridTemplateColumns: "minmax(180px,1.4fr) minmax(130px,1fr) 100px minmax(180px,1.2fr) auto", gap: 8, alignItems: "center" }}>
        <div><div style={{ fontWeight: 800, fontSize: 11 }}>{order.po_number}</div><div style={{ color: "#6b7280", fontSize: 9, marginTop: 3 }}>{order.supplier_name}</div></div>
        <div style={{ color: tone, fontWeight: 800, fontSize: 10, textTransform: "uppercase" }}>{order.status.replaceAll("_", " ")}</div>
        <div style={{ fontSize: 10 }}>{order.total == null ? "Unpriced" : `${order.currency} ${order.total.toLocaleString()}`}</div>
        <div style={{ color: "#9ca3af", fontSize: 9 }}>{order.lines.map(item => `${item.item_name} x ${item.ordered_qty}`).join("; ")}</div>
        <div style={{ display: "flex", gap: 5, justifyContent: "flex-end", flexWrap: "wrap" }}>
          <a href={purchaseOrderExportUrl(order.id)} title="Export purchase order CSV" style={button}><Download size={13} /></a>
          {order.status === "draft" && <button onClick={() => action(order, "approve")} style={button}><CheckCircle2 size={13} /> Approve</button>}
          {order.status === "approved" && <button onClick={() => action(order, "queue")} style={primary}><Send size={13} /> Queue</button>}
          {["draft", "approved", "queued", "sent"].includes(order.status) && <button onClick={() => action(order, "cancel")} title="Cancel purchase order" style={button}><XCircle size={13} /></button>}
        </div>
      </div>
    </div>;
  })}</div>;
}

function ManualOrder({ mappings, suppliers, actor, onCreate }) {
  const [supplierKey, setSupplierKey] = useState(suppliers[0]?.supplier_key ?? "");
  const eligible = mappings.filter(item => item.supplier_key === supplierKey);
  const [mappingId, setMappingId] = useState(eligible[0]?.id ?? "");
  const [quantity, setQuantity] = useState(1);
  const selected = mappings.find(item => item.id === Number(mappingId));
  const create = () => selected && onCreate({ supplier_key: supplierKey, actor,
    lines: [{ object_type: selected.object_type, object_key: selected.object_key, ordered_qty: Number(quantity) }] });
  return <div className="procurement-manual-grid" style={{ display: "grid", gridTemplateColumns: "minmax(140px,1fr) minmax(180px,1.4fr) 100px auto", gap: 7, alignItems: "end", marginBottom: 10 }}>
    <div><div style={label}>Supplier</div><select value={supplierKey} onChange={event => { setSupplierKey(event.target.value); setMappingId(""); }} style={input}>
      <option value="">Select supplier</option>{suppliers.map(item => <option key={item.supplier_key} value={item.supplier_key}>{item.name}</option>)}</select></div>
    <div><div style={label}>Mapped item</div><select value={mappingId} onChange={event => setMappingId(event.target.value)} style={input}>
      <option value="">Select item</option>{eligible.map(item => <option key={item.id} value={item.id}>{item.object_key} / {item.supplier_sku}</option>)}</select></div>
    <NumberField title="Buy quantity" value={quantity} onChange={setQuantity} min="0.000001" />
    <button disabled={!selected || Number(quantity) <= 0} onClick={create} style={primary}><Plus size={13} /> Draft PO</button>
  </div>;
}

function ReceiptForm({ orders, actor, onReceive }) {
  const receivable = orders.filter(order => ["approved", "queued", "sent", "partially_received"].includes(order.status));
  const [orderId, setOrderId] = useState("");
  const order = receivable.find(item => item.id === Number(orderId));
  const [lineNumber, setLineNumber] = useState("");
  const lineItem = order?.lines.find(item => item.line_number === Number(lineNumber));
  const [receiptKey, setReceiptKey] = useState("");
  const [lotCode, setLotCode] = useState("");
  const [accepted, setAccepted] = useState(0);
  const [rejected, setRejected] = useState(0);
  const [reason, setReason] = useState("");
  const [location, setLocation] = useState("");
  const [verified, setVerified] = useState(false);
  const submit = () => onReceive({ receipt_key: receiptKey, purchase_order_id: Number(orderId), location: location || null,
    verified, actor, lines: [{ line_number: Number(lineNumber), lot_code: lotCode || receiptKey,
      accepted_qty: Number(accepted), rejected_qty: Number(rejected), rejection_reason: reason || null }] });
  return <div>
    {!receivable.length && <div style={{ color: "#6b7280", fontSize: 10, padding: 12 }}>Approve a purchase order before receiving stock.</div>}
    {receivable.length > 0 && <div className="procurement-receipt-grid" style={{ display: "grid",
      gridTemplateColumns: "minmax(150px,1fr) minmax(160px,1.2fr) 110px 110px 80px 80px minmax(130px,1fr) minmax(120px,1fr) 90px auto",
      gap: 7, alignItems: "end" }}>
      <div><div style={label}>Purchase order</div><select value={orderId} onChange={event => { setOrderId(event.target.value); setLineNumber(""); }} style={input}>
        <option value="">Select PO</option>{receivable.map(item => <option key={item.id} value={item.id}>{item.po_number} / {item.supplier_name}</option>)}</select></div>
      <div><div style={label}>Line</div><select value={lineNumber} onChange={event => setLineNumber(event.target.value)} style={input}>
        <option value="">Select line</option>{order?.lines.filter(item => item.remaining_qty > 0).map(item => <option key={item.id} value={item.line_number}>{item.item_name} / {item.remaining_qty} {item.purchase_uom} open</option>)}</select></div>
      <div><div style={label}>Receipt ID</div><input value={receiptKey} onChange={event => setReceiptKey(event.target.value.toUpperCase())} style={input} /></div>
      <div><div style={label}>Lot</div><input value={lotCode} onChange={event => setLotCode(event.target.value.toUpperCase())} style={input} /></div>
      <NumberField title="Accepted" value={accepted} onChange={setAccepted} />
      <NumberField title="Rejected" value={rejected} onChange={setRejected} />
      <div><div style={label}>Rejection reason</div><input value={reason} onChange={event => setReason(event.target.value)} style={input} /></div>
      <div><div style={label}>Location</div><input value={location} onChange={event => setLocation(event.target.value)} style={input} /></div>
      <label style={{ ...button, background: "transparent", border: 0, justifyContent: "flex-start", paddingLeft: 0 }}><input type="checkbox" checked={verified} onChange={event => setVerified(event.target.checked)} /> Verified</label>
      <button disabled={!lineItem || !receiptKey || Number(accepted) + Number(rejected) <= 0 || (Number(rejected) > 0 && !reason)} onClick={submit} style={primary}><PackageCheck size={13} /> Post</button>
    </div>}
  </div>;
}

function CsvExchange({ runs, actor, onImport }) {
  const [documentType, setDocumentType] = useState("supplier_catalog");
  const [fileName, setFileName] = useState("");
  const [text, setText] = useState("");
  const [approve, setApprove] = useState(false);
  const [result, setResult] = useState(null);
  const run = async mode => setResult(await onImport({ document_type: documentType, mode, csv_text: text,
    file_name: fileName || null, approve_master_data: approve, actor }));
  return <div>
    <div className="procurement-import-grid" style={{ display: "grid", gridTemplateColumns: "170px minmax(180px,1fr) 130px auto auto", gap: 8, alignItems: "end" }}>
      <div><div style={label}>Document</div><select value={documentType} onChange={event => { setDocumentType(event.target.value); setResult(null); }} style={input}>
        <option value="supplier_catalog">Supplier catalog</option><option value="goods_receipt">Goods receipt</option></select></div>
      <div><div style={label}>CSV file</div><input type="file" accept=".csv,text/csv" style={input} onChange={async event => {
        const file = event.target.files?.[0]; setFileName(file?.name ?? ""); setText(file ? await file.text() : ""); setResult(null);
      }} /></div>
      <label style={{ ...button, background: "transparent", border: 0, justifyContent: "flex-start", paddingLeft: 0 }}>
        <input type="checkbox" checked={approve} onChange={event => setApprove(event.target.checked)} /> Approve master data
      </label>
      <button disabled={!text} onClick={() => run("validate")} style={button}><CheckCircle2 size={13} /> Validate</button>
      <button disabled={!text || !result?.ready_to_apply} onClick={() => run("apply")} style={primary}><Upload size={13} /> Apply</button>
    </div>
    {result && <div style={{ ...line, color: result.ready_to_apply || result.records_imported ? "#22c55e" : "#f87171", fontSize: 10 }}>
      {result.status} / {result.records_accepted} accepted / {result.records_rejected} rejected / {result.records_imported} imported
      {result.duplicate ? " / duplicate ignored" : ""}
      {(result.issues ?? []).slice(0, 8).map((issue, index) => <div key={`${issue.row}-${index}`} style={{ color: "#fca5a5", marginTop: 4 }}>Row {issue.row}: {issue.message}</div>)}
    </div>}
    {runs.length > 0 && <div style={{ marginTop: 14 }}><div style={label}>Recent exchange runs</div>{runs.map(runItem => <div key={runItem.id} style={{ ...line, display: "flex", justifyContent: "space-between", gap: 8, fontSize: 9 }}>
      <span>{runItem.document_type} / {runItem.file_name || "API payload"}</span><span style={{ color: runItem.status === "failed" ? "#f87171" : "#9ca3af" }}>{runItem.status} / {runItem.records_imported} imported</span>
    </div>)}</div>}
  </div>;
}

export function ProcurementPanel({ data, actor, onAction }) {
  const [tab, setTab] = useState("needs");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const run = async (kind, payload) => {
    setError(""); setMessage("");
    try { const result = await onAction(kind, payload); setMessage("Saved"); return result; }
    catch (err) { setError(err.message); return null; }
  };
  const snapshot = data ?? { suppliers: [], mappings: [], recommendations: [], orders: [], receipts: [], outbox: [], exchange_runs: [], summary: {} };
  const summary = snapshot.summary ?? {};
  const needs = useMemo(() => snapshot.recommendations.filter(item => item.uncovered_qty > 0), [snapshot.recommendations]);
  const tabs = [
    ["needs", "Supply needs", ShoppingCart], ["suppliers", "Suppliers", Save],
    ["orders", "Purchase orders", Send], ["receiving", "Receiving", PackageCheck],
    ["exchange", "CSV exchange", Upload],
  ];
  return <div className="procurement-workspace" style={{ borderTop: "1px solid #263244", paddingTop: 12 }}>
    <div className="procurement-summary" style={{ display: "flex", gap: 22, flexWrap: "wrap", paddingBottom: 12 }}>
      <Stat title="Verified suppliers" value={`${summary.verified_suppliers ?? 0}/${summary.suppliers ?? 0}`} tone={summary.verified_suppliers ? "#22c55e" : "#fbbf24"} />
      <Stat title="Uncovered" value={summary.uncovered_shortages ?? 0} tone={summary.uncovered_shortages ? "#fbbf24" : "#22c55e"} />
      <Stat title="Mapped" value={summary.mapped_shortages ?? 0} />
      <Stat title="Supply risks" value={summary.supply_risks ?? 0} tone={summary.supply_risks ? "#f87171" : "#22c55e"} />
      <Stat title="Open POs" value={summary.open_purchase_orders ?? 0} />
      <Stat title="Outbox" value={summary.pending_outbox ?? 0} tone={summary.pending_outbox ? "#fbbf24" : "#9ca3af"} />
    </div>
    <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", flexWrap: "wrap", borderTop: "1px solid #263244", paddingTop: 10 }}>
      <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>{tabs.map(([key, title, Icon]) => <button key={key} onClick={() => setTab(key)}
        style={{ ...button, background: tab === key ? "#374151" : "transparent" }}><Icon size={13} /> {title}</button>)}</div>
      {tab === "needs" && <button disabled={!needs.some(item => item.status === "ready_to_draft")} onClick={() => run("procurementDraft", { actor })} style={primary}><ShoppingCart size={13} /> Draft mapped needs</button>}
    </div>
    {error && <div style={{ color: "#f87171", fontSize: 10, marginTop: 9 }}><AlertTriangle size={12} style={{ verticalAlign: "middle", marginRight: 5 }} />{error}</div>}
    {message && <div style={{ color: "#22c55e", fontSize: 10, marginTop: 9 }}>{message}</div>}
    {tab === "needs" && <div>{needs.map(item => <MappingRow key={`${item.object_type}-${item.object_key}-${item.mapping?.version ?? 0}`} item={item}
      suppliers={snapshot.suppliers} actor={actor} onSave={payload => run("procurementMapping", payload)} />)}
      {!needs.length && <div style={{ color: "#22c55e", fontSize: 10, padding: 14 }}>No uncovered supply requirements.</div>}</div>}
    {tab === "suppliers" && <div><SupplierEditor actor={actor} onSave={payload => run("procurementSupplier", payload)} />
      {snapshot.suppliers.map(item => <SupplierEditor key={`${item.supplier_key}-${item.version}`} supplier={item} actor={actor} onSave={payload => run("procurementSupplier", payload)} />)}</div>}
    {tab === "orders" && <div style={{ paddingTop: 11 }}><div style={label}>Manual purchase order</div><ManualOrder mappings={snapshot.mappings} suppliers={snapshot.suppliers} actor={actor}
      onCreate={payload => run("procurementCreateOrder", payload)} /><OrderList orders={snapshot.orders} actor={actor}
      onAction={payload => run("procurementOrderAction", payload)} /></div>}
    {tab === "receiving" && <div style={{ paddingTop: 12 }}><ReceiptForm orders={snapshot.orders} actor={actor}
      onReceive={payload => run("procurementReceipt", payload)} />
      {snapshot.receipts.length > 0 && <div style={{ marginTop: 15 }}><div style={label}>Recent receipts</div>{snapshot.receipts.map(item => <div key={item.id} style={{ ...line, display: "flex", justifyContent: "space-between", gap: 8, fontSize: 10 }}>
        <span>{item.receipt_key} / {item.po_number} / {item.supplier_name}</span><span style={{ color: item.status.includes("exception") ? "#f87171" : "#22c55e" }}>{item.status.replaceAll("_", " ")}</span>
      </div>)}</div>}</div>}
    {tab === "exchange" && <div style={{ paddingTop: 12 }}><CsvExchange runs={snapshot.exchange_runs} actor={actor}
      onImport={payload => run("procurementImport", payload)} />
      {snapshot.outbox.length > 0 && <div style={{ marginTop: 15 }}><div style={label}>ERP outbox</div>{snapshot.outbox.map(item => <div key={item.id} style={{ ...line, display: "flex", justifyContent: "space-between", gap: 8, fontSize: 10 }}>
        <span>{item.document_type} / {item.object_key}</span><span style={{ color: item.status === "failed" ? "#f87171" : "#fbbf24" }}>{item.status}</span>
      </div>)}</div>}</div>}
    <div style={{ color: "#6b7280", fontSize: 9, marginTop: 14 }}>{snapshot.guardrail}</div>
    <style>{`@media (max-width: 760px) {
      .procurement-mapping-grid, .procurement-supplier-grid, .procurement-order-row,
      .procurement-manual-grid, .procurement-receipt-grid, .procurement-import-grid {
        grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) !important;
      }
      .procurement-order-row > div:first-child, .procurement-order-row > div:nth-child(4) { grid-column: 1 / -1; }
      .procurement-order-row > div:last-child { grid-column: 1 / -1; justify-content: flex-start !important; }
    }
    .procurement-workspace button:disabled { opacity: .45; cursor: not-allowed !important; }
    `}</style>
  </div>;
}
