import { lazy, Suspense, useState, useCallback, useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, UserRound } from "lucide-react";
import { fetchMachines, fetchOee, fetchJobs, fetchActiveJobs, fetchDailyScore, fetchSequence, fetchBottlenecks, syncConstraints, fetchConstraintTimeline, updateConstraintSettings, fetchDataQuality, fetchOptimization, fetchLearningStatus, fetchRoutingGraph, fetchTwinReadiness, fetchForecast, refreshForecast, fetchProductionOrders, fetchProductionReadiness, updateProductionOrder, fetchProductionRoutes, replacePartRoute, fetchRouteExceptions, resolveRouteException, fetchPlanningScenarios, createPlanningScenario, decidePlanningScenario, fetchActiveSchedule, fetchRecovery, analyzeRecovery, decideRecovery, fetchExecutionSnapshot, syncExecution, updateExecutionJob, resolveExecutionException, fetchIdentitySnapshot, createLabelJob, markLabelJobPrinted, fetchResourceSnapshot, fetchProcurementSnapshot, updateProcurementSupplier, updateProcurementMapping, createPurchaseOrder, draftProcurementRecommendations, actOnPurchaseOrder, createGoodsReceipt, importProcurementCsv, updateMaterialStock, updateInventoryItem, updateInventoryLot, updateInventoryRequirement, createInventoryRemnant, updateInventoryRemnant, updateLaborRole, updateToolPool, createToolAsset, updateToolAsset, recordToolUsage, recordToolAction, recordToolService, updateToolProgramMapping, syncTooling, updateMachineResource, updateFactoryCalendar, updateWipBuffer, createResourceUnavailability, deleteResourceUnavailability, fetchDiagnostics, fetchDeployment, fetchResilience, createSystemBackup, verifySystemBackup, fetchConfig, saveConfig, fetchRemoteSetupPlan, forgetRemoteHost, fetchOperationsSummary, fetchDowntime, fetchWorkOrders, fetchMaintenanceSnapshot, syncMaintenance, updateMaintenancePlan, fetchMaintenanceWorkOrder, updateMaintenanceWorkOrder, completeMaintenanceWorkOrder, createSparePart, updateSpareStock, fetchRework, fetchBarcodeEvents, analyzeCommissioningLog, fetchConnectorSnapshot, analyzeConnector, approveConnector, importConnectorRecords, updateConnectorProfile, discoverCabinetVisionSql, syncCabinetVisionSql, fetchIndustrialSnapshot, updateIndustrialProfile, simulateIndustrialProfile, probeIndustrialProfile, probeIndustrialMqtt, approveIndustrialProfile, pollIndustrialProfile, browseIndustrialOpcua, fetchFactoryReadiness, updateMachinePassport, importFactoryInventory, probeFactoryConnection, downloadFactoryReadinessPack, fetchImprovements, syncImprovements, actOnImprovement, fetchRootCauses, syncRootCauses, decideRootCause, fetchAlerts, syncAlerts, actOnAlert, updateAlertDestination, testAlertDestination, dispatchAlerts, updateAlertSettings, postJson, simulateEvent } from "./api";
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
import { ImprovementPanel } from "./ImprovementPanel";
import { RootCausePanel } from "./RootCausePanel";
import { AlertCenter } from "./AlertCenter";
import { CommissioningPanel } from "./CommissioningPanel";
import { PlanningPanel } from "./PlanningPanel";
import { ConstraintHistoryPanel } from "./ConstraintHistoryPanel";
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

const AccessPanel = lazy(() => import("./AccessPanel").then(module => ({ default: module.AccessPanel })));
const ForecastPanel = lazy(() => import("./ForecastPanel").then(module => ({ default: module.ForecastPanel })));

function factoryOee(oeeList) {
  if (!oeeList?.length) return null;
  const active = oeeList.filter(m => m.run_time_s > 0 || m.idle_time_s > 0);
  if (!active.length) return null;
  const avg = k => active.reduce((s, m) => s + (m[k] ?? 0), 0) / active.length;
  return { availability: avg("availability"), oee: avg("oee"), active: active.length,
           provisional: active.some(machine => machine.provisional) };
}

