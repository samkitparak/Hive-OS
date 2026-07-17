const STATUS_COLOR = {
  online: "#22c55e",
  ready: "#22c55e",
  learning: "#f59e0b",
  waiting: "#60a5fa",
  stale: "#f59e0b",
  attention: "#ef4444",
  offline: "#ef4444",
  missing: "#ef4444",
  needs_site_value: "#f59e0b",
  optional: "#60a5fa",
  not_configured: "#6b7280",
};

function Status({ value }) {
  const color = STATUS_COLOR[value] ?? "#6b7280";
  return (
    <span style={{ color, fontSize: 10, fontWeight: 800, textTransform: "uppercase" }}>
      ● {value.replace("_", " ")}
    </span>
  );
}

function ageLabel(seconds) {
  if (seconds == null) return "Never";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function SectionTitle({ children }) {
  return (
    <div style={{ fontSize: 10, fontWeight: 800, color: "#6b7280",
                  letterSpacing: 1, marginBottom: 8, textTransform: "uppercase" }}>
      {children}
    </div>
  );
}

function DeploymentConsole({ deployment }) {
  if (!deployment) return null;
  return (
    <div style={{ borderTop: "1px solid #1f2937", marginTop: 20, paddingTop: 16 }}>
      <SectionTitle>Deployment Package</SectionTitle>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))",
                    gap: 8, marginBottom: 14 }}>
        {deployment.checklist.map(item => (
          <div key={item.key} style={{ border: "1px solid #1f2937", borderRadius: 6, padding: 12 }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
              <span style={{ fontSize: 12, fontWeight: 700 }}>{item.label}</span>
              <Status value={item.status} />
            </div>
            <div style={{ color: "#6b7280", fontSize: 10, marginTop: 5,
                          overflowWrap: "anywhere" }}>{item.detail}</div>
          </div>
        ))}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
                    gap: 10 }}>
        {deployment.assets.map(asset => (
          <div key={asset.key} style={{ border: "1px solid #1f2937", borderRadius: 6, padding: 12 }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, marginBottom: 6 }}>
              <span style={{ fontSize: 12, fontWeight: 700 }}>{asset.label}</span>
              <Status value={asset.exists ? "ready" : "missing"} />
            </div>
            <div style={{ color: "#9ca3af", fontSize: 10, marginBottom: 6 }}>{asset.target}</div>
            <div style={{ color: "#6b7280", fontSize: 10, overflowWrap: "anywhere", marginBottom: 7 }}>
              {asset.path}
            </div>
            <code style={{ display: "block", background: "#111827", border: "1px solid #1f2937",
                           borderRadius: 6, padding: 8, color: "#d1d5db", fontSize: 10,
                           whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
              {asset.command}
            </code>
          </div>
        ))}
      </div>
    </div>
  );
}

