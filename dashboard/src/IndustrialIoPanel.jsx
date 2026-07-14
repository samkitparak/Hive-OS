import { useMemo, useState } from "react";
import { Activity, Check, FlaskConical, Plus, Radio, RefreshCw, Save, Search, Trash2 } from "lucide-react";

const button = {
  border: "1px solid #374151", borderRadius: 6, padding: "8px 11px",
  color: "#f9fafb", background: "#1f2937", cursor: "pointer", fontSize: 10,
  fontWeight: 700, display: "inline-flex", alignItems: "center", gap: 6,
  justifyContent: "center", minHeight: 34,
};
const input = {
  display: "block", width: "100%", marginTop: 5, padding: "8px 9px",
  color: "#f9fafb", background: "#0d1117", border: "1px solid #374151",
  borderRadius: 5, fontSize: 10,
};
const label = { color: "#9ca3af", fontSize: 9, fontWeight: 800 };
const protocolLabel = { modbus_tcp: "Modbus TCP", opcua: "OPC-UA", mqtt_json: "MQTT JSON" };

function stableSignature(value) {
  const stable = item => {
    if (Array.isArray(item)) return item.map(stable);
    if (item && typeof item === "object") return Object.fromEntries(
      Object.keys(item).sort().map(key => [key, stable(item[key])]),
    );
    return item;
  };
  return JSON.stringify(stable(value));
}

function profileDraft(profile) {
  return {
    endpoint: profile?.endpoint ?? "",
    credential_env: profile?.credential_env ?? "",
    poll_interval_s: profile?.poll_interval_s ?? 15,
    settings: JSON.parse(JSON.stringify(profile?.settings ?? { signals: [] })),
  };
}

function Status({ profile }) {
  const color = profile?.enabled ? "#22c55e" : profile?.verified ? "#60a5fa" :
    profile?.status === "probe_failed" || profile?.status === "poll_failed" ? "#ef4444" : "#f59e0b";
  const text = profile?.enabled ? "polling" : profile?.verified ? "approved" :
    (profile?.status ?? "site setup").replaceAll("_", " ");
  return <span style={{ color, fontSize: 9, fontWeight: 800, textTransform: "uppercase" }}>{text}</span>;
}

