import { useMemo, useState } from "react";
import {
  AlertTriangle, Bell, Check, Clock3, RefreshCw, RotateCcw, Send,
  Settings, ShieldCheck, X, XCircle,
} from "lucide-react";

const button = { display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6,
  background: "#1f2937", border: "1px solid #374151", color: "#e5e7eb", borderRadius: 5,
  padding: "7px 10px", minHeight: 32, fontSize: 11, fontWeight: 700, cursor: "pointer" };
const input = { width: "100%", minWidth: 0, background: "#0d1117", border: "1px solid #374151",
  color: "#e5e7eb", borderRadius: 5, padding: "7px 8px", minHeight: 32, fontSize: 11 };
const label = { color: "#6b7280", fontSize: 9, fontWeight: 800, textTransform: "uppercase" };

const severityColor = { critical: "#ef4444", warning: "#f59e0b", info: "#60a5fa" };
const deliveryColor = { delivered: "#22c55e", failed: "#ef4444", pending: "#f59e0b" };

function Field({ name, children }) {
  return <label style={{ display: "flex", flexDirection: "column", gap: 5, minWidth: 0 }}>
    <span style={label}>{name}</span>{children}
  </label>;
}

function formatTime(value) {
  if (!value) return "Not recorded";
  return new Date(value).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function AlertItem({ item, onAction }) {
  const [expanded, setExpanded] = useState(false);
  const [draft, setDraft] = useState({ actor: "", owner: item.owner || "", notes: "", snooze_minutes: 30 });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const due = item.status === "open" && item.response_due_at && new Date(item.response_due_at) <= new Date();
  const set = (key, value) => setDraft(current => ({ ...current, [key]: value }));
  const act = async action => {
    setBusy(true); setMessage("");
    try {
      await onAction(item.id, { action, actor: draft.actor, owner: draft.owner || undefined,
        notes: draft.notes || undefined, snooze_minutes: action === "snooze" ? Number(draft.snooze_minutes) : undefined,
        expected_version: item.version });
    } catch (error) { setMessage(error.message); } finally { setBusy(false); }
  };
  return <article style={{ border: `1px solid ${due ? "#7f1d1d" : "#263244"}`, borderLeft: `3px solid ${severityColor[item.severity]}`,
    borderRadius: 7, background: "#111827", padding: 13 }}>
    <button onClick={() => setExpanded(value => !value)} style={{ width: "100%", border: 0, background: "transparent",
      color: "inherit", cursor: "pointer", textAlign: "left", padding: 0 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 14, alignItems: "flex-start" }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 7, flexWrap: "wrap" }}>
            <span style={{ color: severityColor[item.severity], fontSize: 9, fontWeight: 900, textTransform: "uppercase" }}>{item.severity}</span>
            <span style={{ color: "#9ca3af", fontSize: 9, fontWeight: 800, textTransform: "uppercase" }}>{item.status}</span>
            <span style={{ color: "#6b7280", fontSize: 9 }}>{item.domain}</span>
            {item.machine_name && <span style={{ color: "#6b7280", fontSize: 9 }}>{item.machine_name}</span>}
            {due && <span style={{ color: "#fca5a5", fontSize: 9, fontWeight: 800 }}>RESPONSE OVERDUE</span>}
          </div>
          <h3 style={{ fontSize: 13, marginTop: 5, overflowWrap: "anywhere" }}>{item.title}</h3>
          <div style={{ color: "#9ca3af", fontSize: 10, marginTop: 4, lineHeight: 1.45 }}>{item.detail}</div>
        </div>
        <div style={{ color: "#6b7280", fontSize: 9, textAlign: "right", flex: "0 0 auto" }}>
          <div>{formatTime(item.occurred_at)}</div>
          <div style={{ marginTop: 4 }}>{item.occurrence_count} occurrence{item.occurrence_count === 1 ? "" : "s"}</div>
        </div>
      </div>
    </button>
    {expanded && <div style={{ borderTop: "1px solid #263244", marginTop: 12, paddingTop: 12 }}>
      <div className="alert-facts" style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
        <div><div style={label}>Required action</div><div style={{ color: "#d1d5db", fontSize: 10, lineHeight: 1.45, marginTop: 4 }}>{item.required_action}</div></div>
        <div><div style={label}>Consequence</div><div style={{ color: "#fca5a5", fontSize: 10, lineHeight: 1.45, marginTop: 4 }}>{item.consequence}</div></div>
        <div><div style={label}>Response</div><div style={{ color: due ? "#fca5a5" : "#d1d5db", fontSize: 10, lineHeight: 1.45, marginTop: 4 }}>
          Due {formatTime(item.response_due_at)} · escalation L{item.escalation_level}<br />Role: {item.owner_role}{item.owner ? ` · ${item.owner}` : ""}
        </div></div>
      </div>
      <div className="alert-action-form" style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1.5fr 110px", gap: 8, marginTop: 13 }}>
        <Field name="Operator"><input value={draft.actor} onChange={event => set("actor", event.target.value)} placeholder="Full name" style={input} /></Field>
        <Field name="Owner"><input value={draft.owner} onChange={event => set("owner", event.target.value)} placeholder={item.owner_role} style={input} /></Field>
        <Field name={item.status === "resolved" ? "Reopen note" : "Disposition / snooze reason"}>
          <input value={draft.notes} onChange={event => set("notes", event.target.value)} style={input} />
        </Field>
        {item.status !== "resolved" && <Field name="Snooze min"><input type="number" min="5" max="1440" value={draft.snooze_minutes}
          onChange={event => set("snooze_minutes", event.target.value)} style={input} /></Field>}
      </div>
      <div style={{ display: "flex", gap: 7, flexWrap: "wrap", marginTop: 9 }}>
        {item.status === "open" && <button disabled={busy || !draft.actor} onClick={() => act("acknowledge")} style={{ ...button, opacity: busy || !draft.actor ? .45 : 1 }}><Check size={14} /> Acknowledge</button>}
        {["open", "acknowledged"].includes(item.status) && <button disabled={busy || !draft.actor || !draft.notes} onClick={() => act("snooze")}
          style={{ ...button, opacity: busy || !draft.actor || !draft.notes ? .45 : 1 }}><Clock3 size={14} /> Snooze</button>}
        {item.status !== "resolved" && <button disabled={busy || !draft.actor || !draft.notes} onClick={() => act("resolve")}
          style={{ ...button, opacity: busy || !draft.actor || !draft.notes ? .45 : 1 }}><XCircle size={14} /> Resolve</button>}
        {item.status === "resolved" && <button disabled={busy || !draft.actor} onClick={() => act("reopen")}
          style={{ ...button, opacity: busy || !draft.actor ? .45 : 1 }}><RotateCcw size={14} /> Reopen</button>}
      </div>
      {message && <div style={{ color: "#f87171", fontSize: 10, marginTop: 9 }}>{message}</div>}
      {!!item.events?.length && <details style={{ marginTop: 12 }}><summary style={{ color: "#6b7280", fontSize: 9, cursor: "pointer" }}>Lifecycle history ({item.events.length})</summary>
        <div style={{ display: "grid", gap: 5, marginTop: 7 }}>{item.events.slice(0, 12).map(event => <div key={event.id} style={{ color: "#9ca3af", fontSize: 9 }}>
          {formatTime(event.ts)} · {event.event_type.replaceAll("_", " ")} · {event.actor}{event.notes ? ` · ${event.notes}` : ""}
        </div>)}</div></details>}
    </div>}
  </article>;
}

