const BASE = import.meta.env.VITE_API_URL || "/api";

async function request(path, options) {
  const response = await fetch(`${BASE}${path}`, options);
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const detail = typeof data === "object" ? data?.detail : data;
    throw new Error(detail || `Request failed: ${response.status}`);
  }
  return data;
}

export const fetchMachines = () => request("/machines");

export const fetchMachine = (key) =>
  request(`/machines/${encodeURIComponent(key)}`);

export const fetchOee = (windowHours = 8) => {
  const hours = typeof windowHours === "number" ? windowHours : 8;
  return request(`/oee?window_hours=${hours}`);
};

export const fetchJobs = () => request("/jobs?limit=20");

export const fetchJobParts = (jobName) =>
  request(`/jobs/${encodeURIComponent(jobName)}/parts`);

export const fetchActiveJobs = () => request("/jobs/active");
export const fetchSequence = () => request("/sequence");
export const fetchBottlenecks = () => request("/bottlenecks");
export const fetchDataQuality = () => request("/data-quality");
export const fetchOptimization = () => request("/optimization");
export const fetchLearningStatus = () => request("/learning/status");
export const fetchRoutingGraph = () => request("/routing/graph");
export const fetchTwinReadiness = () => request("/digital-twin/readiness");
export const compareTwinSchedules = (payload = {}) => postJson("/digital-twin/compare", payload);
export const fetchProductionOrders = () => request("/production/orders");
export const fetchProductionReadiness = () => request("/production/readiness");
export const updateProductionOrder = (id, payload) => request(`/production/orders/${id}`, {
  method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
export const fetchProductionRoutes = (jobName) => request(`/production/routes/${encodeURIComponent(jobName)}`);
export const replacePartRoute = (partId, payload) => request(`/production/routes/parts/${partId}`, {
  method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
export const fetchRouteExceptions = () => request("/production/route-exceptions?status=open");
export const resolveRouteException = (id, payload) => postJson(`/production/route-exceptions/${id}/resolve`, payload);
export const fetchPlanningScenarios = () => request("/planning/scenarios");
export const fetchPlanningScenario = (id) => request(`/planning/scenarios/${id}`);
export const createPlanningScenario = (payload) => postJson("/planning/scenarios", payload);
export const decidePlanningScenario = (id, payload) => postJson(`/planning/scenarios/${id}/decision`, payload);
export const fetchActiveSchedule = () => request("/planning/active-schedule");
export const fetchExecutionSnapshot = () => request("/execution/snapshot");
export const syncExecution = () => postJson("/execution/sync", {});
export const updateExecutionJob = (id, payload) => postJson(`/execution/jobs/${id}/action`, payload);
export const resolveExecutionException = (id, payload) => postJson(`/execution/exceptions/${id}/resolve`, payload);
export const fetchResourceSnapshot = () => request("/resources/snapshot");
export const updateMaterialStock = (materialKey, payload) => request(`/resources/materials/${encodeURIComponent(materialKey)}`, {
  method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
export const updateLaborRole = (roleKey, payload) => request(`/resources/labor/${encodeURIComponent(roleKey)}`, {
  method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
export const updateToolPool = (poolKey, payload) => request(`/resources/tooling/${encodeURIComponent(poolKey)}`, {
  method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
export const updateMachineResource = (machineKey, payload) => request(`/resources/machines/${encodeURIComponent(machineKey)}`, {
  method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
export const updateFactoryCalendar = payload => request("/resources/calendar/factory", {
  method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
export const updateWipBuffer = (machineKey, payload) => request(`/resources/wip/${encodeURIComponent(machineKey)}`, {
  method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
export const createResourceUnavailability = payload => postJson("/resources/unavailability", payload);
export const deleteResourceUnavailability = (id, actor) => request(`/resources/unavailability/${id}?actor=${encodeURIComponent(actor)}`, { method: "DELETE" });
export const fetchDiagnostics = () => request("/diagnostics");
export const fetchDeployment = () => request("/deployment");
export const fetchConfig = () => request("/config");

export const saveConfig = (payload) =>
  request("/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

export const fetchRemoteSetupPlan = (machineKey) =>
  request(`/remote-setup/plan/${encodeURIComponent(machineKey)}`);

export const fetchOperationsSummary = () => request("/operations/summary");
export const fetchDowntime = () => request("/downtime?status=open");
export const fetchWorkOrders = () => request("/maintenance/work-orders");
export const fetchRework = () => request("/rework?status=open");
export const fetchBarcodeEvents = () => request("/barcode/events?limit=8");

export const postJson = (path, payload) =>
  request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

export const analyzeCommissioningLog = (payload) =>
  postJson("/commissioning/log/analyze", payload);

export const fetchDailyScore = () => request("/score/daily");

export const simulateEvent = (machineKey, eventType, extras = {}) => {
  const params = new URLSearchParams({ machine_key: machineKey, event_type: eventType, ...extras });
  return request(`/events/simulate?${params}`, { method: "POST" });
};

export const SSE_URL = `${BASE}/events/stream`;
