const CONFIDENCE_COLOR = {
  high: "#22c55e",
  medium: "#f59e0b",
  low: "#6b7280",
};

export function BottleneckPanel({ report }) {
  const current = report?.current;
  if (!current) {
    return (
      <div style={{ color: "#6b7280", fontSize: 11 }}>
        No current constraint detected. Waiting for recent machine events.
      </div>
    );
  }

  const confidenceColor = CONFIDENCE_COLOR[current.confidence] ?? "#6b7280";
  return (
    <div className="constraint-grid" style={{ display: "grid", gridTemplateColumns: "minmax(180px, 1fr) repeat(4, minmax(80px, 0.5fr)) minmax(220px, 1.4fr)",
                  gap: 16, alignItems: "center" }}>
      <div>
        <div style={{ color: "#f9fafb", fontSize: 15, fontWeight: 800 }}>
          {current.machine_name}
        </div>
        <div style={{ color: confidenceColor, fontSize: 10, fontWeight: 700, marginTop: 3 }}>
          {current.confidence.toUpperCase()} CONFIDENCE
        </div>
      </div>
      <Metric label="Constraint score" value={`${Math.round(current.score * 100)}%`} color="#ef4444" />
      <Metric label="Utilisation" value={`${Math.round(current.utilisation * 100)}%`} color="#60a5fa" />
      <Metric label="Queue" value={`${current.queue_depth} parts`} color="#f59e0b" />
      <Metric label="Alarms" value={current.alarms} color={current.alarms ? "#ef4444" : "#6b7280"} />
      <div className="constraint-recommendation" style={{ color: "#9ca3af", fontSize: 11, lineHeight: 1.4 }}>
        {current.recommendation}
      </div>
    </div>
  );
}

function Metric({ label, value, color }) {
  return (
    <div>
      <div style={{ color: "#4b5563", fontSize: 9, fontWeight: 700, textTransform: "uppercase" }}>
        {label}
      </div>
      <div style={{ color, fontSize: 16, fontWeight: 800, marginTop: 3 }}>{value}</div>
    </div>
  );
}
