import { useMemo, useState } from "react";

const button = { background: "#1f2937", border: "1px solid #374151", color: "#e5e7eb",
  padding: "7px 10px", borderRadius: 5, fontSize: 10, fontWeight: 800, cursor: "pointer" };
const input = { background: "#0d1117", border: "1px solid #374151", borderRadius: 5,
  color: "#e5e7eb", padding: "7px 8px", fontSize: 10, minWidth: 0, width: "100%" };
const label = { color: "#6b7280", fontSize: 9, fontWeight: 800, textTransform: "uppercase" };

const stateColor = {
  queued: "#6b7280", available: "#60a5fa", dispatched: "#c084fc",
  acknowledged: "#a78bfa", running: "#22c55e", held: "#f59e0b",
  completed: "#22c55e", cancelled: "#6b7280",
};

function JobRow({ job, actor, busy, onAction }) {
  const startDefault = Math.max(1, job.required_qty - job.completed_qty - job.in_process_qty);
  const completeDefault = Math.max(1, job.in_process_qty);
  const [quantity, setQuantity] = useState(job.state === "running" ? completeDefault : startDefault);
  const [scrap, setScrap] = useState(0);
  const run = (action, payload = {}) => onAction("execution", {
    id: job.id,
    payload: {
      action, actor, expected_version: job.version,
      idempotency_key: `ui:${job.id}:${job.version}:${action}`,
      ...payload,
    },
  });
  return <div className="execution-row" style={{ borderTop: "1px solid #263244", padding: "10px 0",
    display: "grid", gridTemplateColumns: "minmax(210px,1.5fr) minmax(130px,.8fr) 90px minmax(220px,1fr)", gap: 10, alignItems: "center" }}>
    <div style={{ minWidth: 0 }}>
      <div style={{ display: "flex", gap: 7, alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ fontSize: 11, fontWeight: 800 }}>{job.job_name} · {job.part_name}</span>
        <span style={{ color: stateColor[job.state] ?? "#9ca3af", fontSize: 9, fontWeight: 800, textTransform: "uppercase" }}>{job.state}</span>
      </div>
      <div style={{ color: "#6b7280", fontSize: 9, marginTop: 3 }}>
        {job.machine_name} · step {job.step_index} · queue {job.schedule_position}
      </div>
      {job.blocked_reason && <div style={{ color: "#f59e0b", fontSize: 9, marginTop: 3 }}>{job.blocked_reason}</div>}
      {job.held_reason && <div style={{ color: "#f59e0b", fontSize: 9, marginTop: 3 }}>{job.held_reason}</div>}
    </div>
    <div>
      <div style={label}>Quantity</div>
      <div style={{ fontSize: 11, marginTop: 5 }}>{job.completed_qty}/{job.required_qty} good · {job.in_process_qty} running · {job.scrap_qty} scrap</div>
    </div>
    <div>
      <div style={label}>{job.state === "running" ? "Good" : "Start"}</div>
      <input type="number" min="1" max={job.state === "running" ? Math.max(1, job.in_process_qty) : Math.max(1, startDefault)}
        value={quantity} onChange={event => setQuantity(Number(event.target.value))} style={input} />
      {job.state === "running" && <><div style={{ ...label, marginTop: 5 }}>Scrap</div><input type="number" min="0"
        max={Math.max(0, job.in_process_qty - quantity)} value={scrap} onChange={event => setScrap(Number(event.target.value))} style={input} /></>}
    </div>
    <div style={{ display: "flex", gap: 5, flexWrap: "wrap", justifyContent: "flex-end" }}>
      {job.state === "available" && <button disabled={busy} onClick={() => run("dispatch")} style={{ ...button, background: "#1d4ed8" }}>Dispatch</button>}
      {job.state === "dispatched" && <button disabled={busy} onClick={() => run("acknowledge", { assigned_operator: actor })} style={button}>Acknowledge</button>}
      {["dispatched", "acknowledged"].includes(job.state) && <button disabled={busy} onClick={() => run("start", { quantity })} style={{ ...button, background: "#166534" }}>Start</button>}
      {job.state === "running" && <button disabled={busy} onClick={() => run("complete", { good_qty: quantity, scrap_qty: scrap })} style={{ ...button, background: "#166534" }}>Complete</button>}
      {["available", "dispatched", "acknowledged", "running"].includes(job.state) && <button disabled={busy} onClick={() => run("hold", { notes: "Operator hold" })} style={button}>Hold</button>}
      {job.state === "held" && <button disabled={busy} onClick={() => run("resume")} style={{ ...button, background: "#1d4ed8" }}>Resume</button>}
    </div>
  </div>;
}