function SignalEditor({ protocol, definitions, signals, onChange }) {
  const keys = Object.keys(definitions);
  const update = (index, patch) => onChange(signals.map((signal, itemIndex) =>
    itemIndex === index ? { ...signal, ...patch } : signal));
  const remove = index => onChange(signals.filter((_, itemIndex) => itemIndex !== index));
  const add = () => {
    const used = new Set(signals.map(signal => signal.key));
    const key = keys.find(candidate => !used.has(candidate));
    if (!key) return;
    const base = { key, unit: definitions[key].unit, required: signals.length === 0, scale: 1, offset: 0 };
    if (protocol === "modbus_tcp") Object.assign(base, {
      function: "input_register", address: 0, data_type: "float32",
      word_order: "big", byte_order: "big",
    });
    if (protocol === "opcua") base.node_id = "";
    if (protocol === "mqtt_json") base.path = "";
    onChange([...signals, base]);
  };

  return <div style={{ marginTop: 16, borderTop: "1px solid #1f2937", paddingTop: 13 }}>
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, marginBottom: 8 }}>
      <div style={{ color: "#9ca3af", fontSize: 9, fontWeight: 800 }}>SIGNAL CONTRACT</div>
      <button onClick={add} disabled={signals.length >= keys.length} title="Add signal" style={{ ...button, minHeight: 28, padding: "5px 8px" }}>
        <Plus size={13} /> Add
      </button>
    </div>
    {signals.map((signal, index) => <div key={`${signal.key}-${index}`} className="industrial-signal-row"
      style={{ display: "grid", gridTemplateColumns: protocol === "modbus_tcp" ? "1fr .9fr .7fr .8fr 36px" : "1fr 1.5fr .7fr 36px", gap: 7, padding: "7px 0", borderBottom: "1px solid #1f2937", alignItems: "end" }}>
      <label style={label}>SIGNAL<select value={signal.key} onChange={event => {
        const key = event.target.value;
        update(index, { key, unit: definitions[key].unit });
      }} style={input}>{keys.map(key => <option key={key} value={key}>{key}</option>)}</select></label>
      {protocol === "modbus_tcp" && <>
        <label style={label}>FUNCTION<select value={signal.function ?? "input_register"} onChange={event => update(index, { function: event.target.value })} style={input}>
          <option value="input_register">Input register</option><option value="holding_register">Holding register</option>
          <option value="discrete_input">Discrete input</option><option value="coil">Coil read</option>
        </select></label>
        <label style={label}>ADDRESS<input type="number" min="0" max="65535" value={signal.address ?? 0} onChange={event => update(index, { address: Number(event.target.value) })} style={input} /></label>
        <label style={label}>TYPE<select value={signal.data_type ?? "float32"} onChange={event => update(index, { data_type: event.target.value })} style={input}>
          {["float32", "float64", "uint16", "int16", "uint32", "int32", "bool"].map(value => <option key={value}>{value}</option>)}
        </select></label>
      </>}
      {protocol === "opcua" && <label style={label}>NODE ID<input value={signal.node_id ?? ""} onChange={event => update(index, { node_id: event.target.value })} placeholder="ns=3;s=Machine.State" style={input} /></label>}
      {protocol === "mqtt_json" && <label style={label}>JSON PATH<input value={signal.path ?? ""} onChange={event => update(index, { path: event.target.value })} placeholder="metrics.power_w" style={input} /></label>}
      {protocol !== "modbus_tcp" && <label style={label}>UNIT<input value={signal.unit ?? ""} onChange={event => update(index, { unit: event.target.value })} style={input} /></label>}
      <button onClick={() => remove(index)} title="Remove signal" aria-label={`Remove ${signal.key}`} style={{ ...button, width: 34, minHeight: 34, padding: 0, color: "#fca5a5" }}><Trash2 size={13} /></button>
      <label style={{ ...label, display: "flex", alignItems: "center", gap: 6, gridColumn: "1 / -1" }}>
        <input type="checkbox" checked={Boolean(signal.required)} onChange={event => update(index, { required: event.target.checked })} /> Required for healthy poll
      </label>
    </div>)}
    {!signals.length && <div style={{ color: "#6b7280", fontSize: 10, padding: "12px 0" }}>No signals configured</div>}
  </div>;
}

function ProbeResult({ result }) {
  if (!result) return null;
  return <div style={{ marginTop: 15, borderTop: "1px solid #1f2937", paddingTop: 12 }}>
    <div style={{ display: "flex", justifyContent: "space-between", gap: 10, marginBottom: 8 }}>
      <span style={{ color: result.status === "passed" ? "#22c55e" : "#ef4444", fontSize: 11, fontWeight: 800 }}>
        {result.status === "passed" ? "Probe passed" : "Probe failed"}
      </span>
      <span style={{ color: "#6b7280", fontSize: 9 }}>{result.mode}</span>
    </div>
    <div className="industrial-values" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(130px,1fr))", gap: 7 }}>
      {result.values?.map(item => <div key={item.key} style={{ borderBottom: "1px solid #1f2937", padding: "7px 0" }}>
        <div style={{ color: "#9ca3af", fontSize: 9 }}>{item.key}</div>
        <div style={{ color: item.quality === "good" ? "#f9fafb" : "#fca5a5", fontSize: 12, fontWeight: 800, marginTop: 3 }}>
          {item.value == null ? "No value" : String(item.value)} <span style={{ color: "#6b7280", fontSize: 9 }}>{item.unit}</span>
        </div>
      </div>)}
    </div>
    <div style={{ color: "#6b7280", fontSize: 9, marginTop: 8 }}>{result.detail}</div>
  </div>;
}

