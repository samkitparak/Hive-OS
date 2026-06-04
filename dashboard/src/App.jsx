import { useState, useCallback, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchMachines, fetchOee, fetchJobs, fetchActiveJobs, fetchDailyScore, fetchSequence, simulateEvent } from "./api";
import { MachineCard } from "./MachineCard";
import { MachineDetail } from "./MachineDetail";
import { JobProgress } from "./JobProgress";
import { JobQueue } from "./JobQueue";
import { DailyScore } from "./DailyScore";
import { useSSE } from "./useSSE";

const GROUPS = [
  { label: "Cutting",      keys: ["gabbiani_pt80", "nova_si400"] },
  { label: "CNC",          keys: ["morbidelli_cx100", "morbidelli_n100"] },
  { label: "Edge Banding", keys: ["stefani_kd"] },
  { label: "Pressing",     keys: ["sergiani_gs120", "varie_osama"] },
  { label: "Sanding",      keys: ["dmc60_rcs135", "dmc90_xrt135"] },
  { label: "Finishing",    keys: ["superfici", "action_e"] },
  { label: "Utilities",    keys: ["elgi_1", "elgi_2", "aarco_1", "aarco_2"] },
];

function factoryOee(oeeList) {
  if (!oeeList?.length) return null;
  const active = oeeList.filter(m => m.run_time_s > 0 || m.idle_time_s > 0);
  if (!active.length) return null;
  const avg = k => active.reduce((s, m) => s + (m[k] ?? 0), 0) / active.length;
  return { availability: avg("availability"), oee: avg("oee"), active: active.length };
}

