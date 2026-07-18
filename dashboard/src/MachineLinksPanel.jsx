import { useMemo, useRef, useState } from "react";
import {
  Check, CircleAlert, Clock3, Download, ExternalLink, FileUp, LockKeyhole, Pause,
  Play, RefreshCw, RotateCcw, Save, ShieldCheck, X,
} from "lucide-react";

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
const PASSPORT_FIELDS = [
  "status", "asset_tag", "serial_number", "manufacture_year", "physical_location",
  "controller_vendor", "controller_model", "controller_software", "controller_host",
  "mac_address", "network_zone", "ssh_port", "log_folder", "cnc_folder",
  "telemetry_strategy", "notes",
];
const STRATEGIES = [
  ["maestro_agent", "Maestro PC agent"], ["modbus_tcp", "Modbus TCP"],
  ["opcua", "OPC UA"], ["mqtt_json", "MQTT JSON"],
  ["energy_meter", "Energy meter"], ["operator_evidence", "Operator evidence"],
];
const PROBE_DEFAULTS = { ssh: 22, modbus_tcp: 502, opcua: 4840, tcp: "" };

function Badge({ children, color = "#93c5fd" }) {
  return <span style={{ color, border: `1px solid ${color}55`, borderRadius: 4,
    padding: "3px 6px", fontSize: 8, fontWeight: 800, textTransform: "uppercase",
    whiteSpace: "nowrap" }}>{children}</span>;
}

function passportForm(passport) {
  return Object.fromEntries(PASSPORT_FIELDS.map(key => [key, passport?.[key] ?? ""]));
}

function Summary({ data }) {
  const total = data.summary.machines;
  const metrics = [
    ["Passports", data.summary.passports_confirmed], ["Endpoints", data.summary.endpoints_ready],
    ["Offsite", data.summary.offsite_ready],
    ["Transports", data.summary.transports_ready], ["Contracts", data.summary.contracts_ready],
    ["Online", data.summary.online], ["Calibrated", data.summary.calibrated],
    ["Ready", data.summary.plug_and_play_ready],
  ];
  return <div className="machine-link-summary" style={{ display: "grid", gridTemplateColumns: "repeat(8,1fr)",
    border: "1px solid #263244", borderRadius: 6, overflow: "hidden", marginTop: 15 }}>
    {metrics.map(([name, value]) => <div key={name} style={{ padding: "10px 11px", borderRight: "1px solid #263244" }}>
      <div style={label}>{name}</div>
      <div style={{ fontSize: 15, fontWeight: 800, marginTop: 4 }}>{value}<span style={{ color: "#4b5563", fontSize: 9 }}>/{total}</span></div>
    </div>)}
  </div>;
}

