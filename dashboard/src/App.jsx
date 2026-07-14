import { useState, useCallback, useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchMachines, fetchOee, fetchJobs, fetchActiveJobs, fetchDailyScore, fetchSequence, fetchBottlenecks, fetchDataQuality, fetchOptimization, fetchLearningStatus, fetchRoutingGraph, fetchTwinReadiness, fetchProductionOrders, fetchProductionReadiness, updateProductionOrder, fetchProductionRoutes, replacePartRoute, fetchRouteExceptions, resolveRouteException, fetchPlanningScenarios, createPlanningScenario, decidePlanningScenario, fetchActiveSchedule, fetchExecutionSnapshot, syncExecution, updateExecutionJob, resolveExecutionException, fetchIdentitySnapshot, createLabelJob, markLabelJobPrinted, fetchResourceSnapshot, updateMaterialStock, updateLaborRole, updateToolPool, updateMachineResource, updateFactoryCalendar, updateWipBuffer, createResourceUnavailability, deleteResourceUnavailability, fetchDiagnostics, fetchDeployment, fetchConfig, saveConfig, fetchRemoteSetupPlan, fetchOperationsSummary, fetchDowntime, fetchWorkOrders, fetchMaintenanceSnapshot, syncMaintenance, updateMaintenancePlan, fetchMaintenanceWorkOrder, updateMaintenanceWorkOrder, completeMaintenanceWorkOrder, createSparePart, updateSpareStock, fetchRework, fetchBarcodeEvents, analyzeCommissioningLog, postJson, simulateEvent } from "./api";
import { MachineCard } from "./MachineCard";
import { MachineDetail } from "./MachineDetail";
import { JobProgress } from "./JobProgress";
import { JobQueue } from "./JobQueue";
import { DailyScore } from "./DailyScore";
import { BottleneckPanel } from "./BottleneckPanel";
import { DiagnosticsPanel } from "./DiagnosticsPanel";
import { OperationsPanel } from "./OperationsPanel";
import { SetupPanel } from "./SetupPanel";
import { IntelligencePanel } from "./IntelligencePanel";
import { CommissioningPanel } from "./CommissioningPanel";
import { PlanningPanel } from "./PlanningPanel";
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
  return { availability: avg("availability"), oee: avg("oee"), active: active.length,
           provisional: active.some(machine => machine.provisional) };
}

