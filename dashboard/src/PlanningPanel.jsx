import { useMemo, useState } from "react";
import { Check, RefreshCw, TriangleAlert, X } from "lucide-react";
import { ResourcePanel } from "./ResourcePanel";

const panel = { background: "#111827", border: "1px solid #263244", borderRadius: 8, padding: 14 };
const label = { color: "#6b7280", fontSize: 9, fontWeight: 800, textTransform: "uppercase" };
const input = { background: "#0d1117", border: "1px solid #374151", borderRadius: 5,
  color: "#e5e7eb", padding: "7px 8px", fontSize: 11, minWidth: 0 };
const button = { background: "#1f2937", border: "1px solid #374151", color: "#e5e7eb",
  padding: "7px 10px", borderRadius: 5, fontSize: 11, fontWeight: 700, cursor: "pointer" };

const NEXT = {
  draft: ["draft", "ready", "cancelled"], ready: ["ready", "released", "hold", "draft", "cancelled"],
  released: ["released", "in_progress", "hold", "cancelled"],
  in_progress: ["in_progress", "hold", "completed", "cancelled"],
  hold: ["hold", "ready", "released", "in_progress", "draft", "cancelled"],
  completed: ["completed"], cancelled: ["cancelled", "draft"],
};

function localDue(value) {
  if (!value) return "";
  const date = new Date(value);
  const offset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function OrderRow({ order, selected, onSelect, onSave }) {
  const [due, setDue] = useState(localDue(order.due_at));
  const [priority, setPriority] = useState(order.priority);
  const [status, setStatus] = useState(order.status);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const save = async () => {
    setBusy(true); setError("");
    try {
      await onSave(order.id, { expected_version: order.version, due_at: due ? new Date(due).toISOString() : "",
        priority: Number(priority), status, actor: "planning-console" });
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  };
  const tone = order.status === "released" || order.status === "in_progress" ? "#22c55e"
    : order.status === "hold" ? "#ef4444" : order.status === "ready" ? "#60a5fa" : "#9ca3af";
  return <div style={{ borderTop: "1px solid #1f2937", padding: "10px 0" }}>
    <div className="planning-order-row" style={{ display: "grid",
      gridTemplateColumns: "minmax(170px,1.4fr) minmax(170px,1fr) 76px 112px auto", gap: 8, alignItems: "center" }}>
      <button onClick={onSelect} style={{ background: "none", border: 0, textAlign: "left", cursor: "pointer", minWidth: 0 }}>
        <div style={{ color: "#f3f4f6", fontWeight: 800, fontSize: 12, overflow: "hidden", textOverflow: "ellipsis" }}>{order.job_name}</div>
        <div style={{ color: tone, fontSize: 9, fontWeight: 800, textTransform: "uppercase", marginTop: 3 }}>
          {order.status} · {order.total_parts ?? 0} parts
        </div>
      </button>
      <input type="datetime-local" value={due} onChange={event => setDue(event.target.value)} style={input} />
      <input type="number" min="1" max="100" value={priority} onChange={event => setPriority(event.target.value)} style={input} />
      <select value={status} onChange={event => setStatus(event.target.value)} style={input}>
        {(NEXT[order.status] ?? [order.status]).map(value => <option key={value}>{value}</option>)}
      </select>
      <div style={{ display: "flex", gap: 6 }}>
        <button onClick={save} disabled={busy} style={{ ...button, background: "#1d4ed8", borderColor: "#3b82f6" }}>Save</button>
        <button onClick={onSelect} style={{ ...button, borderColor: selected ? "#60a5fa" : "#374151" }}>Route</button>
      </div>
    </div>
    <div style={{ color: "#6b7280", fontSize: 9, marginTop: 5 }}>
      Route {Math.round(order.route.coverage * 100)}% · confirmed {order.route.confirmed_steps}/{order.route.total_steps}
      {order.route.open_exceptions ? ` · ${order.route.open_exceptions} exceptions` : ""}
    </div>
    {error && <div style={{ color: "#f87171", fontSize: 10, marginTop: 5 }}>{error}</div>}
  </div>;
}

function RouteEditor({ routeData, machines, actor, onSave }) {
  const [search, setSearch] = useState("");
  const [selectedPart, setSelectedPart] = useState(null);
  const [steps, setSteps] = useState([]);
  const [message, setMessage] = useState("");
  const grouped = useMemo(() => {
    const result = new Map();
    for (const step of routeData?.steps ?? []) {
      if (!result.has(step.part_id)) result.set(step.part_id, { id: step.part_id, name: step.part_name, qty: step.qty, steps: [] });
      result.get(step.part_id).steps.push(step);
    }
    return [...result.values()].filter(part => `${part.id} ${part.name}`.toLowerCase().includes(search.toLowerCase())).slice(0, 60);
  }, [routeData, search]);
  const choose = part => { setSelectedPart(part); setSteps(part.steps.map(step => step.machine_key)); setMessage(""); };
  const save = async () => {
    if (!selectedPart || !steps.length) return;
    setMessage("");
    try { await onSave(selectedPart.id, { machine_keys: steps, actor, notes: "Route confirmed in planning console" }); setMessage("Route saved"); }
    catch (err) { setMessage(err.message); }
  };
  if (!routeData) return null;
  return <div style={panel}>
    <div style={{ display: "flex", justifyContent: "space-between", gap: 10, marginBottom: 10 }}>
      <div><div style={label}>Route plan</div><div style={{ fontSize: 13, fontWeight: 800, marginTop: 3 }}>{routeData.job_name}</div></div>
      <input placeholder="Find part" value={search} onChange={event => setSearch(event.target.value)} style={input} />
    </div>
    <div className="route-editor-grid" style={{ display: "grid", gridTemplateColumns: "minmax(180px,.8fr) minmax(260px,1.2fr)", gap: 14 }}>
      <div style={{ maxHeight: 280, overflowY: "auto", borderTop: "1px solid #1f2937" }}>
        {grouped.map(part => <button key={part.id} onClick={() => choose(part)} style={{ width: "100%", background: selectedPart?.id === part.id ? "#172554" : "none",
          border: 0, borderBottom: "1px solid #1f2937", color: "#e5e7eb", padding: "8px", textAlign: "left", cursor: "pointer" }}>
          <div style={{ fontSize: 11, fontWeight: 700 }}>{part.name}</div><div style={{ color: "#6b7280", fontSize: 9 }}>#{part.id} · qty {part.qty}</div>
        </button>)}
      </div>
      <div>
        {!selectedPart && <div style={{ color: "#6b7280", fontSize: 11 }}>Select a part to inspect its route.</div>}
        {selectedPart && <>
          {steps.map((machineKey, index) => <div key={`${index}-${machineKey}`} style={{ display: "grid", gridTemplateColumns: "24px 1fr auto", gap: 6, marginBottom: 7, alignItems: "center" }}>
            <span style={{ color: "#6b7280", fontSize: 10 }}>{index + 1}</span>
            <select value={machineKey} onChange={event => setSteps(current => current.map((value, i) => i === index ? event.target.value : value))} style={input}>
              {machines.filter(machine => !steps.includes(machine.machine_key) || machine.machine_key === machineKey).map(machine =>
                <option key={machine.machine_key} value={machine.machine_key}>{machine.name}</option>)}</select>
            <button title="Remove step" onClick={() => setSteps(current => current.filter((_, i) => i !== index))} style={button}>×</button>
          </div>)}
          <div style={{ display: "flex", gap: 7, marginTop: 10 }}>
            <button onClick={() => { const next = machines.find(machine => !steps.includes(machine.machine_key)); if (next) setSteps(current => [...current, next.machine_key]); }} style={button}>Add step</button>
            <button onClick={save} style={{ ...button, background: "#1d4ed8", borderColor: "#3b82f6" }}>Save route</button>
          </div>
          {message && <div style={{ color: message === "Route saved" ? "#22c55e" : "#f87171", fontSize: 10, marginTop: 8 }}>{message}</div>}
        </>}
      </div>
    </div>
  </div>;
}

function ScenarioView({ scenario, actor, onDecision }) {
  const [policy, setPolicy] = useState(scenario?.result?.recommendation?.policy ?? scenario?.result?.scenarios?.[0]?.policy ?? "");
  const [message, setMessage] = useState("");
  if (!scenario) return null;
  const ready = scenario.readiness.operational_recommendation;
  const decide = async decision => { setMessage(""); try { await onDecision(scenario.id, { decision, actor, selected_policy: policy || null }); setMessage(decision === "approve" ? "Schedule approved" : "Scenario rejected"); } catch (err) { setMessage(err.message); } };
  return <div style={panel}>
    <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "center", marginBottom: 10 }}>
      <div><div style={label}>Latest scenario</div><div style={{ fontSize: 13, fontWeight: 800, marginTop: 3 }}>{scenario.name || `Scenario ${scenario.id}`}</div></div>
      <div style={{ color: ready ? "#22c55e" : "#f59e0b", fontSize: 10, fontWeight: 800, textTransform: "uppercase" }}>{ready ? "Production ready" : "Commissioning only"}</div>
    </div>
    <div style={{ overflowX: "auto" }}><table style={{ width: "100%", borderCollapse: "collapse", fontSize: 10 }}><thead><tr style={{ color: "#6b7280", textAlign: "left" }}>
      <th style={{ padding: 6 }}>Policy</th><th>Feasible</th><th>Late jobs</th><th>Tardiness</th><th>Makespan</th><th>Wait</th><th>Setups</th></tr></thead><tbody>
      {(scenario.result.scenarios ?? []).map(item => <tr key={item.policy} style={{ borderTop: "1px solid #1f2937" }}>
        <td style={{ padding: 6, color: "#e5e7eb", fontWeight: 700 }}>{item.policy}</td><td style={{ color: item.feasible ? "#22c55e" : "#f87171" }}>{item.feasible ? "yes" : "no"}</td><td>{item.late_jobs}</td>
        <td>{Math.round(item.total_tardiness_s / 60)}m</td><td>{Math.round(item.makespan_s / 60)}m</td>
        <td>{Math.round(((item.capacity_wait_s ?? 0) + (item.calendar_wait_s ?? 0)) / 60)}m</td><td>{item.setup_count}</td></tr>)}</tbody></table></div>
    <div style={{ color: "#9ca3af", fontSize: 10, marginTop: 10 }}>{scenario.readiness.guardrail}</div>
    {scenario.status === "draft" && <div style={{ display: "flex", gap: 7, justifyContent: "flex-end", marginTop: 12, flexWrap: "wrap" }}>
      <select value={policy} onChange={event => setPolicy(event.target.value)} style={input}>{(scenario.result.scenarios ?? []).map(item => <option key={item.policy}>{item.policy}</option>)}</select>
      <button onClick={() => decide("reject")} style={button}>Reject</button>
      <button onClick={() => decide("approve")} disabled={!ready} title={ready ? "Approve selected schedule" : "Complete model and route commissioning first"}
        style={{ ...button, background: ready ? "#166534" : "#1f2937", borderColor: ready ? "#22c55e" : "#374151", opacity: ready ? 1 : .55 }}>Approve schedule</button>
    </div>}
    {message && <div style={{ color: message.includes("approved") ? "#22c55e" : "#f59e0b", fontSize: 10, marginTop: 8 }}>{message}</div>}
  </div>;
}

