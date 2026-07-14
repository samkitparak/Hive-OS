import { useMemo, useState } from "react";
import { labelPrintUrl, labelZplUrl } from "./api";

const button = { background: "#1f2937", border: "1px solid #374151", color: "#e5e7eb",
  padding: "7px 10px", borderRadius: 5, fontSize: 10, fontWeight: 800, cursor: "pointer" };
const input = { background: "#0d1117", border: "1px solid #374151", borderRadius: 5,
  color: "#e5e7eb", padding: "7px 8px", fontSize: 10, minWidth: 0 };
const label = { color: "#6b7280", fontSize: 9, fontWeight: 800, textTransform: "uppercase" };

export function IdentityPanel({ data, onAction }) {
  const [orderId, setOrderId] = useState("");
  const [actor, setActor] = useState("operator");
  const [onlyUnprinted, setOnlyUnprinted] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [isError, setIsError] = useState(false);
  const orders = useMemo(() => data?.orders ?? [], [data]);
  const activeOrderId = orders.some(item => String(item.order_id) === String(orderId))
    ? orderId : orders[0]?.order_id ?? "";

  const run = async (kind, payload, success) => {
    setBusy(true);
    setMessage("");
    setIsError(false);
    try {
      await onAction(kind, payload);
      setMessage(success);
    } catch (error) {
      setIsError(true);
      setMessage(error.message || "Label action failed");
    } finally {
      setBusy(false);
    }
  };

  if (!data) return null;
  const summary = data.summary ?? {};
  return <section style={{ borderBottom: "1px solid #263244", paddingBottom: 18, marginBottom: 16 }}>
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "end", gap: 10, flexWrap: "wrap" }}>
      <div><div style={label}>Physical traceability</div><div style={{ fontSize: 15, fontWeight: 800, marginTop: 3 }}>Unit identity and labels</div></div>
      <input aria-label="Label operator" value={actor} onChange={event => setActor(event.target.value)} style={{ ...input, width: 130 }} />
    </div>
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(90px,1fr))", gap: 6, margin: "12px 0" }}>
      {[["Unitized", summary.unitized, "#60a5fa"], ["Print queue", summary.pending_print_units, "#c084fc"],
        ["Printed", summary.printed_units, "#22c55e"], ["Unknown scans", summary.unknown_scans, "#f59e0b"],
        ["Duplicates", summary.duplicate_scans, "#ef4444"]].map(([name, value, color]) =>
        <div key={name} style={{ borderTop: `2px solid ${color}`, padding: "7px 4px" }}><div style={label}>{name}</div><div style={{ color, fontSize: 18, fontWeight: 800 }}>{value ?? 0}</div></div>)}
    </div>
    <div className="identity-controls" style={{ display: "grid", gridTemplateColumns: "minmax(220px,1fr) 150px auto", gap: 8, alignItems: "end" }}>
      <label style={{ minWidth: 0 }}><div style={{ ...label, marginBottom: 5 }}>Production order</div>
        <select value={activeOrderId} onChange={event => setOrderId(event.target.value)} style={{ ...input, width: "100%" }}>
          {orders.map(item => <option key={item.order_id} value={item.order_id}>{item.job_name} - {item.status} - {item.expected_units} units</option>)}
        </select>
      </label>
      <label style={{ display: "flex", gap: 7, alignItems: "center", minHeight: 32, color: "#9ca3af", fontSize: 10 }}>
        <input type="checkbox" checked={onlyUnprinted} onChange={event => setOnlyUnprinted(event.target.checked)} /> New labels only
      </label>
      <button disabled={busy || !activeOrderId} onClick={() => run("labelJob", {
        order_id: Number(activeOrderId), requested_by: actor, only_unprinted: onlyUnprinted,
      }, "Label set created")} style={{ ...button, background: "#1d4ed8", opacity: busy || !activeOrderId ? .55 : 1 }}>Create label set</button>
    </div>
    {message && <div role={isError ? "alert" : "status"} style={{ color: isError ? "#f87171" : "#22c55e", fontSize: 10, marginTop: 8 }}>{message}</div>}
    <div style={{ marginTop: 12 }}>
      <div style={label}>Recent label sets</div>
      {(data.print_jobs ?? []).length ? data.print_jobs.slice(0, 8).map(job => <div key={job.id} style={{ borderTop: "1px solid #263244", padding: "8px 0", display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 180 }}><div style={{ fontSize: 10, fontWeight: 800 }}>{job.job_name}</div><div style={{ color: "#6b7280", fontSize: 9, marginTop: 2 }}>{job.unit_count} labels - {job.status} - set {job.id}</div></div>
        <a href={labelPrintUrl(job.id)} target="_blank" rel="noreferrer" style={{ ...button, textDecoration: "none" }}>Preview</a>
        <a href={labelZplUrl(job.id)} style={{ ...button, textDecoration: "none" }}>ZPL</a>
        {job.status === "ready" && <button disabled={busy} onClick={() => run("labelPrinted", { id: job.id, payload: { actor } }, "Label set marked printed")} style={button}>Mark printed</button>}
      </div>) : <div style={{ color: "#6b7280", fontSize: 11, padding: "12px 0" }}>No label sets queued.</div>}
    </div>
    <style>{`@media (max-width: 760px) { .identity-controls { grid-template-columns: minmax(0, 1fr) !important; } }`}</style>
  </section>;
}
