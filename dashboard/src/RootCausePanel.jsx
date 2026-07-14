import { useMemo, useState } from "react";
import { AlertTriangle, Check, ChevronDown, ChevronUp, RefreshCw, RotateCcw, SearchCheck, X, XCircle } from "lucide-react";

const button = { display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6,
  background: "#1f2937", border: "1px solid #374151", color: "#e5e7eb", borderRadius: 5,
  padding: "7px 10px", fontSize: 11, fontWeight: 700, cursor: "pointer" };
const input = { width: "100%", minWidth: 0, background: "#0d1117", border: "1px solid #374151",
  color: "#e5e7eb", borderRadius: 5, padding: "7px 8px", fontSize: 11 };
const label = { color: "#6b7280", fontSize: 9, fontWeight: 800, textTransform: "uppercase" };

function tone(status) {
  if (status === "confirmed") return "#22c55e";
  if (status === "dismissed") return "#6b7280";
  return "#f59e0b";
}

function Field({ name, children }) {
  return <label style={{ display: "flex", flexDirection: "column", gap: 5, minWidth: 0 }}>
    <span style={label}>{name}</span>{children}
  </label>;
}

function EvidenceList({ title, values, color = "#9ca3af" }) {
  if (!values?.length) return null;
  return <div><div style={label}>{title}</div><div style={{ display: "grid", gap: 4, marginTop: 5 }}>
    {values.map((value, index) => <div key={`${title}-${index}`} style={{ color, fontSize: 10, lineHeight: 1.45 }}>
      {typeof value === "string" ? value : value.text}
      {typeof value === "object" && <span style={{ color: "#4b5563" }}> · {value.source}</span>}
    </div>)}
  </div></div>;
}

function Hypothesis({ item, selected, onSelect }) {
  const score = Math.round(item.evidence_score * 100);
  return <button onClick={() => onSelect(item.cause_code)} style={{ width: "100%", textAlign: "left",
    border: selected ? "1px solid #3b82f6" : "1px solid #263244", borderRadius: 6,
    background: selected ? "#172554" : "#0d1117", color: "#e5e7eb", padding: 10, cursor: "pointer" }}>
    <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "center" }}>
      <div><span style={{ color: "#60a5fa", fontSize: 9, fontWeight: 900 }}>#{item.rank}</span>
        <span style={{ fontSize: 11, fontWeight: 800, marginLeft: 7 }}>{item.label}</span></div>
      <span style={{ color: score >= 70 ? "#22c55e" : score >= 42 ? "#f59e0b" : "#9ca3af",
        fontSize: 11, fontWeight: 800 }}>{score}%</span>
    </div>
    <div style={{ height: 3, background: "#1f2937", marginTop: 7 }}><div style={{ width: `${score}%`, height: "100%",
      background: selected ? "#3b82f6" : "#4b5563" }} /></div>
  </button>;
}

