import { RefreshCw } from "lucide-react";

const CONFIDENCE_COLOR = { high: "#22c55e", medium: "#f59e0b", low: "#6b7280" };
const STATE = {
  capacity_constraint: { label: "Capacity constraint", color: "#ef4444" },
  reliability_constraint: { label: "Reliability constraint", color: "#f97316" },
  starved: { label: "Starved", color: "#60a5fa" },
  blocked: { label: "Blocked", color: "#a78bfa" },
  flow_or_staffing: { label: "Flow or staffing", color: "#f59e0b" },
  demand_absent: { label: "No released demand", color: "#6b7280" },
  insufficient_data: { label: "Evidence incomplete", color: "#6b7280" },
};

export function BottleneckPanel({ report, onSync, syncing = false }) {
  const current = report?.current ?? report?.candidate ?? report?.focus;
  if (!current) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16 }}>
        <div style={{ color: "#6b7280", fontSize: 11 }}>
          No released route demand or qualified constraint evidence.
        </div>
        <SyncButton onSync={onSync} syncing={syncing} />
      </div>
    );
  }

  const state = STATE[current.state] ?? STATE.insufficient_data;
  const confidenceColor = CONFIDENCE_COLOR[current.confidence] ?? "#6b7280";
  const episodeOpen = report?.episode?.status === "open"
    && report.episode.machine_key === current.machine_key
    && report.episode.constraint_state === current.state;
  const evidence = current.evidence?.slice(0, 3) ?? [];
  const counter = current.counter_evidence?.slice(0, 2) ?? [];

  return (
    <div>
      <div className="constraint-grid" style={{ display: "grid",
        gridTemplateColumns: "minmax(180px, 1.25fr) repeat(4, minmax(92px, 0.65fr)) minmax(230px, 1.4fr) auto",
        gap: 16, alignItems: "center" }}>
        <div>
          <div style={{ color: "#f9fafb", fontSize: 15, fontWeight: 800 }}>
            {current.machine_name}
          </div>
          <div style={{ color: episodeOpen ? "#22c55e" : confidenceColor,
            fontSize: 10, fontWeight: 700, marginTop: 3, textTransform: "uppercase" }}>
            {episodeOpen ? "Confirmed episode" : report?.current ? "Decision-ready sample" : "Candidate"}
            {` · ${current.confidence} confidence`}
          </div>
        </div>
        <Metric label="State" value={state.label} color={state.color} compact />
        <Metric label="Routed demand" value={`${current.demand_qty} units`} color="#d1d5db" />
        <Metric label="Ready now" value={`${current.ready_qty} units`} color="#f59e0b" />
        <Metric label="Running share" value={`${Math.round((current.active_ratio ?? 0) * 100)}%`} color="#60a5fa" />
        <div style={{ color: "#9ca3af", fontSize: 11, lineHeight: 1.4 }}>
          <div style={{ color: "#d1d5db", fontWeight: 700, marginBottom: 3 }}>
            {current.estimated_recoverable_units != null
              ? `Up to ${current.estimated_recoverable_units} units exposed`
              : (current.primary_cause ?? "learning").replaceAll("_", " ")}
          </div>
          {current.recommendation}
        </div>
        <SyncButton onSync={onSync} syncing={syncing} />
      </div>
      {(evidence.length > 0 || counter.length > 0) && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "5px 18px", marginTop: 11,
          paddingTop: 9, borderTop: "1px solid #1f2937", fontSize: 10, lineHeight: 1.4 }}>
          {evidence.map(item => <span key={item} style={{ color: "#9ca3af" }}>Evidence: {item}</span>)}
          {counter.map(item => <span key={item} style={{ color: "#6b7280" }}>Limit: {item}</span>)}
        </div>
      )}
    </div>
  );
}

function SyncButton({ onSync, syncing }) {
  if (!onSync) return null;
  return (
    <button onClick={onSync} disabled={syncing} title="Record constraint evidence snapshot"
      style={{ display: "inline-flex", alignItems: "center", justifyContent: "center",
        width: 32, height: 32, borderRadius: 6, border: "1px solid #374151",
        background: "#1f2937", color: "#d1d5db", cursor: syncing ? "wait" : "pointer" }}>
      <RefreshCw size={15} style={{ animation: syncing ? "spin 1s linear infinite" : "none" }} />
    </button>
  );
}

function Metric({ label, value, color, compact = false }) {
  return (
    <div>
      <div style={{ color: "#4b5563", fontSize: 9, fontWeight: 700, textTransform: "uppercase" }}>
        {label}
      </div>
      <div style={{ color, fontSize: compact ? 12 : 16, fontWeight: 800, marginTop: 3,
        lineHeight: compact ? 1.25 : 1.2 }}>{value}</div>
    </div>
  );
}
