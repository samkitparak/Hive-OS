import { useQuery } from "@tanstack/react-query";
import { fetchMachine, fetchJobParts } from "./api";

const EVENT_COLOR = {
  power_on:    "#22c55e",
  cycle_start: "#60a5fa",
  cycle_end:   "#a78bfa",
  idle:        "#f59e0b",
  state_idle:  "#f59e0b",
  power_off:   "#6b7280",
  state_off:   "#6b7280",
  alarm:       "#ef4444",
};

export function MachineDetail({ machineKey, onClose }) {
  const { data: machine } = useQuery({
    queryKey:  ["machine", machineKey],
    queryFn:   () => fetchMachine(machineKey),
    refetchInterval: 5000,
  });

  if (!machine) return null;

  return (
    <div style={{
      position:   "fixed", inset: 0,
      background: "rgba(0,0,0,0.7)",
      display:    "flex", alignItems: "center", justifyContent: "center",
      zIndex:     100,
    }}
    onClick={onClose}
    >
      <div
        style={{
          background:   "#111827",
          border:       "1px solid #374151",
          borderRadius: 12,
          padding:      24,
          width:        480,
          maxHeight:    "80vh",
          overflowY:    "auto",
        }}
        onClick={e => e.stopPropagation()}
      >
        {/* Title */}
        <div style={{ display: "flex", justifyContent: "space-between",
                      alignItems: "center", marginBottom: 20 }}>
          <div>
            <div style={{ fontSize: 18, fontWeight: 700, color: "#f9fafb" }}>
              {machine.name}
            </div>
            <div style={{ fontSize: 12, color: "#6b7280" }}>{machine.type} · {machine.brand}</div>
          </div>
          <button
            onClick={onClose}
            style={{ background: "none", border: "none", color: "#6b7280",
                     fontSize: 20, cursor: "pointer" }}
          >✕</button>
        </div>

        {/* Current state */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr",
                      gap: 8, marginBottom: 20 }}>
          {[
            ["State",       machine.state?.toUpperCase() ?? "—"],
            ["Power",       machine.power_w != null ? `${Math.round(machine.power_w)}W` : "—"],
            ["Current CNC", machine.current_cnc?.replace(".xcs","") ?? "—"],
          ].map(([label, val]) => (
            <div key={label} style={{ background: "#1f2937", borderRadius: 8, padding: "10px 12px" }}>
              <div style={{ fontSize: 10, color: "#6b7280", marginBottom: 4 }}>{label}</div>
              <div style={{ fontSize: 13, color: "#f9fafb", fontWeight: 600,
                            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {val}
              </div>
            </div>
          ))}
        </div>

        {/* Recent events */}
        <div style={{ fontSize: 12, color: "#9ca3af", marginBottom: 8, fontWeight: 600 }}>
          RECENT EVENTS
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {(machine.recent_events ?? []).map((ev, i) => (
            <div key={i} style={{
              display:      "flex", justifyContent: "space-between",
              alignItems:   "center",
              background:   "#1f2937",
              borderRadius: 6,
              padding:      "6px 10px",
              fontSize:     11,
            }}>
              <span style={{ color: EVENT_COLOR[ev.event_type] ?? "#d1d5db",
                             fontWeight: 600 }}>
                {ev.event_type}
              </span>
              {ev.cnc_file && (
                <span style={{ color: "#60a5fa", fontSize: 10 }}>
                  {ev.cnc_file.replace(".xcs","")}
                </span>
              )}
              <span style={{ color: "#6b7280" }}>
                {ev.ts ? new Date(ev.ts).toLocaleTimeString([], {
                  hour: "2-digit", minute: "2-digit", second: "2-digit"
                }) : ""}
              </span>
            </div>
          ))}
          {(!machine.recent_events || machine.recent_events.length === 0) && (
            <div style={{ color: "#4b5563", fontSize: 11 }}>No events recorded yet</div>
          )}
        </div>
      </div>
    </div>
  );
}
