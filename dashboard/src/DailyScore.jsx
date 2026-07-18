function Stat({ label, value, color, sub }) {
  return (
    <div style={{ textAlign: "center" }}>
      <div style={{ fontSize: 9, color: "#6b7280", letterSpacing: 0.8,
                    textTransform: "uppercase", marginBottom: 3 }}>
        {label}
      </div>
      <div style={{ fontSize: 24, fontWeight: 800, color: color ?? "#f9fafb",
                    lineHeight: 1 }}>
        {value}
      </div>
      {sub && (
        <div style={{ fontSize: 9, color: "#6b7280", marginTop: 3 }}>{sub}</div>
      )}
    </div>
  );
}

function ScoreRing({ score }) {
  const r      = 36;
  const circ   = 2 * Math.PI * r;
  const filled = (score / 100) * circ;
  const color  = score >= 70 ? "#22c55e" : score >= 50 ? "#f59e0b" : "#ef4444";

  return (
    <div style={{ position: "relative", width: 92, height: 92 }}>
      <svg width={92} height={92} style={{ transform: "rotate(-90deg)" }}>
        <circle cx={46} cy={46} r={r} fill="none"
                stroke="#1f2937" strokeWidth={7} />
        <circle cx={46} cy={46} r={r} fill="none"
                stroke={color} strokeWidth={7}
                strokeDasharray={`${filled} ${circ - filled}`}
                strokeLinecap="round"
                style={{ transition: "stroke-dasharray 0.8s" }} />
      </svg>
      <div style={{
        position: "absolute", inset: 0,
        display: "flex", flexDirection: "column",
        alignItems: "center", justifyContent: "center",
      }}>
        <div style={{ fontSize: 22, fontWeight: 800, color, lineHeight: 1 }}>
          {Math.round(score)}
        </div>
        <div style={{ fontSize: 9, color: "#6b7280" }}>/ 100</div>
      </div>
    </div>
  );
}

export function DailyScore({ data }) {
  if (!data) {
    return (
      <div style={{ color: "#4b5563", fontSize: 11 }}>
        Score loads after first shift data arrives
      </div>
    );
  }

  if (!data.score_ready) {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 18, flexWrap: "wrap" }}>
        <div style={{ color: "#f59e0b", fontSize: 14, fontWeight: 800 }}>Daily score pending</div>
        <div style={{ color: "#9ca3af", fontSize: 11 }}>
          Trusted OEE: {data.oee_ready ? `${Math.round(data.oee_avg * 100)}%` : "pending"}
          {` · ${data.oee_machines ?? 0} machines ready · ${data.jobs_done} completed jobs`}
        </div>
      </div>
    );
  }

  const trendIcon  = data.trend === "up" ? "▲" : data.trend === "down" ? "▼" : "●";
  const trendColor = data.trend === "up" ? "#22c55e" : data.trend === "down" ? "#ef4444" : "#6b7280";
  const vsText     = data.vs_avg > 0 ? `+${data.vs_avg}` : `${data.vs_avg}`;

  return (
    <div style={{ display: "flex", gap: 20, alignItems: "center", flexWrap: "wrap" }}>

      {/* Score ring */}
      <ScoreRing score={data.score} />

      {/* Stats */}
      <div style={{ display: "flex", gap: 20, flex: 1, flexWrap: "wrap",
                    justifyContent: "space-around" }}>

        <Stat
          label="vs 7-day avg"
          value={<span style={{ color: trendColor }}>{trendIcon} {vsText}</span>}
          sub={`avg ${data.rolling_avg}`}
        />

        <Stat
          label="Streak"
          value={
            <span>
              {data.streak}
              <span style={{ fontSize: 14, marginLeft: 2 }}>
                {data.streak >= 5 ? "🔥" : ""}
              </span>
            </span>
          }
          color={data.streak >= 3 ? "#f59e0b" : "#f9fafb"}
          sub="days beating avg"
        />

        <Stat
          label="OEE today"
          value={`${Math.round(data.oee_avg * 100)}%`}
          color={data.oee_avg >= 0.75 ? "#22c55e"
               : data.oee_avg >= 0.5  ? "#f59e0b" : "#ef4444"}
        />

        <Stat
          label="Jobs on time"
          value={
            data.jobs_done > 0
              ? `${data.jobs_on_time}/${data.jobs_done}`
              : "—"
          }
          color={
            data.jobs_done === 0 ? "#6b7280"
            : data.jobs_on_time === data.jobs_done ? "#22c55e"
            : data.jobs_on_time > 0 ? "#f59e0b"
            : "#ef4444"
          }
          sub={data.jobs_done > 0 ? `${Math.round(data.on_time_rate * 100)}% on time` : "no jobs yet"}
        />
      </div>
    </div>
  );
}
