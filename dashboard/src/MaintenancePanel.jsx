import { useEffect, useMemo, useState } from "react";
import { fetchMaintenanceWorkOrder } from "./api";

const colors = {
  overdue: "#ef4444",
  due_soon: "#f59e0b",
  unverified: "#a78bfa",
  awaiting_evidence: "#60a5fa",
  healthy: "#22c55e",
  inactive: "#6b7280",
};

const inputStyle = {
  width: "100%", minHeight: 32, background: "#111827", border: "1px solid #374151",
  color: "#f9fafb", padding: "6px 8px", borderRadius: 6, fontSize: 11,
};

const buttonStyle = {
  background: "#1f2937", border: "1px solid #374151", color: "#f9fafb",
  padding: "7px 12px", borderRadius: 6, fontSize: 11, cursor: "pointer",
};

const labelStyle = {
  display: "block", color: "#6b7280", fontSize: 9, fontWeight: 800,
  textTransform: "uppercase", marginBottom: 5,
};

function Field({ label, children }) {
  return <label style={{ minWidth: 0 }}><span style={labelStyle}>{label}</span>{children}</label>;
}

function Metric({ label, value, color = "#f9fafb" }) {
  return (
    <div style={{ borderRight: "1px solid #1f2937", padding: "4px 14px 4px 0" }}>
      <div style={{ ...labelStyle, marginBottom: 2 }}>{label}</div>
      <div style={{ color, fontSize: 19, fontWeight: 800 }}>{value}</div>
    </div>
  );
}

function Status({ value }) {
  return <span style={{ color: colors[value] ?? "#9ca3af", fontSize: 10, fontWeight: 800 }}>{value.replaceAll("_", " ").toUpperCase()}</span>;
}

function planForm(plan) {
  if (!plan) return null;
  return {
    expected_version: plan.version,
    strategy: plan.strategy,
    runtime_basis: plan.runtime_basis,
    interval_days: plan.interval_days ?? "",
    interval_runtime_h: plan.interval_runtime_h ?? "",
    interval_cycles: plan.interval_cycles ?? "",
    estimated_duration_min: plan.estimated_duration_min,
    criticality: plan.criticality,
    requires_shutdown: Boolean(plan.requires_shutdown),
    loto_required: Boolean(plan.loto_required),
    verified: Boolean(plan.verified),
    actor: "maintenance-planner",
  };
}