export function IndustrialIoPanel({ data, onAction }) {
  const profiles = data?.profiles ?? [];
  const definitions = data?.signal_definitions ?? {};
  const [profileKey, setProfileKey] = useState(profiles[0]?.profile_key ?? "");
  const [overrides, setOverrides] = useState({});
  const propProfile = profiles.find(profile => profile.profile_key === profileKey) ?? profiles[0];
  const localProfile = overrides[propProfile?.profile_key];
  const currentProfile = localProfile && (!propProfile || localProfile.version > propProfile.version)
    ? localProfile : propProfile;
  const [draft, setDraft] = useState(() => profileDraft(currentProfile));
  const [result, setResult] = useState(null);
  const [nodes, setNodes] = useState([]);
  const [mqttTopic, setMqttTopic] = useState("hive/telemetry/device-1");
  const [mqttPayload, setMqttPayload] = useState('{"metrics":{"power_w":1250},"ts":"2026-07-14T12:00:00Z"}');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const savedDraft = useMemo(() => profileDraft(currentProfile), [currentProfile]);
  const dirty = stableSignature(draft) !== stableSignature(savedDraft);
  const signals = draft.settings?.signals ?? [];
  const energyProfile = data?.energy?.profiles?.find(profile => profile.profile_key === currentProfile?.profile_key);

  if (!currentProfile) return <div style={{ color: "#6b7280", fontSize: 10 }}>Loading industrial profiles...</div>;

  const selectProfile = profile => {
    const effective = overrides[profile.profile_key] ?? profile;
    setProfileKey(profile.profile_key); setDraft(profileDraft(effective)); setResult(null); setNodes([]); setError("");
  };
  const setSettings = patch => setDraft(previous => ({ ...previous, settings: { ...previous.settings, ...patch } }));
  const setCurrentProfile = profile => {
    setOverrides(previous => ({ ...previous, [profile.profile_key]: profile }));
    setDraft(profileDraft(profile));
  };

  const act = async kind => {
    setBusy(true); setError("");
    try {
      if (kind === "save") {
        const updated = await onAction("profile", currentProfile.profile_key, {
          expected_version: currentProfile.version,
          endpoint: draft.endpoint,
          credential_env: draft.credential_env,
          poll_interval_s: Number(draft.poll_interval_s),
          settings: draft.settings,
          actor: "commissioning",
        });
        setCurrentProfile(updated); setResult(null);
      } else if (kind === "simulate") {
        setResult(await onAction("simulate", currentProfile.profile_key, { actor: "commissioning" }));
      } else if (kind === "probe") {
        setResult(await onAction("probe", currentProfile.profile_key, { actor: "commissioning" }));
      } else if (kind === "mqttProbe") {
        setResult(await onAction("mqttProbe", currentProfile.profile_key, {
          topic: mqttTopic, payload: JSON.parse(mqttPayload), actor: "commissioning",
        }));
      } else if (kind === "approve") {
        const updated = await onAction("approve", currentProfile.profile_key, {
          run_id: result.run_id, expected_version: currentProfile.version,
          actor: "commissioning", enable: true,
        });
        setCurrentProfile(updated); setResult(previous => ({ ...previous, approvable: false }));
      } else if (kind === "toggle") {
        setCurrentProfile(await onAction("profile", currentProfile.profile_key, {
          expected_version: currentProfile.version,
          enabled: !currentProfile.enabled,
          actor: "commissioning",
        }));
      } else if (kind === "poll") {
        setResult(await onAction("poll", currentProfile.profile_key, { actor: "commissioning" }));
      } else if (kind === "browse") {
        const response = await onAction("browse", currentProfile.profile_key);
        setNodes(response.nodes ?? []);
      }
    } catch (reason) { setError(reason.message); } finally { setBusy(false); }
  };

  const canTest = !dirty && signals.length > 0;
  return <div>
    <div style={{ display: "flex", gap: 18, flexWrap: "wrap", paddingBottom: 12, marginBottom: 14, borderBottom: "1px solid #1f2937" }}>
      <div><div style={{ color: "#6b7280", fontSize: 8 }}>LIVE POWER</div><div style={{ fontSize: 14, fontWeight: 800, marginTop: 2 }}>{Math.round(data.summary?.current_power_w ?? 0)} W</div></div>
      <div><div style={{ color: "#6b7280", fontSize: 8 }}>24H ENERGY</div><div style={{ fontSize: 14, fontWeight: 800, marginTop: 2 }}>{data.energy?.summary?.energy_kwh ?? 0} kWh</div></div>
      <div><div style={{ color: "#6b7280", fontSize: 8 }}>IDLE ENERGY</div><div style={{ fontSize: 14, fontWeight: 800, marginTop: 2 }}>{data.energy?.summary?.idle_energy_kwh ?? 0} kWh</div></div>
      <div><div style={{ color: "#6b7280", fontSize: 8 }}>CONTRACTS</div><div style={{ fontSize: 14, fontWeight: 800, marginTop: 2 }}>{data.summary?.verified ?? 0}/{data.summary?.profiles ?? 0}</div></div>
    </div>
    <div style={{ display: "grid", gridTemplateColumns: "190px minmax(0,1fr)", gap: 20 }} className="industrial-layout">
      <div className="industrial-sidebar" style={{ borderRight: "1px solid #1f2937", paddingRight: 14 }}>
        {profiles.map(profile => {
          const local = overrides[profile.profile_key];
          const effective = local && local.version > profile.version ? local : profile;
          return <button key={profile.profile_key} onClick={() => selectProfile(profile)} style={{ width: "100%", textAlign: "left", border: 0, borderRadius: 5, padding: "9px", marginBottom: 4, background: currentProfile.profile_key === profile.profile_key ? "#1f2937" : "transparent", color: "#f9fafb", cursor: "pointer" }}>
            <div style={{ fontSize: 10, fontWeight: 800 }}>{profile.name}</div>
            <div style={{ marginTop: 3 }}><Status profile={effective} /></div>
          </button>;
        })}
      </div>
      <div style={{ minWidth: 0 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, marginBottom: 13 }}>
          <div><div style={{ fontSize: 13, fontWeight: 800 }}>{currentProfile.name}</div>
            <div style={{ color: "#6b7280", fontSize: 9, marginTop: 3 }}>{protocolLabel[currentProfile.protocol]} | contract {currentProfile.active_contract?.version ?? "none"}</div></div>
          <div style={{ display: "flex", alignItems: "center", gap: 7 }}><Status profile={currentProfile} />
            {currentProfile.verified && <button onClick={() => act("toggle")} disabled={busy} style={{ ...button, minHeight: 28, padding: "5px 8px" }}>{currentProfile.enabled ? "Disable" : "Enable"}</button>}
          </div>
        </div>

        <div className="industrial-config" style={{ display: "grid", gridTemplateColumns: currentProfile.protocol === "mqtt_json" ? "1fr 110px" : "1.4fr 1fr 100px", gap: 8 }}>
          {currentProfile.protocol !== "mqtt_json" && <label style={label}>ENDPOINT<input value={draft.endpoint} onChange={event => setDraft(previous => ({ ...previous, endpoint: event.target.value }))} placeholder={currentProfile.protocol === "opcua" ? "opc.tcp://10.10.0.70:4840" : "10.10.0.51:502"} style={input} /></label>}
          {currentProfile.protocol === "mqtt_json" && <label style={label}>TOPIC FILTER<input value={draft.settings.topic ?? ""} onChange={event => setSettings({ topic: event.target.value })} placeholder="factory/telemetry/+" style={input} /></label>}
          {currentProfile.protocol !== "mqtt_json" && <label style={label}>CREDENTIAL ENV<input value={draft.credential_env} onChange={event => setDraft(previous => ({ ...previous, credential_env: event.target.value }))} placeholder="Optional" style={input} /></label>}
          <label style={label}>POLL SECONDS<input type="number" min="1" max="3600" value={draft.poll_interval_s} onChange={event => setDraft(previous => ({ ...previous, poll_interval_s: Number(event.target.value) }))} style={input} disabled={currentProfile.protocol === "mqtt_json"} /></label>
        </div>

        {currentProfile.protocol === "opcua" && <div style={{ marginTop: 8, maxWidth: 260 }}>
          <label style={label}>SECURITY POLICY<select value={draft.settings.security_policy ?? "Basic256Sha256"} onChange={event => setSettings({ security_policy: event.target.value })} style={input}>
            <option value="Basic256Sha256">Basic256Sha256</option>
            <option value="Aes128Sha256RsaOaep">Aes128Sha256RsaOaep</option>
            <option value="Aes256Sha256RsaPss">Aes256Sha256RsaPss</option>
            <option value="None">None (isolated commissioning only)</option>
          </select></label>
        </div>}

        {currentProfile.protocol === "modbus_tcp" && <div className="industrial-config" style={{ display: "grid", gridTemplateColumns: "90px 1fr 1fr 1fr 1fr", gap: 8, marginTop: 8 }}>
          <label style={label}>UNIT ID<input type="number" min="0" max="247" value={draft.settings.unit_id ?? 1} onChange={event => setSettings({ unit_id: Number(event.target.value) })} style={input} /></label>
          <label style={label}>IDLE W<input type="number" min="0" value={draft.settings.idle_threshold_w ?? 300} onChange={event => setSettings({ idle_threshold_w: Number(event.target.value) })} style={input} /></label>
          <label style={label}>ON W<input type="number" min="1" value={draft.settings.on_threshold_w ?? 2000} onChange={event => setSettings({ on_threshold_w: Number(event.target.value) })} style={input} /></label>
          <label style={label}>DEBOUNCE<input type="number" min="1" max="20" value={draft.settings.debounce_samples ?? 2} onChange={event => setSettings({ debounce_samples: Number(event.target.value) })} style={input} /></label>
          <label style={label}>TARIFF / KWH<input type="number" min="0" step="0.01" value={draft.settings.tariff_per_kwh ?? 0} onChange={event => setSettings({ tariff_per_kwh: Number(event.target.value) })} style={input} /></label>
        </div>}

        <SignalEditor protocol={currentProfile.protocol} definitions={definitions} signals={signals} onChange={next => setSettings({ signals: next })} />

        {currentProfile.protocol === "mqtt_json" && <div className="industrial-config" style={{ display: "grid", gridTemplateColumns: "1fr 1.4fr", gap: 8, marginTop: 12 }}>
          <label style={label}>SAMPLE TOPIC<input value={mqttTopic} onChange={event => setMqttTopic(event.target.value)} style={input} /></label>
          <label style={label}>SAMPLE JSON<textarea value={mqttPayload} onChange={event => setMqttPayload(event.target.value)} rows={3} style={{ ...input, resize: "vertical" }} /></label>
        </div>}

        <div style={{ display: "flex", gap: 7, flexWrap: "wrap", marginTop: 13 }}>
          <button onClick={() => act("save")} disabled={busy || !dirty} style={{ ...button, opacity: dirty ? 1 : .45 }}><Save size={13} /> Save contract</button>
          <button onClick={() => act("simulate")} disabled={busy || !canTest} style={{ ...button, opacity: canTest ? 1 : .45 }}><FlaskConical size={13} /> Simulate</button>
          {currentProfile.protocol === "mqtt_json" ?
            <button onClick={() => act("mqttProbe")} disabled={busy || !canTest} style={{ ...button, opacity: canTest ? 1 : .45 }}><Radio size={13} /> Validate sample</button> :
            <button onClick={() => act("probe")} disabled={busy || !canTest || !draft.endpoint} style={{ ...button, opacity: canTest && draft.endpoint ? 1 : .45 }}><Activity size={13} /> Probe device</button>}
          {currentProfile.protocol === "opcua" && <button onClick={() => act("browse")} disabled={busy || dirty || !draft.endpoint} style={button}><Search size={13} /> Browse nodes</button>}
          {currentProfile.enabled && currentProfile.protocol !== "mqtt_json" && <button onClick={() => act("poll")} disabled={busy} style={button}><RefreshCw size={13} /> Poll now</button>}
          {result?.approvable && <button onClick={() => act("approve")} disabled={busy} style={{ ...button, background: "#14532d", borderColor: "#22c55e" }}><Check size={13} /> Approve and enable</button>}
        </div>
        {dirty && <div style={{ color: "#fbbf24", fontSize: 9, marginTop: 7 }}>Save changes before testing.</div>}
        {error && <div style={{ color: "#fca5a5", fontSize: 10, marginTop: 9 }}>{error}</div>}
        <ProbeResult result={result} />

        {energyProfile && <div style={{ marginTop: 14, borderTop: "1px solid #1f2937", paddingTop: 10 }}>
          <div style={{ color: "#9ca3af", fontSize: 9, fontWeight: 800, marginBottom: 7 }}>UTILITY INTELLIGENCE | {energyProfile.confidence} confidence</div>
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
            <span style={{ fontSize: 10 }}>Energy <strong>{energyProfile.energy_kwh} kWh</strong></span>
            <span style={{ fontSize: 10 }}>Idle share <strong>{Math.round((energyProfile.idle_energy_share ?? 0) * 100)}%</strong></span>
            <span style={{ fontSize: 10 }}>Load factor <strong>{energyProfile.load_factor == null ? "-" : Math.round(energyProfile.load_factor * 100) + "%"}</strong></span>
            <span style={{ fontSize: 10 }}>Coverage <strong>{Math.round((energyProfile.coverage ?? 0) * 100)}%</strong></span>
            {energyProfile.estimated_cost != null && <span style={{ fontSize: 10 }}>Cost <strong>{energyProfile.estimated_cost}</strong></span>}
          </div>
          {energyProfile.alerts.map(alert => <div key={alert.code} style={{ marginTop: 8, color: alert.severity === "warning" ? "#fbbf24" : "#86efac", fontSize: 9 }}>
            {alert.detail} {alert.action}
          </div>)}
        </div>}

        {nodes.length > 0 && <div style={{ marginTop: 14, borderTop: "1px solid #1f2937", paddingTop: 10 }}>
          <div style={{ color: "#9ca3af", fontSize: 9, fontWeight: 800, marginBottom: 6 }}>BROWSED NODES</div>
          <div style={{ maxHeight: 180, overflowY: "auto" }}>{nodes.slice(0, 100).map(node => <div key={node.node_id} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, padding: "6px 0", borderBottom: "1px solid #1f2937" }}>
            <div style={{ minWidth: 0 }}><div style={{ fontSize: 10 }}>{node.name}</div><div style={{ color: "#6b7280", fontSize: 9, overflowWrap: "anywhere" }}>{node.node_id}</div></div>
            <button title="Add node" aria-label={`Add ${node.name}`} onClick={() => {
              const used = new Set(signals.map(signal => signal.key));
              const key = Object.keys(definitions).find(candidate => !used.has(candidate));
              if (key) setSettings({ signals: [...signals, { key, node_id: node.node_id, unit: definitions[key].unit, required: signals.length === 0, scale: 1, offset: 0 }] });
            }} style={{ ...button, width: 30, minHeight: 30, padding: 0 }}><Plus size={13} /></button>
          </div>)}</div>
        </div>}

        {currentProfile.latest?.length > 0 && <div style={{ marginTop: 14, borderTop: "1px solid #1f2937", paddingTop: 10 }}>
          <div style={{ color: "#9ca3af", fontSize: 9, fontWeight: 800, marginBottom: 6 }}>LATEST TELEMETRY</div>
          <div className="industrial-values" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(120px,1fr))", gap: 7 }}>
            {currentProfile.latest.map(item => <div key={item.signal_key} style={{ padding: "6px 0" }}><div style={{ color: "#6b7280", fontSize: 9 }}>{item.signal_key}</div><div style={{ fontSize: 11, fontWeight: 800, marginTop: 2 }}>{item.value_num ?? item.value_text ?? "-"} <span style={{ color: "#6b7280", fontSize: 8 }}>{item.unit}</span></div></div>)}
          </div>
        </div>}
      </div>
    </div>
  </div>;
}
