import { useMemo, useState } from "react";
import Papa from "papaparse";
import { Check, Cpu, Database, FileUp, Play, RefreshCw, Search, X } from "lucide-react";
import { IndustrialIoPanel } from "./IndustrialIoPanel";

const button = {
  border: "1px solid #374151", borderRadius: 6, padding: "8px 12px",
  color: "#f9fafb", background: "#1f2937", cursor: "pointer", fontSize: 11,
  fontWeight: 700, display: "inline-flex", alignItems: "center", gap: 7,
  justifyContent: "center", minHeight: 34,
};
const input = {
  display: "block", width: "100%", marginTop: 6, padding: "9px 10px",
  color: "#f9fafb", background: "#0d1117", border: "1px solid #374151",
  borderRadius: 5, fontSize: 11,
};
const label = { color: "#9ca3af", fontSize: 10, fontWeight: 700 };

const TARGETS = {
  cabinet_vision_sql: [
    ["job_name", true], ["client_name", false], ["room_name", false],
    ["job_date", false], ["part_name", true], ["material", false],
    ["length_mm", false], ["width_mm", false], ["thickness_mm", false],
    ["qty", false], ["cnc_file_back", false], ["cnc_file_front", false],
    ["has_cnc", false],
  ],
  ottimo_barcode: [
    ["barcode", true], ["external_event_id", false], ["job_name", false],
    ["part_name", false], ["station", false], ["event_type", true],
    ["operator", false], ["ts", false], ["notes", false],
  ],
};
const BARCODE_EVENTS = [
  "route_arrival", "operation_start", "operation_complete", "part_complete",
  "qc_pass", "qc_fail", "packed", "dispatched", "unknown",
];

function signature(value) {
  const stable = item => {
    if (Array.isArray(item)) return item.map(stable);
    if (item && typeof item === "object") return Object.fromEntries(
      Object.keys(item).sort().map(key => [key, stable(item[key])]),
    );
    return item;
  };
  return JSON.stringify(stable(value));
}

function Status({ profile }) {
  const color = profile?.verified ? "#22c55e" : profile?.status === "connection_failed" ? "#ef4444" : "#f59e0b";
  const scopeProgress = profile?.required_scopes?.length
    ? `${profile.approved_scopes?.length ?? 0}/${profile.required_scopes.length} approved`
    : null;
  return <span style={{ color, fontSize: 10, fontWeight: 800, textTransform: "uppercase" }}>
    {profile?.enabled ? "live" : profile?.verified ? "approved" : scopeProgress ?? "needs evidence"}
  </span>;
}

function CheckResults({ result }) {
  if (!result) return null;
  const ready = result.ready_to_approve ?? result.ready_to_replay;
  return <div style={{ borderTop: "1px solid #1f2937", paddingTop: 14, marginTop: 14 }}>
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginBottom: 10 }}>
      <div style={{ fontSize: 12, fontWeight: 800, color: ready ? "#22c55e" : "#f59e0b" }}>
        {ready ? "Evidence passed" : "Evidence needs attention"}
      </div>
      {result.records_seen != null && <div style={{ color: "#6b7280", fontSize: 10 }}>
        {result.records_accepted}/{result.records_seen} accepted
      </div>}
    </div>
    {result.checks?.map(check => <div key={check.key} style={{ display: "flex", gap: 8,
          padding: "7px 0", borderBottom: "1px solid #1f2937" }}>
      <span style={{ color: check.passed ? "#22c55e" : "#ef4444" }}>
        {check.passed ? <Check size={14} /> : <X size={14} />}
      </span>
      <div><div style={{ fontSize: 11 }}>{check.label}</div>
        <div style={{ color: "#6b7280", fontSize: 10 }}>{check.detail}</div></div>
    </div>)}
    {result.issues?.slice(0, 8).map((issue, index) => <div key={`${issue.code}-${index}`}
          style={{ color: "#fca5a5", fontSize: 10, padding: "5px 0" }}>
      {issue.record_index != null ? `Row ${issue.record_index + 1}: ` : ""}{issue.detail}
    </div>)}
    <div style={{ color: "#4b5563", fontSize: 9, marginTop: 9 }}>
      SHA-256 {result.sample_sha256?.slice(0, 16) ?? "not recorded"} · raw sample not retained
    </div>
  </div>;
}