function StageStrip({ stages }) {
  return <div className="machine-link-stages" style={{ display: "grid", gridTemplateColumns: "repeat(6,1fr)",
    borderTop: "1px solid #263244", borderBottom: "1px solid #263244", margin: "14px 0" }}>
    {stages.map(stage => <div key={stage.key} title={stage.detail} style={{ padding: "9px 8px 9px 0", minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 5, color: stage.ready ? "#86efac" : "#9ca3af",
        fontSize: 8, fontWeight: 800, textTransform: "uppercase" }}>
        {stage.ready ? <Check size={10} /> : <X size={10} />}{stage.label}
      </div>
      <div style={{ color: "#4b5563", fontSize: 8, marginTop: 4, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{stage.detail}</div>
    </div>)}
  </div>;
}

function MissionRunbook({ mission, busy, onStart, onAction, onStep }) {
  const active = ["in_progress", "paused"].includes(mission.status);
  const statusColor = mission.status === "completed" ? "#86efac"
    : mission.status === "in_progress" ? "#60a5fa" : "#fbbf24";
  return <section style={{ marginTop: 18, padding: "14px 0", borderTop: "1px solid #263244",
    borderBottom: "1px solid #263244" }}>
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12,
      alignItems: "flex-start", flexWrap: "wrap" }}>
      <div>
        <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
          <ShieldCheck size={15} color={statusColor} />
          <div style={{ fontSize: 11, fontWeight: 800 }}>Commissioning mission</div>
          <Badge color={statusColor}>{mission.status.replaceAll("_", " ")}</Badge>
        </div>
        <div style={{ color: "#6b7280", fontSize: 9, marginTop: 5 }}>
          {mission.progress_percent}% evidence complete · about {mission.estimated_site_minutes_remaining} site minutes remaining
        </div>
      </div>
      <div style={{ display: "flex", gap: 7, flexWrap: "wrap" }}>
        {["not_started", "cancelled", "completed"].includes(mission.status) &&
          <button onClick={onStart} disabled={!!busy} style={{ ...button, background: "#14532d", borderColor: "#22c55e" }}>
            {mission.status === "completed" ? <RotateCcw size={13} /> : <Play size={13} />}
            {mission.status === "completed" ? "Recommission" : "Start mission"}
          </button>}
        {mission.status === "in_progress" && <button onClick={() => onAction("pause")} disabled={!!busy} style={button}>
          <Pause size={13} /> Pause
        </button>}
        {mission.status === "paused" && <button onClick={() => onAction("resume")} disabled={!!busy} style={button}>
          <Play size={13} /> Resume
        </button>}
        {active && <button onClick={() => onAction("cancel")} disabled={!!busy}
          style={{ ...button, color: "#fca5a5" }}><X size={13} /> Cancel</button>}
      </div>
    </div>
    <div style={{ height: 4, background: "#1f2937", marginTop: 11, overflow: "hidden" }}>
      <div style={{ height: "100%", width: `${mission.progress_percent}%`, background: statusColor }} />
    </div>
    {mission.blockers.length > 0 && <div style={{ color: "#fbbf24", fontSize: 9, marginTop: 10,
      display: "flex", gap: 6, alignItems: "flex-start" }}>
      <CircleAlert size={12} style={{ flex: "0 0 auto" }} />{mission.blockers[0]}
    </div>}
    <div style={{ marginTop: 12 }}>
      {mission.steps.map((step, index) => <div key={step.key} className="mission-step" style={{
        display: "grid", gridTemplateColumns: "26px minmax(0,1fr) auto", gap: 9,
        alignItems: "center", padding: "8px 0", borderTop: "1px solid #1f2937",
      }}>
        <div style={{ width: 22, height: 22, display: "grid", placeItems: "center", borderRadius: 4,
          border: `1px solid ${step.complete ? "#22c55e55" : "#374151"}`,
          color: step.complete ? "#86efac" : step.status === "blocked" ? "#4b5563" : "#93c5fd" }}>
          {step.complete ? <Check size={12} /> : step.status === "blocked" ? <LockKeyhole size={11} /> : <Clock3 size={11} />}
        </div>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
            <span style={{ fontSize: 9, fontWeight: 800 }}>{index + 1}. {step.label}</span>
            <span style={{ color: step.phase === "offsite" ? "#a78bfa" : "#60a5fa", fontSize: 7,
              fontWeight: 800, textTransform: "uppercase" }}>{step.phase}</span>
          </div>
          <div style={{ color: "#6b7280", fontSize: 8, marginTop: 3 }}>{step.detail}</div>
        </div>
        {!step.complete && step.status === "available" && <button onClick={() => onStep(step)}
          disabled={mission.status !== "in_progress" || !!busy} style={{ ...button, minHeight: 29, padding: "5px 8px",
            opacity: mission.status === "in_progress" ? 1 : .4 }}>{step.action_label}</button>}
      </div>)}
    </div>
    <div style={{ color: "#4b5563", fontSize: 8, marginTop: 9 }}>{mission.guardrail}</div>
  </section>;
}

