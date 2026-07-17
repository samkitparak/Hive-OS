import { useCallback, useEffect, useState } from "react";
import { Check, Clipboard, Download, KeyRound, LockKeyhole, LogOut, RefreshCw, Shield, ShieldCheck, Trash2, UserPlus, X } from "lucide-react";
import {
  changeAuthPassword, createAuthApiKey, createAuthUser, fetchAuthApiKeys, fetchAuthEvents,
  fetchAuthUsers, fetchMachines, fetchMqttSecurity, logoutAuth,
  downloadMqttEnrollment, resetAuthPassword, revokeAuthApiKey, revokeMqttEnrollment, updateAuthUser,
} from "./api";

const input = { width: "100%", minWidth: 0, minHeight: 33, background: "#0d1117", border: "1px solid #374151",
  color: "#e5e7eb", borderRadius: 5, padding: "7px 8px", fontSize: 11 };
const button = { display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6, minHeight: 32,
  background: "#1f2937", border: "1px solid #374151", color: "#e5e7eb", borderRadius: 5,
  padding: "7px 10px", fontSize: 11, fontWeight: 700, cursor: "pointer" };
const label = { color: "#6b7280", fontSize: 9, fontWeight: 800, textTransform: "uppercase" };

function Field({ name, children }) {
  return <label style={{ display: "grid", gap: 5, minWidth: 0 }}><span style={label}>{name}</span>{children}</label>;
}

function UserRow({ user, roles, currentId, onSaved }) {
  const [draft, setDraft] = useState({ display_name: user.display_name, role: user.role, active: user.active });
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const run = async action => { setBusy(true); setMessage(""); try { await action(); setPassword(""); await onSaved(); }
    catch (error) { setMessage(error.message); } finally { setBusy(false); } };
  return <div style={{ borderTop: "1px solid #263244", padding: "11px 0", display: "grid", gap: 8 }}>
    <div className="access-user-grid" style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr 1fr 90px", gap: 8, alignItems: "end" }}>
      <Field name={user.username}><input value={draft.display_name} onChange={event => setDraft(value => ({ ...value, display_name: event.target.value }))} style={input} /></Field>
      <Field name="Role"><select value={draft.role} disabled={user.id === currentId} onChange={event => setDraft(value => ({ ...value, role: event.target.value }))} style={input}>
        {roles.map(role => <option key={role.key} value={role.key}>{role.key}</option>)}</select></Field>
      <Field name="New password"><input type="password" autoComplete="new-password" minLength="15" maxLength="128" value={password}
        onChange={event => setPassword(event.target.value)} placeholder="Leave unchanged" style={input} /></Field>
      <label style={{ ...input, display: "flex", alignItems: "center", gap: 7, opacity: user.id === currentId ? .55 : 1 }}>
        <input type="checkbox" checked={draft.active} disabled={user.id === currentId} onChange={event => setDraft(value => ({ ...value, active: event.target.checked }))} /> Active
      </label>
    </div>
    <div style={{ display: "flex", gap: 7, flexWrap: "wrap", alignItems: "center" }}>
      <button disabled={busy} onClick={() => run(() => updateAuthUser(user.id, { ...draft, expected_version: user.version }))} style={button}><Check size={14} /> Save user</button>
      <button disabled={busy || password.length < 15} onClick={() => run(() => resetAuthPassword(user.id, { password }))}
        style={{ ...button, opacity: busy || password.length < 15 ? .45 : 1 }}><KeyRound size={14} /> Reset password</button>
      <span style={{ color: "#6b7280", fontSize: 9 }}>Last login {user.last_login_at ? new Date(user.last_login_at).toLocaleString() : "never"}</span>
      {message && <span style={{ color: "#f87171", fontSize: 9 }}>{message}</span>}
    </div>
  </div>;
}

