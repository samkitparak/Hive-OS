import { useState } from "react";

const button = {
  border: "1px solid #374151", borderRadius: 6, padding: "8px 13px",
  color: "#f9fafb", background: "#1f2937", cursor: "pointer", fontSize: 11,
  fontWeight: 700,
};

export function CommissioningPanel({ machines, onAnalyze, onClose }) {
  const candidates = machines.filter(machine => machine.has_maestro);
  const [machineKey, setMachineKey] = useState(candidates[0]?.machine_key ?? "");
  const [logText, setLogText] = useState("");
  const [fileName, setFileName] = useState("");
  const [result, setResult] = useState(null);
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

  const analyze = async persist => {
    if (!logText) return;
    if (persist && !window.confirm("Import these validated historical events into HIVE?")) return;
    setBusy(true);
    setError("");
    try {
      const data = await onAnalyze({ machine_key: machineKey, log_text: logText, persist });
      setResult(data);
    } catch (reason) {
      setError(reason.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.72)",
                  display: "grid", placeItems: "center", zIndex: 30, padding: 16 }}>
      <div style={{ width: "min(860px, 100%)", maxHeight: "90vh", overflowY: "auto",
                    background: "#111827", border: "1px solid #374151", borderRadius: 8,
                    padding: 20 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 16,
                      alignItems: "center", marginBottom: 18 }}>
          <div>
            <div style={{ fontSize: 17, fontWeight: 800 }}>Machine commissioning</div>
            <div style={{ color: "#6b7280", fontSize: 11, marginTop: 3 }}>
              Validate a real machine log before HIVE is allowed to learn from it.
            </div>
          </div>
          <button onClick={onClose} aria-label="Close" title="Close" style={{ ...button,
                    width: 34, height: 34, padding: 0, fontSize: 18 }}>×</button>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "minmax(180px, .6fr) 1fr",
                      gap: 12, marginBottom: 16 }} className="commission-controls">
          <label style={{ color: "#9ca3af", fontSize: 10, fontWeight: 700 }}>
            MACHINE
            <select value={machineKey} onChange={event => { setMachineKey(event.target.value); setResult(null); }}
                    style={{ display: "block", width: "100%", marginTop: 6, padding: 9,
                             background: "#0d1117", color: "#f9fafb", border: "1px solid #374151",
                             borderRadius: 5 }}>
              {candidates.map(machine => <option key={machine.machine_key} value={machine.machine_key}>
                {machine.name}
              </option>)}
            </select>
          </label>
          <label style={{ color: "#9ca3af", fontSize: 10, fontWeight: 700 }}>
            MAESTRO LOG FILE
            <input type="file" accept=".log,.txt,.csv" onChange={chooseFile}
                   style={{ display: "block", width: "100%", marginTop: 6, color: "#9ca3af",
                            background: "#0d1117", border: "1px solid #374151", borderRadius: 5,
                            padding: 7 }} />
          </label>
        </div>

        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center",
                      gap: 12, marginBottom: 18, flexWrap: "wrap" }}>
          <div style={{ color: fileName ? "#d1d5db" : "#4b5563", fontSize: 11 }}>
            {fileName ? `${fileName} · ${logText.split(/\r?\n/).length} lines` : "Choose a log from Finder to begin"}
          </div>
          <button disabled={!logText || busy} onClick={() => analyze(false)}
                  style={{ ...button, opacity: !logText || busy ? .45 : 1 }}>
            {busy ? "Analyzing…" : "Analyze evidence"}
          </button>
        </div>

        {error && <div style={{ color: "#fca5a5", fontSize: 11, marginBottom: 14 }}>{error}</div>}
        {result && <>
          <div style={{ display: "flex", gap: 18, alignItems: "baseline", marginBottom: 14,
                        paddingTop: 14, borderTop: "1px solid #1f2937" }}>
            <div style={{ fontSize: 26, fontWeight: 800,
                          color: result.ready_to_replay ? "#22c55e" : "#f59e0b" }}>
              {Math.round(result.recognition_rate * 100)}%
            </div>
            <div>
              <div style={{ fontSize: 12, fontWeight: 700 }}>lines recognized</div>
              <div style={{ color: "#6b7280", fontSize: 10, marginTop: 2 }}>
                {result.recognized_lines} of {result.nonempty_lines} non-empty lines
              </div>
            </div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
                        gap: 8, marginBottom: 14 }}>
            {result.checks.map(check => <div key={check.key} style={{ padding: "9px 0",
                  borderBottom: "1px solid #1f2937", display: "flex", gap: 8 }}>
              <span style={{ color: check.passed ? "#22c55e" : "#ef4444", fontWeight: 800 }}>
                {check.passed ? "✓" : "×"}
              </span>
              <div><div style={{ fontSize: 11, color: "#e5e7eb" }}>{check.label}</div>
                <div style={{ fontSize: 10, color: "#6b7280", marginTop: 2 }}>{check.detail}</div></div>
            </div>)}
          </div>
          <div style={{ color: "#9ca3af", fontSize: 11, marginBottom: 16 }}>
            Events: {Object.entries(result.event_counts).map(([key, count]) => `${key} ${count}`).join(" · ") || "none"}
          </div>
          {result.candidate_keywords?.length > 0 && !result.ready_to_replay && <div style={{ color: "#6b7280",
                fontSize: 10, marginBottom: 16 }}>
            Unmapped keywords: {result.candidate_keywords.slice(0, 8).map(item => item.token).join(", ")}
          </div>}
          {result.persisted && <div style={{ color: "#22c55e", fontSize: 11, marginBottom: 14 }}>
            Imported: {Object.entries(result.ingestion).map(([key, count]) => `${key} ${count}`).join(" · ")}
          </div>}
          {result.ready_to_replay && !result.persisted && <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <button onClick={() => analyze(true)} disabled={busy}
                    style={{ ...button, background: "#166534", borderColor: "#22c55e" }}>
              Import validated history
            </button>
          </div>}
        </>}
      </div>
    </div>
  );
}
