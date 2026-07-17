import { useState } from "react";
import { Clock3, Gauge, RefreshCw, TriangleAlert } from "lucide-react";

const fmtDuration = value => {
  if (value == null) return "—";
  const hours = Math.floor(value / 3600);
  const minutes = Math.round((value % 3600) / 60);
  return hours ? `${hours}h ${minutes}m` : `${minutes}m`;
};

const fmtTime = value => value
  ? new Date(value).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
  : "—";

function Metric({ label, value, detail, color = "#f3f4f6" }) {
  return <div style={{ minWidth: 0 }}>
    <div style={{ color: "#6b7280", fontSize: 9, fontWeight: 700, textTransform: "uppercase" }}>{label}</div>
    <div style={{ color, fontSize: 17, fontWeight: 800, marginTop: 3, overflowWrap: "anywhere" }}>{value}</div>
    {detail && <div style={{ color: "#6b7280", fontSize: 10, marginTop: 2 }}>{detail}</div>}
  </div>;
}

export function ForecastPanel({ forecast, canRefresh, onRefresh }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const latest = forecast?.latest;
  const result = latest?.result;
  const top = result?.constraints?.[0];
  const risks = (result?.jobs ?? []).filter(item => item.late_probability != null && item.late_probability >= 0.2);
  const primaryRisk = risks[0];
  const stale = Boolean(forecast?.stale);
  const ready = Boolean(forecast?.decision_ready);
  const calibration = forecast?.calibration;

  const run = async () => {
    setBusy(true);
    setError("");
    try { await onRefresh({ samples: 50 }); }
    catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  return <section style={{ borderTop: "1px solid #1f2937", borderBottom: "1px solid #1f2937",
    padding: "14px 0", marginBottom: 20 }}>
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, marginBottom: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
        <Gauge size={16} color={ready ? "#22c55e" : stale ? "#ef4444" : "#f59e0b"} />
        <div>
          <div style={{ color: "#d1d5db", fontSize: 11, fontWeight: 800, textTransform: "uppercase" }}>Production forecast</div>
          <div style={{ color: "#6b7280", fontSize: 10, marginTop: 2 }}>
            {latest ? `${result.sample_count} runs · ${result.policy} · ${fmtTime(result.generated_at)}` : "Waiting for a complete factory model"}
          </div>
        </div>
      </div>
      {canRefresh && <button onClick={run} disabled={busy} style={{ display: "inline-flex", alignItems: "center", gap: 6,
        background: "#1f2937", color: "#d1d5db", border: "1px solid #374151", borderRadius: 6,
        padding: "7px 10px", fontSize: 10, fontWeight: 700, cursor: busy ? "wait" : "pointer", opacity: busy ? .65 : 1 }}>
        <RefreshCw size={13} className={busy ? "spin" : ""} /> {busy ? "Forecasting" : "Refresh forecast"}
      </button>}
    </div>

    {!latest ? <div style={{ color: "#9ca3af", fontSize: 11 }}>{forecast?.guardrail}</div> : <>
      <div className="forecast-grid" style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(140px, 1fr))", gap: 18 }}>
        <Metric label="Forecast state" value={stale ? "Stale" : ready ? "Advisory ready" : "Commissioning"}
          detail={stale ? "Factory inputs changed" : `${Math.round((result.feasible_probability ?? 0) * 100)}% feasible runs`}
          color={stale ? "#ef4444" : ready ? "#22c55e" : "#f59e0b"} />
        <Metric label="Likely constraint" value={top?.machine_name ?? "—"}
          detail={top ? `${Math.round(top.bottleneck_probability * 100)}% frequency · ${Math.round(top.p90_utilization * 100)}% P90 load` : "No simulated constraint"}
          color={top?.bottleneck_probability >= .5 ? "#f59e0b" : "#d1d5db"} />
        <Metric label="P50 / P80 completion" value={result.kpis?.makespan_s ? `${fmtDuration(result.kpis.makespan_s.p50)} / ${fmtDuration(result.kpis.makespan_s.p80)}` : "—"}
          detail={`${result.jobs?.length ?? 0} production ${(result.jobs?.length ?? 0) === 1 ? "order" : "orders"}`} />
        <Metric label="Calibration" value={calibration?.status ?? "collecting"}
          detail={`${calibration?.outcome_count ?? 0} completed-order outcomes`}
          color={calibration?.status === "credible" ? "#22c55e" : calibration?.status === "drift" ? "#ef4444" : "#60a5fa"} />
      </div>

      <div style={{ marginTop: 12, display: "flex", alignItems: "center", gap: 8,
        color: primaryRisk ? "#fca5a5" : "#9ca3af", fontSize: 11, minHeight: 20 }}>
        {primaryRisk ? <TriangleAlert size={14} /> : <Clock3 size={14} />}
        {primaryRisk
          ? `${primaryRisk.job_name}: ${Math.round(primaryRisk.late_probability * 100)}% late risk, P80 ${fmtTime(primaryRisk.completion_at.p80)}${risks.length > 1 ? ` · ${risks.length - 1} more at risk` : ""}`
          : result.jobs?.some(item => item.due_at) ? "No material late-order risk in the current ensemble" : "Due times are required before HIVE can score delivery risk"}
      </div>
    </>}
    {(error || stale) && <div style={{ color: "#fca5a5", fontSize: 10, marginTop: 8 }}>{error || forecast.guardrail}</div>}
  </section>;
}