function UsersTab({ auth, data, onRefresh }) {
  const [form, setForm] = useState({ username: "", display_name: "", role: "operator", password: "" });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const create = async () => { setBusy(true); setMessage(""); try { await createAuthUser(form);
    setForm({ username: "", display_name: "", role: "operator", password: "" }); await onRefresh(); }
    catch (error) { setMessage(error.message); } finally { setBusy(false); } };
  return <div style={{ minWidth: 0 }}>
    <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 10 }}><UserPlus size={15} color="#60a5fa" /><h3 style={{ fontSize: 13 }}>Add account</h3></div>
    <div className="access-create-grid" style={{ display: "grid", gridTemplateColumns: "1fr 1.3fr 1fr 1.5fr auto", gap: 8, alignItems: "end" }}>
      <Field name="Username"><input value={form.username} onChange={event => setForm(value => ({ ...value, username: event.target.value.toLowerCase() }))} style={input} /></Field>
      <Field name="Display name"><input value={form.display_name} onChange={event => setForm(value => ({ ...value, display_name: event.target.value }))} style={input} /></Field>
      <Field name="Role"><select value={form.role} onChange={event => setForm(value => ({ ...value, role: event.target.value }))} style={input}>
        {data.roles.map(role => <option key={role.key} value={role.key}>{role.key}</option>)}</select></Field>
      <Field name="Initial password"><input type="password" autoComplete="new-password" minLength="15" maxLength="128" value={form.password}
        onChange={event => setForm(value => ({ ...value, password: event.target.value }))} style={input} /></Field>
      <button disabled={busy || !form.username || !form.display_name || form.password.length < 15} onClick={create}
        style={{ ...button, opacity: busy || !form.username || !form.display_name || form.password.length < 15 ? .45 : 1 }}><UserPlus size={14} /> Add</button>
    </div>
    {message && <div style={{ color: "#f87171", fontSize: 10, marginTop: 8 }}>{message}</div>}
    <div style={{ marginTop: 17 }}>{data.users.map(user => <UserRow key={user.id} user={user} roles={data.roles}
      currentId={auth.user.id} onSaved={onRefresh} />)}</div>
  </div>;
}

function KeysTab({ data, onRefresh }) {
  const [name, setName] = useState("");
  const [issued, setIssued] = useState(null);
  const [message, setMessage] = useState("");
  const create = async () => { setMessage(""); try { const result = await createAuthApiKey({ name, permissions: ["integration"] });
    setIssued(result); setName(""); await onRefresh(); } catch (error) { setMessage(error.message); } };
  const revoke = async id => { setMessage(""); try { await revokeAuthApiKey(id); await onRefresh(); }
    catch (error) { setMessage(error.message); } };
  return <div style={{ minWidth: 0 }}>
    <div style={{ display: "flex", alignItems: "end", gap: 8, flexWrap: "wrap" }}>
      <div style={{ flex: "1 1 250px" }}><Field name="Machine credential name"><input value={name} onChange={event => setName(event.target.value)}
        placeholder="Morbidelli CX100 agent" style={input} /></Field></div>
      <button disabled={!name} onClick={create} style={{ ...button, opacity: name ? 1 : .45 }}><KeyRound size={14} /> Issue integration key</button>
    </div>
    {issued && <div style={{ border: "1px solid #166534", background: "#052e16", borderRadius: 6, padding: 11, marginTop: 12 }}>
      <div style={{ color: "#86efac", fontSize: 9, fontWeight: 800 }}>SHOWN ONCE</div>
      <code style={{ display: "block", color: "#f3f4f6", overflowWrap: "anywhere", marginTop: 6, fontSize: 11 }}>{issued.token}</code>
      <button onClick={() => navigator.clipboard.writeText(issued.token)} style={{ ...button, marginTop: 8 }}><Clipboard size={14} /> Copy token</button>
    </div>}
    <div style={{ marginTop: 16 }}>{data.api_keys.map(key => <div key={key.id} style={{ display: "flex", justifyContent: "space-between",
      gap: 12, alignItems: "center", borderTop: "1px solid #263244", padding: "10px 0" }}>
      <div><div style={{ fontSize: 11, fontWeight: 800 }}>{key.name}</div><div style={{ color: "#6b7280", fontSize: 9, marginTop: 3 }}>
        {key.key_prefix}… · {key.permissions.join(", ")} · last used {key.last_used_at ? new Date(key.last_used_at).toLocaleString() : "never"}</div></div>
      <button disabled={!key.active} onClick={() => revoke(key.id)} style={{ ...button, color: "#fca5a5", opacity: key.active ? 1 : .45 }}><Trash2 size={14} /> {key.active ? "Revoke" : "Revoked"}</button>
    </div>)}</div>
    {message && <div style={{ color: "#f87171", fontSize: 10, marginTop: 8 }}>{message}</div>}
  </div>;
}