function DiagnosticCase({ item, catalog, onDecision }) {
  const initialCause = item.actual_cause_code || item.top_hypothesis_code || "";
  const [expanded, setExpanded] = useState(item.status === "open");
  const [draft, setDraft] = useState({ actor: "", actual_cause_code: initialCause, corrective_action: "", notes: "" });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const selected = item.hypotheses.find(value => value.cause_code === draft.actual_cause_code) || item.hypotheses[0];
  const set = (key, value) => setDraft(current => ({ ...current, [key]: value }));
  const decide = async action => {
    setBusy(true); setMessage("");
    try {
      await onDecision(item.id, { action, actor: draft.actor, expected_version: item.version,
        actual_cause_code: action === "confirm" ? draft.actual_cause_code : undefined,
        corrective_action: action === "confirm" ? draft.corrective_action || undefined : undefined,
        notes: draft.notes || undefined });
    } catch (error) { setMessage(error.message); } finally { setBusy(false); }
  };
  return <article style={{ border: "1px solid #263244", borderRadius: 7, padding: 14, background: "#111827" }}>
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start" }}>
      <div style={{ minWidth: 0 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ color: tone(item.status), fontSize: 9, fontWeight: 900, textTransform: "uppercase" }}>{item.status}</span>
          <span style={{ color: "#4b5563", fontSize: 9, fontWeight: 800, textTransform: "uppercase" }}>{item.incident_type}</span>
          <span style={{ color: "#6b7280", fontSize: 9 }}>{item.machine_name || "Factory"}</span>
          <span style={{ color: "#6b7280", fontSize: 9 }}>{new Date(item.occurred_at).toLocaleString()}</span>
        </div>
        <h3 style={{ fontSize: 14, color: "#f3f4f6", marginTop: 5 }}>{item.symptom_label}</h3>
        <div style={{ color: "#9ca3af", fontSize: 10, marginTop: 4 }}>
          {item.status === "confirmed" ? `Confirmed: ${catalog[item.actual_cause_code]?.label || item.actual_cause_code}`
            : `${item.confidence} confidence · analysis v${item.analysis_version}`}
        </div>
      </div>
      <button onClick={() => setExpanded(value => !value)} title={expanded ? "Collapse case" : "Expand case"}
        style={{ ...button, width: 32, padding: 0, flex: "0 0 32px" }}>
        {expanded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
      </button>
    </div>
    {expanded && <div style={{ borderTop: "1px solid #263244", marginTop: 12, paddingTop: 12 }}>
      <div className="root-cause-layout" style={{ display: "grid", gridTemplateColumns: "minmax(220px,.8fr) minmax(280px,1.2fr)", gap: 14 }}>
        <div style={{ display: "grid", alignContent: "start", gap: 7 }}>
          {item.hypotheses.map(hypothesis => <Hypothesis key={hypothesis.cause_code} item={hypothesis}
            selected={hypothesis.cause_code === selected?.cause_code} onSelect={value => set("actual_cause_code", value)} />)}
        </div>
        <div style={{ borderLeft: "1px solid #263244", paddingLeft: 14, minWidth: 0 }}>
          {selected && <>
            <div style={{ color: "#f3f4f6", fontSize: 13, fontWeight: 800 }}>{selected.label}</div>
            <div style={{ color: "#6b7280", fontSize: 9, marginTop: 3 }}>Engineering prior {Math.round(selected.prior_score * 100)}% · {selected.domain}</div>
            <div className="root-cause-evidence" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginTop: 12 }}>
              <EvidenceList title="Supporting evidence" values={selected.evidence} color="#d1d5db" />
              <EvidenceList title="Contradictions" values={selected.contradictions} color="#fca5a5" />
            </div>
            <div style={{ marginTop: 12 }}><EvidenceList title="Data gaps" values={selected.data_gaps} color="#fbbf24" /></div>
          </>}
        </div>
      </div>
      <div style={{ borderTop: "1px solid #263244", marginTop: 13, paddingTop: 12 }}>
        <div className="root-cause-form" style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1.4fr 1.4fr", gap: 8 }}>
          <Field name="Reviewer"><input value={draft.actor} onChange={event => set("actor", event.target.value)} placeholder="Full name" style={input} /></Field>
          {item.status === "open" && <Field name="Actual cause"><select value={draft.actual_cause_code} onChange={event => set("actual_cause_code", event.target.value)} style={input}>
            {Object.entries(catalog).filter(([code]) => code !== "unknown").map(([code, value]) => <option key={code} value={code}>{value.label}</option>)}
          </select></Field>}
          {item.status === "open" && <Field name="Corrective action"><input value={draft.corrective_action} onChange={event => set("corrective_action", event.target.value)} style={input} /></Field>}
          <Field name={item.status === "open" ? "Review note" : "Reopen note"}><input value={draft.notes} onChange={event => set("notes", event.target.value)} style={input} /></Field>
        </div>
        <div style={{ display: "flex", gap: 7, marginTop: 9, flexWrap: "wrap" }}>
          {item.status === "open" ? <>
            <button disabled={busy || !draft.actor || !draft.actual_cause_code} onClick={() => decide("confirm")}
              style={{ ...button, background: "#166534", borderColor: "#22c55e",
                opacity: busy || !draft.actor || !draft.actual_cause_code ? .45 : 1,
                cursor: busy || !draft.actor || !draft.actual_cause_code ? "not-allowed" : "pointer" }}><Check size={14} /> Confirm cause</button>
            <button disabled={busy || !draft.actor || !draft.notes} onClick={() => decide("dismiss")}
              style={{ ...button, opacity: busy || !draft.actor || !draft.notes ? .45 : 1,
                cursor: busy || !draft.actor || !draft.notes ? "not-allowed" : "pointer" }}>
              <XCircle size={14} /> Dismiss</button>
          </> : <button disabled={busy || !draft.actor} onClick={() => decide("reopen")}
            style={{ ...button, opacity: busy || !draft.actor ? .45 : 1,
              cursor: busy || !draft.actor ? "not-allowed" : "pointer" }}>
            <RotateCcw size={14} /> Reopen</button>}
        </div>
        {message && <div style={{ color: "#f87171", fontSize: 10, marginTop: 9 }}>{message}</div>}
      </div>
    </div>}
  </article>;
}

