import { useEffect, useState } from "react";
import {
  ArrowLeft, Check, ClipboardCheck, Download, FileUp, Play, RefreshCw,
  Save, ShieldCheck, Timer, Trash2, X,
} from "lucide-react";
import {
  actOnCommissioningStudy, addCommissioningObservation, analyzeCommissioningStudy,
  createCommissioningStudy, downloadCommissioningEvidencePack,
  excludeCommissioningObservation, fetchCommissioningEvidence,
  fetchCommissioningStudy, importCommissioningEvidence,
} from "./api";

const button = {
  border: "1px solid #374151", borderRadius: 6, padding: "8px 11px",
  color: "#f9fafb", background: "#1f2937", cursor: "pointer", fontSize: 10,
  fontWeight: 700, display: "inline-flex", alignItems: "center", gap: 6,
  justifyContent: "center", minHeight: 34,
};
const input = {
  width: "100%", marginTop: 5, padding: "8px 9px", color: "#f9fafb",
  background: "#0d1117", border: "1px solid #374151", borderRadius: 5, fontSize: 10,
};
const label = { color: "#6b7280", fontSize: 9, fontWeight: 800, textTransform: "uppercase" };
const SEGMENTS = [
  ["queue_s", "Queue"], ["setup_s", "Setup"], ["load_s", "Load"],
  ["process_s", "Process"], ["blocked_s", "Blocked"], ["starved_s", "Starved"],
  ["unload_s", "Unload"], ["quality_s", "Quality"], ["rework_s", "Rework"],
];

function localDateTime() {
  const date = new Date();
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function blankObservation(family = "") {
  return {
    measured_at: localDateTime(), measurement_method: "stopwatch", observer: "",
    product_family: family, program_key: "", unit_count: 1, operator_count: 1,
    queue_s: 0, setup_s: 0, load_s: 0, process_s: "", blocked_s: 0,
    starved_s: 0, unload_s: 0, quality_s: 0, rework_s: 0,
    good_units: "", reject_units: 0, notes: "",
  };
}

function Badge({ children, color = "#93c5fd" }) {
  return <span style={{ color, background: "#111827", border: `1px solid ${color}55`, borderRadius: 4, padding: "3px 6px", fontSize: 8, fontWeight: 800, textTransform: "uppercase" }}>{children}</span>;
}

function ProtocolList({ data, busy, onCreate, onSelect, onDownload }) {
  return <div>
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
      <div><div style={{ fontSize: 13, fontWeight: 800 }}>Measurement program</div>
        <div style={{ color: "#6b7280", fontSize: 10, marginTop: 4 }}>{data.protocols.length} machine protocols · {data.summary.observations} accepted observations</div></div>
      <button onClick={onDownload} disabled={busy} style={button}><Download size={14} /> Evidence pack</button>
    </div>
    {!!data.studies.length && <section style={{ marginTop: 18 }}>
      <div style={{ fontSize: 10, fontWeight: 800, marginBottom: 7 }}>Studies</div>
      <div className="evidence-study-grid" style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 8 }}>
        {data.studies.filter(item => item.status !== "archived").map(item => <button key={item.id} onClick={() => onSelect(item.id)} style={{ ...button, display: "block", textAlign: "left", minHeight: 66 }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}><span>{item.machine_name}</span><Badge color={item.status === "review_ready" ? "#86efac" : "#93c5fd"}>{item.status.replaceAll("_", " ")}</Badge></div>
          <div style={{ color: "#6b7280", fontSize: 9, marginTop: 8 }}>{item.accepted_count}/{item.target_samples} observations</div>
        </button>)}
      </div>
    </section>}
    <section style={{ marginTop: 20 }}>
      <div style={{ fontSize: 10, fontWeight: 800, marginBottom: 6 }}>Priority protocols</div>
      {data.protocols.map((item, index) => <div key={item.machine_key} className="evidence-protocol-row" style={{ display: "grid", gridTemplateColumns: "30px minmax(150px,.65fr) minmax(260px,1.35fr) 110px", gap: 10, alignItems: "center", padding: "10px 0", borderTop: "1px solid #263244" }}>
        <span style={{ color: "#60a5fa", fontSize: 10, fontWeight: 800 }}>{item.priority_rank ?? index + 1}</span>
        <div><div style={{ color: "#d1d5db", fontSize: 10, fontWeight: 800 }}>{item.machine_name}</div>
          <div style={{ color: "#6b7280", fontSize: 9, marginTop: 3 }}>{item.prior_cycle_s.min}-{item.prior_cycle_s.mode}-{item.prior_cycle_s.max}s prior</div></div>
        <div style={{ color: "#9ca3af", fontSize: 9, lineHeight: 1.45 }}>{item.measurement_instruction}</div>
        <button onClick={() => onCreate(item.machine_key)} disabled={busy} style={button}><Play size={12} /> New study</button>
      </div>)}
    </section>
  </div>;
}

