const URGENCY_COLOR = {
  overdue: "#ef4444",
  urgent:  "#f59e0b",
  normal:  "#22c55e",
  unknown: "#6b7280",
};

const URGENCY_LABEL = {
  overdue: "OVERDUE",
  urgent:  "URGENT",
  normal:  "ON TRACK",
  unknown: "—",
};

function fmtTime(seconds) {
  if (!seconds) return null;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export function JobQueue({ plan }) {
  if (!plan || !plan.jobs?.length) {
    return (
      <div style={{ color: "#4b5563", fontSize: 11 }}>
        No jobs to sequence yet
      </div>
    );
  }

  return (
    <div>
      {plan.uncalibrated && (
        <div style={{
          fontSize: 10, color: "#f59e0b", background: "#451a03",
          border: "1px solid #92400e", borderRadius: 4,
          padding: "4px 8px", marginBottom: 10,
        }}>
          Cycle models are not active yet. Queue order uses controlled due times, priority, and part count.
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {plan.jobs.map(j => {
          const urgColor = URGENCY_COLOR[j.urgency] ?? "#6b7280";
          const eta      = fmtTime(j.estimated_total_s);

          return (
            <div key={j.job_name} style={{
              display:      "flex",
              alignItems:   "center",
              gap:          10,
              background:   "#0d1117",
              border:       `1px solid #1f2937`,
              borderLeft:   `3px solid ${urgColor}`,
              borderRadius: 6,
              padding:      "7px 10px",
            }}>
              {/* Position badge */}
              <div style={{
                fontSize: 11, fontWeight: 800, color: "#4b5563",
                minWidth: 20, textAlign: "center",
              }}>
                #{j.position}
              </div>

              {/* Job name + client */}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 12, fontWeight: 700, color: "#f9fafb",
                              whiteSpace: "nowrap", overflow: "hidden",
                              textOverflow: "ellipsis" }}>
                  {j.job_name}
                  {j.client_name && (
                    <span style={{ color: "#6b7280", fontWeight: 400,
                                   marginLeft: 6, fontSize: 11 }}>
                      {j.client_name}
                    </span>
                  )}
                </div>
                <div style={{ fontSize: 10, color: "#4b5563", marginTop: 1 }}>
                  {j.score_reason}
                </div>
              </div>

              {/* Parts count */}
              <div style={{ fontSize: 10, color: "#6b7280", textAlign: "right",
                            minWidth: 50 }}>
                {j.total_parts} parts
              </div>

              {/* ETA */}
              {eta && (
                <div style={{ fontSize: 10, color: "#60a5fa", minWidth: 40,
                              textAlign: "right" }}>
                  {eta}
                </div>
              )}

              {/* Urgency badge */}
              <div style={{
                fontSize: 9, fontWeight: 700, color: urgColor,
                background: `${urgColor}18`,
                padding: "2px 6px", borderRadius: 3,
                letterSpacing: 0.5, minWidth: 58, textAlign: "center",
              }}>
                {URGENCY_LABEL[j.urgency] ?? "—"}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
