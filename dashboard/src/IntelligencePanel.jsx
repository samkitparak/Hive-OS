const STATUS = {
  ready: { color: "#22c55e", label: "Optimization ready" },
  learning: { color: "#f59e0b", label: "Learning from live data" },
  commissioning: { color: "#60a5fa", label: "Commissioning required" },
};

export function IntelligencePanel({ optimization, quality, learning, routing, twin, onCommission }) {
  if (!optimization || !quality) return null;
  const status = STATUS[optimization.status] ?? STATUS.commissioning;
  const recommendation = optimization.recommendations?.[0];
  const score = Math.round((quality.overall_score ?? 0) * 100);
  return (
    <section style={{ borderTop: "1px solid #1f2937", borderBottom: "1px solid #1f2937",
                      padding: "14px 0", marginBottom: 20 }}>
      <div className="intelligence-grid" style={{ display: "grid",
            gridTemplateColumns: "170px 130px 150px minmax(240px, 1fr) auto", gap: 20,
            alignItems: "center" }}>
        <div>
          <div style={{ color: "#4b5563", fontSize: 9, fontWeight: 700,
                        textTransform: "uppercase" }}>Factory model</div>
          <div style={{ color: learning?.active_models ? "#22c55e" : "#f59e0b",
                        fontSize: 13, fontWeight: 800, marginTop: 4 }}>
            {learning?.active_models ?? 0} active model{learning?.active_models === 1 ? "" : "s"}
          </div>
          <div style={{ color: "#6b7280", fontSize: 10, marginTop: 3 }}>
            {routing?.edge_count ?? 0} route edges · {Math.round((twin?.model_coverage ?? 0) * 100)}% modeled
          </div>
        </div>
        <div>
          <div style={{ color: status.color, fontSize: 11, fontWeight: 800,
                        textTransform: "uppercase" }}>{status.label}</div>
          <div style={{ color: "#6b7280", fontSize: 10, marginTop: 4 }}>
            {quality.summary.reporting_machines} of {quality.summary.total_machines} machines reporting
          </div>
        </div>
        <div>
          <div style={{ color: "#4b5563", fontSize: 9, fontWeight: 700,
                        textTransform: "uppercase" }}>Telemetry confidence</div>
          <div style={{ color: score >= 80 ? "#22c55e" : score >= 50 ? "#f59e0b" : "#60a5fa",
                        fontSize: 22, fontWeight: 800, marginTop: 2 }}>{score}%</div>
        </div>
        <div>
          <div style={{ color: "#4b5563", fontSize: 9, fontWeight: 700,
                        textTransform: "uppercase" }}>Best next action</div>
          <div style={{ color: "#f3f4f6", fontSize: 13, fontWeight: 700, marginTop: 3 }}>
            {recommendation?.title ?? "Continue collecting evidence"}
          </div>
          <div style={{ color: "#9ca3af", fontSize: 11, marginTop: 3 }}>
            {twin?.operational_recommendation
              ? (recommendation?.action ?? optimization.guardrail)
              : (twin?.guardrail ?? recommendation?.action ?? optimization.guardrail)}
          </div>
        </div>
        <button onClick={onCommission} style={{ background: "#2563eb", color: "white",
              border: 0, borderRadius: 6, padding: "8px 13px", fontSize: 11,
              fontWeight: 700, cursor: "pointer" }}>Commission machine</button>
      </div>
    </section>
  );
}