function Gates({ analysis }) {
  return <section style={{ marginTop: 18 }}>
    <div style={{ display: "flex", justifyContent: "space-between", gap: 8, marginBottom: 7 }}>
      <div style={{ fontSize: 10, fontWeight: 800 }}>Credibility gates</div>
      <Badge color={analysis.review_ready ? "#86efac" : "#fbbf24"}>{analysis.status}</Badge>
    </div>
    <div className="evidence-gates" style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", borderTop: "1px solid #263244" }}>
      {analysis.checks.map(check => <div key={check.key} style={{ padding: "9px 10px 9px 0", borderBottom: "1px solid #263244", minWidth: 0 }}>
        <div style={{ color: check.passed ? "#86efac" : "#fbbf24", fontSize: 9, fontWeight: 800, display: "flex", gap: 5, alignItems: "center" }}>
          {check.passed ? <Check size={11} /> : <X size={11} />}{check.label}
        </div>
        <div style={{ color: "#6b7280", fontSize: 8, marginTop: 4 }}>{check.detail}</div>
      </div>)}
    </div>
  </section>;
}

function Analysis({ analysis }) {
  const observed = analysis.occupancy_s_per_unit;
  const proposal = analysis.proposal;
  return <div>
    <div className="evidence-metrics" style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", border: "1px solid #263244", borderRadius: 6, overflow: "hidden", marginTop: 16 }}>
      {[
        ["Samples", analysis.sample_count, `${analysis.unit_count} units`],
        ["Median occupancy", `${observed.median.toFixed(1)} s`, `P10 ${observed.p10.toFixed(1)} · P90 ${observed.p90.toFixed(1)}`],
        ["Prior delta", analysis.prior_comparison.observed_median_vs_prior_mode_pct == null ? "-" : `${analysis.prior_comparison.observed_median_vs_prior_mode_pct > 0 ? "+" : ""}${analysis.prior_comparison.observed_median_vs_prior_mode_pct.toFixed(1)}%`, "Observed median vs mode"],
        ["Prior coverage", analysis.prior_comparison.observations_inside_prior_range_pct == null ? "-" : `${analysis.prior_comparison.observations_inside_prior_range_pct.toFixed(0)}%`, "Inside shipped range"],
      ].map(([name, value, detail]) => <div key={name} style={{ padding: "11px 12px", borderRight: "1px solid #263244" }}>
        <div style={label}>{name}</div><div style={{ fontSize: 15, fontWeight: 800, marginTop: 5 }}>{value}</div>
        <div style={{ color: "#6b7280", fontSize: 8, marginTop: 3 }}>{detail}</div>
      </div>)}
    </div>
    <Gates analysis={analysis} />
    {proposal && <section style={{ marginTop: 16, borderTop: "1px solid #263244", paddingTop: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <div><div style={{ fontSize: 10, fontWeight: 800 }}>Prior proposal</div>
          <div style={{ color: "#d1d5db", fontSize: 13, fontWeight: 800, marginTop: 5 }}>{proposal.cycle_s.min} / {proposal.cycle_s.mode} / {proposal.cycle_s.max} s</div></div>
        <div style={{ display: "flex", gap: 7, alignItems: "center" }}><ShieldCheck size={14} color="#60a5fa" /><span style={{ color: "#93c5fd", fontSize: 9 }}>Virtual prior review only · no production promotion</span></div>
      </div>
    </section>}
  </div>;
}

function ObservationForm({ families, busy, onSubmit }) {
  const [form, setForm] = useState(blankObservation(families[0]?.key));
  const set = (key, value) => setForm(previous => ({ ...previous, [key]: value }));
  const submit = async () => {
    const payload = { ...form, measured_at: new Date(form.measured_at).toISOString(), actor: "field-capture" };
    if (payload.good_units === "") delete payload.good_units;
    await onSubmit(payload);
    setForm(previous => ({ ...blankObservation(previous.product_family), observer: previous.observer, measurement_method: previous.measurement_method }));
  };
  const disabled = busy || !form.observer || !form.process_s;
  return <section style={{ marginTop: 18 }}>
    <div style={{ fontSize: 10, fontWeight: 800, marginBottom: 9 }}>Timed observation</div>
    <div className="evidence-form-primary" style={{ display: "grid", gridTemplateColumns: "1.1fr .8fr .8fr .8fr", gap: 9 }}>
      <label style={label}>Measured at<input type="datetime-local" value={form.measured_at} onChange={event => set("measured_at", event.target.value)} style={input} /></label>
      <label style={label}>Method<select value={form.measurement_method} onChange={event => set("measurement_method", event.target.value)} style={input}><option value="stopwatch">Stopwatch</option><option value="video_review">Video review</option><option value="machine_log">Machine log</option><option value="controller_counter">Controller counter</option><option value="operator_scan">Operator scan</option></select></label>
      <label style={label}>Observer<input value={form.observer} onChange={event => set("observer", event.target.value)} style={input} /></label>
      <label style={label}>Family<select value={form.product_family} onChange={event => set("product_family", event.target.value)} style={input}>{families.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}</select></label>
    </div>
    <div className="evidence-form-secondary" style={{ display: "grid", gridTemplateColumns: "1.5fr repeat(3,.6fr)", gap: 9, marginTop: 9 }}>
      <label style={label}>Program / recipe<input value={form.program_key} onChange={event => set("program_key", event.target.value)} style={input} /></label>
      <label style={label}>Units<input type="number" min="1" value={form.unit_count} onChange={event => set("unit_count", event.target.value)} style={input} /></label>
      <label style={label}>Operators<input type="number" min="1" value={form.operator_count} onChange={event => set("operator_count", event.target.value)} style={input} /></label>
      <label style={label}>Good units<input type="number" min="0" value={form.good_units} onChange={event => set("good_units", event.target.value)} style={input} /></label>
    </div>
    <div className="evidence-segments" style={{ display: "grid", gridTemplateColumns: "repeat(9,1fr)", gap: 7, marginTop: 10 }}>
      {SEGMENTS.map(([key, name]) => <label key={key} style={label}>{name}<input type="number" min="0" step="0.1" value={form[key]} onChange={event => set(key, event.target.value)} style={input} /></label>)}
    </div>
    <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 10 }}>
      <button onClick={submit} disabled={disabled} style={{ ...button, background: "#14532d", borderColor: "#22c55e", opacity: disabled ? .45 : 1 }}><Timer size={13} /> Add observation</button>
    </div>
  </section>;
}