function MachineLogs({ machines, profile, onAnalyze, onConnectorAction }) {
  const candidates = machines.filter(machine => machine.has_maestro);
  const [machineKey, setMachineKey] = useState(candidates[0]?.machine_key ?? "");
  const [logText, setLogText] = useState("");
  const [fileName, setFileName] = useState("");
  const [result, setResult] = useState(null);
  const [profileOverride, setProfileOverride] = useState(null);
  const currentProfile = profileOverride ?? profile;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const chooseFile = async event => {
    const file = event.target.files?.[0];
    if (!file) return;
    setFileName(file.name);
    setLogText(await file.text());
    setResult(null);
    setError("");
  };

  const act = async kind => {
    setBusy(true); setError("");
    try {
      if (kind === "analyze") {
        const data = await onConnectorAction("analyze", "maestro_logs", {
          scope_key: machineKey, log_text: logText, file_name: fileName,
        });
        setResult({ ...data, mappingApproved: currentProfile?.approved_scopes?.includes(machineKey) });
      } else if (kind === "approve") {
        const updated = await onConnectorAction("approve", "maestro_logs", {
          run_id: result.run_id, expected_version: currentProfile.version,
          actor: "commissioning", enable: false,
        });
        setProfileOverride(updated);
        setResult(previous => ({ ...previous, mappingApproved: true }));
      } else if (kind === "replay") {
        if (!window.confirm("Import these validated historical machine events into HIVE?")) return;
        setResult(await onAnalyze({ machine_key: machineKey, log_text: logText, persist: true }));
      }
    } catch (reason) { setError(reason.message); } finally { setBusy(false); }
  };

  return <div>
    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 14 }}>
      <div><div style={{ fontSize: 13, fontWeight: 800 }}>SCM Maestro evidence</div>
        <div style={{ color: "#6b7280", fontSize: 10, marginTop: 3 }}>Validate each real log format before replay or live tailing.</div></div>
      <Status profile={currentProfile} />
    </div>
    <div className="commission-controls" style={{ display: "grid", gridTemplateColumns: ".7fr 1.3fr", gap: 12 }}>
      <label style={label}>MACHINE<select value={machineKey} onChange={event => { setMachineKey(event.target.value); setResult(null); }} style={input}>
        {candidates.map(machine => <option key={machine.machine_key} value={machine.machine_key}>{machine.name}</option>)}
      </select></label>
      <label style={label}>MAESTRO LOG<input type="file" accept=".log,.txt,.csv" onChange={chooseFile} style={input} /></label>
    </div>
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, marginTop: 14, flexWrap: "wrap" }}>
      <span style={{ color: fileName ? "#d1d5db" : "#4b5563", fontSize: 10 }}>
        {fileName ? `${fileName} · ${logText.split(/\r?\n/).length} lines` : "Choose a log file from Finder"}
      </span>
      <button disabled={!logText || busy} onClick={() => act("analyze")} style={{ ...button, opacity: !logText || busy ? .45 : 1 }}>
        <Search size={14} /> Analyze
      </button>
    </div>
    {error && <div style={{ color: "#fca5a5", fontSize: 10, marginTop: 12 }}>{error}</div>}
    <CheckResults result={result} />
    {result?.ready_to_replay && <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 14, flexWrap: "wrap" }}>
      {!result.mappingApproved && <button disabled={busy} onClick={() => act("approve")} style={button}><Check size={14} /> Approve this machine</button>}
      {result.mappingApproved && !result.persisted && <button disabled={busy} onClick={() => act("replay")} style={{ ...button, background: "#14532d", borderColor: "#22c55e" }}><Play size={14} /> Replay history</button>}
    </div>}
  </div>;
}