function RecoveryView({ state, actor, onAction }) {
  const latest = state?.latest;
  const recommendation = latest?.result?.recommendation;
  const scenarios = latest?.result?.scenarios ?? [];
  const triggers = state?.current?.triggers ?? [];
  const [policy, setPolicy] = useState(recommendation?.policy ?? scenarios[0]?.policy ?? "");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const actionablePolicies = scenarios.filter(item => item.feasible && item.recovery?.actionable);
  const selectedPolicy = actionablePolicies.some(item => item.policy === policy)
    ? policy : recommendation?.policy ?? actionablePolicies[0]?.policy ?? "";
  if (!state) return null;

  const analyze = async () => {
    setBusy(true); setMessage("");
    try {
      const result = await onAction("recoveryAnalyze", { payload: { actor, force: false } });
      setMessage(result.action_required ? "Recovery evidence is ready for review" : "Recovery assessment refreshed");
    } catch (err) { setMessage(err.message); } finally { setBusy(false); }
  };
  const decide = async decision => {
    if (!latest) return;
    setBusy(true); setMessage("");
    try {
      await onAction("recoveryDecision", { id: latest.id, payload: {
        decision, actor, selected_policy: decision === "approve" ? selectedPolicy : null,
        notes: "Decision recorded in planning console",
      } });
      setMessage(decision === "approve" ? "Recovery sequence approved" : "Recovery proposal rejected");
    } catch (err) { setMessage(err.message); } finally { setBusy(false); }
  };
  const tone = state.action_required ? "#ef4444" : triggers.length ? "#f59e0b" : "#22c55e";
  return <div style={{ ...panel, marginBottom: 12, borderColor: state.action_required ? "#7f1d1d" : "#263244" }}>
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
      <div>
        <div style={label}>Schedule recovery</div>
        <div style={{ display: "flex", alignItems: "center", gap: 6, color: tone, fontSize: 12, fontWeight: 800, marginTop: 4, textTransform: "uppercase" }}>
          {triggers.length > 0 && <TriangleAlert size={14} aria-hidden="true" />}
          {state.status.replaceAll("_", " ")} · {triggers.length} trigger{triggers.length === 1 ? "" : "s"}
        </div>
      </div>
      <button onClick={analyze} disabled={busy || state.current?.status === "waiting_for_schedule"}
        title="Recalculate recovery candidates from current factory state"
        style={{ ...button, display: "inline-flex", alignItems: "center", gap: 6 }}>
        <RefreshCw size={13} aria-hidden="true" />{busy ? "Working" : "Analyze"}
      </button>
    </div>
    <div style={{ color: "#9ca3af", fontSize: 10, marginTop: 8 }}>{state.guardrail}</div>
    {triggers.length > 0 && <div className="recovery-trigger-grid" style={{ display: "grid", gridTemplateColumns: "repeat(2,minmax(0,1fr))", gap: 7, marginTop: 10 }}>
      {triggers.slice(0, 6).map(item => <div key={item.key} style={{ borderTop: "1px solid #1f2937", paddingTop: 7, minWidth: 0 }}>
        <div style={{ color: item.severity === "critical" ? "#f87171" : "#fbbf24", fontSize: 10, fontWeight: 800 }}>{item.title}</div>
        <div style={{ color: "#6b7280", fontSize: 9, marginTop: 3, overflowWrap: "anywhere" }}>{item.detail}</div>
      </div>)}
    </div>}
    {scenarios.length > 0 && <div style={{ overflowX: "auto", marginTop: 10 }}><table style={{ width: "100%", minWidth: 650, borderCollapse: "collapse", fontSize: 10 }}>
      <thead><tr style={{ color: "#6b7280", textAlign: "left" }}><th style={{ padding: 6 }}>Policy</th><th>Late jobs</th><th>Tardiness</th><th>Recovered</th><th>Moved</th><th>Stability</th><th>Frozen</th></tr></thead>
      <tbody>{scenarios.map(item => <tr key={item.policy} style={{ borderTop: "1px solid #1f2937", background: item.policy === recommendation?.policy ? "#13251c" : "transparent" }}>
        <td style={{ padding: 6, color: "#e5e7eb", fontWeight: 700 }}>{item.policy}</td><td>{item.late_jobs}</td>
        <td>{Math.round(item.total_tardiness_s / 60)}m</td><td style={{ color: (item.recovery?.tardiness_reduction_s ?? 0) > 0 ? "#22c55e" : "#9ca3af" }}>{Math.round((item.recovery?.tardiness_reduction_s ?? 0) / 60)}m</td>
        <td>{item.stability?.moved_jobs ?? 0}</td><td>{Math.round((item.stability?.score ?? 0) * 100)}%</td>
        <td style={{ color: item.stability?.frozen_positions_preserved ? "#22c55e" : "#f87171" }}>{item.stability?.frozen_positions_preserved ? "preserved" : "changed"}</td>
      </tr>)}</tbody>
    </table></div>}
    {latest?.result?.recovery?.frozen_jobs?.length > 0 && <div style={{ color: "#6b7280", fontSize: 9, marginTop: 8 }}>
      Frozen: {latest.result.recovery.frozen_jobs.join(" · ")}
    </div>}
    {state.action_required && <div style={{ display: "flex", gap: 7, justifyContent: "flex-end", alignItems: "center", marginTop: 12, flexWrap: "wrap" }}>
      <select value={selectedPolicy} onChange={event => setPolicy(event.target.value)} style={input}>
        {actionablePolicies.map(item => <option key={item.policy}>{item.policy}</option>)}
      </select>
      <button onClick={() => decide("reject")} disabled={busy} style={{ ...button, display: "inline-flex", alignItems: "center", gap: 5 }}><X size={13} aria-hidden="true" />Reject</button>
      <button onClick={() => decide("approve")} disabled={busy || state.stale || !selectedPolicy || !actor.trim()} title="Approve selected recovery sequence"
        style={{ ...button, display: "inline-flex", alignItems: "center", gap: 5, background: "#166534", borderColor: "#22c55e" }}><Check size={13} aria-hidden="true" />Approve recovery</button>
    </div>}
    {message && <div style={{ color: message.includes("approved") || message.includes("ready") ? "#22c55e" : "#f59e0b", fontSize: 10, marginTop: 8 }}>{message}</div>}
  </div>;
}