function StudyView({ study, families, busy, onBack, onRefresh, onObserve, onImport, onAction, onExclude }) {
  const [csvState, setCsvState] = useState({ name: "", text: "", preview: null });
  const chooseCsv = async event => {
    const file = event.target.files?.[0];
    if (!file) return;
    const text = await file.text();
    const preview = await onImport(text, false);
    setCsvState({ name: file.name, text, preview });
  };
  const analysis = study.analysis;
  const terminal = ["proposal_approved", "proposal_rejected", "archived"].includes(study.status);
  return <div>
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start", flexWrap: "wrap" }}>
      <div style={{ display: "flex", gap: 9, alignItems: "flex-start" }}>
        <button onClick={onBack} title="Back to protocols" style={{ ...button, width: 34, padding: 0 }}><ArrowLeft size={14} /></button>
        <div><div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}><span style={{ fontSize: 13, fontWeight: 800 }}>{study.machine_name}</span><Badge color={analysis.review_ready ? "#86efac" : "#93c5fd"}>{study.status.replaceAll("_", " ")}</Badge></div>
          <div style={{ color: "#6b7280", fontSize: 9, marginTop: 4 }}>{study.study_key} · version {study.version}</div></div>
      </div>
      <div style={{ display: "flex", gap: 7, flexWrap: "wrap" }}>
        <button onClick={onRefresh} disabled={busy} title="Refresh analysis" style={button}><RefreshCw size={13} /> Refresh</button>
        {study.status === "draft" && <button onClick={() => onAction("start")} disabled={busy} style={button}><Play size={13} /> Start</button>}
        {study.status === "collecting" && <button onClick={() => onAction("submit_review")} disabled={busy || !analysis.review_ready} style={button}><ClipboardCheck size={13} /> Submit review</button>}
        {study.status === "review_ready" && <><button onClick={() => onAction("reject_proposal")} disabled={busy} style={button}><X size={13} /> Reject</button><button onClick={() => onAction("approve_proposal")} disabled={busy} style={{ ...button, background: "#14532d", borderColor: "#22c55e" }}><Check size={13} /> Approve proposal</button></>}
      </div>
    </div>
    <Analysis analysis={analysis} />
    {!terminal && <ObservationForm families={families} busy={busy} onSubmit={onObserve} />}
    {!terminal && <section style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center", borderTop: "1px solid #263244", marginTop: 16, paddingTop: 12, flexWrap: "wrap" }}>
      <div><div style={{ fontSize: 10, fontWeight: 800 }}>CSV evidence</div><div style={{ color: "#6b7280", fontSize: 9, marginTop: 3 }}>{csvState.name || "No file selected"}</div></div>
      <div style={{ display: "flex", gap: 7 }}><label style={{ ...button, cursor: "pointer" }}><FileUp size={13} /> Preview CSV<input type="file" accept=".csv,text/csv" onChange={chooseCsv} style={{ display: "none" }} /></label>
        {csvState.preview?.ready_to_apply && <button onClick={() => onImport(csvState.text, true).then(() => setCsvState({ name: "", text: "", preview: null }))} disabled={busy} style={{ ...button, background: "#14532d", borderColor: "#22c55e" }}><Save size={13} /> Apply {csvState.preview.rows_valid}</button>}</div>
      {csvState.preview && <div style={{ width: "100%", color: csvState.preview.ready_to_apply ? "#86efac" : "#fca5a5", fontSize: 9 }}>{csvState.preview.rows_valid} valid · {csvState.preview.rows_invalid} invalid · {csvState.preview.duplicates} duplicates{csvState.preview.issues[0] ? ` · row ${csvState.preview.issues[0].row}: ${csvState.preview.issues[0].detail}` : ""}</div>}
    </section>}
    {!!study.observations.length && <section style={{ marginTop: 18 }}>
      <div style={{ fontSize: 10, fontWeight: 800, marginBottom: 6 }}>Evidence ledger</div>
      {study.observations.slice(-10).reverse().map(item => <div key={item.id} style={{ display: "grid", gridTemplateColumns: "80px 1fr 1fr 90px 34px", gap: 8, alignItems: "center", padding: "8px 0", borderTop: "1px solid #263244", color: item.validity === "excluded" ? "#6b7280" : "#d1d5db", fontSize: 9 }}>
        <span>{new Date(item.measured_at).toLocaleDateString()}</span><span>{item.product_family}</span><span>{item.program_key || item.measurement_method}</span><span>{item.process_s}s process</span>
        {item.validity === "accepted" && !terminal ? <button onClick={() => onExclude(item.id)} title="Exclude observation" style={{ ...button, width: 30, minHeight: 30, padding: 0 }}><Trash2 size={12} /></button> : <span>{item.validity}</span>}
      </div>)}
    </section>}
  </div>;
}