function PlansView({ plans, onAction, run, busy }) {
  const [selectedId, setSelectedId] = useState(plans[0]?.id ?? null);
  const selected = plans.find(plan => plan.id === selectedId) ?? plans[0];
  const [form, setForm] = useState(() => planForm(selected));

  if (!selected || !form) return <div style={{ color: "#6b7280", fontSize: 11 }}>No maintenance plans</div>;
  const change = event => {
    const { name, value, checked, type } = event.target;
    setForm(current => ({ ...current, [name]: type === "checkbox" ? checked : value }));
  };
  const submit = event => {
    event.preventDefault();
    const payload = { ...form };
    ["interval_days", "interval_runtime_h", "interval_cycles"].forEach(key => {
      payload[key] = payload[key] === "" ? null : Number(payload[key]);
    });
    payload.estimated_duration_min = Number(payload.estimated_duration_min);
    run(() => onAction("maintenancePlan", { id: selected.id, payload }), "Plan saved")
      .then(result => { if (result) setForm(planForm(result)); });
  };

  return (
    <div className="maintenance-split" style={{ display: "grid", gridTemplateColumns: "minmax(250px, .8fr) minmax(360px, 1.4fr)", gap: 16 }}>
      <div style={{ borderRight: "1px solid #1f2937", paddingRight: 12, maxHeight: 470, overflowY: "auto" }}>
        {plans.map(plan => (
          <button key={plan.id} onClick={() => { setSelectedId(plan.id); setForm(planForm(plan)); }} style={{
            width: "100%", textAlign: "left", display: "block", padding: "10px 8px",
            border: 0, borderBottom: "1px solid #1f2937", cursor: "pointer",
            background: plan.id === selected.id ? "#151e2d" : "transparent", color: "#f9fafb",
          }}>
            <div style={{ fontSize: 11, fontWeight: 700 }}>{plan.machine_name}</div>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, marginTop: 4 }}>
              <Status value={plan.status} />
              <span style={{ color: "#6b7280", fontSize: 10 }}>{plan.strategy}</span>
            </div>
          </button>
        ))}
      </div>
      <form onSubmit={submit}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "start", marginBottom: 14 }}>
          <div>
            <div style={{ fontSize: 14, fontWeight: 800 }}>{selected.machine_name}</div>
            <div style={{ color: "#9ca3af", fontSize: 11, marginTop: 3 }}>{selected.title}</div>
          </div>
          <Status value={selected.status} />
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 9 }}>
          <Field label="Strategy"><select name="strategy" value={form.strategy} onChange={change} style={inputStyle}>
            <option value="calendar">Calendar</option><option value="usage">Usage</option>
            <option value="hybrid">Hybrid</option><option value="condition">Condition</option>
          </select></Field>
          <Field label="Runtime basis"><select name="runtime_basis" value={form.runtime_basis} onChange={change} style={inputStyle}>
            <option value="powered">Powered</option><option value="cycle">Active cycle</option>
          </select></Field>
          <Field label="Criticality"><select name="criticality" value={form.criticality} onChange={change} style={inputStyle}>
            <option value="low">Low</option><option value="medium">Medium</option>
            <option value="high">High</option><option value="critical">Critical</option>
          </select></Field>
          <Field label="Calendar days"><input name="interval_days" type="number" min="0.01" step="0.01" value={form.interval_days} onChange={change} style={inputStyle} /></Field>
          <Field label="Runtime hours"><input name="interval_runtime_h" type="number" min="0.01" step="0.01" value={form.interval_runtime_h} onChange={change} style={inputStyle} /></Field>
          <Field label="Cycle count"><input name="interval_cycles" type="number" min="1" value={form.interval_cycles} onChange={change} style={inputStyle} /></Field>
          <Field label="Duration minutes"><input name="estimated_duration_min" type="number" min="1" value={form.estimated_duration_min} onChange={change} style={inputStyle} /></Field>
        </div>
        <div style={{ display: "flex", gap: 18, marginTop: 12, flexWrap: "wrap", color: "#d1d5db", fontSize: 11 }}>
          <label><input name="requires_shutdown" type="checkbox" checked={form.requires_shutdown} onChange={change} /> Shutdown required</label>
          <label><input name="loto_required" type="checkbox" checked={form.loto_required} onChange={change} /> LOTO required</label>
          <label><input name="verified" type="checkbox" checked={form.verified} onChange={change} /> OEM schedule verified</label>
        </div>
        <div style={{ borderTop: "1px solid #1f2937", marginTop: 14, paddingTop: 12 }}>
          <div style={{ ...labelStyle, marginBottom: 7 }}>Inspection checklist</div>
          {selected.tasks.map(task => <div key={task.id} style={{ color: "#d1d5db", fontSize: 11, padding: "4px 0" }}>{task.sequence}. {task.title}</div>)}
        </div>
        <button disabled={busy} style={{ ...buttonStyle, marginTop: 14, opacity: busy ? .55 : 1 }}>Save plan</button>
      </form>
    </div>
  );
}

