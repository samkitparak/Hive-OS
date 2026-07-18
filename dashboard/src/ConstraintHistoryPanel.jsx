import { Activity, Check, Clock3, RefreshCw, Save, X } from "lucide-react";
import { useRef, useState } from "react";

const button = {
  display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6,
  minHeight: 32, border: "1px solid #374151", borderRadius: 6,
  background: "#1f2937", color: "#e5e7eb", padding: "6px 10px",
  fontSize: 10, fontWeight: 700, cursor: "pointer",
};
const input = {
  width: "100%", minHeight: 32, border: "1px solid #374151", borderRadius: 5,
  background: "#111827", color: "#f3f4f6", padding: "6px 8px", fontSize: 10,
};
const STATUS_COLOR = {
  healthy: "#22c55e", starting: "#60a5fa", degraded: "#f59e0b",
  stale: "#ef4444", disabled: "#6b7280", open: "#ef4444",
  observing: "#f59e0b", closed: "#6b7280",
};

const formatTime = value => value ? new Date(value).toLocaleString([], {
  month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
}) : "Never";

const duration = seconds => {
  const minutes = Math.max(0, Math.round((seconds || 0) / 60));
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
};

export function ConstraintHistoryPanel({ data, currentUser, canManage, onSync, onSettings, onClose }) {
  const settingsVersion = useRef(data.runtime.version);
  const [settings, setSettings] = useState({
    auto_sync: Boolean(data.runtime.auto_sync),
    interval_seconds: data.runtime.interval_seconds,
    window_hours: data.runtime.window_hours,
    retention_days: data.runtime.retention_days,
    expected_version: data.runtime.version,
  });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const run = async (action, success = "Saved") => {
    setBusy(true);
    setMessage("");
    try {
      const result = await action();
      setMessage(success);
      return result;
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy(false);
    }
  };
  const save = () => run(async () => {
    const updated = await onSettings({
      ...settings,
      interval_seconds: Number(settings.interval_seconds),
      window_hours: Number(settings.window_hours),
      retention_days: Number(settings.retention_days),
      expected_version: settingsVersion.current,
      actor: currentUser,
    });
    settingsVersion.current = updated.version;
    setSettings({
      auto_sync: Boolean(updated.auto_sync), interval_seconds: updated.interval_seconds,
      window_hours: updated.window_hours, retention_days: updated.retention_days,
      expected_version: updated.version,
    });
    return updated;
  });
  const sample = () => run(onSync, "Sample recorded");
  const runtimeColor = STATUS_COLOR[data.runtime.status] || "#6b7280";

  return <div style={{ position: "fixed", inset: 0, zIndex: 1150,
    background: "rgba(2,6,23,.9)", display: "grid", placeItems: "center", padding: 18 }}>
    <div style={{ width: "min(1180px,96vw)", maxHeight: "94vh", overflow: "hidden",
      background: "#0d1117", border: "1px solid #374151", borderRadius: 8,
      color: "#f9fafb", display: "flex", flexDirection: "column" }}>
      <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
        gap: 14, borderBottom: "1px solid #263244", padding: "14px 16px" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Activity size={18} color={runtimeColor} />
            <h2 style={{ fontSize: 16 }}>Constraint intelligence</h2>
          </div>
          <div style={{ color: runtimeColor, fontSize: 10, fontWeight: 800,
            textTransform: "uppercase", marginTop: 4 }}>
            Automation {data.runtime.status}
          </div>
        </div>
        <div style={{ display: "flex", gap: 7 }}>
          {canManage && <button onClick={sample} disabled={busy} style={{ ...button,
            opacity: busy ? .45 : 1 }}><RefreshCw size={14} /> Sample now</button>}
          <button onClick={onClose} title="Close" style={{ ...button, width: 32, padding: 0 }}>
            <X size={16} />
          </button>
        </div>
      </header>

      <main style={{ overflowY: "auto", overflowX: "hidden", padding: 16 }}>
        <section className="constraint-history-metrics" style={{ display: "grid",
          gridTemplateColumns: "repeat(5,minmax(100px,1fr))", gap: 16,
          paddingBottom: 15, borderBottom: "1px solid #263244" }}>
          <Metric label="Snapshots" value={data.summary.snapshots} />
          <Metric label="Open episodes" value={data.summary.open} color="#ef4444" />
          <Metric label="Observing" value={data.summary.observing} color="#f59e0b" />
          <Metric label="Shifts sampled" value={data.summary.shifts_sampled} />
          <Metric label="Last success" value={formatTime(data.runtime.last_success_at)} compact />
        </section>

        {canManage && <section style={{ padding: "15px 0", borderBottom: "1px solid #263244" }}>
          <div className="constraint-runtime-settings" style={{ display: "grid",
            gridTemplateColumns: "1.1fr repeat(3,minmax(120px,.7fr)) auto", gap: 10,
            alignItems: "end" }}>
            <label style={{ ...input, display: "flex", alignItems: "center", gap: 8 }}>
              <input type="checkbox" checked={settings.auto_sync}
                onChange={event => setSettings(value => ({ ...value, auto_sync: event.target.checked }))} />
              Automatic sampling
            </label>
            <Field label="Cadence">
              <select value={settings.interval_seconds}
                onChange={event => setSettings(value => ({ ...value, interval_seconds: event.target.value }))}
                style={input}>
                <option value="300">5 minutes</option><option value="600">10 minutes</option>
                <option value="900">15 minutes</option><option value="1800">30 minutes</option>
                <option value="3600">60 minutes</option>
              </select>
            </Field>
            <Field label="Window hours"><input type="number" min="1" max="24"
              value={settings.window_hours} onChange={event => setSettings(value => ({
                ...value, window_hours: event.target.value,
              }))} style={input} /></Field>
            <Field label="Retention days"><input type="number" min="7" max="3650"
              value={settings.retention_days} onChange={event => setSettings(value => ({
                ...value, retention_days: event.target.value,
              }))} style={input} /></Field>
            <button onClick={save} disabled={busy} style={{ ...button, background: "#1d4ed8",
              borderColor: "#3b82f6", opacity: busy ? .45 : 1 }}><Save size={14} /> Save</button>
          </div>
          {message && <div style={{ display: "flex", alignItems: "center", gap: 5,
            color: ["Saved", "Sample recorded"].includes(message) ? "#22c55e" : "#f87171", fontSize: 10, marginTop: 8 }}>
            {["Saved", "Sample recorded"].includes(message) && <Check size={12} />}{message}
          </div>}
        </section>}

        <div className="constraint-history-layout" style={{ display: "grid",
          gridTemplateColumns: "minmax(0,1.2fr) minmax(300px,.8fr)", gap: 22, paddingTop: 15 }}>
          <section style={{ minWidth: 0 }}>
            <h3 style={{ fontSize: 12, marginBottom: 9 }}>Episode history</h3>
            <div style={{ overflowX: "auto" }}><table style={{ width: "100%", minWidth: 660,
              borderCollapse: "collapse", fontSize: 10 }}>
              <thead><tr style={{ color: "#6b7280", textAlign: "left" }}>
                {["Machine", "State", "Status", "Started", "Duration", "Samples", "Shift"].map(label =>
                  <th key={label} style={{ padding: 7, borderBottom: "1px solid #263244" }}>{label}</th>)}
              </tr></thead>
              <tbody>{data.episodes.map(item => <tr key={item.id}>
                <td style={cell}><strong>{item.machine_name}</strong></td>
                <td style={cell}>{item.constraint_state.replaceAll("_", " ")}</td>
                <td style={{ ...cell, color: STATUS_COLOR[item.status], fontWeight: 800 }}>{item.status}</td>
                <td style={cell}>{formatTime(item.started_at)}</td>
                <td style={cell}>{duration(item.duration_s)}</td>
                <td style={cell}>{item.snapshot_count}</td>
                <td style={cell}>{item.last_shift_key || "Unclassified"}</td>
              </tr>)}</tbody>
            </table>{!data.episodes.length && <Empty text="No constraint episodes recorded." />}</div>
          </section>

          <section style={{ minWidth: 0 }}>
            <h3 style={{ fontSize: 12, marginBottom: 9 }}>Shift coverage</h3>
            <div style={{ display: "grid", gap: 0 }}>{data.shifts.slice(0, 20).map(shift =>
              <div key={shift.shift_key} style={{ display: "grid",
                gridTemplateColumns: "minmax(120px,1fr) 72px minmax(120px,1fr)", gap: 8,
                alignItems: "center", padding: "9px 0", borderBottom: "1px solid #1f2937",
                fontSize: 10 }}>
                <div><div style={{ color: "#e5e7eb", fontWeight: 700 }}>{shift.local_date || "Legacy"}</div>
                  <div style={{ color: shift.calendar_verified ? "#22c55e" : "#6b7280", marginTop: 2 }}>
                    {shift.shift_label} | {shift.timezone}
                  </div></div>
                <div style={{ color: "#9ca3af" }}>{shift.sample_count} samples</div>
                <div style={{ color: shift.dominant ? "#f59e0b" : "#6b7280" }}>
                  {shift.dominant
                    ? `${shift.dominant.machine_name} ${Math.round(shift.dominant.share * 100)}%`
                    : "No constraint"}
                </div>
              </div>)}
              {!data.shifts.length && <Empty text="No shifts sampled." />}
            </div>
          </section>
        </div>
      </main>
      <footer style={{ display: "flex", alignItems: "center", gap: 6, color: "#6b7280",
        fontSize: 9, borderTop: "1px solid #263244", padding: "9px 16px" }}>
        <Clock3 size={11} />{data.guardrail}
      </footer>
    </div>
    <style>{`@media(max-width:760px){.constraint-history-metrics{grid-template-columns:1fr 1fr!important}.constraint-runtime-settings,.constraint-history-layout{grid-template-columns:1fr!important}}`}</style>
  </div>;
}

const cell = { padding: 7, borderBottom: "1px solid #1f2937", color: "#9ca3af" };

function Field({ label, children }) {
  return <label style={{ display: "grid", gap: 4, color: "#6b7280", fontSize: 9,
    textTransform: "uppercase", fontWeight: 700 }}>{label}{children}</label>;
}

function Metric({ label, value, color = "#d1d5db", compact = false }) {
  return <div><div style={{ color: "#6b7280", fontSize: 9, textTransform: "uppercase",
    fontWeight: 700 }}>{label}</div><div style={{ color, fontSize: compact ? 11 : 18,
    fontWeight: 800, marginTop: 3 }}>{value}</div></div>;
}

function Empty({ text }) {
  return <div style={{ color: "#6b7280", fontSize: 10, padding: "14px 0" }}>{text}</div>;
}