function CertificatesTab({ data, onRefresh }) {
  const security = data.mqtt;
  const [machineKey, setMachineKey] = useState(data.machines[0]?.machine_key || "");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const run = async action => { setBusy(true); setMessage(""); try { await action(); await onRefresh(); }
    catch (error) { setMessage(error.message); } finally { setBusy(false); } };
  const issue = () => run(() => downloadMqttEnrollment({ machine_key: machineKey, validity_days: 397 }));
  const revoke = enrollment => run(() => revokeMqttEnrollment(enrollment.id, { reason: "Revoked by administrator" }));
  if (!security.initialized) return <div style={{ display: "grid", gap: 13, maxWidth: 680 }}>
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}><LockKeyhole size={17} color="#60a5fa" />
      <div><h3 style={{ fontSize: 13 }}>Machine trust is not provisioned</h3><div style={{ color: "#9ca3af", fontSize: 10, marginTop: 3 }}>
        Run install-central.ps1 on the central PC to create the site authority and secure broker identity.</div></div></div>
    {message && <div style={{ color: "#f87171", fontSize: 10 }}>{message}</div>}
  </div>;
  return <div style={{ minWidth: 0 }}>
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
      <div><div style={{ display: "flex", gap: 7, alignItems: "center", fontSize: 12, fontWeight: 800 }}><ShieldCheck size={16} color="#22c55e" /> Mutual TLS active</div>
        <div style={{ color: "#6b7280", fontSize: 9, marginTop: 4 }}>{security.broker_host}:{security.broker_port} · {security.active_enrollments} active · {security.expiring_within_30_days} expiring soon</div></div>
      {security.broker_restart_required && <div style={{ border: "1px solid #92400e", background: "#451a03", color: "#fdba74", borderRadius: 5, padding: "7px 9px", fontSize: 9 }}>Run restart-hive-mqtt.ps1 on the central PC</div>}
    </div>
    <div style={{ display: "flex", gap: 8, alignItems: "end", flexWrap: "wrap", marginTop: 16 }}>
      <div style={{ flex: "1 1 260px" }}><Field name="Machine"><select value={machineKey} onChange={event => setMachineKey(event.target.value)} style={input}>
        <option value="">Select a machine</option>{data.machines.map(machine => <option key={machine.machine_key} value={machine.machine_key}>{machine.name}</option>)}</select></Field></div>
      <button disabled={busy || !machineKey} onClick={issue} style={{ ...button, opacity: busy || !machineKey ? .45 : 1 }}><Download size={14} /> Issue enrollment ZIP</button>
    </div>
    <div style={{ marginTop: 16 }}>{security.enrollments.map(enrollment => <div key={enrollment.id} style={{ display: "flex", justifyContent: "space-between", gap: 12,
      alignItems: "center", borderTop: "1px solid #263244", padding: "10px 0" }}>
      <div><div style={{ fontSize: 11, fontWeight: 800 }}>{enrollment.machine_name}</div><div style={{ color: "#6b7280", fontSize: 9, marginTop: 3 }}>
        {enrollment.status} · expires {new Date(enrollment.expires_at).toLocaleDateString()} · {enrollment.certificate_sha256.slice(0, 16)}…</div></div>
      <button disabled={busy || enrollment.status !== "active"} onClick={() => revoke(enrollment)} style={{ ...button, color: "#fca5a5", opacity: enrollment.status === "active" ? 1 : .45 }}>
        <Trash2 size={14} /> {enrollment.status === "active" ? "Revoke" : "Revoked"}</button>
    </div>)}</div>
    {!security.enrollments.length && <div style={{ color: "#6b7280", fontSize: 10, marginTop: 15 }}>No machine certificates issued yet.</div>}
    {message && <div style={{ color: "#f87171", fontSize: 10, marginTop: 8 }}>{message}</div>}
  </div>;
}

