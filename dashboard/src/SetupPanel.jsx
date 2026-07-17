import { useMemo, useState } from "react";
import { Download, FileSearch, Fingerprint, FolderSearch, KeyRound, PlugZap, RefreshCw, RotateCw, ShieldCheck, ShieldX, Terminal } from "lucide-react";

function cloneConfig(config) {
  return JSON.parse(JSON.stringify(config ?? {}));
}

function Field({ label, children }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 5, minWidth: 0 }}>
      <span style={labelStyle}>{label}</span>
      {children}
    </label>
  );
}

function Section({ title, children }) {
  return (
    <div>
      <div style={eyebrowStyle}>{title}</div>
      <div style={{ marginTop: 10 }}>{children}</div>
    </div>
  );
}

function TextInput({ value, onChange, placeholder, disabled = false }) {
  return (
    <input value={value ?? ""} onChange={event => onChange(event.target.value)}
           placeholder={placeholder} disabled={disabled} style={{ ...inputStyle, opacity: disabled ? .55 : 1 }} />
  );
}

function NumberInput({ value, onChange, placeholder, disabled = false }) {
  return (
    <input type="number" value={value ?? ""} onChange={event => onChange(event.target.value === "" ? "" : Number(event.target.value))}
           placeholder={placeholder} disabled={disabled} style={{ ...inputStyle, opacity: disabled ? .55 : 1 }} />
  );
}

function updateListItem(list, index, key, value) {
  return list.map((item, itemIndex) => itemIndex === index ? { ...item, [key]: value } : item);
}

