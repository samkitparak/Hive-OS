import { useMemo, useState } from "react";
import { Activity, Link2, Plus, RefreshCw, ScanLine, ShieldCheck, Wrench } from "lucide-react";

const line = { borderTop: "1px solid #263244", padding: "10px 0" };
const label = { color: "#6b7280", fontSize: 9, fontWeight: 800, textTransform: "uppercase" };
const input = { background: "#0d1117", border: "1px solid #374151", borderRadius: 5,
  color: "#e5e7eb", padding: "7px 8px", fontSize: 11, minWidth: 0, width: "100%" };
const button = { background: "#1f2937", border: "1px solid #374151", color: "#e5e7eb",
  padding: "7px 10px", borderRadius: 5, fontSize: 11, fontWeight: 700, cursor: "pointer" };
const primary = { ...button, background: "#1d4ed8", borderColor: "#3b82f6" };

const STATUS_COLOR = {
  available: "#22c55e", allocated: "#60a5fa", in_use: "#38bdf8", service_due: "#f59e0b",
  in_service: "#a78bfa", expired: "#f87171", broken: "#ef4444", retired: "#6b7280",
};

function Field({ title, children }) {
  return <div><div style={label}>{title}</div>{children}</div>;
}

function RegisterTool({ pools, actor, onRun }) {
  const [form, setForm] = useState({ tool_key: "", name: "", tool_type: "",
    pool_key: pools[0]?.pool_key ?? "", life_basis: "cycles", rated_life: "",
    warning_remaining: "", manufacturer: "", serial_number: "", verified: false });
  const set = (key, value) => setForm(current => ({ ...current, [key]: value }));
  const submit = event => {
    event.preventDefault();
    onRun("toolAsset", { ...form, rated_life: form.rated_life ? Number(form.rated_life) : undefined,
      warning_remaining: form.warning_remaining ? Number(form.warning_remaining) : undefined,
      manufacturer: form.manufacturer || undefined, serial_number: form.serial_number || undefined, actor });
  };
  return <form onSubmit={submit} style={{ ...line, marginTop: 10 }}>
    <div style={{ ...label, marginBottom: 8 }}>Register tool</div>
    <div className="tool-register-grid" style={{ display: "grid", gridTemplateColumns: "130px minmax(150px,1fr) minmax(140px,1fr) minmax(150px,1fr)", gap: 8 }}>
      <Field title="Tool key"><input required value={form.tool_key} onChange={event => set("tool_key", event.target.value)} style={input} /></Field>
      <Field title="Name"><input required value={form.name} onChange={event => set("name", event.target.value)} style={input} /></Field>
      <Field title="Type"><input required value={form.tool_type} onChange={event => set("tool_type", event.target.value)} style={input} /></Field>
      <Field title="Pool"><select value={form.pool_key} onChange={event => set("pool_key", event.target.value)} style={input}>{pools.map(pool => <option key={pool.pool_key} value={pool.pool_key}>{pool.name}</option>)}</select></Field>
      <Field title="Life basis"><select value={form.life_basis} onChange={event => set("life_basis", event.target.value)} style={input}><option value="cycles">Cycles</option><option value="parts">Parts</option><option value="runtime_minutes">Runtime minutes</option></select></Field>
      <Field title="Rated life"><input type="number" min="0.001" step="any" value={form.rated_life} onChange={event => set("rated_life", event.target.value)} style={input} /></Field>
      <Field title="Warning remaining"><input type="number" min="0" step="any" value={form.warning_remaining} onChange={event => set("warning_remaining", event.target.value)} style={input} /></Field>
      <Field title="Manufacturer"><input value={form.manufacturer} onChange={event => set("manufacturer", event.target.value)} style={input} /></Field>
      <Field title="Serial"><input value={form.serial_number} onChange={event => set("serial_number", event.target.value)} style={input} /></Field>
      <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 10, color: form.verified ? "#86efac" : "#9ca3af" }}><input type="checkbox" checked={form.verified} onChange={event => set("verified", event.target.checked)} /> Verified</label>
      <button disabled={!form.tool_key || !form.name || !form.tool_type || !form.pool_key} style={{ ...primary, display: "inline-flex", gap: 6, alignItems: "center", justifyContent: "center" }}><Plus size={13} /> Register</button>
    </div>
  </form>;
}