function DeliveryConsole({ data, onDestination, onTestDestination, onDispatch, onSettings }) {
  const existing = data.destinations[0];
  const [actor, setActor] = useState("");
  const [destination, setDestination] = useState({ key: existing?.destination_key || "shift_webhook",
    name: existing?.name || "Shift alert webhook", endpoint: existing?.endpoint || "", secret_env: existing?.secret_env || "",
    min_severity: existing?.min_severity || "warning", enabled: Boolean(existing?.enabled), expected_version: existing?.version });
  const [settings, setSettings] = useState({ auto_sync: Boolean(data.settings.auto_sync), auto_dispatch: Boolean(data.settings.auto_dispatch),
    interval_seconds: data.settings.interval_seconds, expected_version: data.settings.version });
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const run = async (name, action) => {
    setBusy(name); setMessage("");
    try { const result = await action(); setMessage(name === "simulate" ? "Simulation passed. No network request was sent." : `${name} completed.`); return result; }
    catch (error) { setMessage(error.message); } finally { setBusy(""); }
  };
  const verified = Boolean(existing?.verified_at);
  const saveDestination = () => run("save", () => onDestination(destination.key, { name: destination.name, endpoint: destination.endpoint,
    secret_env: destination.secret_env || undefined, min_severity: destination.min_severity, enabled: destination.enabled,
    expected_version: destination.expected_version, actor }));
  const test = live => run(live ? "live test" : "simulate", () => onTestDestination(destination.key, { live, actor }));
  const saveSettings = () => run("settings", () => onSettings({ ...settings, interval_seconds: Number(settings.interval_seconds), actor }));
  return <div style={{ display: "grid", gap: 18, minWidth: 0, width: "100%" }}>
    <section style={{ minWidth: 0 }}><div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 10 }}><Settings size={15} color="#60a5fa" /><h3 style={{ fontSize: 13 }}>Automation</h3></div>
      <div className="alert-settings" style={{ display: "grid", gridTemplateColumns: "1fr 1fr 130px 1fr", gap: 10, alignItems: "end" }}>
        <label style={{ ...input, display: "flex", alignItems: "center", gap: 8 }}><input type="checkbox" checked={settings.auto_sync} onChange={event => setSettings(value => ({ ...value, auto_sync: event.target.checked }))} /> Automatic condition sync</label>
        <label style={{ ...input, display: "flex", alignItems: "center", gap: 8 }}><input type="checkbox" checked={settings.auto_dispatch} onChange={event => setSettings(value => ({ ...value, auto_dispatch: event.target.checked }))} /> Automatic dispatch</label>
        <Field name="Interval seconds"><input type="number" min="15" max="3600" value={settings.interval_seconds} onChange={event => setSettings(value => ({ ...value, interval_seconds: event.target.value }))} style={input} /></Field>
        <button disabled={!actor || busy} onClick={saveSettings} style={{ ...button, opacity: !actor || busy ? .45 : 1 }}><Check size={14} /> Save automation</button>
      </div>
      <div style={{ color: "#6b7280", fontSize: 9, marginTop: 7 }}>Automatic dispatch requires an enabled destination that passed a live test.</div>
    </section>
    <section style={{ borderTop: "1px solid #263244", paddingTop: 16, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, marginBottom: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 7 }}><ShieldCheck size={15} color={verified ? "#22c55e" : "#f59e0b"} /><h3 style={{ fontSize: 13 }}>Webhook destination</h3></div>
        <span style={{ color: verified ? "#22c55e" : "#f59e0b", fontSize: 9, fontWeight: 800 }}>{verified ? "LIVE VERIFIED" : "NOT VERIFIED"}</span>
      </div>
      <div className="alert-destination" style={{ display: "grid", gridTemplateColumns: "1fr 1fr 2fr 1fr 130px", gap: 8 }}>
        <Field name="Key"><input value={destination.key} disabled={Boolean(existing)} onChange={event => setDestination(value => ({ ...value, key: event.target.value }))} style={input} /></Field>
        <Field name="Name"><input value={destination.name} onChange={event => setDestination(value => ({ ...value, name: event.target.value }))} style={input} /></Field>
        <Field name="HTTPS endpoint"><input value={destination.endpoint} placeholder="https://gateway.example/hive" onChange={event => setDestination(value => ({ ...value, endpoint: event.target.value }))} style={input} /></Field>
        <Field name="HMAC secret env"><input value={destination.secret_env} placeholder="HIVE_ALERT_SECRET" onChange={event => setDestination(value => ({ ...value, secret_env: event.target.value.toUpperCase() }))} style={input} /></Field>
        <Field name="Minimum severity"><select value={destination.min_severity} onChange={event => setDestination(value => ({ ...value, min_severity: event.target.value }))} style={input}>
          <option value="info">Info</option><option value="warning">Warning</option><option value="critical">Critical</option>
        </select></Field>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 7, flexWrap: "wrap", marginTop: 10 }}>
        <label style={{ ...input, width: "auto", display: "inline-flex", alignItems: "center", gap: 7 }}><input type="checkbox" checked={destination.enabled}
          disabled={!verified} onChange={event => setDestination(value => ({ ...value, enabled: event.target.checked }))} /> Enabled</label>
        <button disabled={!actor || !destination.endpoint || busy} onClick={saveDestination} style={{ ...button, opacity: !actor || !destination.endpoint || busy ? .45 : 1 }}><Check size={14} /> Save contract</button>
        <button disabled={!actor || !existing || busy} onClick={() => test(false)} style={{ ...button, opacity: !actor || !existing || busy ? .45 : 1 }}><ShieldCheck size={14} /> Simulate</button>
        <button disabled={!actor || !existing || busy} onClick={() => test(true)} title="Sends a real network request" style={{ ...button, background: "#7f1d1d", borderColor: "#ef4444", opacity: !actor || !existing || busy ? .45 : 1 }}><Send size={14} /> Send live test</button>
        <button disabled={!actor || !verified || busy} onClick={() => run("dispatch", () => onDispatch({ limit: 50, actor }))} style={{ ...button, opacity: !actor || !verified || busy ? .45 : 1 }}><Send size={14} /> Dispatch pending</button>
      </div>
      <div style={{ color: "#6b7280", fontSize: 9, marginTop: 7 }}>Simulation stays local. A live test sends one CloudEvents request, verifies the contract, and queues current active state.</div>
    </section>
    <section style={{ borderTop: "1px solid #263244", paddingTop: 16, minWidth: 0 }}>
      <h3 style={{ fontSize: 13, marginBottom: 9 }}>Delivery history</h3>
      <div style={{ overflowX: "auto", width: "100%", maxWidth: "100%", minWidth: 0 }}><table style={{ width: "100%", minWidth: 700, borderCollapse: "collapse", fontSize: 10 }}>
        <thead><tr style={{ color: "#6b7280", textAlign: "left" }}>{["Time", "Destination", "Alert", "Event", "Status", "Attempts"].map(value => <th key={value} style={{ padding: 7, borderBottom: "1px solid #263244" }}>{value}</th>)}</tr></thead>
        <tbody>{data.deliveries.slice(0, 30).map(row => <tr key={row.id}><td style={{ padding: 7, borderBottom: "1px solid #1f2937" }}>{formatTime(row.created_at)}</td>
          <td style={{ padding: 7, borderBottom: "1px solid #1f2937" }}>{row.destination_name}</td><td style={{ padding: 7, borderBottom: "1px solid #1f2937" }}>{row.title || "Destination test"}</td>
          <td style={{ padding: 7, borderBottom: "1px solid #1f2937" }}>{row.event_type.replaceAll("_", " ")}</td><td style={{ color: deliveryColor[row.status], padding: 7, borderBottom: "1px solid #1f2937", fontWeight: 800 }}>{row.status}</td>
          <td style={{ padding: 7, borderBottom: "1px solid #1f2937" }}>{row.attempts}/5</td></tr>)}</tbody>
      </table>{!data.deliveries.length && <div style={{ color: "#6b7280", fontSize: 10, padding: "14px 0" }}>No deliveries have been queued.</div>}</div>
    </section>
    <Field name="Named operator"><input value={actor} onChange={event => setActor(event.target.value)} placeholder="Required for commissioning changes" style={input} /></Field>
    {message && <div style={{ color: message.includes("passed") || message.includes("completed") ? "#22c55e" : "#f87171", fontSize: 10 }}>{message}</div>}
  </div>;
}