export function EvidenceCapturePanel() {
  const [data, setData] = useState(null);
  const [study, setStudy] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let active = true;
    fetchCommissioningEvidence().then(result => { if (active) setData(result); })
      .catch(reason => { if (active) setError(reason.message); });
    return () => { active = false; };
  }, []);

  const refreshData = async () => setData(await fetchCommissioningEvidence());
  const loadStudy = async id => { setError(""); setStudy(await fetchCommissioningStudy(id)); };
  const run = async operation => {
    setBusy(true); setError(""); setNotice("");
    try { return await operation(); }
    catch (reason) { setError(reason.message); return null; }
    finally { setBusy(false); }
  };
  const create = machineKey => run(async () => {
    const created = await createCommissioningStudy({ machine_key: machineKey, actor: "field-capture" });
    setStudy(created); await refreshData();
  });
  const observe = payload => run(async () => {
    const result = await addCommissioningObservation(study.id, payload);
    setStudy(result.study); setNotice(result.status === "duplicate" ? "Duplicate evidence ignored" : "Observation recorded");
    await refreshData();
  });
  const importCsv = (text, apply) => run(async () => {
    const result = await importCommissioningEvidence(study.id, { csv_text: text, apply, actor: "field-capture" });
    if (apply) { await loadStudy(study.id); await refreshData(); setNotice(`${result.accepted} observations imported`); }
    return result;
  });
  const act = action => run(async () => {
    const notes = ["approve_proposal", "reject_proposal"].includes(action)
      ? window.prompt("Decision notes") : null;
    if (["approve_proposal", "reject_proposal"].includes(action) && notes === null) return;
    const result = await actOnCommissioningStudy(study.id, {
      action, expected_version: study.version, notes: notes || undefined, actor: "field-capture",
    });
    setStudy(result); await refreshData();
  });
  const exclude = observationId => {
    const reason = window.prompt("Reason for excluding this observation");
    if (!reason) return;
    run(async () => {
      setStudy(await excludeCommissioningObservation(study.id, observationId, { reason, actor: "field-capture" }));
      await refreshData();
    });
  };
  const download = () => run(async () => {
    const result = await downloadCommissioningEvidencePack();
    setNotice(`${result.filename} · SHA-256 ${result.sha256?.slice(0, 12)}`);
  });

  if (!data) return <div style={{ color: error ? "#fca5a5" : "#6b7280", fontSize: 10 }}>{error || "Loading evidence program..."}</div>;
  return <div>
    {error && <div style={{ color: "#fca5a5", fontSize: 9, marginBottom: 10 }}>{error}</div>}
    {notice && <div style={{ color: "#86efac", fontSize: 9, marginBottom: 10 }}>{notice}</div>}
    {study ? <StudyView study={study} families={data.product_families} busy={busy}
      onBack={() => setStudy(null)} onRefresh={() => run(async () => { await analyzeCommissioningStudy(study.id); await loadStudy(study.id); })}
      onObserve={observe} onImport={importCsv} onAction={act} onExclude={exclude} />
      : <ProtocolList data={data} busy={busy} onCreate={create} onSelect={id => run(() => loadStudy(id))} onDownload={download} />}
  </div>;
}
