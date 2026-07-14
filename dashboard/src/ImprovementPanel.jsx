import { useMemo, useState } from "react";
import { Check, ClipboardCheck, Clock3, FlaskConical, Play, RefreshCw, X, XCircle } from "lucide-react";

const button = { display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6,
  background: "#1f2937", border: "1px solid #374151", color: "#e5e7eb", borderRadius: 5,
  padding: "7px 10px", fontSize: 11, fontWeight: 700, cursor: "pointer" };
const input = { width: "100%", minWidth: 0, background: "#0d1117", border: "1px solid #374151",
  color: "#e5e7eb", borderRadius: 5, padding: "7px 8px", fontSize: 11 };
const label = { color: "#6b7280", fontSize: 9, fontWeight: 800, textTransform: "uppercase" };
const ACTIVE = new Set(["accepted", "evaluating"]);
const CLOSED = new Set(["validated", "promising", "ineffective", "inconclusive", "completed", "rejected", "cancelled"]);

function Field({ name, children }) {
  return <label style={{ display: "flex", flexDirection: "column", gap: 5, minWidth: 0 }}>
    <span style={label}>{name}</span>{children}
  </label>;
}

function tone(status) {
  if (status === "validated" || status === "completed") return "#22c55e";
  if (status === "ineffective" || status === "rejected" || status === "cancelled") return "#f87171";
  if (status === "promising" || status === "evaluating") return "#f59e0b";
  return "#60a5fa";
}

function MetricResult({ experiment }) {
  if (!experiment) return null;
  const baseline = experiment.baseline;
  const evaluation = experiment.evaluation;
  return <div className="improvement-result" style={{ display: "grid", gridTemplateColumns: "repeat(4,minmax(0,1fr))",
    gap: 8, marginTop: 10, borderTop: "1px solid #263244", paddingTop: 10 }}>
    <div><div style={label}>Baseline</div><div style={{ fontSize: 13, fontWeight: 800, marginTop: 4 }}>
      {baseline?.value ?? "Pending"} {baseline?.unit ?? ""}</div><div style={{ color: "#6b7280", fontSize: 9 }}>{baseline?.sample_count ?? 0} samples</div></div>
    <div><div style={label}>Evaluation</div><div style={{ fontSize: 13, fontWeight: 800, marginTop: 4 }}>
      {evaluation?.value ?? "Pending"} {evaluation?.unit ?? ""}</div><div style={{ color: "#6b7280", fontSize: 9 }}>{evaluation?.sample_count ?? 0} samples</div></div>
    <div><div style={label}>Effect</div><div style={{ color: experiment.effect_pct == null ? "#9ca3af" : experiment.effect_pct >= 0 ? "#22c55e" : "#f87171",
      fontSize: 13, fontWeight: 800, marginTop: 4 }}>{experiment.effect_pct == null ? "Pending" : `${experiment.effect_pct}%`}</div>
      <div style={{ color: "#6b7280", fontSize: 9 }}>target {experiment.target_delta_pct}%</div></div>
    <div><div style={label}>90% interval</div><div style={{ fontSize: 13, fontWeight: 800, marginTop: 4 }}>
      {experiment.ci_lower_pct == null ? "Pending" : `${experiment.ci_lower_pct}% to ${experiment.ci_upper_pct}%`}</div>
      <div style={{ color: "#6b7280", fontSize: 9 }}>{experiment.guardrails?.filter(item => item.status === "fail").length ?? 0} guardrail failures</div></div>
  </div>;
}