export function AlertCenter({ data, onSync, onAction, onDestination, onTestDestination, onDispatch, onSettings, onClose }) {
  const [tab, setTab] = useState("open");
  const [syncActor, setSyncActor] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const rows = useMemo(() => data.alerts.filter(item => item.status === tab), [data.alerts, tab]);
  const sync = async () => { setBusy(true); setMessage(""); try { await onSync({ actor: syncActor }); setMessage("Conditions synchronized"); }
    catch (error) { setMessage(error.message); } finally { setBusy(false); } };
  const tabs = [["open", `Open ${data.summary.open}`], ["acknowledged", `Acknowledged ${data.summary.acknowledged}`],
    ["snoozed", `Snoozed ${data.summary.snoozed}`], ["resolved", `Resolved ${data.summary.resolved}`], ["delivery", `Delivery ${data.summary.failed_deliveries || ""}`]];
  return <div style={{ position: "fixed", inset: 0, background: "rgba(2,6,23,.88)", zIndex: 1100,
    display: "flex", alignItems: "center", justifyContent: "center", padding: 18 }}>
    <div style={{ width: "min(1220px,96vw)", maxHeight: "94vh", overflow: "hidden", background: "#0d1117",
      border: "1px solid #374151", borderRadius: 8, color: "#f9fafb", display: "flex", flexDirection: "column" }}>
      <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 14, borderBottom: "1px solid #263244", padding: "14px 16px" }}>
        <div><div style={{ display: "flex", alignItems: "center", gap: 8 }}><Bell size={18} color={data.summary.critical_unacknowledged ? "#ef4444" : "#60a5fa"} /><h2 style={{ fontSize: 16 }}>Alert center</h2></div>
          <div style={{ color: "#6b7280", fontSize: 10, marginTop: 4 }}>{data.summary.active} active · {data.summary.critical_unacknowledged} critical unacknowledged · {data.summary.response_overdue} response overdue</div></div>
        <div style={{ display: "flex", gap: 7, flexWrap: "wrap", justifyContent: "flex-end" }}>
          <input aria-label="Operator for condition sync" value={syncActor} onChange={event => setSyncActor(event.target.value)} placeholder="Operator name" style={{ ...input, width: 150 }} />
          <button onClick={sync} disabled={busy || !syncActor} style={{ ...button, opacity: busy || !syncActor ? .45 : 1 }}><RefreshCw size={14} /> Sync conditions</button>
          <button onClick={onClose} title="Close" style={{ ...button, width: 32, padding: 0 }}><X size={16} /></button></div>
      </header>
      <nav style={{ display: "flex", gap: 4, padding: "10px 16px 0", overflowX: "auto" }}>{tabs.map(([key, text]) => <button key={key} onClick={() => setTab(key)}
        style={{ ...button, whiteSpace: "nowrap", background: tab === key ? "#172554" : "transparent", borderColor: tab === key ? "#3b82f6" : "transparent" }}>{text}</button>)}</nav>
      <main style={{ overflowY: "auto", overflowX: "hidden", padding: 16, minWidth: 0 }}>
        {tab === "delivery" ? <DeliveryConsole key={`${data.settings.version}:${data.destinations.map(row => row.version).join("-")}`}
          data={data} onDestination={onDestination} onTestDestination={onTestDestination} onDispatch={onDispatch} onSettings={onSettings} />
          : <div style={{ display: "grid", gap: 9 }}>{rows.map(item => <AlertItem key={item.id} item={item} onAction={onAction} />)}
            {!rows.length && <div style={{ color: "#6b7280", fontSize: 11, borderTop: "1px solid #263244", paddingTop: 18 }}>No alerts in this view.</div>}</div>}
        {message && <div style={{ display: "flex", alignItems: "center", gap: 6, color: message.includes("synchronized") ? "#22c55e" : "#f87171", fontSize: 10, marginTop: 10 }}>
          {!message.includes("synchronized") && <AlertTriangle size={12} />}{message}</div>}
      </main>
      <footer style={{ color: "#6b7280", fontSize: 9, borderTop: "1px solid #263244", padding: "9px 16px" }}>{data.guardrail}</footer>
    </div>
    <style>{`@media(max-width:760px){.alert-facts,.alert-action-form,.alert-settings,.alert-destination{grid-template-columns:1fr!important}}`}</style>
  </div>;
}
