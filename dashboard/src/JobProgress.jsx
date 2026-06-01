const ON_TIME_COLOR = {
  on_time:  "#22c55e",
  at_risk:  "#f59e0b",
  late:     "#ef4444",
};

const ON_TIME_LABEL = {
  on_time:  "ON TIME",
  at_risk:  "AT RISK",
  late:     "LATE",
};

function ProgressBar({ pct }) {
  const color = pct >= 0.75 ? "#22c55e" : pct >= 0.4 ? "#f59e0b" : "#60a5fa";
  return (
    <div style={{ height: 5, background: "#1f2937", borderRadius: 3, marginTop: 6 }}>
      <div style={{
        height: "100%",
        width: `${Math.round(pct * 100)}%`,
        background: color,
        borderRadius: 3,
        transition: "width 0.6s",
      }} />
    </div>
  );
}

function fmtEta(seconds) {
  if (!seconds) return null;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `~${h}h ${m}m left`;
  if (m > 0) return `~${m}m left`;
  return "< 1m left";
}

export function JobProgress({ jobs }) {
  if (!jobs?.length) {
    return (
      <div style={{ color: "#4b5563", fontSize: 11 }}>
        No active jobs — parts start appearing here once cycle events arrive
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {jobs.map(j => {
        const pct         = j.pct_done ?? 0;
        const statusColor = j.on_time ? ON_TIME_COLOR[j.on_time] : "#374151";
        const statusLabel = j.on_time ? ON_TIME_LABEL[j.on_time] : null;
        const eta         = fmtEta(j.eta_seconds);

        return (
          <div key={j.job_name}>
            <div style={{ display: "flex", justifyContent: "space-between",
                          alignItems: "center", marginBottom: 2 }}>
              <div>
                <span style={{ fontSize: 12, fontWeight: 700, color: "#f9fafb" }}>
                  {j.job_name}
                </span>
                {j.active_machines?.length > 0 && (
                  <span style={{ fontSize: 10, color: "#6b7280", marginLeft: 8 }}>
                    {j.active_machines.join(", ")}
                  </span>
                )}
              </div>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                {statusLabel && (
                  <span style={{
                    fontSize: 9, fontWeight: 700, color: statusColor,
                    background: `${statusColor}18`,
                    padding: "2px 6px", borderRadius: 3, letterSpacing: 0.5,
                  }}>
                    {statusLabel}
                  </span>
                )}
                <span style={{ fontSize: 11, color: "#9ca3af" }}>
                  {j.parts_done}/{j.total_parts}
                </span>
              </div>
            </div>

            <div style={{ display: "flex", justifyContent: "space-between",
                          fontSize: 10, color: "#6b7280" }}>
              <span>{Math.round(pct * 100)}% complete</span>
              {eta && <span style={{ color: j.on_time === "late" ? "#ef4444" : "#6b7280" }}>{eta}</span>}
              {!eta && j.parts_left > 0 && (
                <span style={{ color: "#4b5563" }}>ETA: set cycle times</span>
              )}
            </div>

            <ProgressBar pct={pct} />
          </div>
        );
      })}
    </div>
  );
}