export function SetupPanel({ config, onClose, onSave, onRemoteAction }) {
  const initial = useMemo(() => cloneConfig(config), [config]);
  const [draft, setDraft] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [remoteMachineKey, setRemoteMachineKey] = useState(initial.maestro_agents?.[0]?.machine_key ?? "");
  const [remoteUser, setRemoteUser] = useState("");
  const [sshPort, setSshPort] = useState(22);
  const [remoteBusy, setRemoteBusy] = useState(false);
  const [remoteResult, setRemoteResult] = useState(null);
  const [remotePlan, setRemotePlan] = useState(null);
  const [scanResult, setScanResult] = useState(null);
  const [fingerprint, setFingerprint] = useState("");
  const [fingerprintConfirmed, setFingerprintConfirmed] = useState(false);
  const [liveMode, setLiveMode] = useState(false);

  const setMqtt = (key, value) => {
    setDraft(prev => ({ ...prev, mqtt: { ...(prev.mqtt ?? {}), [key]: value } }));
  };

  const setEnergyDefaults = (key, value) => {
    setDraft(prev => ({ ...prev, energy_defaults: { ...(prev.energy_defaults ?? {}), [key]: value } }));
  };

  const setMaestro = (index, key, value) => {
    setDraft(prev => ({
      ...prev,
      maestro_agents: updateListItem(prev.maestro_agents ?? [], index, key, value),
    }));
  };

  const setEnergy = (index, key, value) => {
    setDraft(prev => ({
      ...prev,
      energy_meters: updateListItem(prev.energy_meters ?? [], index, key, value),
    }));
  };

  const submit = async event => {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    try {
      const result = await onSave(draft);
      setMessage(`Saved. Backup: ${result.backup_path}`);
    } catch (error) {
      setMessage(error.message || "Save failed");
    } finally {
      setBusy(false);
    }
  };

  const selectedRemoteAgent = (draft.maestro_agents ?? []).find(
    agent => agent.machine_key === remoteMachineKey
  );

  const runRemote = async kind => {
    if (!remoteMachineKey) return;
    setRemoteBusy(true);
    setRemoteResult(null);
    try {
      const payload = {
        machine_key: remoteMachineKey,
        host: selectedRemoteAgent?.host,
        log_folder: selectedRemoteAgent?.log_folder,
        cnc_folder: selectedRemoteAgent?.cnc_folder,
        username: remoteUser,
        port: sshPort,
        execute: ["folders", "install", "restart", "log"].includes(kind) && liveMode,
      };
      if (kind === "trust") {
        payload.fingerprint = fingerprint;
        payload.expected_version = remotePlan?.host_trust?.version;
      }
      const result = await onRemoteAction(kind, payload);
      setRemoteResult(result);
      if (kind === "plan") setRemotePlan(result);
      if (kind === "scan") {
        setScanResult(result);
        setFingerprint(result.keys?.[0]?.fingerprint ?? "");
        setFingerprintConfirmed(false);
      }
      if (kind === "folders" && payload.execute) {
        const log = result.log_candidates?.find(item => item.exists)?.path;
        const cnc = result.cnc_candidates?.find(item => item.exists)?.path;
        const index = (draft.maestro_agents ?? []).findIndex(agent => agent.machine_key === remoteMachineKey);
        if (index >= 0 && (log || cnc)) {
          setDraft(current => ({
            ...current,
            maestro_agents: updateListItem(
              current.maestro_agents ?? [], index, "log_folder", log ?? current.maestro_agents[index].log_folder
            ).map((agent, agentIndex) => agentIndex === index && cnc ? { ...agent, cnc_folder: cnc } : agent),
          }));
          setMessage("Detected machine folders added to the setup draft.");
        }
      }
      if (["identity", "trust", "forget", "install"].includes(kind)) {
        const refreshed = await onRemoteAction("plan", payload);
        setRemotePlan(refreshed);
        if (kind === "forget") setLiveMode(false);
      }
    } catch (error) {
      setRemoteResult({ status: "error", detail: error.message || "Remote setup action failed" });
    } finally {
      setRemoteBusy(false);
    }
  };

  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 130, background: "rgba(0,0,0,.78)",
                  display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}
         onClick={onClose}>
      <form style={{ width: "min(1180px, 100%)", maxHeight: "90vh", overflowY: "auto",
                     background: "#0d1117", border: "1px solid #374151", borderRadius: 8,
                     padding: 20, boxSizing: "border-box" }} onSubmit={submit} onClick={event => event.stopPropagation()}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 16,
                      alignItems: "center", marginBottom: 18 }}>
          <div>
            <div style={{ fontSize: 18, fontWeight: 800 }}>Site Setup</div>
            <div style={{ color: "#6b7280", fontSize: 11, marginTop: 3 }}>
              Configure the CV PC, machine agents, and energy meters without editing YAML.
            </div>
          </div>
          <button type="button" onClick={onClose} style={{ background: "none", border: 0,
                                                           color: "#9ca3af", fontSize: 20,
                                                           cursor: "pointer" }}>x</button>
        </div>

        {message && (
          <div style={{ color: /^(Saved|Detected)/.test(message) ? "#22c55e" : "#ef4444",
                        fontSize: 11, marginBottom: 12, overflowWrap: "anywhere" }}>
            {message}
          </div>
        )}

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
                      gap: 12, marginBottom: 12 }}>
          <Section title="Central PC">
            <div style={{ ...formGridStyle, border: "1px solid #1f2937", borderRadius: 6,
                          padding: 12 }}>
              <Field label="Cabinet Vision export folder">
                <TextInput value={draft.cv_watch_folder}
                           onChange={value => setDraft(prev => ({ ...prev, cv_watch_folder: value }))}
                           placeholder="C:\\CabinetVision\\Export" />
              </Field>
              <Field label="MQTT broker host">
                <TextInput value={draft.mqtt?.broker_host}
                           onChange={value => setMqtt("broker_host", value)}
                           placeholder="Central PC IP" disabled={draft.mqtt?.require_tls} />
              </Field>
              <Field label="MQTT broker port">
                <NumberInput value={draft.mqtt?.broker_port}
                             onChange={value => setMqtt("broker_port", value)}
                             placeholder="8883" disabled={draft.mqtt?.require_tls} />
              </Field>
              <Field label="Topic prefix">
                <TextInput value={draft.mqtt?.topic_prefix}
                           onChange={value => setMqtt("topic_prefix", value)}
                           placeholder="hive/machines" disabled={draft.mqtt?.require_tls} />
              </Field>
            </div>
          </Section>

          <Section title="Energy Defaults">
            <div style={{ ...formGridStyle, border: "1px solid #1f2937", borderRadius: 6,
                          padding: 12 }}>
              <Field label="On threshold W">
                <NumberInput value={draft.energy_defaults?.on_threshold_w}
                             onChange={value => setEnergyDefaults("on_threshold_w", value)}
                             placeholder="2000" />
              </Field>
              <Field label="Idle threshold W">
                <NumberInput value={draft.energy_defaults?.idle_threshold_w}
                             onChange={value => setEnergyDefaults("idle_threshold_w", value)}
                             placeholder="300" />
              </Field>
              <Field label="Poll interval sec">
                <NumberInput value={draft.energy_defaults?.poll_interval_s}
                             onChange={value => setEnergyDefaults("poll_interval_s", value)}
                             placeholder="5" />
              </Field>
            </div>
          </Section>
        </div>

        <div style={{ marginBottom: 16 }}>
          <Section title="Remote Agent Setup">
            <div style={{ border: "1px solid #1f2937", borderRadius: 6, padding: 12 }}>
              <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 10 }}>
                <span style={{ ...statusStyle, color: remotePlan?.identity?.status === "ready" ? "#86efac" : "#fbbf24" }}>
                  Key {remotePlan?.identity?.status ?? "unchecked"}
                </span>
                <span style={{ ...statusStyle, color: remotePlan?.host_trust?.status === "trusted" ? "#86efac" : "#fbbf24" }}>
                  Host {remotePlan?.host_trust?.status ?? "untrusted"}
                </span>
                <span style={{ ...statusStyle, color: remotePlan?.agent_payload?.ready ? "#86efac" : "#fbbf24" }}>
                  Payload {remotePlan?.agent_payload?.status ?? "unchecked"}
                </span>
                <span style={{ ...statusStyle, color: liveMode ? "#fca5a5" : "#93c5fd" }}>
                  {liveMode ? "Live SSH" : "Preview"}
                </span>
              </div>
              <div style={{ ...formGridStyle, gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))" }}>
                <Field label="Machine">
                  <select value={remoteMachineKey} onChange={event => {
                    setRemoteMachineKey(event.target.value);
                    setRemoteResult(null);
                    setRemotePlan(null);
                    setScanResult(null);
                    setFingerprint("");
                    setFingerprintConfirmed(false);
                    setLiveMode(false);
                  }} style={inputStyle}>
                    {(draft.maestro_agents ?? []).map(agent => (
                      <option key={agent.machine_key} value={agent.machine_key}>{agent.label}</option>
                    ))}
                  </select>
                </Field>
                <Field label="Host / IP">
                  <TextInput value={selectedRemoteAgent?.host} onChange={value => {
                    const index = (draft.maestro_agents ?? []).findIndex(agent => agent.machine_key === remoteMachineKey);
                    if (index >= 0) setMaestro(index, "host", value);
                  }} placeholder="192.168.1.xxx" />
                </Field>
                <Field label="SSH port">
                  <NumberInput value={sshPort} onChange={setSshPort} placeholder="22" />
                </Field>
                <Field label="Username (session only)">
                  <TextInput value={remoteUser} onChange={setRemoteUser} placeholder="Windows admin user" />
                </Field>
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 10 }}>
                <button type="button" disabled={remoteBusy} onClick={() => runRemote("plan")} style={actionButtonStyle}>
                  <RefreshCw size={13} /> Status
                </button>
                <button type="button" disabled={remoteBusy} onClick={() => runRemote("identity")} style={actionButtonStyle}>
                  <KeyRound size={13} /> Generate key
                </button>
                <button type="button" disabled={remoteBusy || !selectedRemoteAgent?.host} onClick={() => runRemote("test")} style={actionButtonStyle}>
                  <PlugZap size={13} /> Test port
                </button>
                <button type="button" disabled={remoteBusy || !selectedRemoteAgent?.host} onClick={() => runRemote("scan")} style={actionButtonStyle}>
                  <Fingerprint size={13} /> Scan fingerprint
                </button>
                <button type="button" disabled={remoteBusy || !remotePlan?.ready_for_live_execution} onClick={() => runRemote("auth")} style={actionButtonStyle}>
                  <Terminal size={13} /> Authenticate
                </button>
              </div>
              {scanResult?.keys?.length > 0 && <div style={{ borderTop: "1px solid #1f2937", marginTop: 12, paddingTop: 10 }}>
                <div style={labelStyle}>Machine host fingerprint</div>
                <div style={{ display: "grid", gap: 6, marginTop: 7 }}>
                  {scanResult.keys.map(key => <label key={key.fingerprint} style={{ display: "flex", gap: 8, alignItems: "center", color: "#d1d5db", fontSize: 10, minWidth: 0 }}>
                    <input type="radio" name="host-fingerprint" checked={fingerprint === key.fingerprint} onChange={() => { setFingerprint(key.fingerprint); setFingerprintConfirmed(false); }} />
                    <code style={{ overflowWrap: "anywhere" }}>{key.key_type} {key.fingerprint}</code>
                  </label>)}
                </div>
                <label style={{ display: "flex", gap: 8, alignItems: "center", color: fingerprintConfirmed ? "#86efac" : "#fbbf24", fontSize: 10, marginTop: 9 }}>
                  <input type="checkbox" checked={fingerprintConfirmed} onChange={event => setFingerprintConfirmed(event.target.checked)} />
                  Fingerprint matches the machine screen
                </label>
                <button type="button" disabled={remoteBusy || !remoteUser || !fingerprint || !fingerprintConfirmed} onClick={() => runRemote("trust")}
                        style={{ ...actionButtonStyle, marginTop: 8, opacity: !remoteUser || !fingerprintConfirmed ? .45 : 1 }}>
                  <ShieldCheck size={13} /> Trust host
                </button>
              </div>}
              <div style={{ borderTop: "1px solid #1f2937", marginTop: 12, paddingTop: 10 }}>
                <label style={{ display: "inline-flex", gap: 8, alignItems: "center", color: liveMode ? "#fca5a5" : "#9ca3af", fontSize: 10 }}>
                  <input type="checkbox" checked={liveMode} disabled={!remotePlan?.ready_for_live_execution}
                         onChange={event => setLiveMode(event.target.checked)} />
                  Execute approved SSH actions
                </label>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 9 }}>
                  <button type="button" disabled={remoteBusy} onClick={() => runRemote("folders")} style={actionButtonStyle}>
                    <FolderSearch size={13} /> {liveMode ? "Detect folders" : "Preview folders"}
                  </button>
                  <button type="button" disabled={remoteBusy} onClick={() => runRemote("install")} style={actionButtonStyle}>
                    <Download size={13} /> {liveMode ? "Install agent" : "Preview install"}
                  </button>
                  <button type="button" disabled={remoteBusy || !liveMode} onClick={() => runRemote("restart")} style={actionButtonStyle}>
                    <RotateCw size={13} /> Restart
                  </button>
                  <button type="button" disabled={remoteBusy || !liveMode} onClick={() => runRemote("log")} style={actionButtonStyle}>
                    <FileSearch size={13} /> Fetch log
                  </button>
                  {remotePlan?.host_trust?.status === "trusted" && <button type="button" disabled={remoteBusy || liveMode} onClick={() => runRemote("forget")}
                    style={{ ...actionButtonStyle, color: "#fca5a5" }}><ShieldX size={13} /> Revoke trust</button>}
                </div>
              </div>
              {remoteBusy && <div style={{ color: "#60a5fa", fontSize: 11, marginTop: 10 }}>Working...</div>}
              {remoteResult && (
                <pre style={{ background: "#111827", border: "1px solid #1f2937", borderRadius: 6,
                              padding: 10, color: remoteResult.status === "error" ? "#ef4444" : "#d1d5db",
                              fontSize: 10, lineHeight: 1.5, whiteSpace: "pre-wrap", overflowWrap: "anywhere",
                              marginTop: 10, maxHeight: 240, overflowY: "auto" }}>
                  {JSON.stringify(remoteResult, null, 2)}
                </pre>
              )}
            </div>
          </Section>
        </div>

        <Section title="Maestro Machine PCs">
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(270px, 1fr))",
                        gap: 10 }}>
            {(draft.maestro_agents ?? []).map((agent, index) => (
              <div key={agent.machine_key} style={cardStyle}>
                <div style={{ fontSize: 12, fontWeight: 800, marginBottom: 8 }}>{agent.label}</div>
                <div style={formGridStyle}>
                  <Field label="Machine key">
                    <TextInput value={agent.machine_key} onChange={value => setMaestro(index, "machine_key", value)} />
                  </Field>
                  <Field label="PC host / IP">
                    <TextInput value={agent.host} onChange={value => setMaestro(index, "host", value)}
                               placeholder="192.168.1.xxx" />
                  </Field>
                  <Field label="Maestro log folder">
                    <TextInput value={agent.log_folder} onChange={value => setMaestro(index, "log_folder", value)}
                               placeholder="C:\\SCM\\Maestro\\Logs" />
                  </Field>
                  <Field label="CNC folder">
                    <TextInput value={agent.cnc_folder} onChange={value => setMaestro(index, "cnc_folder", value)}
                               placeholder="optional" />
                  </Field>
                </div>
              </div>
            ))}
          </div>
        </Section>

        <Section title="Energy Meters">
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(270px, 1fr))",
                        gap: 10 }}>
            {(draft.energy_meters ?? []).map((meter, index) => (
              <div key={meter.machine_key} style={cardStyle}>
                <div style={{ fontSize: 12, fontWeight: 800, marginBottom: 8 }}>{meter.label}</div>
                <div style={formGridStyle}>
                  <Field label="Machine key">
                    <TextInput value={meter.machine_key} onChange={value => setEnergy(index, "machine_key", value)} />
                  </Field>
                  <Field label="Modbus host / IP">
                    <TextInput value={meter.modbus_host} onChange={value => setEnergy(index, "modbus_host", value)}
                               placeholder="192.168.1.xxx" />
                  </Field>
                  <Field label="Port">
                    <NumberInput value={meter.modbus_port} onChange={value => setEnergy(index, "modbus_port", value)}
                                 placeholder="502" />
                  </Field>
                  <Field label="Unit ID">
                    <NumberInput value={meter.unit_id} onChange={value => setEnergy(index, "unit_id", value)}
                                 placeholder="1" />
                  </Field>
                </div>
              </div>
            ))}
          </div>
        </Section>

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8,
                      position: "sticky", bottom: 0, background: "#0d1117",
                      paddingTop: 14, marginTop: 14 }}>
          <button type="button" onClick={onClose} style={buttonStyle}>Close</button>
          <button disabled={busy} style={{ ...buttonStyle, background: "#14532d",
                                           opacity: busy ? 0.6 : 1 }}>
            {busy ? "Saving..." : "Save setup"}
          </button>
        </div>
      </form>
    </div>
  );
}

const eyebrowStyle = {
  color: "#6b7280",
  fontSize: 10,
  fontWeight: 800,
  letterSpacing: 1,
  textTransform: "uppercase",
};

const labelStyle = {
  color: "#6b7280",
  fontSize: 9,
  fontWeight: 800,
  textTransform: "uppercase",
};

const formGridStyle = {
  display: "grid",
  gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
  gap: 8,
};

const inputStyle = {
  width: "100%",
  boxSizing: "border-box",
  minHeight: 32,
  background: "#111827",
  border: "1px solid #374151",
  color: "#f9fafb",
  padding: "6px 8px",
  borderRadius: 6,
  fontSize: 11,
};

const buttonStyle = {
  background: "#1f2937",
  border: "1px solid #374151",
  color: "#f9fafb",
  padding: "7px 12px",
  borderRadius: 6,
  fontSize: 11,
  cursor: "pointer",
};

const actionButtonStyle = {
  ...buttonStyle,
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  gap: 6,
};

const statusStyle = {
  fontSize: 9,
  fontWeight: 800,
  textTransform: "uppercase",
};

const cardStyle = {
  border: "1px solid #1f2937",
  borderRadius: 6,
  padding: 12,
  background: "#0f1623",
};