export function RootCausePanel({ data, onSync, onDecision, onClose }) {
  const [tab, setTab] = useState("open");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const catalog = useMemo(() => Object.fromEntries(data.cause_catalog.map(item => [item.code, item])), [data.cause_catalog]);
  const rows = data.cases.filter(item => item.status === tab);
  const sync = async () => {
    setBusy(true); setMessage("");
    try { await onSync(); setMessage("Incident evidence synchronized"); }
    catch (error) { setMessage(error.message); } finally { setBusy(false); }
  };
  const tabs = [["open", `Open ${data.summary.open}`], ["confirmed", `Confirmed ${data.summary.confirmed}`],
    ["dismissed", `Dismissed ${data.summary.dismissed}`]];
  return <div style={{ position: "fixed", inset: 0, background: "rgba(2,6,23,.86)", zIndex: 1000,
    display: "flex", alignItems: "center", justifyContent: "center", padding: 18 }}>
    <div style={{ width: "min(1180px,96vw)", maxHeight: "92vh", overflow: "hidden", background: "#0d1117",
      border: "1px solid #374151", borderRadius: 8, color: "#f9fafb", display: "flex", flexDirection: "column" }}>
      <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 14,
        borderBottom: "1px solid #263244", padding: "14px 16px" }}>
        <div><div style={{ display: "flex", alignItems: "center", gap: 8 }}><SearchCheck size={18} color="#60a5fa" />
          <h2 style={{ fontSize: 16 }}>Root-cause diagnostics</h2></div>
          <div style={{ color: "#6b7280", fontSize: 10, marginTop: 4 }}>{data.summary.high_confidence_open} high-confidence open · {data.summary.confirmed} confirmed</div></div>
        <div style={{ display: "flex", gap: 7 }}><button onClick={sync} disabled={busy} title="Synchronize incidents" style={button}>
          <RefreshCw size={14} /> Sync incidents</button><button onClick={onClose} title="Close" style={{ ...button, width: 32, padding: 0 }}><X size={16} /></button></div>
      </header>
      <nav style={{ display: "flex", gap: 4, padding: "10px 16px 0" }}>{tabs.map(([key, text]) => <button key={key} onClick={() => setTab(key)}
        style={{ ...button, background: tab === key ? "#172554" : "transparent", borderColor: tab === key ? "#3b82f6" : "transparent" }}>{text}</button>)}</nav>
      <main style={{ overflowY: "auto", padding: 16 }}>
        <div style={{ display: "grid", gap: 10 }}>{rows.map(item => <DiagnosticCase key={item.id} item={item} catalog={catalog} onDecision={onDecision} />)}
          {!rows.length && <div style={{ color: "#6b7280", fontSize: 11, borderTop: "1px solid #263244", paddingTop: 18 }}>No diagnostic cases in this view</div>}
        </div>
        {message && <div style={{ display: "flex", alignItems: "center", gap: 6, color: message.includes("synchronized") ? "#22c55e" : "#f87171", fontSize: 10, marginTop: 10 }}>
          {!message.includes("synchronized") && <AlertTriangle size={12} />}{message}</div>}
      </main>
      <footer style={{ color: "#6b7280", fontSize: 9, borderTop: "1px solid #263244", padding: "9px 16px" }}>{data.guardrail}</footer>
    </div>
    <style>{`@media(max-width:760px){.root-cause-layout,.root-cause-form,.root-cause-evidence{grid-template-columns:1fr!important}.root-cause-layout>div:last-child{border-left:0!important;border-top:1px solid #263244;padding-left:0!important;padding-top:12px}}`}</style>
  </div>;
}