export function MachineLinksPanel({ data, onAction, onNavigate }) {
  const fileRef = useRef(null);
  const passportRef = useRef(null);
  const [selectedKey, setSelectedKey] = useState(data?.machines?.[0]?.machine_key ?? "");
  const [machineDrafts, setMachineDrafts] = useState({});
  const [csv, setCsv] = useState({ name: "", text: "", preview: null });
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState(null);
  const machine = useMemo(() => data?.machines?.find(item => item.machine_key === selectedKey)
    ?? data?.machines?.[0], [data, selectedKey]);

  if (!data || !machine) return <div style={{ color: "#6b7280", fontSize: 11 }}>Loading machine readiness…</div>;
  const draftKey = `${machine.machine_key}:${machine.passport.version}`;
  const probeType = machine.research.probe_type || "tcp";
  const defaultDraft = {
    form: passportForm(machine.passport),
    probe: { probe_type: probeType, host: machine.passport.controller_host || machine.endpoint || "",
      port: machine.research.default_port || machine.passport.ssh_port || PROBE_DEFAULTS[probeType] || "" },
    probeResult: null,
  };
  const draft = machineDrafts[draftKey] ?? defaultDraft;
  const { form, probe, probeResult } = draft;
  const updateDraft = change => setMachineDrafts(previous => ({
    ...previous, [draftKey]: change(previous[draftKey] ?? defaultDraft),
  }));
  const set = (key, value) => updateDraft(previous => ({
    ...previous, form: { ...previous.form, [key]: value },
  }));
  const setProbe = change => updateDraft(previous => ({
    ...previous, probe: typeof change === "function" ? change(previous.probe) : change,
  }));
  const setProbeResult = value => updateDraft(previous => ({ ...previous, probeResult: value }));

  const execute = async (kind, action) => {
    setBusy(kind); setNotice(null);
    try {
      const result = await action();
      return result;
    } catch (reason) {
      setNotice({ type: "error", text: reason.message });
      return null;
    } finally { setBusy(""); }
  };

  const save = async confirm => {
    const payload = Object.fromEntries(PASSPORT_FIELDS.map(key => [key, form[key] === "" ? null : form[key]]));
    if (payload.manufacture_year != null) payload.manufacture_year = Number(payload.manufacture_year);
    if (payload.ssh_port != null) payload.ssh_port = Number(payload.ssh_port);
    if (confirm) payload.status = "confirmed";
    const result = await execute("save", () => onAction("passport", machine.machine_key, {
      ...payload, expected_version: machine.passport.version, actor: "commissioning-console",
    }));
    if (result) setNotice({ type: "success", text: confirm ? "Machine passport confirmed." : "Machine passport saved." });
  };

  const runProbe = async executeLive => {
    if (executeLive && !window.confirm("Open this read-only TCP connection from the HIVE central PC? No device data will be written.")) return;
    const result = await execute("probe", () => onAction("probe", machine.machine_key, {
      probe_type: probe.probe_type, host: probe.host || null,
      port: probe.port === "" ? null : Number(probe.port), execute: executeLive,
      timeout_s: 2, actor: "commissioning-console",
    }));
    if (result) setProbeResult(result);
  };

  const chooseCsv = async event => {
    const file = event.target.files?.[0];
    if (!file) return;
    const text = await file.text();
    const preview = await execute("csv", () => onAction("inventory", null, {
      csv_text: text, apply: false, actor: "commissioning-console",
    }));
    setCsv({ name: file.name, text, preview });
  };

  const applyCsv = async () => {
    if (!csv.preview?.valid || !window.confirm(`Apply ${csv.preview.rows_changed} validated passport updates atomically?`)) return;
    const result = await execute("csv", () => onAction("inventory", null, {
      csv_text: csv.text, apply: true, actor: "commissioning-console",
    }));
    if (result) {
      setCsv(previous => ({ ...previous, preview: result }));
      setNotice({ type: "success", text: `${result.rows_applied} passport updates applied.` });
    }
  };

  const download = async () => {
    const result = await execute("pack", () => onAction("pack"));
    if (result) setNotice({ type: "success", text: `${result.filename} downloaded. SHA-256 ${result.sha256?.slice(0, 16)}…` });
  };

  const startMission = async () => {
    const result = await execute("mission", () => onAction("missionStart", machine.machine_key, {
      actor: "commissioning-console",
    }));
    if (result) setNotice({ type: "success", text: "Commissioning mission started." });
  };

  const actOnMission = async action => {
    if (action === "cancel" && !window.confirm("Cancel this commissioning mission? Recorded evidence remains intact.")) return;
    const result = await execute("mission", () => onAction("missionAction", machine.machine_key, {
      action, expected_version: machine.mission.version, actor: "commissioning-console",
    }));
    if (result) setNotice({ type: "success", text: `Commissioning mission ${action === "pause" ? "paused" : action === "resume" ? "resumed" : "cancelled"}.` });
  };

  const openMissionStep = step => {
    if (step.action_surface === "field_pack") {
      download();
    } else if (step.action_surface === "machine_links") {
      passportRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    } else {
      onNavigate(step.action_surface, machine.machine_key);
    }
  };

  return <div>
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start", flexWrap: "wrap" }}>
      <div><div style={{ fontSize: 13, fontWeight: 800 }}>Machine links</div>
        <div style={{ color: "#6b7280", fontSize: 10, marginTop: 4 }}>Confirm installed controllers and prove each connection before approving live data.</div></div>
      <div style={{ display: "flex", gap: 7, flexWrap: "wrap" }}>
        <input ref={fileRef} type="file" accept=".csv,text/csv" onChange={chooseCsv} style={{ display: "none" }} />
        <button onClick={() => fileRef.current?.click()} disabled={!!busy} style={button}><FileUp size={13} /> Import inventory</button>
        <button onClick={download} disabled={!!busy} style={button}><Download size={13} /> Field pack</button>
      </div>
    </div>
    <Summary data={data} />
    <div style={{ display: "flex", alignItems: "flex-start", gap: 7, marginTop: 10, color: "#93c5fd", fontSize: 9 }}>
      <ShieldCheck size={13} style={{ flex: "0 0 auto" }} />{data.guardrail}
    </div>
    {notice && <div style={{ marginTop: 10, color: notice.type === "error" ? "#fca5a5" : "#86efac", fontSize: 10 }}>{notice.text}</div>}
    {csv.name && <div style={{ marginTop: 12, padding: "10px 0", borderTop: "1px solid #263244", borderBottom: "1px solid #263244",
      display: "flex", justifyContent: "space-between", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
      <div><div style={{ fontSize: 10, fontWeight: 800 }}>{csv.name}</div>
        <div style={{ color: csv.preview?.valid ? "#86efac" : "#fbbf24", fontSize: 9, marginTop: 3 }}>
          {csv.preview ? `${csv.preview.rows_changed ?? 0} changes · ${csv.preview.errors?.length ?? 0} errors · ${csv.preview.valid ? "valid preview" : "fix before apply"}` : "Preview failed"}
        </div></div>
      <div style={{ display: "flex", gap: 7 }}>
        <button onClick={() => { setCsv({ name: "", text: "", preview: null }); if (fileRef.current) fileRef.current.value = ""; }} style={{ ...button, width: 34, padding: 0 }} title="Clear inventory import"><X size={13} /></button>
        <button onClick={applyCsv} disabled={!csv.preview?.valid || !csv.preview?.rows_changed || !!busy} style={{ ...button, background: "#14532d", borderColor: "#22c55e", opacity: !csv.preview?.valid || !csv.preview?.rows_changed ? .45 : 1 }}><Check size={13} /> Apply atomically</button>
      </div>
    </div>}

    <div className="machine-link-layout" style={{ display: "grid", gridTemplateColumns: "245px minmax(0,1fr)", gap: 18, marginTop: 17 }}>
      <div className="machine-link-list" style={{ borderRight: "1px solid #263244", paddingRight: 12 }}>
        {data.machines.map(item => <button key={item.machine_key} onClick={() => setSelectedKey(item.machine_key)} style={{
          width: "100%", border: 0, borderBottom: "1px solid #263244", background: item.machine_key === machine.machine_key ? "#1f2937" : "transparent",
          color: "#f9fafb", cursor: "pointer", textAlign: "left", padding: "9px 8px", borderRadius: 4,
        }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 6, alignItems: "center" }}>
            <span style={{ fontSize: 10, fontWeight: 800 }}>{item.name}</span>
            <span style={{ color: item.readiness_score === 100 ? "#86efac" : "#60a5fa", fontSize: 9, fontWeight: 800 }}>{item.readiness_score}%</span>
          </div>
          <div style={{ color: "#4b5563", fontSize: 8, marginTop: 4 }}>{item.passport.status} · {item.effective_strategy.replaceAll("_", " ")}</div>
        </button>)}
      </div>

      <div style={{ minWidth: 0 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "flex-start", flexWrap: "wrap" }}>
          <div><div style={{ fontSize: 14, fontWeight: 800 }}>{machine.name}</div>
            <div style={{ color: "#6b7280", fontSize: 9, marginTop: 3 }}>{machine.brand} {machine.model} · {machine.type}</div></div>
          <div style={{ display: "flex", gap: 6 }}><Badge color={machine.passport.status === "confirmed" ? "#86efac" : "#fbbf24"}>{machine.passport.status}</Badge><Badge>{machine.readiness_score}% ready</Badge></div>
        </div>
        <StageStrip stages={machine.stages} />
        <div style={{ display: "flex", gap: 7, color: "#fbbf24", fontSize: 9, lineHeight: 1.4 }}>
          <CircleAlert size={13} style={{ flex: "0 0 auto" }} /><span><b>Next:</b> {machine.next_action}</span>
        </div>

        <MissionRunbook mission={machine.mission} busy={busy} onStart={startMission}
          onAction={actOnMission} onStep={openMissionStep} />

        <section ref={passportRef} style={{ marginTop: 17, scrollMarginTop: 12 }}>
          <div style={{ fontSize: 10, fontWeight: 800, marginBottom: 9 }}>Installed machine passport</div>
          <div className="machine-link-form" style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 9 }}>
            <label style={label}>Record status<select value={form.status ?? "assumption"} onChange={event => set("status", event.target.value)} style={input}><option value="assumption">Assumption</option><option value="inventory">Inventory captured</option><option value="confirmed">Confirmed on site</option></select></label>
            <label style={label}>Asset tag<input value={form.asset_tag ?? ""} onChange={event => set("asset_tag", event.target.value)} style={input} /></label>
            <label style={label}>Serial number<input value={form.serial_number ?? ""} onChange={event => set("serial_number", event.target.value)} style={input} /></label>
            <label style={label}>Manufacture year<input type="number" min="1950" max="2100" value={form.manufacture_year ?? ""} onChange={event => set("manufacture_year", event.target.value)} style={input} /></label>
            <label style={label}>Physical location<input value={form.physical_location ?? ""} onChange={event => set("physical_location", event.target.value)} style={input} /></label>
            <label style={label}>Network zone<input value={form.network_zone ?? ""} onChange={event => set("network_zone", event.target.value)} style={input} /></label>
            <label style={label}>Controller vendor<input value={form.controller_vendor ?? ""} onChange={event => set("controller_vendor", event.target.value)} style={input} /></label>
            <label style={label}>Controller model<input value={form.controller_model ?? ""} onChange={event => set("controller_model", event.target.value)} style={input} /></label>
            <label style={label}>Controller software<input value={form.controller_software ?? ""} onChange={event => set("controller_software", event.target.value)} style={input} /></label>
            <label style={label}>Static host / IP<input value={form.controller_host ?? ""} onChange={event => set("controller_host", event.target.value)} style={input} /></label>
            <label style={label}>MAC address<input value={form.mac_address ?? ""} onChange={event => set("mac_address", event.target.value)} style={input} /></label>
            <label style={label}>SSH port<input type="number" min="1" max="65535" value={form.ssh_port ?? ""} onChange={event => set("ssh_port", event.target.value)} style={input} /></label>
            <label style={label}>Telemetry strategy<select value={form.telemetry_strategy ?? ""} onChange={event => set("telemetry_strategy", event.target.value)} style={input}><option value="">Choose on site</option>{STRATEGIES.map(([value, name]) => <option key={value} value={value}>{name}</option>)}</select></label>
            <label style={{ ...label, gridColumn: "span 2" }}>Log folder<input value={form.log_folder ?? ""} onChange={event => set("log_folder", event.target.value)} placeholder="C:\\SCM\\Maestro\\Logs" style={input} /></label>
            <label style={{ ...label, gridColumn: "span 2" }}>CNC folder<input value={form.cnc_folder ?? ""} onChange={event => set("cnc_folder", event.target.value)} placeholder="C:\\SCM\\Maestro\\CncPrograms" style={input} /></label>
            <label style={label}>Notes<input value={form.notes ?? ""} onChange={event => set("notes", event.target.value)} style={input} /></label>
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 9, marginTop: 11, flexWrap: "wrap" }}>
            <span style={{ color: "#4b5563", fontSize: 8 }}>Passport version {machine.passport.version} · confirmation is audited</span>
            <div style={{ display: "flex", gap: 7 }}>
              <button onClick={() => save(false)} disabled={!!busy} style={button}><Save size={13} /> Save inventory</button>
              <button onClick={() => save(true)} disabled={!!busy} style={{ ...button, background: "#14532d", borderColor: "#22c55e" }}><ShieldCheck size={13} /> Confirm on site</button>
            </div>
          </div>
        </section>

        <section style={{ marginTop: 20, paddingTop: 15, borderTop: "1px solid #263244" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
            <div><div style={{ fontSize: 10, fontWeight: 800 }}>Read-only transport check</div>
              <div style={{ color: "#6b7280", fontSize: 9, marginTop: 3 }}>TCP reachability only; no registers, files, or controller settings are changed.</div></div>
            {machine.latest_probe && <Badge color={machine.latest_probe.status === "reachable" ? "#86efac" : "#fbbf24"}>{machine.latest_probe.status}</Badge>}
          </div>
          <div className="machine-link-probe" style={{ display: "grid", gridTemplateColumns: ".7fr 1.3fr .55fr auto auto", gap: 8, alignItems: "end", marginTop: 10 }}>
            <label style={label}>Probe<select value={probe.probe_type} onChange={event => { const type = event.target.value; setProbe(previous => ({ ...previous, probe_type: type, port: PROBE_DEFAULTS[type] })); setProbeResult(null); }} style={input}><option value="tcp">TCP</option><option value="ssh">SSH banner</option><option value="modbus_tcp">Modbus port</option><option value="opcua">OPC UA port</option></select></label>
            <label style={label}>Host<input value={probe.host} onChange={event => setProbe(previous => ({ ...previous, host: event.target.value }))} style={input} /></label>
            <label style={label}>Port<input type="number" min="1" max="65535" value={probe.port} onChange={event => setProbe(previous => ({ ...previous, port: event.target.value }))} style={input} /></label>
            <button onClick={() => runProbe(false)} disabled={!!busy || !probe.host || !probe.port} style={button}><RefreshCw size={13} /> Preview</button>
            <button onClick={() => runProbe(true)} disabled={!!busy || !probe.host || !probe.port} style={{ ...button, background: "#172554", borderColor: "#3b82f6" }}><Play size={13} /> Run check</button>
          </div>
          {probeResult && <div style={{ color: probeResult.status === "reachable" || probeResult.status === "preview_ready" ? "#86efac" : "#fbbf24", fontSize: 9, marginTop: 9 }}>
            {probeResult.status.replaceAll("_", " ")} · {probeResult.host}:{probeResult.port}{probeResult.latency_ms != null ? ` · ${probeResult.latency_ms} ms` : ""}{probeResult.detail ? ` · ${probeResult.detail}` : ""}
          </div>}
        </section>

        <section style={{ marginTop: 20, paddingTop: 15, borderTop: "1px solid #263244" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
            <div style={{ fontSize: 10, fontWeight: 800 }}>Research candidate</div><Badge color="#fbbf24">{machine.research.confidence} confidence · assumption</Badge>
          </div>
          <div style={{ color: "#9ca3af", fontSize: 9, lineHeight: 1.5, marginTop: 8 }}>{machine.research.rationale}</div>
          <div className="machine-link-research" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginTop: 10 }}>
            <div><div style={label}>Verify on site</div>{machine.research.verify_on_site.map(item => <div key={item} style={{ color: "#9ca3af", fontSize: 9, padding: "4px 0", display: "flex", gap: 6 }}><Check size={10} color="#4b5563" style={{ flex: "0 0 auto" }} />{item}</div>)}</div>
            <div><div style={label}>Official sources</div>{machine.research.sources.length ? machine.research.sources.map(source => <a key={source.key} href={source.url} target="_blank" rel="noreferrer" style={{ color: "#60a5fa", fontSize: 9, padding: "4px 0", display: "flex", gap: 6, textDecoration: "none" }}><ExternalLink size={10} style={{ flex: "0 0 auto" }} />{source.label}</a>) : <div style={{ color: "#6b7280", fontSize: 9, marginTop: 6 }}>No model-specific published interface contract.</div>}</div>
          </div>
        </section>
      </div>
    </div>
    <style>{`@media(max-width:760px){.machine-link-summary{grid-template-columns:repeat(3,1fr)!important}.machine-link-layout{grid-template-columns:1fr!important}.machine-link-list{border-right:0!important;border-bottom:1px solid #263244;padding-right:0!important;padding-bottom:8px;display:grid;grid-template-columns:1fr 1fr}.machine-link-stages{grid-template-columns:repeat(2,1fr)!important}.machine-link-form,.machine-link-probe,.machine-link-research{grid-template-columns:1fr!important}.machine-link-form label{grid-column:auto!important}.mission-step{grid-template-columns:26px minmax(0,1fr)!important}.mission-step button{grid-column:2;width:100%}}`}</style>
  </div>;
}