function DataConnectors({ profiles, onConnectorAction }) {
  const available = profiles.filter(profile => profile.record_type !== "machine_log");
  const [connectorKey, setConnectorKey] = useState(available[0]?.connector_key ?? "cabinet_vision_sql");
  const propProfile = available.find(profile => profile.connector_key === connectorKey);
  const [profileOverrides, setProfileOverrides] = useState({});
  const currentProfile = profileOverrides[connectorKey] ?? propProfile;
  const setCurrentProfile = updated => setProfileOverrides(previous => ({
    ...previous, [connectorKey]: updated,
  }));
  const [records, setRecords] = useState([]);
  const [fileName, setFileName] = useState("");
  const [result, setResult] = useState(null);
  const [mapping, setMapping] = useState({ fields: {}, values: {} });
  const [credentialEnv, setCredentialEnv] = useState(propProfile?.credential_env ?? "");
  const [sourceObject, setSourceObject] = useState(propProfile?.settings?.source_object ?? "");
  const [maxRows, setMaxRows] = useState(propProfile?.settings?.max_rows ?? 5000);
  const [discovery, setDiscovery] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const eventValues = useMemo(() => {
    const source = mapping.fields?.event_type;
    if (!source) return [];
    return [...new Set(records.map(record => record[source]).filter(value => value != null && value !== "").map(String))];
  }, [mapping.fields, records]);

  const chooseFile = async event => {
    const file = event.target.files?.[0];
    if (!file) return;
    setError(""); setResult(null); setFileName(file.name);
    try {
      const text = await file.text();
      let parsed;
      if (file.name.toLowerCase().endsWith(".json")) {
        const value = JSON.parse(text);
        parsed = Array.isArray(value) ? value : value.records ?? value.rows ?? [value];
      } else {
        const output = Papa.parse(text, { header: true, skipEmptyLines: true, transformHeader: header => header.trim() });
        if (output.errors.length) throw new Error(output.errors[0].message);
        parsed = output.data;
      }
      if (!parsed.length) throw new Error("The selected file contains no records");
      setRecords(parsed.slice(0, 10000));
    } catch (reason) { setRecords([]); setError(reason.message); }
  };

  const act = async kind => {
    setBusy(true); setError("");
    try {
      if (kind === "analyze") {
        const data = await onConnectorAction("analyze", connectorKey, {
          records, mapping: Object.keys(mapping.fields ?? {}).length ? mapping : undefined,
          file_name: fileName, actor: "commissioning",
        });
        const activeMapping = currentProfile?.active_mapping?.mapping;
        const mappingApproved = activeMapping && signature(activeMapping) === signature(data.mapping);
        setResult({ ...data, mappingApproved }); setMapping(data.mapping);
      } else if (kind === "approve") {
        const updated = await onConnectorAction("approve", connectorKey, {
          run_id: result.run_id, expected_version: currentProfile.version,
          actor: "commissioning", enable: true,
        });
        setCurrentProfile(updated);
        setResult(previous => ({ ...previous, mappingApproved: true }));
      } else if (kind === "import") {
        if (!window.confirm(`Import ${records.length} validated records into HIVE?`)) return;
        const data = await onConnectorAction("import", connectorKey, {
          records, file_name: fileName, actor: "commissioning",
        });
        setResult(previous => ({ ...previous, import_result: data }));
      } else if (kind === "saveProfile") {
        const updated = await onConnectorAction("profile", connectorKey, {
          expected_version: currentProfile.version, credential_env: credentialEnv,
          settings: { source_object: sourceObject, max_rows: Number(maxRows) },
          actor: "commissioning",
        });
        setCurrentProfile(updated);
      } else if (kind === "discover") {
        setDiscovery(await onConnectorAction("discover", connectorKey));
      } else if (kind === "sync") {
        if (!window.confirm("Read and import the approved Cabinet Vision SQL view now?")) return;
        setDiscovery(await onConnectorAction("sync", connectorKey, { actor: "commissioning" }));
      } else if (kind === "toggle") {
        const updated = await onConnectorAction("profile", connectorKey, {
          expected_version: currentProfile.version, enabled: !currentProfile.enabled,
          actor: "commissioning",
        });
        setCurrentProfile(updated);
      }
    } catch (reason) { setError(reason.message); } finally { setBusy(false); }
  };

  const setField = (target, source) => {
    const fields = { ...(mapping.fields ?? {}) };
    if (source) fields[target] = source; else delete fields[target];
    setMapping({ ...mapping, fields }); setResult(null);
  };
  const setEventValue = (sourceValue, canonical) => {
    const eventMap = { ...(mapping.values?.event_type ?? {}) };
    if (canonical) eventMap[sourceValue] = canonical; else delete eventMap[sourceValue];
    setMapping({ ...mapping, values: { ...(mapping.values ?? {}), event_type: eventMap } });
    setResult(null);
  };

  return <div>
    <div className="connector-layout" style={{ display: "grid", gridTemplateColumns: "190px minmax(0,1fr)", gap: 20 }}>
      <div style={{ borderRight: "1px solid #1f2937", paddingRight: 14 }}>
        {available.map(profile => {
          const effectiveProfile = profileOverrides[profile.connector_key] ?? profile;
          return <button key={profile.connector_key} onClick={() => {
          setConnectorKey(profile.connector_key); setRecords([]); setFileName(""); setResult(null); setMapping({ fields: {}, values: {} }); setDiscovery(null);
          setCredentialEnv(effectiveProfile.credential_env ?? ""); setSourceObject(effectiveProfile.settings?.source_object ?? ""); setMaxRows(effectiveProfile.settings?.max_rows ?? 5000);
        }} style={{ width: "100%", textAlign: "left", border: 0, borderRadius: 5, padding: "10px 9px", marginBottom: 4,
                    background: connectorKey === profile.connector_key ? "#1f2937" : "transparent", color: "#f9fafb", cursor: "pointer" }}>
          <div style={{ fontSize: 11, fontWeight: 800 }}>{profile.name}</div>
          <div style={{ marginTop: 3 }}><Status profile={effectiveProfile} /></div>
        </button>})}
      </div>
      <div style={{ minWidth: 0 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center", marginBottom: 14 }}>
          <div><div style={{ fontSize: 13, fontWeight: 800 }}>{currentProfile?.name}</div>
            <div style={{ color: "#6b7280", fontSize: 10, marginTop: 3 }}>Mapping version {currentProfile?.active_mapping?.version ?? "none"}</div></div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <Status profile={currentProfile} />
            {currentProfile?.verified && <button onClick={() => act("toggle")} disabled={busy} style={{ ...button, padding: "5px 9px", minHeight: 28 }}>{currentProfile.enabled ? "Disable" : "Enable"}</button>}
          </div>
        </div>

        {connectorKey === "cabinet_vision_sql" && <div style={{ paddingBottom: 15, marginBottom: 15, borderBottom: "1px solid #1f2937" }}>
          <div className="sql-config" style={{ display: "grid", gridTemplateColumns: "1fr 1fr 100px", gap: 9 }}>
            <label style={label}>CREDENTIAL ENV<input value={credentialEnv} onChange={event => setCredentialEnv(event.target.value)} placeholder="HIVE_CV_SQL_CONNECTION" style={input} /></label>
            <label style={label}>READ-ONLY VIEW<input value={sourceObject} onChange={event => setSourceObject(event.target.value)} placeholder="dbo.HiveJobParts" style={input} /></label>
            <label style={label}>ROW LIMIT<input type="number" min="1" max="10000" value={maxRows} onChange={event => setMaxRows(event.target.value)} style={input} /></label>
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
            <button onClick={() => act("saveProfile")} disabled={busy} style={button}><Database size={14} /> Save source</button>
            <button onClick={() => act("discover")} disabled={busy || !sourceObject || !credentialEnv} style={button}><Search size={14} /> Test metadata</button>
            {currentProfile?.enabled && <button onClick={() => act("sync")} disabled={busy} style={button}><RefreshCw size={14} /> Sync now</button>}
          </div>
          {discovery && <div style={{ color: discovery.connected || discovery.status === "imported" ? "#86efac" : "#fbbf24", fontSize: 10, marginTop: 8 }}>
            {discovery.detail ?? `${discovery.records_imported ?? 0} records imported`}
          </div>}
        </div>}

        <label style={label}>SAMPLE FILE (.CSV OR .JSON)<input type="file" accept=".csv,.json,.txt" onChange={chooseFile} style={input} /></label>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, marginTop: 10, flexWrap: "wrap" }}>
          <span style={{ color: records.length ? "#d1d5db" : "#4b5563", fontSize: 10 }}>
            {records.length ? `${fileName} · ${records.length} records` : "Choose a vendor export from Finder"}
          </span>
          <button onClick={() => act("analyze")} disabled={!records.length || busy} style={{ ...button, opacity: !records.length || busy ? .45 : 1 }}><Search size={14} /> {result ? "Re-analyze mapping" : "Analyze sample"}</button>
        </div>

        {(result || Object.keys(mapping.fields ?? {}).length > 0) && <div style={{ marginTop: 16 }}>
          <div style={{ fontSize: 10, fontWeight: 800, color: "#9ca3af", marginBottom: 8 }}>FIELD MAPPING</div>
          <div className="mapping-grid" style={{ display: "grid", gridTemplateColumns: "repeat(2,minmax(0,1fr))", gap: "7px 14px" }}>
            {(TARGETS[connectorKey] ?? []).map(([target, required]) => <label key={target} style={label}>
              {target}{required ? " *" : ""}
              <select value={mapping.fields?.[target] ?? ""} onChange={event => setField(target, event.target.value)} style={{ ...input, marginTop: 3, padding: "7px 8px" }}>
                <option value="">Not mapped</option>
                {(result?.source_columns ?? Object.keys(records[0] ?? {})).map(column => <option key={column} value={column}>{column}</option>)}
              </select>
            </label>)}
          </div>
          {connectorKey === "ottimo_barcode" && eventValues.length > 0 && <div style={{ marginTop: 14 }}>
            <div style={{ fontSize: 10, fontWeight: 800, color: "#9ca3af", marginBottom: 6 }}>EVENT VALUES</div>
            <div className="mapping-grid" style={{ display: "grid", gridTemplateColumns: "repeat(2,minmax(0,1fr))", gap: "7px 14px" }}>
              {eventValues.map(value => <label key={value} style={label}>{value}<select value={mapping.values?.event_type?.[value] ?? ""} onChange={event => setEventValue(value, event.target.value)} style={{ ...input, marginTop: 3, padding: "7px 8px" }}>
                <option value="">Not mapped</option>{BARCODE_EVENTS.map(event => <option key={event} value={event}>{event}</option>)}
              </select></label>)}
            </div>
          </div>}
        </div>}
        {error && <div style={{ color: "#fca5a5", fontSize: 10, marginTop: 12 }}>{error}</div>}
        <CheckResults result={result} />
        {result?.import_result && <div style={{ color: "#86efac", fontSize: 10, marginTop: 10 }}>
          {result.import_result.status === "duplicate" ? "Batch already imported; no duplicate writes." : `${result.import_result.records_imported} records imported.`}
        </div>}
        {result?.ready_to_approve && <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 14 }}>
          {!result.mappingApproved && <button onClick={() => act("approve")} disabled={busy} style={{ ...button, background: "#14532d", borderColor: "#22c55e" }}><Check size={14} /> Approve and enable</button>}
          {result.mappingApproved && currentProfile?.enabled && <button onClick={() => act("import")} disabled={busy} style={{ ...button, background: "#14532d", borderColor: "#22c55e" }}><Play size={14} /> Import sample</button>}
        </div>}
      </div>
    </div>
  </div>;
}