export function ExecutionPanel({ data, onAction }) {
  const [station, setStation] = useState("all");
  const [actor, setActor] = useState("operator");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [isError, setIsError] = useState(false);
  const jobs = useMemo(() => (data?.jobs ?? []).filter(job => station === "all" || job.machine_key === station), [data, station]);
  const run = async (kind, payload) => {
    setBusy(true);
    setMessage("");
    setIsError(false);
    try {
      await onAction(kind, payload);
      setMessage("Execution updated");
    } catch (error) {
      setIsError(true);
      setMessage(error.message || "Execution action failed");
    } finally {
      setBusy(false);
    }
  };
  if (!data) return null;
  const summary = data.summary ?? {};
  return <section style={{ borderBottom: "1px solid #263244", paddingBottom: 18, marginBottom: 16 }}>
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "end", gap: 10, flexWrap: "wrap" }}>
      <div><div style={label}>Station dispatch</div><div style={{ fontSize: 15, fontWeight: 800, marginTop: 3 }}>Factory execution</div></div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <input aria-label="Execution operator" value={actor} onChange={event => setActor(event.target.value)} style={{ ...input, width: 130 }} />
        <button disabled={busy} onClick={() => run("executionSync", {})} style={button}>Refresh queue</button>
      </div>
    </div>
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(90px,1fr))", gap: 6, margin: "12px 0" }}>
      {[["Available", summary.available, "#60a5fa"], ["Dispatched", (summary.dispatched ?? 0) + (summary.acknowledged ?? 0), "#c084fc"],
        ["Running", summary.running, "#22c55e"], ["Held", summary.held, "#f59e0b"], ["Exceptions", summary.open_exceptions, "#ef4444"]].map(([name, value, color]) =>
        <div key={name} style={{ borderTop: `2px solid ${color}`, padding: "7px 4px" }}><div style={label}>{name}</div><div style={{ color, fontSize: 18, fontWeight: 800 }}>{value ?? 0}</div></div>)}
    </div>
    <div style={{ display: "flex", gap: 5, overflowX: "auto", paddingBottom: 4 }}>
      <button onClick={() => setStation("all")} style={{ ...button, background: station === "all" ? "#1d4ed8" : "#1f2937" }}>All stations</button>
      {(data.stations ?? []).map(item => <button key={item.machine_key} onClick={() => setStation(item.machine_key)}
        style={{ ...button, whiteSpace: "nowrap", background: station === item.machine_key ? "#1d4ed8" : "#1f2937" }}>{item.machine_name}</button>)}
    </div>
    {message && <div role={isError ? "alert" : "status"} style={{ color: isError ? "#f87171" : "#22c55e", fontSize: 10, marginTop: 8 }}>{message}</div>}
    <div style={{ marginTop: 8 }}>
      {jobs.length ? jobs.slice(0, 50).map(job => <JobRow key={`${job.id}-${job.version}`} job={job} actor={actor} busy={busy} onAction={run} />)
        : <div style={{ color: "#6b7280", fontSize: 11, padding: "14px 0" }}>{data.scenario_id ? "No station work in this view." : "Approve a production schedule to generate station work."}</div>}
    </div>
    {(data.exceptions ?? []).length > 0 && <div style={{ marginTop: 12 }}>
      <div style={label}>Execution exceptions</div>
      {data.exceptions.slice(0, 10).map(item => <div key={item.id} style={{ borderTop: "1px solid #263244", padding: "8px 0", display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 180 }}><div style={{ color: "#f87171", fontSize: 10, fontWeight: 800 }}>{item.exception_type}</div><div style={{ color: "#9ca3af", fontSize: 9, marginTop: 2 }}>{item.job_name ?? "Unlinked"} · {item.details}</div></div>
        <button disabled={busy} onClick={() => run("executionException", { id: item.id, payload: { status: "corrected", actor } })} style={button}>Corrected</button>
        <button disabled={busy} onClick={() => run("executionException", { id: item.id, payload: { status: "ignored", actor } })} style={button}>Ignore</button>
      </div>)}
    </div>}
    <style>{`@media (max-width: 760px) { .execution-row { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) !important; } .execution-row > div:first-child, .execution-row > div:last-child { grid-column: 1 / -1; } .execution-row > div:last-child { justify-content: flex-start !important; } }`}</style>
  </section>;
}