export function PlanningPanel({ data, machines, onClose, onAction }) {
  const { orders = [], readiness = null, scenarios = [], activeSchedule = null, recovery = null, exceptions = [], resources = null } = data;
  const [actor, setActor] = useState("operator");
  const [selectedJobs, setSelectedJobs] = useState([]);
  const [routeData, setRouteData] = useState(null);
  const [latestScenario, setLatestScenario] = useState(null);
  const [exceptionLimit, setExceptionLimit] = useState(25);
  const [showResources, setShowResources] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const loadRoute = async order => { setError(""); try { setRouteData(await onAction("routes", { job_name: order.job_name })); } catch (err) { setError(err.message); } };
  const run = async () => { setBusy(true); setError(""); try { setLatestScenario(await onAction("scenario", { created_by: actor, job_names: selectedJobs.length ? selectedJobs : undefined,
      policies: ["current", "fifo", "edd", "spt", "material_batch"], seed: 1 })); } catch (err) { setError(err.message); } finally { setBusy(false); } };
  const eligible = orders.filter(order => !["completed", "cancelled"].includes(order.status));
  return <div style={{ position: "fixed", inset: 0, zIndex: 60, background: "rgba(0,0,0,.72)", display: "flex", justifyContent: "center", alignItems: "flex-start", padding: 18, overflowY: "auto" }}>
    <div style={{ width: "min(1180px, 100%)", background: "#0d1117", border: "1px solid #374151", borderRadius: 8, padding: 18, color: "#f9fafb" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center", marginBottom: 16 }}>
        <div><div style={{ fontSize: 17, fontWeight: 800 }}>Production planning</div><div style={{ color: "#6b7280", fontSize: 10, marginTop: 3 }}>Orders · routes · simulation · approval</div></div>
        <div style={{ display: "flex", gap: 7, flexWrap: "wrap", justifyContent: "flex-end" }}>
          <button onClick={() => setShowResources(current => !current)} style={{ ...button, borderColor: showResources ? "#60a5fa" : "#374151" }}>Resources</button>
          <input value={actor} onChange={event => setActor(event.target.value)} placeholder="Operator" style={input} /><button onClick={onClose} title="Close" style={button}>×</button></div>
      </div>
      {error && <div style={{ color: "#f87171", fontSize: 10, marginBottom: 10 }}>{error}</div>}
      {readiness && <div style={{ ...panel, marginBottom: 12 }}><div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
        <div><div style={label}>Commissioning readiness</div><div style={{ color: readiness.control_ready ? "#22c55e" : "#f59e0b", fontSize: 13, fontWeight: 800, marginTop: 4 }}>{readiness.status.replace("_", " ")}</div></div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", justifyContent: "flex-end" }}>{readiness.checks.map(check => <span key={check.key} title={check.detail} style={{ color: check.passed ? "#22c55e" : "#6b7280", fontSize: 9, fontWeight: 800 }}>{check.passed ? "✓" : "○"} {check.label}</span>)}</div>
      </div></div>}
      {showResources && resources && <div style={{ ...panel, marginBottom: 12 }}><ResourcePanel data={resources} actor={actor} onAction={onAction} /></div>}
      {activeSchedule?.items?.length > 0 && <div style={{ ...panel, borderColor: "#166534", marginBottom: 12 }}><div style={label}>Approved schedule</div>
        <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>{activeSchedule.items.map(item => <span key={item.position} style={{ fontSize: 10, color: "#d1fae5" }}>{item.position}. {item.job_name}</span>)}</div></div>}
      <RecoveryView state={recovery} actor={actor} onAction={onAction} />
      <div style={{ ...panel, marginBottom: 12 }}><div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "end" }}>
        <div><div style={label}>Production orders</div><div style={{ color: "#9ca3af", fontSize: 10, marginTop: 4 }}>{eligible.length} open orders</div></div>
        <div style={{ color: "#6b7280", fontSize: 9 }}>DUE TIME · PRIORITY · STATE</div></div>
        {orders.map(order => <OrderRow key={`${order.id}-${order.version}`} order={order} selected={routeData?.job_name === order.job_name}
          onSelect={() => loadRoute(order)} onSave={(id, payload) => onAction("order", { id, payload })} />)}
        {!orders.length && <div style={{ color: "#6b7280", fontSize: 11, padding: "14px 0" }}>No imported jobs.</div>}
      </div>
      {routeData && <div style={{ marginBottom: 12 }}><RouteEditor routeData={routeData} machines={machines.filter(machine => !["Compressor", "Dust Collector"].includes(machine.type))}
        actor={actor} onSave={async (partId, payload) => { await onAction("saveRoute", { part_id: partId, payload }); setRouteData(await onAction("routes", { job_name: routeData.job_name })); }} /></div>}
      <div className="planning-lower-grid" style={{ display: "grid", gridTemplateColumns: "minmax(300px,1fr) minmax(300px,1fr)", gap: 12 }}>
        <div style={panel}><div style={label}>Scenario input</div><div style={{ maxHeight: 180, overflowY: "auto", marginTop: 8 }}>
          {eligible.map(order => <label key={order.id} style={{ display: "flex", gap: 8, padding: "6px 0", borderBottom: "1px solid #1f2937", fontSize: 10, alignItems: "center" }}>
            <input type="checkbox" checked={selectedJobs.includes(order.job_name)} onChange={event => setSelectedJobs(current => event.target.checked ? [...current, order.job_name] : current.filter(name => name !== order.job_name))} />
            <span style={{ flex: 1 }}>{order.job_name}</span><span style={{ color: "#6b7280" }}>{order.status}</span></label>)}
        </div><button onClick={run} disabled={busy} style={{ ...button, width: "100%", marginTop: 10, background: "#1d4ed8", borderColor: "#3b82f6" }}>{busy ? "Running…" : "Compare schedules"}</button>
        <div style={{ color: "#6b7280", fontSize: 9, marginTop: 8 }}>{scenarios.length} saved scenarios</div></div>
        <ScenarioView scenario={latestScenario} actor={actor} onDecision={(id, payload) => onAction("decision", { id, payload })} />
      </div>
      {exceptions.length > 0 && <div style={{ ...panel, marginTop: 12 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
          <div style={label}>Route exceptions</div>
          <div style={{ color: "#9ca3af", fontSize: 9 }}>{Math.min(exceptionLimit, exceptions.length)} of {exceptions.length} unresolved</div>
        </div>
        {exceptions.slice(0, exceptionLimit).map(item => <div className="planning-exception-row" key={item.id} style={{ display: "grid", gridTemplateColumns: "120px minmax(240px,1fr) auto auto", gap: 8, alignItems: "center", padding: "8px 0", borderTop: "1px solid #1f2937", fontSize: 10 }}>
          <span style={{ color: "#f59e0b", fontWeight: 800 }}>{item.exception_type}</span><span style={{ overflowWrap: "anywhere" }}>{item.job_name} · {item.part_name} · expected {item.expected_machine ?? "—"}, observed {item.observed_machine ?? "—"}</span>
          <button onClick={() => onAction("exception", { id: item.id, payload: { status: "corrected", actor } })} style={button}>Corrected</button>
          <button onClick={() => onAction("exception", { id: item.id, payload: { status: "ignored", actor } })} style={button}>Ignore</button>
        </div>)}
        {exceptionLimit < exceptions.length && <button onClick={() => setExceptionLimit(current => current + 25)} style={{ ...button, width: "100%", marginTop: 8 }}>
          Show next {Math.min(25, exceptions.length - exceptionLimit)}
        </button>}
        {exceptionLimit > 25 && <button onClick={() => setExceptionLimit(25)} style={{ ...button, width: "100%", marginTop: 8 }}>Show first 25</button>}
      </div>}
      <style>{`@media (max-width: 760px) { .planning-order-row { grid-template-columns: 1fr 1fr !important; } .planning-lower-grid, .route-editor-grid, .recovery-trigger-grid { grid-template-columns: 1fr !important; } .planning-exception-row { grid-template-columns: 1fr 1fr !important; } .planning-exception-row > span:nth-child(2) { grid-column: 1 / -1; grid-row: 2; } }`}</style>
    </div>
  </div>;
}
