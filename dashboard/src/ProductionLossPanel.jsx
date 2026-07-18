import { ChevronDown, ChevronUp, Gauge, ShieldCheck, TriangleAlert } from "lucide-react";
import { useState } from "react";

const COLOR = {
  telemetry_unknown: "#60a5fa",
  breakdown: "#ef4444",
  setup_adjustment: "#f59e0b",
  material_starvation: "#a78bfa",
  tooling_stop: "#f97316",
  staffing_loss: "#22c55e",
  quality_stop: "#ec4899",
  quality_loss: "#ec4899",
  no_demand: "#6b7280",
  minor_stop: "#38bdf8",
  unclassified_idle: "#94a3b8",
  unclassified_downtime: "#fb7185",
  speed_loss: "#facc15",
};

const pct = value => value == null ? "Pending" : `${Math.round(value * 100)}%`;

function Metric({ label, value, tone = "#f9fafb", detail }) {
  return <div>
    <div style={{ color: "#4b5563", fontSize: 9, fontWeight: 700, textTransform: "uppercase" }}>{label}</div>
    <div style={{ color: tone, fontSize: 20, fontWeight: 800, marginTop: 2 }}>{value}</div>
    {detail && <div style={{ color: "#6b7280", fontSize: 9, marginTop: 2 }}>{detail}</div>}
  </div>;
}

export function ProductionLossPanel({ data }) {
  const [expanded, setExpanded] = useState(false);
  if (!data) return null;
  const { shift, summary, recommendation, pareto = [], machines = [] } = data;
  const visible = pareto.slice(0, 5);
  const maxSeconds = Math.max(1, ...visible.map(item => item.seconds));
  const coverage = summary.classified_coverage ?? 0;
  const attentionColor = COLOR[recommendation?.category] ?? "#6b7280";

  return <section style={{ borderTop: "1px solid #1f2937", borderBottom: "1px solid #1f2937",
    padding: "14px 0", marginBottom: 20 }}>
    <div className="loss-summary" style={{ display: "grid",
      gridTemplateColumns: "minmax(180px, 1.1fr) 120px 120px minmax(280px, 1.8fr) auto",
      gap: 20, alignItems: "center" }}>
      <div>
        <div style={{ display: "flex", alignItems: "center", gap: 7, color: "#9ca3af",
          fontSize: 10, fontWeight: 800, textTransform: "uppercase" }}>
          <Gauge size={14} /> Production loss waterfall
        </div>
        <div style={{ color: "#f3f4f6", fontSize: 12, fontWeight: 700, marginTop: 5 }}>
          {shift.local_date} · {shift.label}
        </div>
        <div style={{ color: shift.verified ? "#22c55e" : "#f59e0b", fontSize: 9, marginTop: 3,
          textTransform: "uppercase", fontWeight: 700 }}>
          {shift.verified ? "Verified production calendar" : "Calendar confirmation required"}
          {shift.active ? " · live shift" : " · latest completed shift"}
        </div>
      </div>
      <Metric label="Classified" value={pct(coverage)}
        tone={coverage >= .9 ? "#22c55e" : coverage >= .5 ? "#f59e0b" : "#60a5fa"}
        detail="scheduled machine time" />
      <Metric label="Trusted OEE" value={pct(summary.decision_ready_oee)}
        tone={summary.decision_ready_oee == null ? "#6b7280" : "#22c55e"}
        detail={`${summary.decision_ready_machines}/${summary.production_machines} machines ready`} />
      <div>
        <div style={{ display: "flex", alignItems: "center", gap: 6, color: attentionColor,
          fontSize: 10, fontWeight: 800, textTransform: "uppercase" }}>
          {recommendation?.decision_ready ? <ShieldCheck size={13} /> : <TriangleAlert size={13} />}
          {recommendation?.title ?? "No measured loss"}
        </div>
        <div style={{ color: "#d1d5db", fontSize: 11, marginTop: 4 }}>
          {recommendation
            ? `${recommendation.machine_minutes.toLocaleString()} machine-min · ${recommendation.action}`
            : "No loss exposure exists in this production window."}
        </div>
        {!!visible.length && <div style={{ display: "grid", gap: 4, marginTop: 8 }}>
          {visible.map(item => <div key={item.category} style={{ display: "grid",
            gridTemplateColumns: "105px minmax(60px,1fr) 58px", gap: 7, alignItems: "center" }}>
            <span style={{ color: "#9ca3af", fontSize: 9, overflow: "hidden", textOverflow: "ellipsis",
              whiteSpace: "nowrap" }}>{item.label}</span>
            <span style={{ height: 5, background: "#1f2937", overflow: "hidden" }}>
              <span style={{ display: "block", height: "100%", width: `${item.seconds / maxSeconds * 100}%`,
                background: COLOR[item.category] ?? "#6b7280" }} />
            </span>
            <span style={{ color: "#6b7280", fontSize: 9, textAlign: "right" }}>
              {item.machine_minutes.toLocaleString()} min
            </span>
          </div>)}
        </div>}
      </div>
      <button onClick={() => setExpanded(value => !value)} title={expanded ? "Hide machine losses" : "Show machine losses"}
        style={{ width: 32, height: 32, display: "inline-grid", placeItems: "center", cursor: "pointer",
          background: "#111827", border: "1px solid #374151", borderRadius: 6, color: "#d1d5db" }}>
        {expanded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
      </button>
    </div>

    {expanded && <div style={{ marginTop: 14, overflowX: "auto" }}>
      <table className="loss-table" style={{ width: "100%", borderCollapse: "collapse", minWidth: 720 }}>
        <thead><tr>{["Machine", "State evidence", "Availability", "Performance", "Quality", "OEE", "Largest measured loss"].map(label =>
          <th key={label} style={{ color: "#4b5563", fontSize: 9, textTransform: "uppercase",
            textAlign: "left", padding: "7px 9px", borderBottom: "1px solid #1f2937" }}>{label}</th>)}</tr></thead>
        <tbody>{machines.map(machine => <tr key={machine.machine_key}>
          <td style={cell}><strong style={{ color: "#e5e7eb" }}>{machine.machine_name}</strong></td>
          <td style={cell}>{pct(machine.telemetry_coverage)}</td>
          <td style={cell}>{pct(machine.availability)}</td>
          <td style={cell}>{pct(machine.performance)}</td>
          <td style={cell}>{pct(machine.quality)}</td>
          <td style={{ ...cell, color: machine.decision_ready ? "#22c55e" : "#6b7280", fontWeight: 800 }}>
            {pct(machine.oee)}</td>
          <td style={cell}>{machine.top_measured_loss
            ? `${machine.top_measured_loss.label} · ${machine.top_measured_loss.minutes} min`
            : machine.telemetry_unknown_s ? "Evidence incomplete" : "No measured loss"}</td>
        </tr>)}</tbody>
      </table>
    </div>}
  </section>;
}

const cell = { color: "#9ca3af", fontSize: 10, padding: "8px 9px", borderBottom: "1px solid #161e2b" };