function ToolControls({ tool, machines, actor, onRun }) {
  const [machine, setMachine] = useState(tool.machine_key ?? machines[0]?.machine_key ?? "");
  const [pocket, setPocket] = useState(tool.pocket ?? "");
  const [quantity, setQuantity] = useState(1);
  const [action, setAction] = useState(tool.machine_key ? "remove" : "install");
  const [service, setService] = useState("recondition");
  const [reason, setReason] = useState("scheduled");
  const [cncFile, setCncFile] = useState("");
  const [mappingVerified, setMappingVerified] = useState(true);
  return <div style={{ ...line, background: "#101722", padding: 12 }}>
    <div className="tool-action-grid" style={{ display: "grid", gridTemplateColumns: "repeat(4,minmax(130px,1fr))", gap: 10 }}>
      <div>
        <div style={label}>Assignment</div>
        <select value={action} onChange={event => setAction(event.target.value)} style={{ ...input, marginTop: 5 }}><option value="install">Install</option><option value="allocate">Allocate</option><option value="remove">Remove</option><option value="service_start">Start service</option><option value="broken">Mark broken</option><option value="retire">Retire</option></select>
        {!["remove", "service_start", "broken", "retire"].includes(action) && <><select value={machine} onChange={event => setMachine(event.target.value)} style={{ ...input, marginTop: 5 }}>{machines.map(item => <option key={item.machine_key} value={item.machine_key}>{item.machine_name}</option>)}</select><input placeholder="Pocket" value={pocket} onChange={event => setPocket(event.target.value)} style={{ ...input, marginTop: 5 }} /></>}
        <button onClick={() => onRun("toolAction", { key: tool.tool_key, payload: { action, machine_key: machine || undefined, pocket: pocket || undefined, actor } })} style={{ ...button, marginTop: 6, width: "100%" }}><Wrench size={12} /> Apply</button>
      </div>
      <div>
        <div style={label}>Usage · {tool.life_basis.replaceAll("_", " ")}</div>
        <input type="number" min="0.001" step="any" value={quantity} onChange={event => setQuantity(event.target.value)} style={{ ...input, marginTop: 5 }} />
        <button onClick={() => onRun("toolUsage", { key: tool.tool_key, payload: {
          event_key: `manual:${tool.tool_key}:${Date.now()}`, actor,
          machine_key: tool.machine_key || undefined,
          [`delta_${tool.life_basis}`]: Number(quantity),
        } })} style={{ ...button, marginTop: 6, width: "100%" }}><Activity size={12} /> Record</button>
      </div>
      <div>
        <div style={label}>Service result</div>
        <select value={service} onChange={event => setService(event.target.value)} style={{ ...input, marginTop: 5 }}><option value="inspect">Inspect</option><option value="recondition">Recondition</option><option value="replace">Replace</option><option value="retire">Retire</option></select>
        <select value={reason} onChange={event => setReason(event.target.value)} style={{ ...input, marginTop: 5 }}><option value="scheduled">Scheduled</option><option value="worn">Worn</option><option value="quality">Quality</option><option value="broken">Broken</option><option value="other">Other</option></select>
        <button onClick={() => onRun("toolService", { key: tool.tool_key, payload: { action: service, end_reason: reason, actor } })} style={{ ...button, marginTop: 6, width: "100%" }}><ShieldCheck size={12} /> Complete</button>
      </div>
      <div>
        <div style={label}>CNC program mapping</div>
        <select value={machine} onChange={event => setMachine(event.target.value)} style={{ ...input, marginTop: 5 }}>{machines.map(item => <option key={item.machine_key} value={item.machine_key}>{item.machine_name}</option>)}</select>
        <input placeholder="Program file" value={cncFile} onChange={event => setCncFile(event.target.value)} style={{ ...input, marginTop: 5 }} />
        <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 9, color: mappingVerified ? "#86efac" : "#9ca3af", marginTop: 6 }}><input type="checkbox" checked={mappingVerified} onChange={event => setMappingVerified(event.target.checked)} /> Verified mapping</label>
        <button disabled={!machine || !cncFile} onClick={() => onRun("toolMapping", { key: tool.tool_key, payload: { machine_key: machine, cnc_file: cncFile, verified: mappingVerified, actor } })} style={{ ...button, marginTop: 6, width: "100%" }}><Link2 size={12} /> Save mapping</button>
        {tool.program_mappings.map(mapping => <div key={mapping.id} style={{ color: mapping.verified ? "#86efac" : "#9ca3af", fontSize: 9, marginTop: 5, overflowWrap: "anywhere" }}>{mapping.machine_key} · {mapping.cnc_file}</div>)}
      </div>
    </div>
  </div>;
}