function Recommendation({ item, metrics, onAction }) {
  const defaults = { owner: item.owner || "operator", primary_metric: item.metric_hint || "throughput_per_hour",
    target_direction: item.target_direction || "increase", target_delta_pct: 5, baseline_hours: 8,
    evaluation_hours: 8, min_samples: 4, hypothesis: "", notes: "" };
  const [draft, setDraft] = useState(defaults);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const experiment = item.latest_experiment;
  const set = (key, value) => setDraft(current => ({ ...current, [key]: value }));
  const run = async (action, extra = {}) => {
    setBusy(true); setMessage("");
    try {
      await onAction(item.id, { action, actor: draft.owner || "operator", expected_version: item.version,
        notes: draft.notes || undefined, ...extra });
    } catch (error) { setMessage(error.message); } finally { setBusy(false); }
  };
  const accept = () => run("accept", item.experiment_eligible ? {
    owner: draft.owner, primary_metric: draft.primary_metric, target_direction: draft.target_direction,
    target_delta_pct: Number(draft.target_delta_pct), baseline_hours: Number(draft.baseline_hours),
    evaluation_hours: Number(draft.evaluation_hours), min_samples: Number(draft.min_samples),
    hypothesis: draft.hypothesis || undefined,
  } : { owner: draft.owner });
  const canAccept = item.status === "proposed" || CLOSED.has(item.status);

  return <article style={{ border: "1px solid #263244", borderRadius: 7, padding: 14, background: "#111827" }}>
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start" }}>
      <div style={{ minWidth: 0 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ color: tone(item.status), fontSize: 9, fontWeight: 900, textTransform: "uppercase" }}>{item.status}</span>
          <span style={{ color: "#4b5563", fontSize: 9, fontWeight: 800, textTransform: "uppercase" }}>{item.category}</span>
          <span style={{ color: "#6b7280", fontSize: 9 }}>{item.target_key}</span>
          {item.owner && <span style={{ color: "#6b7280", fontSize: 9 }}>owner {item.owner}</span>}
        </div>
        <h3 style={{ fontSize: 14, color: "#f3f4f6", marginTop: 5 }}>{item.title}</h3>
        <div style={{ color: "#9ca3af", fontSize: 11, lineHeight: 1.5, marginTop: 4 }}>{item.action}</div>
      </div>
      <div style={{ color: "#6b7280", fontSize: 9, whiteSpace: "nowrap" }}>{item.confidence} confidence</div>
    </div>
    {!!item.evidence?.length && <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 9 }}>
      {item.evidence.slice(0, 3).map(value => <span key={value} style={{ background: "#0d1117", border: "1px solid #263244",
        borderRadius: 4, color: "#9ca3af", fontSize: 9, padding: "4px 6px" }}>{value}</span>)}
    </div>}

    {canAccept && <div style={{ borderTop: "1px solid #263244", marginTop: 12, paddingTop: 12 }}>
      <div className="improvement-form" style={{ display: "grid", gridTemplateColumns: item.experiment_eligible ? "1.1fr 1.2fr .7fr .7fr .7fr .7fr" : "1fr 2fr", gap: 8 }}>
        <Field name="Owner"><input value={draft.owner} onChange={event => set("owner", event.target.value)} style={input} /></Field>
        {item.experiment_eligible && <>
          <Field name="Metric"><select value={draft.primary_metric} onChange={event => {
            const metric = event.target.value; setDraft(current => ({ ...current, primary_metric: metric,
              target_direction: metrics[metric]?.direction || current.target_direction }));
          }} style={input}>{Object.entries(metrics).map(([key, value]) => <option key={key} value={key}>{value.label}</option>)}</select></Field>
          <Field name="Target %"><input type="number" min="0" value={draft.target_delta_pct} onChange={event => set("target_delta_pct", event.target.value)} style={input} /></Field>
          <Field name="Baseline h"><input type="number" min="1" value={draft.baseline_hours} onChange={event => set("baseline_hours", event.target.value)} style={input} /></Field>
          <Field name="Evaluate h"><input type="number" min="1" value={draft.evaluation_hours} onChange={event => set("evaluation_hours", event.target.value)} style={input} /></Field>
          <Field name="Min samples"><input type="number" min="2" value={draft.min_samples} onChange={event => set("min_samples", event.target.value)} style={input} /></Field>
        </>}
        <Field name={item.experiment_eligible ? "Hypothesis" : "Completion note"}><input value={item.experiment_eligible ? draft.hypothesis : draft.notes}
          onChange={event => set(item.experiment_eligible ? "hypothesis" : "notes", event.target.value)}
          placeholder={item.experiment_eligible ? "Expected operational effect" : "Optional note"} style={input} /></Field>
      </div>
      <div style={{ display: "flex", gap: 7, marginTop: 9 }}>
        <button disabled={busy || !draft.owner} onClick={accept} style={{ ...button, background: "#1d4ed8", borderColor: "#3b82f6" }}>
          <Check size={14} /> {item.status === "proposed" ? "Accept" : "Repeat"}</button>
        {item.status === "proposed" && <button disabled={busy} onClick={() => run("reject")} style={button}><XCircle size={14} /> Reject</button>}
      </div>
    </div>}

    {ACTIVE.has(item.status) && <div style={{ borderTop: "1px solid #263244", marginTop: 12, paddingTop: 12 }}>
      {experiment && <div style={{ color: "#d1d5db", fontSize: 11 }}>{experiment.hypothesis}</div>}
      <MetricResult experiment={experiment} />
      {experiment?.evaluation_due_at && <div style={{ display: "flex", alignItems: "center", gap: 6, color: "#6b7280", fontSize: 9, marginTop: 9 }}>
        <Clock3 size={12} /> Evaluation window closes {new Date(experiment.evaluation_due_at).toLocaleString()}
      </div>}
      <div style={{ display: "flex", gap: 7, marginTop: 10 }}>
        {item.status === "accepted" && experiment && <button disabled={busy} onClick={() => run("implement")}
          style={{ ...button, background: "#1d4ed8", borderColor: "#3b82f6" }}><Play size={14} /> Implement</button>}
        {item.status === "accepted" && !experiment && <button disabled={busy} onClick={() => run("complete")}
          style={{ ...button, background: "#166534", borderColor: "#22c55e" }}><ClipboardCheck size={14} /> Complete</button>}
        {item.status === "evaluating" && <button disabled={busy || !experiment?.evaluation_ready} onClick={() => run("evaluate")}
          title={experiment?.evaluation_ready ? "Evaluate experiment" : "Evaluation window is still open"}
          style={{ ...button, background: experiment?.evaluation_ready ? "#166534" : "#1f2937", opacity: experiment?.evaluation_ready ? 1 : .55 }}>
          <FlaskConical size={14} /> Evaluate</button>}
        <button disabled={busy} onClick={() => run("cancel")} style={button}><XCircle size={14} /> Cancel</button>
      </div>
    </div>}
    {CLOSED.has(item.status) && experiment && <MetricResult experiment={experiment} />}
    {message && <div style={{ color: "#f87171", fontSize: 10, marginTop: 9 }}>{message}</div>}
  </article>;
}