function AccountTab({ auth, onLogout }) {
  const [form, setForm] = useState({ current_password: "", new_password: "", confirm: "" });
  const [message, setMessage] = useState("");
  const change = async () => { if (form.new_password !== form.confirm) { setMessage("Passwords do not match"); return; }
    try { await changeAuthPassword({ current_password: form.current_password, new_password: form.new_password });
      setForm({ current_password: "", new_password: "", confirm: "" }); setMessage("Password changed; other sessions revoked"); }
    catch (error) { setMessage(error.message); } };
  return <div style={{ display: "grid", gap: 14 }}>
    <div><div style={{ fontSize: 15, fontWeight: 800 }}>{auth.user.display_name}</div><div style={{ color: "#6b7280", fontSize: 10, marginTop: 3 }}>
      {auth.user.username} · {auth.user.role} · session expires {auth.expires_at ? new Date(auth.expires_at).toLocaleString() : "with this browser"}</div></div>
    <div className="access-password-grid" style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr auto", gap: 8, alignItems: "end" }}>
      <Field name="Current password"><input type="password" autoComplete="current-password" value={form.current_password} onChange={event => setForm(value => ({ ...value, current_password: event.target.value }))} style={input} /></Field>
      <Field name="New password"><input type="password" autoComplete="new-password" minLength="15" maxLength="128" value={form.new_password} onChange={event => setForm(value => ({ ...value, new_password: event.target.value }))} style={input} /></Field>
      <Field name="Confirm"><input type="password" autoComplete="new-password" value={form.confirm} onChange={event => setForm(value => ({ ...value, confirm: event.target.value }))} style={input} /></Field>
      <button disabled={!form.current_password || form.new_password.length < 15} onClick={change} style={{ ...button, opacity: !form.current_password || form.new_password.length < 15 ? .45 : 1 }}><Check size={14} /> Change</button>
    </div>
    {message && <div style={{ color: message.includes("changed") ? "#22c55e" : "#f87171", fontSize: 10 }}>{message}</div>}
    <button onClick={onLogout} style={{ ...button, width: "fit-content", color: "#fca5a5" }}><LogOut size={14} /> Sign out</button>
  </div>;
}

function AuditTab({ events }) {
  return <div style={{ overflowX: "auto", maxWidth: "100%" }}><table style={{ width: "100%", minWidth: 760, borderCollapse: "collapse", fontSize: 10 }}>
    <thead><tr style={{ color: "#6b7280", textAlign: "left" }}>{["Time", "Identity", "Event", "Target", "Result"].map(value => <th key={value} style={{ padding: 7, borderBottom: "1px solid #263244" }}>{value}</th>)}</tr></thead>
    <tbody>{events.map(event => <tr key={event.id}><td style={{ padding: 7, borderBottom: "1px solid #1f2937" }}>{new Date(event.ts).toLocaleString()}</td>
      <td style={{ padding: 7, borderBottom: "1px solid #1f2937" }}>{event.actor_name}</td><td style={{ padding: 7, borderBottom: "1px solid #1f2937" }}>{event.event_type.replaceAll("_", " ")}</td>
      <td style={{ padding: 7, borderBottom: "1px solid #1f2937" }}>{event.target_key || "—"}</td><td style={{ padding: 7, borderBottom: "1px solid #1f2937", color: event.success ? "#22c55e" : "#ef4444" }}>{event.success ? "accepted" : "rejected"}</td></tr>)}</tbody>
  </table></div>;
}