function WorkOrdersView({ workOrders, onAction, run, busy }) {
  const openOrders = workOrders.filter(order => order.status === "open" || order.status === "in_progress");
  const [selectedId, setSelectedId] = useState(openOrders[0]?.id ?? null);
  const [detail, setDetail] = useState(null);
  const [actor, setActor] = useState("maintainer");
  const [verifier, setVerifier] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [results, setResults] = useState({});

  useEffect(() => {
    if (!selectedId) return;
    fetchMaintenanceWorkOrder(selectedId).then(order => {
      setDetail(order);
      setStart(order.scheduled_start_at?.slice(0, 16) ?? "");
      setEnd(order.scheduled_end_at?.slice(0, 16) ?? "");
      setResults(Object.fromEntries(order.tasks.map(task => [task.id, task.response_type === "check" ? "checked" : task.response_type === "pass_fail" ? "pass" : ""])));
    }).catch(() => setDetail(null));
  }, [selectedId]);

  if (!openOrders.length) return <div style={{ color: "#6b7280", fontSize: 11 }}>No open work orders</div>;
  const schedule = () => run(() => onAction("maintenanceWorkOrder", {
    id: detail.id, payload: { scheduled_start_at: new Date(start).toISOString(), scheduled_end_at: new Date(end).toISOString(), actor },
  }), "Maintenance window scheduled");
  const changeStatus = status => run(() => onAction("maintenanceWorkOrder", { id: detail.id, payload: { status, actor } }), `Work order ${status.replace("_", " ")}`);
  const complete = () => {
    const task_results = detail.tasks.map(task => ({
      task_id: task.id,
      result: task.response_type === "number" ? "recorded" : task.response_type === "text" ? "recorded" : results[task.id],
      value_number: task.response_type === "number" ? Number(results[task.id]) : undefined,
      value_text: task.response_type === "text" ? results[task.id] : undefined,
    }));
    run(() => onAction("maintenanceComplete", { id: detail.id, payload: {
      completed_by: actor, loto_verified: Boolean(detail.loto_required),
      loto_verified_by: detail.loto_required ? verifier : undefined, task_results,
    }}), "Work order completed");
  };

  return (
    <div className="maintenance-split" style={{ display: "grid", gridTemplateColumns: "minmax(250px, .8fr) minmax(360px, 1.4fr)", gap: 16 }}>
      <div style={{ borderRight: "1px solid #1f2937", paddingRight: 12 }}>
        {openOrders.map(order => <button key={order.id} onClick={() => setSelectedId(order.id)} style={{
          width: "100%", textAlign: "left", padding: "10px 8px", border: 0, borderBottom: "1px solid #1f2937",
          background: order.id === selectedId ? "#151e2d" : "transparent", color: "#f9fafb", cursor: "pointer",
        }}><div style={{ fontSize: 11, fontWeight: 700 }}>{order.title}</div><div style={{ color: "#6b7280", fontSize: 10, marginTop: 3 }}>{order.machine_name ?? "Factory"} · {order.priority}</div></button>)}
      </div>
      {detail && <div>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
          <div><div style={{ fontSize: 14, fontWeight: 800 }}>{detail.title}</div><div style={{ color: "#6b7280", fontSize: 10, marginTop: 3 }}>{detail.machine_name} · {detail.status}</div></div>
          {detail.required_spare_shortages > 0 && <span style={{ color: "#ef4444", fontSize: 10, fontWeight: 800 }}>SPARES SHORT</span>}
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 9, marginTop: 14 }}>
          <Field label="Maintainer"><input value={actor} onChange={e => setActor(e.target.value)} style={inputStyle} /></Field>
          <Field label="Window start"><input type="datetime-local" value={start} onChange={e => setStart(e.target.value)} style={inputStyle} /></Field>
          <Field label="Window end"><input type="datetime-local" value={end} onChange={e => setEnd(e.target.value)} style={inputStyle} /></Field>
        </div>
        <div style={{ display: "flex", gap: 8, marginTop: 9, flexWrap: "wrap" }}>
          <button type="button" disabled={busy || !start || !end} onClick={schedule} style={buttonStyle}>Schedule</button>
          {detail.status === "open" && <button type="button" disabled={busy} onClick={() => changeStatus("in_progress")} style={buttonStyle}>Start work</button>}
          <button type="button" disabled={busy} onClick={() => changeStatus("cancelled")} style={{ ...buttonStyle, color: "#fca5a5" }}>Cancel</button>
        </div>
        <div style={{ borderTop: "1px solid #1f2937", marginTop: 14, paddingTop: 10 }}>
          {detail.tasks.map(task => <Field key={task.id} label={task.title}>
            {task.response_type === "pass_fail" ? <select value={results[task.id] ?? "pass"} onChange={e => setResults(r => ({ ...r, [task.id]: e.target.value }))} style={inputStyle}><option value="pass">Pass</option><option value="fail">Fail</option></select>
              : task.response_type === "check" ? <label style={{ color: "#d1d5db", fontSize: 11 }}><input type="checkbox" checked={results[task.id] === "checked"} onChange={e => setResults(r => ({ ...r, [task.id]: e.target.checked ? "checked" : "" }))} /> Checked</label>
              : <input type={task.response_type === "number" ? "number" : "text"} value={results[task.id] ?? ""} onChange={e => setResults(r => ({ ...r, [task.id]: e.target.value }))} style={{ ...inputStyle, marginBottom: 8 }} />}
          </Field>)}
        </div>
        {detail.loto_required ? <Field label="Authorized LOTO verifier"><input value={verifier} onChange={e => setVerifier(e.target.value)} style={inputStyle} /></Field> : null}
        <button type="button" disabled={busy || detail.required_spare_shortages > 0 || (detail.loto_required && !verifier)} onClick={complete} style={{ ...buttonStyle, marginTop: 12, background: "#14532d", borderColor: "#16a34a" }}>Complete inspection</button>
      </div>}
    </div>
  );
}