export default function App() {
  const qc = useQueryClient();
  const [selectedKey, setSelectedKey]     = useState(null);
  const [liveLog, setLiveLog]             = useState([]);
  const [machineStates, setMachineStates] = useState({});
  const demoRef = useRef(null);

  const { data: machines = [] } = useQuery({
    queryKey: ["machines"], queryFn: fetchMachines, refetchInterval: 10000,
  });
  const { data: oeeList = [] } = useQuery({
    queryKey: ["oee"], queryFn: fetchOee, refetchInterval: 30000,
  });
  const { data: jobs = [] } = useQuery({
    queryKey: ["jobs"], queryFn: fetchJobs, refetchInterval: 60000,
  });
  const { data: activeJobs = [] } = useQuery({
    queryKey: ["activeJobs"], queryFn: fetchActiveJobs, refetchInterval: 15000,
  });
  const { data: dailyScore = null } = useQuery({
    queryKey: ["dailyScore"], queryFn: fetchDailyScore, refetchInterval: 60000,
  });
  const { data: sequence = null } = useQuery({
    queryKey: ["sequence"], queryFn: fetchSequence, refetchInterval: 120000,
  });

  const onEvent = useCallback((ev) => {
    if (ev._type === "snapshot") {
      setMachineStates(prev => ({ ...prev, [ev.machine_key]: ev }));
      return;
    }
    setMachineStates(prev => ({ ...prev, [ev.machine_key]: ev }));
    setLiveLog(prev => [ev, ...prev].slice(0, 40));
    if (ev.event_type === "cycle_end") {
      qc.invalidateQueries(["oee"]);
      qc.invalidateQueries(["activeJobs"]);
      qc.invalidateQueries(["dailyScore"]);
    }
  }, [qc]);

  useSSE(onEvent);

  const enriched = machines.map(m => {
    const live = machineStates[m.machine_key];
    if (!live) return m;
    const et = live.event_type;
    const state = live.state
      ?? (et === "state_on"   || et === "power_on"  || et === "cycle_start" ? "on"
        : et === "state_idle" || et === "idle"       || et === "cycle_end"   ? "idle"
        : et === "state_off"  || et === "power_off"                          ? "off"
        : et === "alarm"                                                      ? "alarm"
        : m.state);
    return { ...m, state,
             power_w:     live.power_w     ?? m.power_w,
             current_cnc: live.cnc_file    ?? m.current_cnc,
             last_event:  live.event_type,
             last_seen:   live.ts };
  });

  const safeOeeList = Array.isArray(oeeList) ? oeeList : [];
  const machineMap  = Object.fromEntries(enriched.map(m => [m.machine_key, m]));
  const oeeMap      = Object.fromEntries(safeOeeList.map(o => [o.machine_key, o]));
  const summary     = factoryOee(safeOeeList);

  const toggleDemo = () => {
    if (demoRef.current) {
      clearInterval(demoRef.current);
      demoRef.current = null;
      return;
    }
    const seq = [
      ["morbidelli_cx100", "power_on",    {}],
      ["morbidelli_cx100", "cycle_start", { cnc_file: "r86b0002.xcs" }],
      ["gabbiani_pt80",    "power_on",    {}],
      ["elgi_1",           "state_on",    { power_w: 6800 }],
      ["morbidelli_cx100", "cycle_end",   { cnc_file: "r86b0002.xcs" }],
      ["morbidelli_cx100", "cycle_start", { cnc_file: "r86b0006.xcs" }],
      ["elgi_1",           "state_idle",  { power_w: 420 }],
      ["gabbiani_pt80",    "cycle_start", {}],
      ["elgi_2",           "state_on",    { power_w: 7100 }],
      ["morbidelli_cx100", "idle",        {}],
    ];
    let i = 0;
    demoRef.current = setInterval(() => {
      const [mk, et, extras] = seq[i % seq.length];
      simulateEvent(mk, et, extras).then(() => qc.invalidateQueries(["machines"]));
      i++;
    }, 1200);
  };

  return (
    <div style={{ minHeight: "100vh", background: "#0d1117", color: "#f9fafb",
                  fontFamily: "'Inter', system-ui, sans-serif", padding: "20px 24px" }}>

      {/* ── Header ── */}
      <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "center", marginBottom: 24 }}>
        <div>
          <div style={{ fontSize: 22, fontWeight: 800, letterSpacing: -0.5 }}>
            HIVE OS{" "}
            <span style={{ color: "#374151", fontWeight: 400 }}>/ HAEEV Factory</span>
          </div>
          <div style={{ fontSize: 11, color: "#6b7280", marginTop: 2 }}>
            {enriched.filter(m => m.state === "on").length} running ·{" "}
            {enriched.filter(m => m.state === "idle").length} idle ·{" "}
            {enriched.filter(m => m.state === "alarm").length} alarms
          </div>
        </div>

        {summary && (
          <div style={{ display: "flex", gap: 24, alignItems: "center" }}>
            <div style={{ textAlign: "right" }}>
              <div style={{ fontSize: 10, color: "#6b7280" }}>FACTORY OEE (8h)</div>
              <div style={{ fontSize: 28, fontWeight: 800,
                            color: summary.oee >= 0.75 ? "#22c55e"
                                 : summary.oee >= 0.5  ? "#f59e0b" : "#ef4444" }}>
                {Math.round(summary.oee * 100)}%
              </div>
            </div>
            <div style={{ textAlign: "right" }}>
              <div style={{ fontSize: 10, color: "#6b7280" }}>AVAILABILITY</div>
              <div style={{ fontSize: 28, fontWeight: 800, color: "#60a5fa" }}>
                {Math.round(summary.availability * 100)}%
              </div>
            </div>
          </div>
        )}

        <div style={{ display: "flex", gap: 8 }}>
          <button onClick={() => window.open("/api/report/shift", "_blank")} style={{
            background: "#1f2937", border: "1px solid #374151", color: "#f9fafb",
            padding: "7px 14px", borderRadius: 6, fontSize: 12, cursor: "pointer",
          }}>
            ⬇ Shift Report
          </button>
          <button onClick={toggleDemo} style={{
            background: demoRef.current ? "#7f1d1d" : "#1f2937",
            border: "1px solid #374151", color: "#f9fafb",
            padding: "7px 14px", borderRadius: 6, fontSize: 12, cursor: "pointer",
          }}>
            {demoRef.current ? "■ Stop Demo" : "▶ Demo Mode"}
          </button>
        </div>
      </div>

      {/* ── Daily Score ── */}
      <div style={{ background: "#111827", border: "1px solid #1f2937",
                    borderRadius: 10, padding: "14px 20px", marginBottom: 20 }}>
        <div style={{ fontSize: 11, fontWeight: 700, color: "#4b5563",
                      letterSpacing: 1, marginBottom: 12 }}>TODAY'S SCORE</div>
        <DailyScore data={dailyScore} />
      </div>

      {/* ── Job Queue ── */}
      <div style={{ background: "#111827", border: "1px solid #1f2937",
                    borderRadius: 10, padding: "14px 20px", marginBottom: 20 }}>
        <div style={{ display: "flex", justifyContent: "space-between",
                      alignItems: "center", marginBottom: 12 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "#4b5563",
                        letterSpacing: 1 }}>PRODUCTION QUEUE</div>
          {sequence?.generated_at && (
            <div style={{ fontSize: 10, color: "#374151" }}>
              auto-sequenced · {new Date(sequence.generated_at).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})}
            </div>
          )}
        </div>
        <JobQueue plan={sequence} />
      </div>

      {/* ── Machine grid ── */}
      <div style={{ display: "flex", flexDirection: "column", gap: 20, marginBottom: 28 }}>
        {GROUPS.map(({ label, keys }) => {
          const gm = keys.map(k => machineMap[k]).filter(Boolean);
          if (!gm.length) return null;
          return (
            <div key={label}>
              <div style={{ fontSize: 10, fontWeight: 700, color: "#4b5563",
                            letterSpacing: 1, marginBottom: 8, textTransform: "uppercase" }}>
                {label}
              </div>
              <div style={{ display: "grid",
                            gridTemplateColumns: "repeat(auto-fill, minmax(190px, 1fr))",
                            gap: 10 }}>
                {gm.map(m => (
                  <MachineCard key={m.machine_key} machine={m} oee={oeeMap[m.machine_key]}
                               onClick={() => setSelectedKey(m.machine_key)} />
                ))}
              </div>
            </div>
          );
        })}
      </div>

      {/* ── Bottom strip ── */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 16 }}>

        {/* Active job progress */}
        <div style={{ background: "#111827", border: "1px solid #1f2937",
                      borderRadius: 10, padding: 16 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "#4b5563",
                        letterSpacing: 1, marginBottom: 12 }}>ACTIVE JOBS</div>
          <JobProgress jobs={activeJobs} />
        </div>

        {/* Recent jobs */}
        <div style={{ background: "#111827", border: "1px solid #1f2937",
                      borderRadius: 10, padding: 16 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "#4b5563",
                        letterSpacing: 1, marginBottom: 12 }}>RECENT JOBS</div>
          {jobs.slice(0, 8).map((j, i) => (
            <div key={i} style={{ display: "flex", justifyContent: "space-between",
                                  padding: "6px 0", fontSize: 12,
                                  borderBottom: i < 7 ? "1px solid #1f2937" : "none" }}>
              <div>
                <span style={{ color: "#f9fafb", fontWeight: 600 }}>{j.job_name}</span>
                {j.client_name && (
                  <span style={{ color: "#6b7280", marginLeft: 8 }}>{j.client_name}</span>
                )}
              </div>
              <div style={{ color: "#6b7280", fontSize: 11 }}>
                {j.total_parts} parts · {j.job_date}
              </div>
            </div>
          ))}
          {!jobs.length && (
            <div style={{ color: "#4b5563", fontSize: 11 }}>No jobs ingested yet</div>
          )}
        </div>

        {/* Live event log */}
        <div style={{ background: "#111827", border: "1px solid #1f2937",
                      borderRadius: 10, padding: 16 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "#4b5563",
                        letterSpacing: 1, marginBottom: 12 }}>
            LIVE EVENTS
            <span style={{ marginLeft: 8, display: "inline-block", width: 6, height: 6,
                           borderRadius: "50%", background: "#22c55e",
                           verticalAlign: "middle" }} />
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
            {liveLog.map((ev, i) => (
              <div key={i} style={{ display: "flex", gap: 8, fontSize: 11,
                                    color: i === 0 ? "#f9fafb" : "#6b7280" }}>
                <span style={{ color: "#374151", minWidth: 50 }}>
                  {ev.ts ? new Date(ev.ts).toLocaleTimeString([],
                    { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : ""}
                </span>
                <span style={{ color: "#60a5fa", minWidth: 110 }}>{ev.machine_key}</span>
                <span>{ev.event_type}</span>
                {ev.cnc_file && (
                  <span style={{ color: "#a78bfa" }}>{ev.cnc_file.replace(".xcs","")}</span>
                )}
              </div>
            ))}
            {!liveLog.length && (
              <div style={{ color: "#4b5563", fontSize: 11 }}>
                Waiting for events… hit ▶ Demo Mode to test
              </div>
            )}
          </div>
        </div>
      </div>

      {selectedKey && (
        <MachineDetail machineKey={selectedKey} onClose={() => setSelectedKey(null)} />
      )}

      <style>{`
        * { box-sizing: border-box; margin: 0; padding: 0; }
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-track { background: #0d1117; }
        ::-webkit-scrollbar-thumb { background: #374151; border-radius: 2px; }
      `}</style>
    </div>
  );
}
