const STATE_COLOR = {
  on:      "#22c55e",   // green
  idle:    "#f59e0b",   // amber
  off:     "#6b7280",   // grey
  alarm:   "#ef4444",   // red
  unknown: "#374151",   // dark grey
};

const STATE_LABEL = {
  on:      "RUNNING",
  idle:    "IDLE",
  off:     "OFF",
  alarm:   "ALARM",
  unknown: "—",
};

function OeeBar({ value, label }) {
  const pct = Math.round((value ?? 0) * 100);
  const color = pct >= 75 ? "#22c55e" : pct >= 50 ? "#f59e0b" : "#ef4444";
  return (
    <div style={{ marginBottom: 4 }}>
      <div style={{ display: "flex", justifyContent: "space-between",
                    fontSize: 10, color: "#9ca3af", marginBottom: 2 }}>
        <span>{label}</span><span>{pct}%</span>
      </div>
      <div style={{ height: 4, background: "#1f2937", borderRadius: 2 }}>
        <div style={{ height: "100%", width: `${pct}%`,
                      background: color, borderRadius: 2,
                      transition: "width 0.4s" }} />
      </div>
    </div>
  );
}

export function MachineCard({ machine, oee, onClick }) {
  const state      = machine.state ?? "unknown";
  const stateColor = STATE_COLOR[state] ?? STATE_COLOR.unknown;
  const cnc        = machine.current_cnc?.replace(".xcs", "") ?? null;
  const power      = machine.power_w != null ? `${Math.round(machine.power_w)}W` : null;

  const lastSeen = machine.last_seen
    ? new Date(machine.last_seen).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : null;

  return (
    <div
      onClick={onClick}
      style={{
        background:    "#111827",
        border:        `1px solid ${stateColor}40`,
        borderLeft:    `3px solid ${stateColor}`,
        borderRadius:  8,
        padding:       "12px 14px",
        cursor:        "pointer",
        transition:    "border-color 0.3s",
        minWidth:      0,
      }}
    >
      {/* Header row */}
      <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "flex-start", marginBottom: 8 }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 600, color: "#f9fafb",
                        whiteSpace: "nowrap", overflow: "hidden",
                        textOverflow: "ellipsis", maxWidth: 140 }}>
            {machine.name}
          </div>
          <div style={{ fontSize: 10, color: "#6b7280", marginTop: 1 }}>
            {machine.type}
          </div>
        </div>
        <div style={{
          fontSize:     10,
          fontWeight:   700,
          color:        stateColor,
          background:   `${stateColor}15`,
          padding:      "2px 7px",
          borderRadius: 4,
          letterSpacing: 0.5,
        }}>
          {STATE_LABEL[state]}
        </div>
      </div>

      {/* CNC file / power */}
      <div style={{ fontSize: 10, color: "#6b7280", minHeight: 14, marginBottom: 8 }}>
        {cnc   && <span style={{ color: "#60a5fa" }}>▶ {cnc}</span>}
        {power && <span style={{ marginLeft: cnc ? 8 : 0 }}>{power}</span>}
        {lastSeen && (
          <span style={{ float: "right" }}>{lastSeen}</span>
        )}
      </div>

      {/* OEE bars */}
      {oee && (
        <div>
          <OeeBar value={oee.availability} label="Avail" />
          <OeeBar value={oee.oee}          label="OEE" />
        </div>
      )}
      {!oee && (
        <div style={{ fontSize: 10, color: "#4b5563" }}>No OEE data yet</div>
      )}
    </div>
  );
}