export default function App({ auth }) {
  const qc = useQueryClient();
  const permissions = new Set(auth.user.permissions || []);
  const can = (...requested) => permissions.has("admin") || requested.some(permission => permissions.has(permission));
  const [selectedKey, setSelectedKey]     = useState(null);
  const [liveLog, setLiveLog]             = useState([]);
  const [machineStates, setMachineStates] = useState({});
  const [demoRunning, setDemoRunning]     = useState(false);
  const [showDiagnostics, setShowDiagnostics] = useState(false);
  const [showOperations, setShowOperations] = useState(false);
  const [showSetup, setShowSetup] = useState(false);
  const [showCommissioning, setShowCommissioning] = useState(false);
  const [showPlanning, setShowPlanning] = useState(false);
  const [showImprovements, setShowImprovements] = useState(false);
  const [showRootCauses, setShowRootCauses] = useState(false);
  const [showAlerts, setShowAlerts] = useState(false);
  const [showAccess, setShowAccess] = useState(false);
  const [showConstraintHistory, setShowConstraintHistory] = useState(false);
  const [constraintSyncing, setConstraintSyncing] = useState(false);
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
  const { data: constraintTimeline = null } = useQuery({
    queryKey: ["constraintTimeline"], queryFn: fetchConstraintTimeline, refetchInterval: 30000,
  });
  const { data: dataQuality = null } = useQuery({
    queryKey: ["dataQuality"], queryFn: fetchDataQuality, refetchInterval: 30000,
  });
  const { data: optimization = null } = useQuery({
    queryKey: ["optimization"], queryFn: fetchOptimization, refetchInterval: 30000,
  });
  const { data: improvements = null } = useQuery({
    queryKey: ["improvements"], queryFn: fetchImprovements, refetchInterval: 30000,
  });
  const { data: rootCauses = null } = useQuery({
    queryKey: ["rootCauses"], queryFn: fetchRootCauses, refetchInterval: 30000,
  });
  const { data: alerts = null } = useQuery({
    queryKey: ["alerts"], queryFn: fetchAlerts, refetchInterval: 15000,
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
  const { data: forecast = null } = useQuery({
    queryKey: ["forecast"], queryFn: fetchForecast, refetchInterval: 60000,
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
  const { data: recovery = null } = useQuery({
    queryKey: ["recovery"], queryFn: fetchRecovery, refetchInterval: 30000,
  });
  const { data: resourceSnapshot = null } = useQuery({
    queryKey: ["resourceSnapshot"], queryFn: fetchResourceSnapshot, refetchInterval: 30000,
  });
  const { data: procurementSnapshot = null } = useQuery({
    queryKey: ["procurementSnapshot"], queryFn: fetchProcurementSnapshot, refetchInterval: 30000,
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
  const { data: resilience = null } = useQuery({
    queryKey: ["resilience"], queryFn: fetchResilience, refetchInterval: 60000,
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
  const { data: connectorSnapshot = null } = useQuery({
    queryKey: ["connectorSnapshot"], queryFn: fetchConnectorSnapshot, refetchInterval: 30000,
  });
  const { data: industrialSnapshot = null } = useQuery({
    queryKey: ["industrialSnapshot"], queryFn: fetchIndustrialSnapshot, refetchInterval: 10000,
  });
  const { data: factoryReadiness = null } = useQuery({
    queryKey: ["factoryReadiness"], queryFn: fetchFactoryReadiness, refetchInterval: 30000,
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
      qc.invalidateQueries({ queryKey: ["forecast"] });
      qc.invalidateQueries({ queryKey: ["productionOrders"] });
      qc.invalidateQueries({ queryKey: ["routeExceptions"] });
      qc.invalidateQueries({ queryKey: ["executionSnapshot"] });
      qc.invalidateQueries({ queryKey: ["maintenance"] });
      qc.invalidateQueries({ queryKey: ["alerts"] });
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

  const runCreateBackup = async () => {
    const result = await createSystemBackup();
    await qc.invalidateQueries({ queryKey: ["resilience"] });
    return result;
  };

  const runVerifyBackup = async filename => {
    const result = await verifySystemBackup(filename);
    await qc.invalidateQueries({ queryKey: ["resilience"] });
    return result;
  };

  const runRemoteSetupAction = async (kind, payload) => {
    if (kind === "plan") return fetchRemoteSetupPlan(payload.machine_key);
    if (kind === "forget") {
      const result = await forgetRemoteHost(payload.machine_key);
      qc.invalidateQueries({ queryKey: ["diagnostics"] });
      return result;
    }
    const paths = {
      identity: "/remote-setup/identity",
      test: "/remote-setup/test-connection",
      scan: "/remote-setup/scan-host-key",
      trust: "/remote-setup/trust-host",
      auth: "/remote-setup/authenticate",
      folders: "/remote-setup/detect-folders",
      install: payload.execute ? "/remote-setup/install-agent/live" : "/remote-setup/install-agent",
      restart: "/remote-setup/restart-agent",
      log: "/remote-setup/fetch-log",
    };
    const result = await postJson(paths[kind], payload);
    if (["identity", "trust", "forget", "install"].includes(kind)) {
      ["diagnostics", "deployment"].forEach(key => qc.invalidateQueries({ queryKey: [key] }));
    }
    return result;
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

  const runConstraintSync = async () => {
    setConstraintSyncing(true);
    try {
      await syncConstraints({ actor: auth.user.username || "operator", window_hours: 8 });
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["bottlenecks"] }),
        qc.invalidateQueries({ queryKey: ["optimization"] }),
        qc.invalidateQueries({ queryKey: ["constraintTimeline"] }),
      ]);
    } finally {
      setConstraintSyncing(false);
    }
  };

  const runConstraintSettings = async payload => {
    const result = await updateConstraintSettings(payload);
    ["constraintTimeline", "bottlenecks", "optimization", "diagnostics"].forEach(key => {
      qc.invalidateQueries({ queryKey: [key] });
    });
    return result;
  };

  const runConnectorAction = async (kind, connectorKey, payload = {}) => {
    let result;
    if (kind === "analyze") result = await analyzeConnector(connectorKey, payload);
    else if (kind === "approve") result = await approveConnector(connectorKey, payload);
    else if (kind === "import") result = await importConnectorRecords(connectorKey, payload);
    else if (kind === "profile") result = await updateConnectorProfile(connectorKey, payload);
    else if (kind === "discover") result = await discoverCabinetVisionSql();
    else if (kind === "sync") result = await syncCabinetVisionSql(payload);
    qc.invalidateQueries({ queryKey: ["connectorSnapshot"] });
    if (["import", "sync"].includes(kind)) refreshOperations();
    return result;
  };

  const runIndustrialAction = async (kind, profileKey, payload = {}) => {
    let result;
    if (kind === "profile") result = await updateIndustrialProfile(profileKey, payload);
    else if (kind === "simulate") result = await simulateIndustrialProfile(profileKey, payload);
    else if (kind === "probe") result = await probeIndustrialProfile(profileKey, payload);
    else if (kind === "mqttProbe") result = await probeIndustrialMqtt(profileKey, payload);
    else if (kind === "approve") result = await approveIndustrialProfile(profileKey, payload);
    else if (kind === "poll") result = await pollIndustrialProfile(profileKey, payload);
    else if (kind === "browse") result = await browseIndustrialOpcua(profileKey);
    qc.invalidateQueries({ queryKey: ["industrialSnapshot"] });
    if (["poll", "approve"].includes(kind)) {
      qc.invalidateQueries({ queryKey: ["machines"] });
      qc.invalidateQueries({ queryKey: ["diagnostics"] });
    }
    return result;
  };

  const runFactoryAction = async (kind, machineKey, payload = {}) => {
    let result;
    if (kind === "passport") result = await updateMachinePassport(machineKey, payload);
    else if (kind === "inventory") result = await importFactoryInventory(payload);
    else if (kind === "probe") result = await probeFactoryConnection(machineKey, payload);
    else if (kind === "pack") return downloadFactoryReadinessPack();
    await qc.invalidateQueries({ queryKey: ["factoryReadiness"] });
    if (["passport", "inventory", "probe"].includes(kind)) {
      qc.invalidateQueries({ queryKey: ["diagnostics"] });
    }
    return result;
  };

  const runImprovementSync = async () => {
    const result = await syncImprovements({ actor: "improvement-console", window_hours: 8 });
    ["improvements", "diagnostics"].forEach(key => qc.invalidateQueries({ queryKey: [key] }));
    return result;
  };

  const runImprovementAction = async (id, payload) => {
    const result = await actOnImprovement(id, payload);
    ["improvements", "diagnostics"].forEach(key => qc.invalidateQueries({ queryKey: [key] }));
    return result;
  };

  const runRootCauseSync = async () => {
    const result = await syncRootCauses({ actor: "diagnostic-console", lookback_days: 30 });
    ["rootCauses", "diagnostics", "optimization"].forEach(key => qc.invalidateQueries({ queryKey: [key] }));
    return result;
  };

  const runRootCauseDecision = async (id, payload) => {
    const result = await decideRootCause(id, payload);
    ["rootCauses", "diagnostics", "optimization", "improvements"].forEach(key => qc.invalidateQueries({ queryKey: [key] }));
    return result;
  };

  const refreshAlerts = () => {
    qc.invalidateQueries({ queryKey: ["alerts"] });
    qc.invalidateQueries({ queryKey: ["diagnostics"] });
  };

  const runAlertOperation = async (kind, key, payload = {}) => {
    let result;
    if (kind === "sync") result = await syncAlerts(payload);
    else if (kind === "action") result = await actOnAlert(key, payload);
    else if (kind === "destination") result = await updateAlertDestination(key, payload);
    else if (kind === "test") result = await testAlertDestination(key, payload);
    else if (kind === "dispatch") result = await dispatchAlerts(payload);
    else if (kind === "settings") result = await updateAlertSettings(payload);
    refreshAlerts();
    return result;
  };

  const refreshPlanning = () => {
    ["productionOrders", "productionReadiness", "planningScenarios", "activeSchedule", "recovery", "resourceSnapshot", "procurementSnapshot", "routeExceptions", "executionSnapshot", "identitySnapshot", "sequence", "twin", "forecast", "jobs", "optimization", "alerts", "diagnostics"].forEach(key => {
      qc.invalidateQueries({ queryKey: [key] });
    });
  };

  const runForecast = async payload => {
    const result = await refreshForecast(payload);
    ["forecast", "optimization", "alerts", "diagnostics"].forEach(key => {
      qc.invalidateQueries({ queryKey: [key] });
    });
    return result;
  };

  const runPlanningAction = async (kind, data) => {
    let result;
    if (kind === "order") result = await updateProductionOrder(data.id, data.payload);
    else if (kind === "routes") return fetchProductionRoutes(data.job_name);
    else if (kind === "saveRoute") result = await replacePartRoute(data.part_id, data.payload);
    else if (kind === "scenario") result = await createPlanningScenario(data);
    else if (kind === "decision") result = await decidePlanningScenario(data.id, data.payload);
    else if (kind === "recoveryAnalyze") result = await analyzeRecovery(data.payload);
    else if (kind === "recoveryDecision") result = await decideRecovery(data.id, data.payload);
    else if (kind === "exception") result = await resolveRouteException(data.id, data.payload);
    else if (kind === "material") result = await updateMaterialStock(data.key, data.payload);
    else if (kind === "inventoryItem") result = await updateInventoryItem(data.key, data.payload);
    else if (kind === "inventoryLot") result = await updateInventoryLot(data.key, data.lotCode, data.payload);
    else if (kind === "inventoryRequirement") result = await updateInventoryRequirement(data.orderId, data.key, data.payload);
    else if (kind === "remnantCreate") result = await createInventoryRemnant(data);
    else if (kind === "remnantUpdate") result = await updateInventoryRemnant(data.key, data.payload);
    else if (kind === "labor") result = await updateLaborRole(data.key, data.payload);
    else if (kind === "tool") result = await updateToolPool(data.key, data.payload);
    else if (kind === "toolAsset") result = await createToolAsset(data);
    else if (kind === "toolAssetUpdate") result = await updateToolAsset(data.key, data.payload);
    else if (kind === "toolUsage") result = await recordToolUsage(data.key, data.payload);
    else if (kind === "toolAction") result = await recordToolAction(data.key, data.payload);
    else if (kind === "toolService") result = await recordToolService(data.key, data.payload);
    else if (kind === "toolMapping") result = await updateToolProgramMapping(data.key, data.payload);
    else if (kind === "toolSync") result = await syncTooling();
    else if (kind === "machineResource") result = await updateMachineResource(data.key, data.payload);
    else if (kind === "calendar") result = await updateFactoryCalendar(data);
    else if (kind === "wip") result = await updateWipBuffer(data.key, data.payload);
    else if (kind === "unavailability") result = await createResourceUnavailability(data);
    else if (kind === "deleteUnavailability") result = await deleteResourceUnavailability(data.id, data.actor);
    else if (kind === "procurementSupplier") result = await updateProcurementSupplier(data.key, data.payload);
    else if (kind === "procurementMapping") result = await updateProcurementMapping(data.supplierKey, data.objectType, data.objectKey, data.payload);
    else if (kind === "procurementCreateOrder") result = await createPurchaseOrder(data);
    else if (kind === "procurementDraft") result = await draftProcurementRecommendations(data);
    else if (kind === "procurementOrderAction") result = await actOnPurchaseOrder(data.id, data.payload);
    else if (kind === "procurementReceipt") result = await createGoodsReceipt(data);
    else if (kind === "procurementImport") result = await importProcurementCsv(data);
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
          <button onClick={() => setShowAccess(true)} title="Open account and access control" style={{
            display: "inline-flex", alignItems: "center", gap: 6, background: "#1f2937",
            border: "1px solid #374151", color: "#f9fafb", padding: "7px 12px", borderRadius: 6,
            fontSize: 12, cursor: "pointer",
          }}><UserRound size={14} /> {auth.user.display_name}</button>
          <button onClick={() => setShowAlerts(true)} title="Open alert center" style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            background: alerts?.summary.critical_unacknowledged ? "#7f1d1d" : alerts?.summary.active ? "#78350f" : "#1f2937",
            border: `1px solid ${alerts?.summary.critical_unacknowledged ? "#ef4444" : alerts?.summary.active ? "#f59e0b" : "#374151"}`,
            color: "#f9fafb", padding: "7px 12px", borderRadius: 6, fontSize: 12, cursor: "pointer",
          }}>
            <Bell size={14} /> Alerts{alerts?.summary.active ? ` (${alerts.summary.active})` : ""}
          </button>
          {can("commission") && <button onClick={() => setShowCommissioning(true)} style={{
            background: "#1d4ed8", border: "1px solid #3b82f6", color: "#f9fafb",
            padding: "7px 14px", borderRadius: 6, fontSize: 12, cursor: "pointer",
          }}>
            Commission
          </button>}
          {can("commission") && <button onClick={() => setShowSetup(true)} style={{
            background: "#1f2937", border: "1px solid #374151", color: "#f9fafb",
            padding: "7px 14px", borderRadius: 6, fontSize: 12, cursor: "pointer",
          }}>
            Setup
          </button>}
          <button onClick={() => setShowOperations(true)} style={{
            background: "#1f2937", border: "1px solid #374151", color: "#f9fafb",
            padding: "7px 14px", borderRadius: 6, fontSize: 12, cursor: "pointer",
          }}>
            Operations
          </button>
          {can("plan") && <button onClick={() => setShowPlanning(true)} style={{
            background: routeExceptions.length ? "#78350f" : "#1f2937",
            border: `1px solid ${routeExceptions.length ? "#f59e0b" : "#374151"}`,
            color: "#f9fafb", padding: "7px 14px", borderRadius: 6, fontSize: 12, cursor: "pointer",
          }}>
            Planning{routeExceptions.length ? ` (${routeExceptions.length})` : ""}
          </button>}
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
          {can("commission") && <button onClick={toggleDemo} style={{
            background: demoRunning ? "#7f1d1d" : "#1f2937",
            border: "1px solid #374151", color: "#f9fafb",
            padding: "7px 14px", borderRadius: 6, fontSize: 12, cursor: "pointer",
          }}>
            {demoRunning ? "■ Stop Demo" : "▶ Demo Mode"}
          </button>}
        </div>
      </div>

      <IntelligencePanel optimization={optimization} quality={dataQuality}
                         learning={learning} routing={routing} twin={twin}
                         onCommission={can("commission") ? () => setShowCommissioning(true) : null}
                         onReviewActions={can("optimize", "supervise") ? () => setShowImprovements(true) : null}
                         onDiagnose={can("optimize", "supervise") ? () => setShowRootCauses(true) : null} />

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
        <BottleneckPanel report={bottlenecks}
          onSync={can("optimize", "supervise") ? runConstraintSync : null}
          syncing={constraintSyncing}
          runtime={constraintTimeline?.runtime}
          onHistory={() => setShowConstraintHistory(true)} />
      </div>

      <Suspense fallback={<section style={{ borderTop: "1px solid #1f2937", borderBottom: "1px solid #1f2937",
        padding: "14px 0", marginBottom: 20, color: "#6b7280", fontSize: 11 }}>Loading production forecast…</section>}>
        <ForecastPanel forecast={forecast} canRefresh={can("optimize", "supervise")}
                       onRefresh={runForecast} />
      </Suspense>

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
        <DiagnosticsPanel data={diagnostics} deployment={deployment} resilience={resilience}
                          canManage={can("admin")} onCreateBackup={runCreateBackup}
                          onVerifyBackup={runVerifyBackup} onClose={() => setShowDiagnostics(false)} />
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
        <CommissioningPanel machines={enriched} connectors={connectorSnapshot}
                            industrial={industrialSnapshot}
                            factoryReadiness={factoryReadiness}
                            onAnalyze={runCommissioningAnalysis}
                            onConnectorAction={runConnectorAction}
                            onIndustrialAction={runIndustrialAction}
                            onFactoryAction={runFactoryAction}
                            onClose={() => setShowCommissioning(false)} />
      )}
      {showPlanning && (
        <PlanningPanel
          data={{ orders: productionOrders, readiness: productionReadiness, scenarios: planningScenarios, activeSchedule, recovery, exceptions: routeExceptions,
            resources: resourceSnapshot ? { ...resourceSnapshot, procurement: procurementSnapshot } : null }}
          machines={enriched}
          onAction={runPlanningAction}
          onClose={() => setShowPlanning(false)}
        />
      )}
      {showImprovements && improvements && (
        <ImprovementPanel data={improvements} onSync={runImprovementSync}
                          onAction={runImprovementAction}
                          onClose={() => setShowImprovements(false)} />
      )}
      {showRootCauses && rootCauses && (
        <RootCausePanel data={rootCauses} onSync={runRootCauseSync}
                        onDecision={runRootCauseDecision}
                        onClose={() => setShowRootCauses(false)} />
      )}
      {showAlerts && alerts && (
        <AlertCenter data={alerts} currentUser={auth.user.display_name}
          canManage={can("alerts")} canCommission={can("commission")}
          onSync={payload => runAlertOperation("sync", null, payload)}
          onAction={(id, payload) => runAlertOperation("action", id, payload)}
          onDestination={(key, payload) => runAlertOperation("destination", key, payload)}
          onTestDestination={(key, payload) => runAlertOperation("test", key, payload)}
          onDispatch={payload => runAlertOperation("dispatch", null, payload)}
          onSettings={payload => runAlertOperation("settings", null, payload)}
          onClose={() => setShowAlerts(false)} />
      )}
      {showConstraintHistory && constraintTimeline && (
        <ConstraintHistoryPanel data={constraintTimeline}
          currentUser={auth.user.display_name}
          canManage={can("optimize", "supervise")}
          onSync={runConstraintSync}
          onSettings={runConstraintSettings}
          onClose={() => setShowConstraintHistory(false)} />
      )}
      {showAccess && (
        <Suspense fallback={<div style={{ position: "fixed", inset: 0, zIndex: 1150, background: "rgba(2,6,23,.88)",
          display: "grid", placeItems: "center", color: "#9ca3af", fontSize: 11 }}>Loading access control…</div>}>
          <AccessPanel auth={auth} onClose={() => setShowAccess(false)} onExpired={auth.expireAuth} />
        </Suspense>
      )}

      <style>{`
        * { box-sizing: border-box; margin: 0; padding: 0; }
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-track { background: #0d1117; }
        ::-webkit-scrollbar-thumb { background: #374151; border-radius: 2px; }
        @keyframes hive-spin { to { transform: rotate(360deg); } }
        .spin { animation: hive-spin .8s linear infinite; }
        @media (max-width: 760px) {
          .bottom-grid { grid-template-columns: 1fr !important; }
          .constraint-grid { grid-template-columns: 1fr 1fr !important; }
          .constraint-recommendation { grid-column: 1 / -1; }
          .intelligence-grid { grid-template-columns: 1fr 1fr !important; }
          .forecast-grid { grid-template-columns: 1fr 1fr !important; }
          .commission-controls { grid-template-columns: 1fr !important; }
          .lab-metrics, .lab-interventions, .lab-columns { grid-template-columns: 1fr !important; }
          .evidence-study-grid, .evidence-gates, .evidence-metrics, .evidence-form-primary, .evidence-form-secondary { grid-template-columns: 1fr !important; }
          .evidence-protocol-row { grid-template-columns: 24px 1fr !important; }
          .evidence-segments { grid-template-columns: repeat(3,1fr) !important; }
          .connector-layout { grid-template-columns: 1fr !important; }
          .connector-layout > div:first-child { border-right: 0 !important; border-bottom: 1px solid #1f2937; padding-right: 0 !important; padding-bottom: 10px; display: grid; grid-template-columns: 1fr 1fr; }
          .sql-config, .mapping-grid { grid-template-columns: 1fr !important; }
          .industrial-layout { grid-template-columns: 1fr !important; }
          .industrial-sidebar { border-right: 0 !important; border-bottom: 1px solid #1f2937; padding-right: 0 !important; padding-bottom: 10px; display: grid; grid-template-columns: 1fr 1fr; }
          .industrial-config, .industrial-signal-row { grid-template-columns: 1fr !important; }
        }
      `}</style>
    </div>
  );
}