export function CommissioningPanel({ machines, connectors, industrial, onAnalyze, onConnectorAction, onIndustrialAction, onClose }) {
  const [tab, setTab] = useState("data");
  const profiles = connectors?.profiles ?? [];
  const maestroProfile = profiles.find(profile => profile.connector_key === "maestro_logs");

  return <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.72)", display: "grid", placeItems: "center", zIndex: 30, padding: 16 }}>
    <div style={{ width: "min(980px, 100%)", maxHeight: "92vh", overflowY: "auto", background: "#111827", border: "1px solid #374151", borderRadius: 8, padding: 20 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 16, alignItems: "center", marginBottom: 15 }}>
        <div><div style={{ fontSize: 17, fontWeight: 800 }}>Connector commissioning</div>
          <div style={{ color: "#6b7280", fontSize: 10, marginTop: 3 }}>Inspect, map, approve, then enable each factory data source.</div></div>
        <button onClick={onClose} aria-label="Close" title="Close" style={{ ...button, width: 34, height: 34, padding: 0 }}><X size={16} /></button>
      </div>
      <div style={{ display: "inline-flex", background: "#0d1117", border: "1px solid #374151", borderRadius: 6, padding: 2, marginBottom: 18 }}>
        <button onClick={() => setTab("data")} style={{ ...button, minHeight: 30, border: 0, background: tab === "data" ? "#374151" : "transparent" }}><Database size={13} /> Data connectors</button>
        <button onClick={() => setTab("industrial")} style={{ ...button, minHeight: 30, border: 0, background: tab === "industrial" ? "#374151" : "transparent" }}><Cpu size={13} /> Industrial I/O</button>
        <button onClick={() => setTab("machines")} style={{ ...button, minHeight: 30, border: 0, background: tab === "machines" ? "#374151" : "transparent" }}><FileUp size={13} /> Machine logs</button>
      </div>
      {tab === "industrial" ? (industrial?.profiles?.length
          ? <IndustrialIoPanel data={industrial} onAction={onIndustrialAction} />
          : <div style={{ color: "#6b7280", fontSize: 11 }}>Loading industrial registry…</div>)
        : !profiles.length ? <div style={{ color: "#6b7280", fontSize: 11 }}>Loading connector registry…</div>
        : tab === "data"
          ? <DataConnectors profiles={profiles} onConnectorAction={onConnectorAction} />
          : <MachineLogs machines={machines} profile={maestroProfile} onAnalyze={onAnalyze} onConnectorAction={onConnectorAction} />}
    </div>
  </div>;
}