function SparesView({ spares, onAction, run, busy }) {
  const [part, setPart] = useState({ part_key: "", name: "", criticality: "medium", reorder_point: 0, reorder_qty: 0, verified: false });
  const [selectedKey, setSelectedKey] = useState(spares[0]?.part_key ?? "");
  const [stock, setStock] = useState({ on_hand_qty: 0, location: "maintenance_store", verified: false, actor: "storekeeper" });
  const selected = spares.find(item => item.part_key === selectedKey);
  return <div>
    <div className="maintenance-split" style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 18 }}>
      <form onSubmit={e => { e.preventDefault(); run(() => onAction("maintenanceSpare", part), "Spare part created"); }}>
        <div style={{ ...labelStyle, marginBottom: 9 }}>Add spare part</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 9 }}>
          <Field label="Part key"><input required value={part.part_key} onChange={e => setPart(p => ({ ...p, part_key: e.target.value }))} style={inputStyle} /></Field>
          <Field label="Name"><input required value={part.name} onChange={e => setPart(p => ({ ...p, name: e.target.value }))} style={inputStyle} /></Field>
          <Field label="Criticality"><select value={part.criticality} onChange={e => setPart(p => ({ ...p, criticality: e.target.value }))} style={inputStyle}><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="critical">Critical</option></select></Field>
          <Field label="Reorder point"><input type="number" min="0" value={part.reorder_point} onChange={e => setPart(p => ({ ...p, reorder_point: Number(e.target.value) }))} style={inputStyle} /></Field>
        </div>
        <label style={{ color: "#d1d5db", fontSize: 11, display: "block", marginTop: 10 }}><input type="checkbox" checked={part.verified} onChange={e => setPart(p => ({ ...p, verified: e.target.checked }))} /> Manufacturer part verified</label>
        <button disabled={busy} style={{ ...buttonStyle, marginTop: 10 }}>Create part</button>
      </form>
      <form onSubmit={e => { e.preventDefault(); run(() => onAction("maintenanceStock", { key: selectedKey, payload: stock }), "Stock balance saved"); }}>
        <div style={{ ...labelStyle, marginBottom: 9 }}>Set stock balance</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 9 }}>
          <Field label="Spare"><select value={selectedKey} onChange={e => setSelectedKey(e.target.value)} style={inputStyle}>{spares.map(item => <option key={item.part_key} value={item.part_key}>{item.name}</option>)}</select></Field>
          <Field label="On hand"><input type="number" min="0" step="0.01" value={stock.on_hand_qty} onChange={e => setStock(s => ({ ...s, on_hand_qty: Number(e.target.value) }))} style={inputStyle} /></Field>
          <Field label="Location"><input value={stock.location} onChange={e => setStock(s => ({ ...s, location: e.target.value }))} style={inputStyle} /></Field>
          <Field label="Storekeeper"><input value={stock.actor} onChange={e => setStock(s => ({ ...s, actor: e.target.value }))} style={inputStyle} /></Field>
        </div>
        <label style={{ color: "#d1d5db", fontSize: 11, display: "block", marginTop: 10 }}><input type="checkbox" checked={stock.verified} onChange={e => setStock(s => ({ ...s, verified: e.target.checked }))} /> Count verified</label>
        <button disabled={busy || !selected} style={{ ...buttonStyle, marginTop: 10 }}>Save balance</button>
      </form>
    </div>
    <div style={{ borderTop: "1px solid #1f2937", marginTop: 16 }}>
      {spares.map(item => <div key={item.part_key} style={{ display: "grid", gridTemplateColumns: "minmax(180px, 1fr) repeat(3, 90px)", gap: 8, padding: "9px 0", borderBottom: "1px solid #1f2937", fontSize: 11 }}>
        <b>{item.name}</b><span>{item.on_hand_qty} on hand</span><span>{item.reserved_qty} reserved</span><span style={{ color: item.below_reorder ? "#ef4444" : "#22c55e" }}>{item.below_reorder ? "Reorder" : "Stocked"}</span>
      </div>)}
      {!spares.length && <div style={{ color: "#6b7280", fontSize: 11, paddingTop: 12 }}>No spare parts commissioned</div>}
    </div>
  </div>;
}

