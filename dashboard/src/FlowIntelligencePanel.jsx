import { Activity, ChevronDown, ChevronUp, Clock3, Layers3, RefreshCw } from "lucide-react";
import { useState } from "react";

const age = value => {
  if (value == null) return "Pending";
  if (value < 60) return `${Math.round(value)}s`;
  if (value < 3600) return `${Math.round(value / 60)}m`;
  return `${(value / 3600).toFixed(value < 36000 ? 1 : 0)}h`;
};

function Metric({ label, value, detail, tone = "#f3f4f6" }) {
  return <div className="flow-metric">
    <div>{label}</div>
    <strong style={{ color: tone }}>{value}</strong>
    <span>{detail}</span>
  </div>;
}

export function FlowIntelligencePanel({ data, onSync, syncing }) {
  const [expanded, setExpanded] = useState(false);
  if (!data) return null;
  const { current, history, sampling } = data;
  const summary = current.summary;
  const controlledWip = summary.ready_wip_qty + summary.in_process_qty + summary.held_wip_qty;
  const active = current.machines.filter(machine => (
    machine.released_queue_qty + machine.ready_wip_qty + machine.in_process_qty
    + machine.held_wip_qty + machine.blocked_demand_qty
  ) > 0).sort((a, b) => b.pressure_score - a.pressure_score);
  const top = current.top_flow_pressure;
  const latestShift = data.shifts[0];
  const samplingTone = sampling.status === "healthy" ? "#22c55e"
    : sampling.status === "stale" ? "#ef4444" : "#f59e0b";

  return <section className="flow-panel">
    <div className="flow-summary">
      <div className="flow-title">
        <div><Activity size={14} /> WIP and flow</div>
        <strong>{current.shift.local_date} · {current.shift.label}</strong>
        <span style={{ color: samplingTone }}>
          {sampling.status} sampler · {current.status.replaceAll("_", " ")}
        </span>
      </div>
      <Metric label="Controlled WIP" value={controlledWip}
        detail={`${summary.physically_observed_qty} physically observed`}
        tone={summary.decision_ready ? "#22c55e" : "#f3f4f6"} />
      <Metric label="Released queue" value={summary.released_queue_qty}
        detail={`${summary.blocked_demand_qty} blocked demand`} />
      <Metric label="Queue P90" value={age(top?.ready_age_p90_s)}
        detail={top?.machine_name || "No active station queue"}
        tone={top?.ready_age_p90_s ? "#f59e0b" : "#6b7280"} />
      <Metric label="Trusted shifts" value={`${history.decision_ready_shifts}/${history.archived_shifts}`}
        detail={latestShift ? `latest ${latestShift.local_date}` : "history starts at first shift close"} />
      <div className="flow-actions">
        {onSync && <button onClick={onSync} disabled={syncing} title="Capture flow sample and close due shifts">
          <RefreshCw size={14} className={syncing ? "spin" : ""} />
        </button>}
        <button onClick={() => setExpanded(value => !value)}
          title={expanded ? "Hide station flow" : "Show station flow"}>
          {expanded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
        </button>
      </div>
    </div>

    <div className="flow-pressure">
      <div className="flow-pressure-label">
        <Layers3 size={13} />
        <span>{top ? `${top.machine_name} · ${top.state.replaceAll("_", " ")}` : "No released flow"}</span>
        <strong>{top ? `${Math.round(top.pressure_score)} pressure` : "Learning"}</strong>
      </div>
      <div className="flow-pressure-track">
        <span style={{ width: `${Math.min(100, top?.pressure_score || 0)}%` }} />
      </div>
      <div className="flow-pressure-note">
        <Clock3 size={12} />
        {top
          ? `${top.ready_wip_qty} downstream-ready · ${top.confidence} evidence · pressure corroborates, but does not prove, the constraint`
          : current.evidence_gaps[0] || "No queue exposure in the current shift"}
      </div>
    </div>

    {expanded && <div className="flow-table-wrap">
      <table className="flow-table">
        <thead><tr>{[
          "Station", "Released", "Ready WIP", "Running", "Held", "Blocked", "Queue P90", "Buffer", "Pressure",
        ].map(label => <th key={label}>{label}</th>)}</tr></thead>
        <tbody>{(active.length ? active : current.machines).map(machine => <tr key={machine.machine_key}>
          <td><strong>{machine.machine_name}</strong><span>{machine.confidence}</span></td>
          <td>{machine.released_queue_qty}</td>
          <td>{machine.ready_wip_qty}</td>
          <td>{machine.in_process_qty}</td>
          <td>{machine.held_wip_qty}</td>
          <td>{machine.blocked_demand_qty}</td>
          <td>{age(machine.ready_age_p90_s)}</td>
          <td style={{ color: machine.buffer.reconciled ? "#22c55e" : "#6b7280" }}>
            {machine.buffer.quantity == null ? "Pending" : machine.buffer.reconciled ? "Matched" : `${machine.buffer.difference_qty > 0 ? "+" : ""}${machine.buffer.difference_qty}`}
          </td>
          <td>{Math.round(machine.pressure_score)}</td>
        </tr>)}</tbody>
      </table>
    </div>}
  </section>;
}