export function ToolingPanel({ data, pools, machines, actor, onAction }) {
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const current = useMemo(() => data.assets.find(tool => tool.tool_key === selected), [data.assets, selected]);
  const run = async (kind, payload) => {
    setError(""); setBusy(true);
    try { return await onAction(kind, payload); }
    catch (err) { setError(err.message); throw err; }
    finally { setBusy(false); }
  };
  return <div>
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
        <span style={{ fontSize: 10 }}><strong>{data.summary.registered}</strong> registered</span>
        <span style={{ fontSize: 10, color: "#86efac" }}><strong>{data.summary.usable}</strong> usable</span>
        <span style={{ fontSize: 10, color: data.summary.service_due ? "#fbbf24" : "#9ca3af" }}><strong>{data.summary.service_due}</strong> service due</span>
        <span style={{ fontSize: 10, color: data.summary.expired || data.summary.broken ? "#f87171" : "#9ca3af" }}><strong>{data.summary.expired + data.summary.broken}</strong> unavailable</span>
      </div>
      <button disabled={busy} onClick={() => run("toolSync", {})} title="Synchronize program usage and service work" style={{ ...button, display: "inline-flex", gap: 6, alignItems: "center" }}><RefreshCw size={13} /> Sync</button>
    </div>
    {error && <div style={{ color: "#f87171", fontSize: 10, marginTop: 8 }}>{error}</div>}
    <RegisterTool pools={pools} actor={actor} onRun={run} />
    <div style={{ marginTop: 12 }}>
      <div style={label}>Tool registry</div>
      {data.assets.map(tool => <div key={`${tool.tool_key}-${tool.version}`}>
        <button type="button" onClick={() => setSelected(currentKey => currentKey === tool.tool_key ? null : tool.tool_key)} style={{ ...line, width: "100%", borderLeft: 0, borderRight: 0, borderBottom: 0, background: "transparent", color: "#e5e7eb", textAlign: "left", cursor: "pointer" }}>
          <div className="tool-row" style={{ display: "grid", gridTemplateColumns: "minmax(170px,1.2fr) minmax(130px,1fr) 130px 110px", gap: 12, alignItems: "center" }}>
            <div><div style={{ fontSize: 11, fontWeight: 800 }}>{tool.name}</div><div style={{ color: "#6b7280", fontSize: 9, marginTop: 3 }}><ScanLine size={10} style={{ verticalAlign: "middle", marginRight: 4 }} />{tool.tool_key} · {tool.pool_name}</div></div>
            <div><div style={{ height: 5, background: "#253041", overflow: "hidden" }}><div style={{ height: "100%", width: `${tool.remaining_percent == null ? 0 : Math.max(0, Math.min(100, tool.remaining_percent))}%`, background: tool.remaining_percent != null && tool.remaining_percent <= 20 ? "#f59e0b" : "#22c55e" }} /></div><div style={{ color: "#9ca3af", fontSize: 9, marginTop: 4 }}>{tool.life_used} / {tool.life_limit ?? "limit pending"} {tool.life_basis.replaceAll("_", " ")}</div></div>
            <div style={{ fontSize: 9, color: "#9ca3af" }}>{tool.machine_name || tool.location || "Tool store"}{tool.pocket ? ` · pocket ${tool.pocket}` : ""}</div>
            <div style={{ color: STATUS_COLOR[tool.status] ?? "#9ca3af", fontSize: 9, fontWeight: 800, textTransform: "uppercase" }}>{tool.status.replaceAll("_", " ")}</div>
          </div>
        </button>
        {current?.tool_key === tool.tool_key && <ToolControls tool={tool} machines={machines} actor={actor} onRun={run} />}
      </div>)}
      {!data.assets.length && <div style={{ color: "#6b7280", fontSize: 10, padding: 12 }}>No individual tools registered.</div>}
    </div>
    <style>{`@media (max-width: 760px) { .tool-register-grid, .tool-action-grid { grid-template-columns: minmax(0,1fr) minmax(0,1fr) !important; } .tool-row { grid-template-columns: minmax(0,1fr) minmax(0,1fr) !important; } } @media (max-width: 480px) { .tool-register-grid, .tool-action-grid, .tool-row { grid-template-columns: minmax(0,1fr) !important; } }`}</style>
  </div>;
}