function formatBytes(value) {
  if (!value) return "0 B";
  if (value < 1024 * 1024) return `${Math.ceil(value / 1024)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function ResilienceConsole({ resilience, canManage, onCreateBackup, onVerifyBackup }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  if (!resilience) return null;
  const latest = resilience.latest;

  const run = async action => {
    setBusy(true);
    setMessage("");
    try {
      const result = await action();
      setMessage(`Verified ${result.filename}`);
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ borderTop: "1px solid #1f2937", marginTop: 20, paddingTop: 16 }}>
      <SectionTitle>Recovery Readiness</SectionTitle>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(260px, 100%), 1fr))",
                    gap: 10 }}>
        <div style={{ border: "1px solid #1f2937", borderRadius: 6, padding: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
            <span style={{ fontSize: 12, fontWeight: 700 }}>Latest system backup</span>
            <Status value={latest?.status === "verified" ? "ready" : "missing"} />
          </div>
          <div style={{ color: "#9ca3af", fontSize: 10, marginTop: 7, overflowWrap: "anywhere" }}>
            {latest ? `${latest.filename} · ${formatBytes(latest.size)}` : "No verified recovery point"}
          </div>
          {latest?.generated_at && <div style={{ color: "#6b7280", fontSize: 10, marginTop: 4 }}>
            {new Date(latest.generated_at).toLocaleString()}
          </div>}
          {canManage && <div style={{ display: "flex", gap: 7, marginTop: 10, flexWrap: "wrap" }}>
            <button type="button" disabled={busy} onClick={() => run(onCreateBackup)} title="Create verified backup"
                    style={{ display: "inline-flex", alignItems: "center", gap: 6, background: "#1d4ed8",
                             border: "1px solid #3b82f6", color: "#fff", borderRadius: 6, padding: "7px 10px",
                             fontSize: 11, cursor: busy ? "wait" : "pointer" }}>
              <DatabaseBackup size={14} /> Back up now
            </button>
            {latest && <button type="button" disabled={busy} onClick={() => run(() => onVerifyBackup(latest.filename))}
                               title="Verify latest backup" style={{ display: "inline-flex", alignItems: "center", gap: 6,
                               background: "#111827", border: "1px solid #374151", color: "#d1d5db", borderRadius: 6,
                               padding: "7px 10px", fontSize: 11, cursor: busy ? "wait" : "pointer" }}>
              <RefreshCw size={14} /> Verify
            </button>}
          </div>}
          {message && <div style={{ color: message.startsWith("Verified") ? "#86efac" : "#fca5a5",
                                    fontSize: 10, marginTop: 8, overflowWrap: "anywhere" }}>{message}</div>}
        </div>
        <div style={{ border: "1px solid #1f2937", borderRadius: 6, padding: 12 }}>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 7 }}>Controlled recovery commands</div>
          <code style={{ display: "block", background: "#111827", borderRadius: 6, padding: 8,
                         color: "#d1d5db", fontSize: 10, whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
            {resilience.restore_command}
          </code>
          <code style={{ display: "block", background: "#111827", borderRadius: 6, padding: 8,
                         color: "#d1d5db", fontSize: 10, whiteSpace: "pre-wrap", overflowWrap: "anywhere",
                         marginTop: 7 }}>{resilience.upgrade_command}</code>
          <div style={{ color: "#6b7280", fontSize: 10, marginTop: 7, overflowWrap: "anywhere" }}>
            Archives: {resilience.backup_directory}
          </div>
        </div>
      </div>
    </div>
  );
}

export function DiagnosticsPanel({ data, deployment, resilience, canManage,
                                   onCreateBackup, onVerifyBackup, onClose }) {
  if (!data) return null;
  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 120, background: "rgba(0,0,0,.78)",
                  display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}
         onClick={onClose}>
      <div style={{ width: "min(980px, 100%)", maxHeight: "90vh", overflowY: "auto",
                    background: "#0d1117", border: "1px solid #374151", borderRadius: 8,
                    padding: 20 }} onClick={event => event.stopPropagation()}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center",
                      marginBottom: 18 }}>
          <div>
            <div style={{ fontSize: 18, fontWeight: 800 }}>System Diagnostics</div>
            <div style={{ color: "#6b7280", fontSize: 11, marginTop: 3 }}>
              {data.summary.online_machines}/{data.summary.total_machines} machines reporting recently
            </div>
          </div>
          <button onClick={onClose} title="Close diagnostics" style={{ background: "none", border: 0,
                                            color: "#9ca3af", cursor: "pointer", padding: 5 }}><X size={20} /></button>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
                      gap: 8, marginBottom: 20 }}>
          {data.services.map(service => (
            <div key={service.key} style={{ border: "1px solid #1f2937", padding: 12, borderRadius: 6 }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                <span style={{ fontSize: 12, fontWeight: 700 }}>{service.name}</span>
                <Status value={service.status} />
              </div>
              <div style={{ color: "#6b7280", fontSize: 10, marginTop: 5,
                            overflowWrap: "anywhere" }}>{service.detail}</div>
            </div>
          ))}
        </div>

        <SectionTitle>Machine Connections</SectionTitle>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", minWidth: 760, borderCollapse: "collapse", fontSize: 11 }}>
            <thead>
              <tr style={{ color: "#6b7280", textAlign: "left" }}>
                {["Machine", "Source", "Status", "Last report", "Host / path"].map(label => (
                  <th key={label} style={{ padding: "7px 8px", borderBottom: "1px solid #1f2937" }}>{label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.machines.map(machine => (
                <tr key={machine.machine_key}>
                  <td style={{ padding: "8px", borderBottom: "1px solid #1f2937", fontWeight: 700 }}>
                    {machine.name}
                  </td>
                  <td style={{ padding: "8px", borderBottom: "1px solid #1f2937", color: "#9ca3af" }}>
                    {machine.source.replace("_", " ")}
                  </td>
                  <td style={{ padding: "8px", borderBottom: "1px solid #1f2937" }}>
                    <Status value={machine.status} />
                  </td>
                  <td style={{ padding: "8px", borderBottom: "1px solid #1f2937", color: "#9ca3af" }}>
                    {ageLabel(machine.age_seconds)}
                  </td>
                  <td style={{ padding: "8px", borderBottom: "1px solid #1f2937", color: "#6b7280",
                               maxWidth: 280, overflowWrap: "anywhere" }}>
                    {machine.host || machine.path || "Not configured"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <ResilienceConsole resilience={resilience} canManage={canManage}
                           onCreateBackup={onCreateBackup} onVerifyBackup={onVerifyBackup} />
        <DeploymentConsole deployment={deployment} />
      </div>
    </div>
  );
}
import { useState } from "react";
import { DatabaseBackup, RefreshCw, X } from "lucide-react";