export function AccessPanel({ auth, onClose, onExpired }) {
  const admin = auth.user.role === "admin";
  const [tab, setTab] = useState(admin ? "users" : "account");
  const [data, setData] = useState({ users: [], roles: [], api_keys: [], events: [], machines: [], mqtt: { initialized: false, enrollments: [] } });
  const [message, setMessage] = useState("");
  const refresh = useCallback(async () => {
    if (!admin) return;
    try {
      const [users, keys, audit, machines, mqtt] = await Promise.all([fetchAuthUsers(), fetchAuthApiKeys(), fetchAuthEvents(), fetchMachines(), fetchMqttSecurity()]);
      setData({ users: users.users, roles: users.roles, api_keys: keys.api_keys, events: audit.events, machines, mqtt });
    } catch (error) { setMessage(error.message); }
  }, [admin]);
  useEffect(() => {
    const timer = window.setTimeout(() => { void refresh(); }, 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);
  const signOut = async () => { try { await logoutAuth(); } finally { onExpired(); } };
  const tabs = admin ? [["users", "Users"], ["keys", "HTTP keys"], ["certificates", "Device certificates"], ["audit", "Access audit"], ["account", "My account"]]
    : [["account", "My account"]];
  return <div style={{ position: "fixed", inset: 0, zIndex: 1150, background: "rgba(2,6,23,.88)", display: "grid", placeItems: "center", padding: 18 }}>
    <div style={{ width: "min(1100px,96vw)", maxHeight: "92vh", overflow: "hidden", display: "flex", flexDirection: "column",
      background: "#0d1117", border: "1px solid #374151", borderRadius: 8 }}>
      <header style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center", padding: "14px 16px", borderBottom: "1px solid #263244" }}>
        <div><div style={{ display: "flex", gap: 8, alignItems: "center" }}><Shield size={18} color="#60a5fa" /><h2 style={{ fontSize: 16 }}>Access control</h2></div>
          <div style={{ color: "#6b7280", fontSize: 10, marginTop: 3 }}>{auth.user.display_name} · {auth.user.role}</div></div>
        <div style={{ display: "flex", gap: 7 }}>{admin && <button onClick={refresh} style={button}><RefreshCw size={14} /> Refresh</button>}
          <button onClick={onClose} title="Close" style={{ ...button, width: 32, padding: 0 }}><X size={16} /></button></div>
      </header>
      <nav style={{ display: "flex", gap: 4, padding: "10px 16px 0", overflowX: "auto" }}>{tabs.map(([key, text]) => <button key={key} onClick={() => setTab(key)}
        style={{ ...button, whiteSpace: "nowrap", background: tab === key ? "#172554" : "transparent", borderColor: tab === key ? "#3b82f6" : "transparent" }}>{text}</button>)}</nav>
      <main style={{ overflow: "auto", padding: 16, minWidth: 0 }}>
        {tab === "users" && <UsersTab auth={auth} data={data} onRefresh={refresh} />}
        {tab === "keys" && <KeysTab data={data} onRefresh={refresh} />}
        {tab === "certificates" && <CertificatesTab data={data} onRefresh={refresh} />}
        {tab === "audit" && <AuditTab events={data.events} />}
        {tab === "account" && <AccountTab auth={auth} onLogout={signOut} />}
        {message && <div style={{ color: "#f87171", fontSize: 10, marginTop: 10 }}>{message}</div>}
      </main>
    </div>
    <style>{`@media(max-width:760px){.access-create-grid,.access-user-grid,.access-password-grid{grid-template-columns:1fr!important}}`}</style>
  </div>;
}