export function ImprovementPanel({ data, onSync, onAction, onClose }) {
  const [tab, setTab] = useState("proposed");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const rows = useMemo(() => {
    if (tab === "proposed") return data.recommendations.filter(item => item.status === "proposed");
    if (tab === "active") return data.recommendations.filter(item => ACTIVE.has(item.status));
    if (tab === "closed") return data.recommendations.filter(item => CLOSED.has(item.status));
    return [];
  }, [data.recommendations, tab]);
  const sync = async () => { setBusy(true); setMessage(""); try { await onSync(); setMessage("Priorities synchronized"); }
    catch (error) { setMessage(error.message); } finally { setBusy(false); } };
  const tabs = [["proposed", `Proposed ${data.summary.proposed}`], ["active", `Active ${data.summary.active}`],
    ["closed", `Closed ${data.summary.completed}`], ["learned", `Learned ${data.learned_patterns.length}`]];
  return <div style={{ position: "fixed", inset: 0, background: "rgba(2,6,23,.86)", zIndex: 1000,
    display: "flex", alignItems: "center", justifyContent: "center", padding: 18 }}>
    <div style={{ width: "min(1180px,96vw)", maxHeight: "92vh", overflow: "hidden", background: "#0d1117",
      border: "1px solid #374151", borderRadius: 8, color: "#f9fafb", display: "flex", flexDirection: "column" }}>
      <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 14,
        borderBottom: "1px solid #263244", padding: "14px 16px" }}>
        <div><div style={{ display: "flex", alignItems: "center", gap: 8 }}><FlaskConical size={18} color="#60a5fa" />
          <h2 style={{ fontSize: 16 }}>Improvement learning</h2></div>
          <div style={{ color: "#6b7280", fontSize: 10, marginTop: 4 }}>{data.summary.evaluable} ready to evaluate · {data.summary.validated} validated</div></div>
        <div style={{ display: "flex", gap: 7 }}><button onClick={sync} disabled={busy} title="Synchronize current priorities" style={button}>
          <RefreshCw size={14} /> Sync priorities</button><button onClick={onClose} title="Close" style={{ ...button, width: 32, padding: 0 }}><X size={16} /></button></div>
      </header>
      <nav style={{ display: "flex", gap: 4, padding: "10px 16px 0" }}>{tabs.map(([key, text]) => <button key={key} onClick={() => setTab(key)}
        style={{ ...button, background: tab === key ? "#172554" : "transparent", borderColor: tab === key ? "#3b82f6" : "transparent" }}>{text}</button>)}</nav>
      <main style={{ overflowY: "auto", padding: 16 }}>
        {tab !== "learned" && <div style={{ display: "grid", gap: 10 }}>{rows.map(item => <Recommendation key={item.id}
          item={item} metrics={data.metrics} onAction={onAction} />)}{!rows.length && <div style={{ color: "#6b7280", fontSize: 11,
            borderTop: "1px solid #263244", paddingTop: 18 }}>No actions in this view</div>}</div>}
        {tab === "learned" && <div style={{ display: "grid", gap: 10 }}>{data.learned_patterns.map(item => <div key={item.recommendation_id}
          style={{ border: "1px solid #263244", borderRadius: 7, padding: 14, background: "#111827" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}><div><div style={{ color: item.promoted ? "#22c55e" : "#f59e0b",
            fontSize: 9, fontWeight: 900, textTransform: "uppercase" }}>{item.promoted ? "Reusable advisory" : "Collecting evidence"}</div>
            <div style={{ fontSize: 13, fontWeight: 800, marginTop: 4 }}>{item.title}</div></div>
            <div style={{ color: "#9ca3af", fontSize: 11 }}>{Math.round(item.success_rate * 100)}% validated</div></div>
          <div style={{ color: "#6b7280", fontSize: 10, marginTop: 7 }}>{item.experiment_count} decisive outcomes · {item.distinct_dates} dates · {item.cause_code}</div>
        </div>)}{!data.learned_patterns.length && <div style={{ color: "#6b7280", fontSize: 11 }}>No decisive experiment outcomes yet</div>}</div>}
        {message && <div style={{ color: message === "Priorities synchronized" ? "#22c55e" : "#f87171", fontSize: 10, marginTop: 10 }}>{message}</div>}
      </main>
      <footer style={{ color: "#6b7280", fontSize: 9, borderTop: "1px solid #263244", padding: "9px 16px" }}>{data.guardrail}</footer>
    </div>
    <style>{`@media(max-width:760px){.improvement-form,.improvement-result{grid-template-columns:1fr 1fr!important}}`}</style>
  </div>;
}