export function MaintenancePanel({ data, onAction }) {
  const [tab, setTab] = useState("plans");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const summary = data?.summary ?? {};
  const run = async (action, success) => {
    setBusy(true); setMessage("");
    try { const result = await action(); setMessage(success); return result; }
    catch (error) { setMessage(error.message || "Action failed"); return null; }
    finally { setBusy(false); }
  };
  const tabs = useMemo(() => [
    ["plans", `Plans (${summary.plans ?? 0})`],
    ["work", `Work orders (${summary.open_work_orders ?? 0})`],
    ["spares", `Spares (${data?.spares?.length ?? 0})`],
  ], [summary.plans, summary.open_work_orders, data?.spares?.length]);

  return <section style={{ borderTop: "1px solid #374151", borderBottom: "1px solid #374151", padding: "16px 0", marginBottom: 18 }}>
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
      <div><div style={{ fontSize: 15, fontWeight: 800 }}>Preventive maintenance</div><div style={{ color: "#6b7280", fontSize: 10, marginTop: 3 }}>Status: {data?.status?.replaceAll("_", " ") ?? "loading"}</div></div>
      <button type="button" disabled={busy} onClick={() => run(() => onAction("maintenanceSync", {}), "Maintenance triggers synchronized")} style={buttonStyle}>Sync triggers</button>
    </div>
    <div style={{ display: "flex", gap: 18, margin: "14px 0", flexWrap: "wrap" }}>
      <Metric label="Overdue" value={summary.overdue_plans ?? 0} color="#ef4444" />
      <Metric label="Due soon" value={summary.due_soon_plans ?? 0} color="#f59e0b" />
      <Metric label="Unverified" value={summary.unverified_plans ?? 0} color="#a78bfa" />
      <Metric label="Spare shortages" value={summary.spare_shortages ?? 0} color="#ef4444" />
    </div>
    <div role="tablist" style={{ display: "flex", borderBottom: "1px solid #1f2937", marginBottom: 14 }}>
      {tabs.map(([key, label]) => <button key={key} type="button" role="tab" aria-selected={tab === key} onClick={() => setTab(key)} style={{ ...buttonStyle, border: 0, borderRadius: 0, background: "transparent", borderBottom: tab === key ? "2px solid #60a5fa" : "2px solid transparent", color: tab === key ? "#f9fafb" : "#6b7280" }}>{label}</button>)}
    </div>
    {message && <div style={{ color: message.toLowerCase().includes("fail") ? "#ef4444" : "#22c55e", fontSize: 11, marginBottom: 10 }}>{message}</div>}
    {tab === "plans" && <PlansView plans={data?.plans ?? []} onAction={onAction} run={run} busy={busy} />}
    {tab === "work" && <WorkOrdersView workOrders={data?.work_orders ?? []} onAction={onAction} run={run} busy={busy} />}
    {tab === "spares" && <SparesView spares={data?.spares ?? []} onAction={onAction} run={run} busy={busy} />}
    <style>{`@media (max-width: 760px) { .maintenance-split { grid-template-columns: 1fr !important; } }`}</style>
  </section>;
}
