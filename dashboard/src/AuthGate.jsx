import { useCallback, useEffect, useState } from "react";
import { KeyRound, LockKeyhole, ShieldCheck } from "lucide-react";
import { bootstrapAuth, fetchAuthStatus, fetchCurrentUser, loginAuth, setAuthCsrf } from "./api";

const input = { width: "100%", minWidth: 0, background: "#0d1117", border: "1px solid #374151",
  color: "#f3f4f6", borderRadius: 5, minHeight: 38, padding: "8px 10px", fontSize: 12 };
const button = { width: "100%", display: "inline-flex", justifyContent: "center", alignItems: "center", gap: 7,
  border: "1px solid #3b82f6", background: "#1d4ed8", color: "#f9fafb", borderRadius: 5,
  minHeight: 38, padding: "8px 12px", fontSize: 12, fontWeight: 800, cursor: "pointer" };

function Field({ label, children }) {
  return <label style={{ display: "grid", gap: 5 }}><span style={{ color: "#9ca3af", fontSize: 9,
    fontWeight: 800, textTransform: "uppercase" }}>{label}</span>{children}</label>;
}

function AccessForm({ setup, status, onSuccess }) {
  const [form, setForm] = useState(setup ? { bootstrap_token: "", username: "", display_name: "", password: "", confirm: "" }
    : { username: "", password: "" });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const set = (key, value) => setForm(current => ({ ...current, [key]: value }));
  const submit = async event => {
    event.preventDefault();
    if (setup && form.password !== form.confirm) { setMessage("Passwords do not match"); return; }
    setBusy(true); setMessage("");
    try {
      const payload = setup ? { bootstrap_token: form.bootstrap_token, username: form.username,
        display_name: form.display_name, password: form.password } : form;
      const result = setup ? await bootstrapAuth(payload) : await loginAuth(payload);
      onSuccess(result);
    } catch (error) { setMessage(error.message); } finally { setBusy(false); }
  };
  return <form onSubmit={submit} style={{ width: "min(390px,calc(100vw - 32px))", border: "1px solid #374151",
    borderRadius: 8, background: "#111827", padding: 22, display: "grid", gap: 13 }}>
    <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 2 }}>
      {setup ? <ShieldCheck size={22} color="#60a5fa" /> : <LockKeyhole size={22} color="#60a5fa" />}
      <div><h1 style={{ fontSize: 18 }}>HIVE OS</h1><div style={{ color: "#6b7280", fontSize: 10, marginTop: 2 }}>
        {setup ? "Create first administrator" : "Factory access"}</div></div>
    </div>
    {!status.transport_acceptable && <div style={{ color: "#fca5a5", fontSize: 10, lineHeight: 1.5,
      border: "1px solid #7f1d1d", padding: 9, borderRadius: 5 }}>Open HIVE on the central PC or through HTTPS before entering credentials.</div>}
    {setup && <Field label="One-time setup token"><input autoComplete="one-time-code" value={form.bootstrap_token}
      onChange={event => set("bootstrap_token", event.target.value)} style={input} required /></Field>}
    {setup && <Field label="Administrator name"><input autoComplete="name" value={form.display_name}
      onChange={event => set("display_name", event.target.value)} style={input} required /></Field>}
    <Field label="Username"><input autoComplete="username" value={form.username}
      onChange={event => set("username", event.target.value.toLowerCase())} style={input} required /></Field>
    <Field label="Password"><input type="password" autoComplete={setup ? "new-password" : "current-password"} value={form.password}
      onChange={event => set("password", event.target.value)} minLength={setup ? 15 : 1} maxLength={128} style={input} required /></Field>
    {setup && <Field label="Confirm password"><input type="password" autoComplete="new-password" value={form.confirm}
      onChange={event => set("confirm", event.target.value)} minLength={15} maxLength={128} style={input} required /></Field>}
    <button disabled={busy || !status.transport_acceptable} style={{ ...button,
      opacity: busy || !status.transport_acceptable ? .45 : 1, cursor: busy || !status.transport_acceptable ? "not-allowed" : "pointer" }}>
      <KeyRound size={15} /> {busy ? "Checking…" : setup ? "Create administrator" : "Sign in"}
    </button>
    {message && <div style={{ color: "#f87171", fontSize: 10 }}>{message}</div>}
    {setup && status.bootstrap_token_path && <div style={{ color: "#6b7280", fontSize: 9,
      overflowWrap: "anywhere" }}>Token file: {status.bootstrap_token_path}</div>}
  </form>;
}

export function AuthGate({ children }) {
  const [state, setState] = useState({ loading: true, status: null, session: null });
  const refresh = useCallback(async () => {
    try {
      const status = await fetchAuthStatus();
      if (!status.auth_required) {
        setAuthCsrf(null);
        setState({ loading: false, status, session: { user: { display_name: "Development Admin", username: "dev",
          role: "admin", permissions: status.roles.flatMap(item => item.permissions) } } });
      } else if (status.setup_required) {
        setState({ loading: false, status, session: null });
      } else {
        try { setState({ loading: false, status, session: await fetchCurrentUser() }); }
        catch { setState({ loading: false, status, session: null }); }
      }
    } catch (error) { setState({ loading: false, status: null, session: null, error: error.message }); }
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => { void refresh(); }, 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);
  useEffect(() => {
    const expired = () => { setAuthCsrf(null); setState(current => ({ ...current, session: null })); };
    window.addEventListener("hive-auth-expired", expired);
    return () => window.removeEventListener("hive-auth-expired", expired);
  }, []);
  if (state.loading) return <div style={{ minHeight: "100vh", display: "grid", placeItems: "center",
    background: "#0d1117", color: "#6b7280", fontFamily: "Inter,system-ui,sans-serif", fontSize: 12 }}>Loading HIVE OS…</div>;
  if (!state.status) return <div style={{ minHeight: "100vh", display: "grid", placeItems: "center",
    background: "#0d1117", color: "#f87171", fontFamily: "Inter,system-ui,sans-serif", padding: 24 }}>{state.error || "HIVE API unavailable"}</div>;
  if (!state.session) return <div style={{ minHeight: "100vh", display: "grid", placeItems: "center",
    background: "#0d1117", fontFamily: "Inter,system-ui,sans-serif", padding: 16 }}>
    <AccessForm setup={state.status.setup_required} status={state.status}
      onSuccess={session => setState(current => ({ ...current, status: { ...current.status, setup_required: false }, session }))} />
  </div>;
  return children({ ...state.session, status: state.status, refreshAuth: refresh,
    expireAuth: () => { setAuthCsrf(null); setState(current => ({ ...current, session: null })); } });
}
