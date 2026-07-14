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