export default function App() {
  const qc = useQueryClient();
  const [selectedKey, setSelectedKey]     = useState(null);
  const [liveLog, setLiveLog]             = useState([]);
  const [machineStates, setMachineStates] = useState({});
  const [demoRunning, setDemoRunning]     = useState(false);
  const [showDiagnostics, setShowDiagnostics] = useState(false);
  const [showOperations, setShowOperations] = useState(false);
  const [showSetup, setShowSetup] = useState(false);
  const [showCommissioning, setShowCommissioning] = useState(false);
  const [showPlanning, setShowPlanning] = useState(false);
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
  const { data: bottlenecks = null } = useQuery({
    queryKey: ["bottlenecks"], queryFn: fetchBottlenecks, refetchInterval: 30000,
  });
  const { data: dataQuality = null } = useQuery({
    queryKey: ["dataQuality"], queryFn: fetchDataQuality, refetchInterval: 30000,
  });
  const { data: optimization = null } = useQuery({
    queryKey: ["optimization"], queryFn: fetchOptimization, refetchInterval: 30000,
  });
  const { data: learning = null } = useQuery({
    queryKey: ["learning"], queryFn: fetchLearningStatus, refetchInterval: 30000,
  });
  const { data: routing = null } = useQuery({
    queryKey: ["routing"], queryFn: fetchRoutingGraph, refetchInterval: 30000,
  });
  const { data: twin = null } = useQuery({
    queryKey: ["twin"], queryFn: fetchTwinReadiness, refetchInterval: 60000,
  });
  const { data: productionOrders = [] } = useQuery({
    queryKey: ["productionOrders"], queryFn: fetchProductionOrders, refetchInterval: 30000,
  });
  const { data: productionReadiness = null } = useQuery({
    queryKey: ["productionReadiness"], queryFn: fetchProductionReadiness, refetchInterval: 30000,
  });
  const { data: planningScenarios = [] } = useQuery({
    queryKey: ["planningScenarios"], queryFn: fetchPlanningScenarios, refetchInterval: 30000,
  });
  const { data: activeSchedule = null } = useQuery({
    queryKey: ["activeSchedule"], queryFn: fetchActiveSchedule, refetchInterval: 30000,
  });
  const { data: resourceSnapshot = null } = useQuery({
    queryKey: ["resourceSnapshot"], queryFn: fetchResourceSnapshot, refetchInterval: 30000,
  });
  const { data: routeExceptions = [] } = useQuery({
    queryKey: ["routeExceptions"], queryFn: fetchRouteExceptions, refetchInterval: 15000,
  });
  const { data: diagnostics = null } = useQuery({
    queryKey: ["diagnostics"], queryFn: fetchDiagnostics, refetchInterval: 30000,
  });
  const { data: deployment = null } = useQuery({
    queryKey: ["deployment"], queryFn: fetchDeployment, refetchInterval: 60000,
  });
  const { data: siteConfig = null } = useQuery({
    queryKey: ["siteConfig"], queryFn: fetchConfig, refetchInterval: 60000,
  });
  const { data: operationsSummary = null } = useQuery({
    queryKey: ["operationsSummary"], queryFn: fetchOperationsSummary, refetchInterval: 15000,
  });
  const { data: downtime = [] } = useQuery({
    queryKey: ["downtime"], queryFn: fetchDowntime, refetchInterval: 15000,
  });
  const { data: workOrders = [] } = useQuery({
    queryKey: ["workOrders"], queryFn: fetchWorkOrders, refetchInterval: 30000,
  });
  const { data: maintenance = null } = useQuery({
    queryKey: ["maintenance"], queryFn: fetchMaintenanceSnapshot, refetchInterval: 30000,
  });
  const { data: rework = [] } = useQuery({
    queryKey: ["rework"], queryFn: fetchRework, refetchInterval: 15000,
  });
  const { data: barcodeEvents = [] } = useQuery({
    queryKey: ["barcodeEvents"], queryFn: fetchBarcodeEvents, refetchInterval: 15000,
  });
  const { data: executionSnapshot = null } = useQuery({
    queryKey: ["executionSnapshot"], queryFn: fetchExecutionSnapshot, refetchInterval: 10000,
  });
  const { data: identitySnapshot = null } = useQuery({
    queryKey: ["identitySnapshot"], queryFn: fetchIdentitySnapshot, refetchInterval: 10000,
  });

  const onEvent = useCallback((ev) => {
    if (ev._type === "snapshot") {
      setMachineStates(prev => ({ ...prev, [ev.machine_key]: ev }));
      return;
    }
    setMachineStates(prev => ({ ...prev, [ev.machine_key]: ev }));
    setLiveLog(prev => [ev, ...prev].slice(0, 40));
    if (ev.event_type === "cycle_end") {
      qc.invalidateQueries({ queryKey: ["oee"] });
      qc.invalidateQueries({ queryKey: ["activeJobs"] });
      qc.invalidateQueries({ queryKey: ["dailyScore"] });
      qc.invalidateQueries({ queryKey: ["bottlenecks"] });
      qc.invalidateQueries({ queryKey: ["dataQuality"] });
      qc.invalidateQueries({ queryKey: ["optimization"] });
      qc.invalidateQueries({ queryKey: ["learning"] });
      qc.invalidateQueries({ queryKey: ["routing"] });
      qc.invalidateQueries({ queryKey: ["twin"] });
      qc.invalidateQueries({ queryKey: ["productionOrders"] });
      qc.invalidateQueries({ queryKey: ["routeExceptions"] });
      qc.invalidateQueries({ queryKey: ["executionSnapshot"] });
      qc.invalidateQueries({ queryKey: ["maintenance"] });
    }
  }, [qc]);

  useSSE(onEvent);

  useEffect(() => () => {
    if (demoRef.current) clearInterval(demoRef.current);
  }, []);

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
      setDemoRunning(false);
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
      simulateEvent(mk, et, extras).then(() => {
        qc.invalidateQueries({ queryKey: ["machines"] });
      });
      i++;
    }, 1200);
    setDemoRunning(true);
  };

  const refreshOperations = () => {
    ["operationsSummary", "downtime", "workOrders", "maintenance", "rework", "barcodeEvents", "executionSnapshot", "identitySnapshot", "productionOrders", "resourceSnapshot", "jobs"].forEach(key => {
      qc.invalidateQueries({ queryKey: [key] });
    });
  };

  const runOperationsDemo = async (kind) => {
    if (kind === "downtime") {
      await postJson("/downtime", {
        machine_key: "stefani_kd",
        reason_code: "setup",
        notes: "Placeholder setup/changeover event",
      });
    } else if (kind === "quality") {
      await postJson("/quality/checks", {
        result: "fail",
        defect_code: "edge_band",
        assigned_area: "edge_banding",
        notes: "Placeholder QC defect",
      });
    } else if (kind === "ottimo") {
      await postJson("/connectors/ottimo/placeholder", {
        barcode: "AA-GBR|Fixed Shelf",
        event: "QC_OK",
        station: "packing",
        operator: "demo",
      });
    } else if (kind === "cvsql") {
      await postJson("/connectors/cabinet-vision-sql/placeholder", [{
        job_name: "PLACEHOLDER_SQL_JOB",
        client_name: "Demo Client",
        part_name: `Demo Part ${Date.now()}`,
        material: "HDHMR_18mm",
        length_mm: 1000,
        width_mm: 500,
        thickness_mm: 18,
      }]);
    }
    refreshOperations();
  };

  const runOperationsAction = async (kind, payload) => {
    let result;
    if (kind === "downtime") {
      result = await postJson("/downtime", payload);
    } else if (kind === "closeDowntime") {
      result = await postJson(`/downtime/${payload.id}/close`, { notes: payload.notes });
    } else if (kind === "quality") {
      result = await postJson("/quality/checks", payload);
    } else if (kind === "closeRework") {
      result = await postJson(`/rework/${payload.id}/close`, { notes: payload.notes });
    } else if (kind === "workOrder") {
      result = await postJson("/maintenance/work-orders", payload);
    } else if (kind === "executionSync") {
      result = await syncExecution();
    } else if (kind === "execution") {
      result = await updateExecutionJob(payload.id, payload.payload);
    } else if (kind === "executionException") {
      result = await resolveExecutionException(payload.id, payload.payload);
    } else if (kind === "labelJob") {
      result = await createLabelJob(payload);
    } else if (kind === "labelPrinted") {
      result = await markLabelJobPrinted(payload.id, payload.payload);
    } else if (kind === "maintenanceSync") {
      result = await syncMaintenance();
    } else if (kind === "maintenancePlan") {
      result = await updateMaintenancePlan(payload.id, payload.payload);
    } else if (kind === "maintenanceWorkOrderDetail") {
      return fetchMaintenanceWorkOrder(payload.id);
    } else if (kind === "maintenanceWorkOrder") {
      result = await updateMaintenanceWorkOrder(payload.id, payload.payload);
    } else if (kind === "maintenanceComplete") {
      result = await completeMaintenanceWorkOrder(payload.id, payload.payload);
    } else if (kind === "maintenanceSpare") {
      result = await createSparePart(payload);
    } else if (kind === "maintenanceStock") {
      result = await updateSpareStock(payload.key, payload.payload);
    }
    refreshOperations();
    return result;
  };

  const runConfigSave = async (payload) => {
    const result = await saveConfig(payload);
    ["siteConfig", "diagnostics", "deployment"].forEach(key => {
      qc.invalidateQueries({ queryKey: [key] });
    });
    return result;
  };

  const runRemoteSetupAction = async (kind, payload) => {
    if (kind === "plan") return fetchRemoteSetupPlan(payload.machine_key);
    const paths = {
      test: "/remote-setup/test-connection",
      folders: "/remote-setup/detect-folders",
      install: "/remote-setup/install-agent",
      restart: "/remote-setup/restart-agent",
      log: "/remote-setup/fetch-log",
    };
    return postJson(paths[kind], payload);
  };

  const runCommissioningAnalysis = async payload => {
    const result = await analyzeCommissioningLog(payload);
    if (payload.persist) {
      ["machines", "oee", "bottlenecks", "dataQuality", "optimization", "diagnostics"].forEach(key => {
        qc.invalidateQueries({ queryKey: [key] });
      });
    }
    return result;
  };

  const refreshPlanning = () => {
    ["productionOrders", "productionReadiness", "planningScenarios", "activeSchedule", "resourceSnapshot", "routeExceptions", "executionSnapshot", "identitySnapshot", "sequence", "twin", "jobs"].forEach(key => {
      qc.invalidateQueries({ queryKey: [key] });
    });
  };

  const runPlanningAction = async (kind, data) => {
    let result;
    if (kind === "order") result = await updateProductionOrder(data.id, data.payload);
    else if (kind === "routes") return fetchProductionRoutes(data.job_name);
    else if (kind === "saveRoute") result = await replacePartRoute(data.part_id, data.payload);
    else if (kind === "scenario") result = await createPlanningScenario(data);
    else if (kind === "decision") result = await decidePlanningScenario(data.id, data.payload);
    else if (kind === "exception") result = await resolveRouteException(data.id, data.payload);
    else if (kind === "material") result = await updateMaterialStock(data.key, data.payload);
    else if (kind === "labor") result = await updateLaborRole(data.key, data.payload);
    else if (kind === "tool") result = await updateToolPool(data.key, data.payload);
    else if (kind === "machineResource") result = await updateMachineResource(data.key, data.payload);
    else if (kind === "calendar") result = await updateFactoryCalendar(data);
    else if (kind === "wip") result = await updateWipBuffer(data.key, data.payload);
    else if (kind === "unavailability") result = await createResourceUnavailability(data);
    else if (kind === "deleteUnavailability") result = await deleteResourceUnavailability(data.id, data.actor);
    refreshPlanning();
    return result;
  };

  return (
    <div style={{ minHeight: "100vh", background: "#0d1117", color: "#f9fafb",
                  fontFamily: "'Inter', system-ui, sans-serif", padding: "20px 24px" }}>

      {/* ── Header ── */}
      <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "center", marginBottom: 24, gap: 16, flexWrap: "wrap" }}>
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
              <div title={summary.provisional ? "Performance or quality evidence is not calibrated yet" : "Calibrated factory OEE"}
                   style={{ fontSize: 10, color: "#6b7280" }}>
                FACTORY OEE{summary.provisional ? "*" : ""} (8h)
              </div>
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

        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button onClick={() => setShowCommissioning(true)} style={{
            background: "#1d4ed8", border: "1px solid #3b82f6", color: "#f9fafb",
            padding: "7px 14px", borderRadius: 6, fontSize: 12, cursor: "pointer",
          }}>
            Commission
          </button>
          <button onClick={() => setShowSetup(true)} style={{
            background: "#1f2937", border: "1px solid #374151", color: "#f9fafb",
            padding: "7px 14px", borderRadius: 6, fontSize: 12, cursor: "pointer",
          }}>
            Setup
          </button>
          <button onClick={() => setShowOperations(true)} style={{
            background: "#1f2937", border: "1px solid #374151", color: "#f9fafb",
            padding: "7px 14px", borderRadius: 6, fontSize: 12, cursor: "pointer",
          }}>
            Operations
          </button>
          <button onClick={() => setShowPlanning(true)} style={{
            background: routeExceptions.length ? "#78350f" : "#1f2937",
            border: `1px solid ${routeExceptions.length ? "#f59e0b" : "#374151"}`,
            color: "#f9fafb", padding: "7px 14px", borderRadius: 6, fontSize: 12, cursor: "pointer",
          }}>
            Planning{routeExceptions.length ? ` (${routeExceptions.length})` : ""}
          </button>
          <button onClick={() => setShowDiagnostics(true)} style={{
            background: "#1f2937", border: "1px solid #374151", color: "#f9fafb",
            padding: "7px 14px", borderRadius: 6, fontSize: 12, cursor: "pointer",
          }}>
            Diagnostics
          </button>
          <button onClick={() => window.open("/api/report/shift", "_blank")} style={{
            background: "#1f2937", border: "1px solid #374151", color: "#f9fafb",
            padding: "7px 14px", borderRadius: 6, fontSize: 12, cursor: "pointer",
          }}>
            ⬇ Shift Report
          </button>
          <button onClick={toggleDemo} style={{
            background: demoRunning ? "#7f1d1d" : "#1f2937",
            border: "1px solid #374151", color: "#f9fafb",
            padding: "7px 14px", borderRadius: 6, fontSize: 12, cursor: "pointer",
          }}>
            {demoRunning ? "■ Stop Demo" : "▶ Demo Mode"}
          </button>
        </div>
      </div>

      <IntelligencePanel optimization={optimization} quality={dataQuality}
                         learning={learning} routing={routing} twin={twin}
                         onCommission={() => setShowCommissioning(true)} />

      {/* ── Daily Score ── */}
      <div style={{ background: "#111827", border: "1px solid #1f2937",
                    borderRadius: 10, padding: "14px 20px", marginBottom: 20 }}>
        <div style={{ fontSize: 11, fontWeight: 700, color: "#4b5563",
                      letterSpacing: 1, marginBottom: 12 }}>TODAY'S SCORE</div>
        <DailyScore data={dailyScore} />
      </div>

      {/* ── Job Queue ── */}
      <div style={{ background: "#111827", border: "1px solid #1f2937",
                    borderLeft: "3px solid #ef4444", borderRadius: 8,
                    padding: "14px 20px", marginBottom: 20 }}>
        <div style={{ fontSize: 11, fontWeight: 700, color: "#4b5563",
                      letterSpacing: 1, marginBottom: 12 }}>CURRENT CONSTRAINT</div>
        <BottleneckPanel report={bottlenecks} />
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
      <div className="bottom-grid" style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 16 }}>

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
      {showDiagnostics && diagnostics && (
        <DiagnosticsPanel data={diagnostics} deployment={deployment} onClose={() => setShowDiagnostics(false)} />
      )}
      {showSetup && siteConfig && (
        <SetupPanel
          config={siteConfig}
          onClose={() => setShowSetup(false)}
          onSave={runConfigSave}
          onRemoteAction={runRemoteSetupAction}
        />
      )}
      {showOperations && (
        <OperationsPanel
          data={{ summary: operationsSummary, downtime, workOrders, maintenance, rework, barcodeEvents, execution: executionSnapshot, identity: identitySnapshot }}
          machines={enriched}
          jobs={jobs}
          onClose={() => setShowOperations(false)}
          onAction={runOperationsAction}
          onDemo={runOperationsDemo}
        />
      )}
      {showCommissioning && (
        <CommissioningPanel machines={enriched} onAnalyze={runCommissioningAnalysis}
                            onClose={() => setShowCommissioning(false)} />
      )}
      {showPlanning && (
        <PlanningPanel
          data={{ orders: productionOrders, readiness: productionReadiness, scenarios: planningScenarios, activeSchedule, exceptions: routeExceptions, resources: resourceSnapshot }}
          machines={enriched}
          onAction={runPlanningAction}
          onClose={() => setShowPlanning(false)}
        />
      )}

      <style>{`
        * { box-sizing: border-box; margin: 0; padding: 0; }
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-track { background: #0d1117; }
        ::-webkit-scrollbar-thumb { background: #374151; border-radius: 2px; }
        @media (max-width: 760px) {
          .bottom-grid { grid-template-columns: 1fr !important; }
          .constraint-grid { grid-template-columns: 1fr 1fr !important; }
          .constraint-recommendation { grid-column: 1 / -1; }
          .intelligence-grid { grid-template-columns: 1fr 1fr !important; }
          .commission-controls { grid-template-columns: 1fr !important; }
        }
      `}</style>
    </div>
  );
}
