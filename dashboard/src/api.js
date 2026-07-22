const BASE = import.meta.env.VITE_API_URL || "/api";
let csrfToken = null;

export const setAuthCsrf = value => { csrfToken = value || null; };

async function request(path, options) {
  const method = (options?.method || "GET").toUpperCase();
  const headers = new Headers(options?.headers || {});
  if (csrfToken && ["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const response = await fetch(`${BASE}${path}`, {
    ...options, method, headers, credentials: "same-origin",
  });
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    if (response.status === 401 && !["/auth/login", "/auth/me"].includes(path)) {
      window.dispatchEvent(new CustomEvent("hive-auth-expired"));
    }
    const detail = typeof data === "object" ? data?.detail : data;
    throw new Error(detail || `Request failed: ${response.status}`);
  }
  return data;
}

export const fetchAuthStatus = () => request("/auth/status");
export const fetchCurrentUser = async () => {
  const result = await request("/auth/me");
  setAuthCsrf(result.csrf_token);
  return result;
};
export const bootstrapAuth = async payload => {
  const result = await request("/auth/bootstrap", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
  setAuthCsrf(result.csrf_token);
  return result;
};
export const loginAuth = async payload => {
  const result = await request("/auth/login", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
  setAuthCsrf(result.csrf_token);
  return result;
};
export const logoutAuth = async () => {
  const result = await postJson("/auth/logout", {});
  setAuthCsrf(null);
  return result;
};
export const changeAuthPassword = payload => postJson("/auth/password", payload);
export const fetchAuthUsers = () => request("/auth/users");
export const createAuthUser = payload => postJson("/auth/users", payload);
export const updateAuthUser = (id, payload) => request(`/auth/users/${id}`, {
  method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
export const resetAuthPassword = (id, payload) => postJson(`/auth/users/${id}/reset-password`, payload);
export const fetchAuthApiKeys = () => request("/auth/api-keys");
export const createAuthApiKey = payload => postJson("/auth/api-keys", payload);
export const revokeAuthApiKey = id => request(`/auth/api-keys/${id}`, { method: "DELETE" });
export const fetchAuthEvents = () => request("/auth/events?limit=150");
export const fetchMqttSecurity = () => request("/mqtt-security");
export const revokeMqttEnrollment = (id, payload = {}) =>
  postJson(`/mqtt-security/enrollments/${id}/revoke`, payload);
export const downloadMqttEnrollment = async payload => {
  const headers = new Headers({ "Content-Type": "application/json" });
  if (csrfToken) headers.set("X-CSRF-Token", csrfToken);
  const response = await fetch(`${BASE}/mqtt-security/enrollments`, {
    method: "POST", headers, credentials: "same-origin", body: JSON.stringify(payload),
  });
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new CustomEvent("hive-auth-expired"));
    let detail = `Request failed: ${response.status}`;
    try { detail = (await response.json())?.detail || detail; } catch { /* response was not JSON */ }
    throw new Error(detail);
  }
  const disposition = response.headers.get("content-disposition") || "";
  const filename = disposition.match(/filename="?([^";]+)"?/)?.[1] || `hive-machine-enrollment.zip`;
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
};

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
export const syncConstraints = (payload = {}) => postJson("/constraints/sync", payload);
export const fetchConstraintTimeline = () => request("/constraints/timeline?days=30&limit=100");
export const updateConstraintSettings = (payload) => request("/constraints/settings", {
  method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
export const fetchDataQuality = () => request("/data-quality");
export const fetchOptimization = () => request("/optimization");
export const fetchProductionLosses = () => request("/production-losses");
export const fetchFlowIntelligence = () => request("/flow-intelligence?days=90");
export const syncFlowIntelligence = (payload = {}) => postJson("/flow-intelligence/sync", payload);
export const fetchReleaseControl = () => request("/release-control");
export const syncReleaseControl = (payload = {}) => postJson("/release-control/sync", payload);
export const updateReleaseControlSettings = payload => request("/release-control/settings", {
  method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
export const updateReleaseControlNorm = (machineKey, payload) => request(
  `/release-control/norms/${encodeURIComponent(machineKey)}`,
  { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) },
);
export const actOnReleaseRecommendation = (id, payload) =>
  postJson(`/release-control/recommendations/${id}/action`, payload);
export const fetchEconomics = () => request("/economics");
export const syncEconomics = (payload = {}) => postJson("/economics/sync", payload);
export const updateEconomicsSettings = payload => request("/economics/settings", {
  method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
export const updateEconomicsRate = (rateKey, payload) => request(
  `/economics/rates/${encodeURIComponent(rateKey)}`,
  { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) },
);
export const updateEconomicsAdjustment = (experimentId, payload) => request(
  `/economics/experiments/${experimentId}/adjustments`,
  { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) },
);
export const fetchImprovements = () => request("/improvements");
export const syncImprovements = (payload = {}) => postJson("/improvements/sync", payload);
export const actOnImprovement = (id, payload) =>
  postJson(`/improvements/recommendations/${id}/action`, payload);
export const fetchRootCauses = () => request("/root-causes");
export const syncRootCauses = (payload = {}) => postJson("/root-causes/sync", payload);
export const decideRootCause = (id, payload) =>
  postJson(`/root-causes/${id}/decision`, payload);
export const fetchAlerts = () => request("/alerts");
export const syncAlerts = (payload) => postJson("/alerts/sync", payload);
export const actOnAlert = (id, payload) => postJson(`/alerts/${id}/action`, payload);
export const updateAlertDestination = (key, payload) => request(
  `/alerts/destinations/${encodeURIComponent(key)}`,
  { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) },
);
export const testAlertDestination = (key, payload) =>
  postJson(`/alerts/destinations/${encodeURIComponent(key)}/test`, payload);
export const dispatchAlerts = (payload) => postJson("/alerts/deliveries/dispatch", payload);
export const updateAlertSettings = (payload) => request("/alerts/settings", {
  method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
export const fetchLearningStatus = () => request("/learning/status");
export const fetchRoutingGraph = () => request("/routing/graph");
export const fetchTwinReadiness = () => request("/digital-twin/readiness");
export const compareTwinSchedules = (payload = {}) => postJson("/digital-twin/compare", payload);
export const fetchForecast = () => request("/forecast");
export const refreshForecast = (payload = {}) => postJson("/forecast/refresh", payload);
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
export const fetchRecovery = () => request("/recovery");
export const analyzeRecovery = (payload) => postJson("/recovery/analyze", payload);
export const decideRecovery = (id, payload) => postJson(`/recovery/${id}/decision`, payload);
export const fetchExecutionSnapshot = () => request("/execution/snapshot");
export const syncExecution = () => postJson("/execution/sync", {});
export const updateExecutionJob = (id, payload) => postJson(`/execution/jobs/${id}/action`, payload);
export const resolveExecutionException = (id, payload) => postJson(`/execution/exceptions/${id}/resolve`, payload);
export const fetchIdentitySnapshot = () => request("/identity/snapshot");
export const createLabelJob = payload => postJson("/labels/jobs", payload);
export const markLabelJobPrinted = (id, payload) => postJson(`/labels/jobs/${id}/printed`, payload);
export const labelPrintUrl = id => `${BASE}/labels/jobs/${id}/print`;
export const labelZplUrl = id => `${BASE}/labels/jobs/${id}/zpl`;
export const fetchResourceSnapshot = () => request("/resources/snapshot");
export const updateChangeoverStandard = (machineKey, payload) => request(
  `/changeovers/machines/${encodeURIComponent(machineKey)}/standard`,
  { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) },
);
export const recordChangeoverObservation = payload => postJson("/changeovers/observations", payload);
export const excludeChangeoverObservation = (id, payload) =>
  postJson(`/changeovers/observations/${id}/exclude`, payload);
export const syncChangeovers = payload => postJson("/changeovers/sync", payload);
export const fetchProcurementSnapshot = () => request("/procurement/snapshot");
export const updateProcurementSupplier = (supplierKey, payload) => request(
  `/procurement/suppliers/${encodeURIComponent(supplierKey)}`,
  { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) },
);
export const updateProcurementMapping = (supplierKey, objectType, objectKey, payload) => request(
  `/procurement/suppliers/${encodeURIComponent(supplierKey)}/mappings/${encodeURIComponent(objectType)}/${encodeURIComponent(objectKey)}`,
  { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) },
);
export const createPurchaseOrder = payload => postJson("/procurement/orders", payload);
export const draftProcurementRecommendations = payload => postJson("/procurement/orders/draft-recommendations", payload);
export const actOnPurchaseOrder = (id, payload) => postJson(`/procurement/orders/${id}/action`, payload);
export const createGoodsReceipt = payload => postJson("/procurement/receipts", payload);
export const importProcurementCsv = payload => postJson("/procurement/imports/csv", payload);
export const purchaseOrderExportUrl = id => `${BASE}/procurement/orders/${id}/export.csv`;
export const updateMaterialStock = (materialKey, payload) => request(`/resources/materials/${encodeURIComponent(materialKey)}`, {
  method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
export const updateInventoryItem = (itemKey, payload) => request(`/inventory/items/${encodeURIComponent(itemKey)}`, {
  method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
export const updateInventoryLot = (itemKey, lotCode, payload) => request(
  `/inventory/items/${encodeURIComponent(itemKey)}/lots/${encodeURIComponent(lotCode)}`,
  { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) },
);
export const updateInventoryRequirement = (orderId, itemKey, payload) => request(
  `/inventory/orders/${orderId}/requirements/${encodeURIComponent(itemKey)}`,
  { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) },
);
export const createInventoryRemnant = payload => postJson("/inventory/remnants", payload);
export const updateInventoryRemnant = (key, payload) => request(`/inventory/remnants/${encodeURIComponent(key)}`, {
  method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
export const updateLaborRole = (roleKey, payload) => request(`/resources/labor/${encodeURIComponent(roleKey)}`, {
  method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
export const updateToolPool = (poolKey, payload) => request(`/resources/tooling/${encodeURIComponent(poolKey)}`, {
  method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
export const createToolAsset = payload => postJson("/tooling/tools", payload);
export const updateToolAsset = (toolKey, payload) => request(`/tooling/tools/${encodeURIComponent(toolKey)}`, {
  method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
export const recordToolUsage = (toolKey, payload) => postJson(`/tooling/tools/${encodeURIComponent(toolKey)}/usage`, payload);
export const recordToolAction = (toolKey, payload) => postJson(`/tooling/tools/${encodeURIComponent(toolKey)}/actions`, payload);
export const recordToolService = (toolKey, payload) => postJson(`/tooling/tools/${encodeURIComponent(toolKey)}/service`, payload);
export const updateToolProgramMapping = (toolKey, payload) => request(`/tooling/tools/${encodeURIComponent(toolKey)}/program-mappings`, {
  method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
export const syncTooling = () => postJson("/tooling/sync", {});
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
export const fetchResilience = () => request("/resilience");
export const createSystemBackup = () => postJson("/resilience/backups", {});
export const verifySystemBackup = filename =>
  postJson(`/resilience/backups/${encodeURIComponent(filename)}/verify`, {});
export const fetchConfig = () => request("/config");

export const saveConfig = (payload) =>
  request("/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

export const fetchRemoteSetupPlan = (machineKey) =>
  request(`/remote-setup/plan/${encodeURIComponent(machineKey)}`);
export const forgetRemoteHost = machineKey => request(`/remote-setup/trust-host/${encodeURIComponent(machineKey)}`, {
  method: "DELETE",
});

export const fetchOperationsSummary = () => request("/operations/summary");
export const fetchDowntime = () => request("/downtime?status=open");
export const fetchWorkOrders = () => request("/maintenance/work-orders");
export const fetchMaintenanceSnapshot = () => request("/maintenance/snapshot");
export const syncMaintenance = () => postJson("/maintenance/sync", {});
export const updateMaintenancePlan = (id, payload) => request(`/maintenance/plans/${id}`, {
  method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
export const fetchMaintenanceWorkOrder = id => request(`/maintenance/work-orders/${id}`);
export const updateMaintenanceWorkOrder = (id, payload) => request(`/maintenance/work-orders/${id}`, {
  method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
});
export const completeMaintenanceWorkOrder = (id, payload) =>
  postJson(`/maintenance/work-orders/${id}/complete`, payload);
export const createSparePart = payload => postJson("/maintenance/spares", payload);
export const updateSpareStock = (partKey, payload) =>
  request(`/maintenance/spares/${encodeURIComponent(partKey)}/stock`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
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

export const fetchCommissioningLab = () => request("/commissioning-lab");
export const runCommissioningLab = payload => postJson("/commissioning-lab/run", payload);
export const fetchCommissioningEvidence = () => request("/commissioning-evidence");
export const fetchCommissioningStudy = id => request(`/commissioning-evidence/studies/${id}`);
export const createCommissioningStudy = payload => postJson("/commissioning-evidence/studies", payload);
export const addCommissioningObservation = (id, payload) =>
  postJson(`/commissioning-evidence/studies/${id}/observations`, payload);
export const importCommissioningEvidence = (id, payload) =>
  postJson(`/commissioning-evidence/studies/${id}/import`, payload);
export const analyzeCommissioningStudy = id =>
  postJson(`/commissioning-evidence/studies/${id}/analyze`, {});
export const actOnCommissioningStudy = (id, payload) =>
  postJson(`/commissioning-evidence/studies/${id}/action`, payload);
export const excludeCommissioningObservation = (studyId, observationId, payload) =>
  postJson(`/commissioning-evidence/studies/${studyId}/observations/${observationId}/exclude`, payload);
export const downloadCommissioningEvidencePack = async () => {
  const response = await fetch(`${BASE}/commissioning-evidence/pack`, {
    credentials: "same-origin",
  });
  if (!response.ok) throw new Error(`Evidence pack download failed: ${response.status}`);
  const disposition = response.headers.get("content-disposition") || "";
  const filename = disposition.match(/filename="?([^";]+)"?/)?.[1] || "hive-commissioning-evidence.zip";
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url; anchor.download = filename;
  document.body.appendChild(anchor); anchor.click(); anchor.remove();
  URL.revokeObjectURL(url);
  return { filename, sha256: response.headers.get("x-hive-pack-sha256") };
};

export const fetchFactoryReadiness = () => request("/factory-readiness");
export const updateMachinePassport = (machineKey, payload) => request(
  `/factory-readiness/machines/${encodeURIComponent(machineKey)}`,
  { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) },
);
export const importFactoryInventory = payload => postJson("/factory-readiness/import", payload);
export const probeFactoryConnection = (machineKey, payload) =>
  postJson(`/factory-readiness/machines/${encodeURIComponent(machineKey)}/probe`, payload);
export const startFactoryMission = (machineKey, payload) =>
  postJson(`/factory-readiness/machines/${encodeURIComponent(machineKey)}/mission`, payload);
export const actOnFactoryMission = (machineKey, payload) =>
  postJson(`/factory-readiness/machines/${encodeURIComponent(machineKey)}/mission/action`, payload);
export const downloadFactoryReadinessPack = async () => {
  const response = await fetch(`${BASE}/factory-readiness/pack`, { credentials: "same-origin" });
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new CustomEvent("hive-auth-expired"));
    throw new Error(`Factory readiness pack download failed: ${response.status}`);
  }
  const disposition = response.headers.get("content-disposition") || "";
  const filename = disposition.match(/filename="?([^";]+)"?/)?.[1] || "hive-factory-readiness.zip";
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url; anchor.download = filename;
  document.body.appendChild(anchor); anchor.click(); anchor.remove();
  URL.revokeObjectURL(url);
  return { filename, sha256: response.headers.get("x-hive-pack-sha256") };
};

export const fetchConnectorSnapshot = () => request("/connectors/snapshot");
export const analyzeConnector = (key, payload) =>
  postJson(`/connectors/${encodeURIComponent(key)}/analyze`, payload);
export const approveConnector = (key, payload) =>
  postJson(`/connectors/${encodeURIComponent(key)}/approve`, payload);
export const importConnectorRecords = (key, payload) =>
  postJson(`/connectors/${encodeURIComponent(key)}/import`, payload);
export const updateConnectorProfile = (key, payload) => request(
  `/connectors/${encodeURIComponent(key)}`,
  { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) },
);
export const discoverCabinetVisionSql = () =>
  postJson("/connectors/cabinet_vision_sql/discover", {});
export const syncCabinetVisionSql = (payload = {}) =>
  postJson("/connectors/cabinet_vision_sql/sync", payload);

export const fetchIndustrialSnapshot = () => request("/industrial/snapshot");
export const updateIndustrialProfile = (key, payload) => request(
  `/industrial/profiles/${encodeURIComponent(key)}`,
  { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) },
);
export const simulateIndustrialProfile = (key, payload = {}) =>
  postJson(`/industrial/profiles/${encodeURIComponent(key)}/simulate`, payload);
export const probeIndustrialProfile = (key, payload = {}) =>
  postJson(`/industrial/profiles/${encodeURIComponent(key)}/probe`, payload);
export const probeIndustrialMqtt = (key, payload) =>
  postJson(`/industrial/profiles/${encodeURIComponent(key)}/mqtt-probe`, payload);
export const approveIndustrialProfile = (key, payload) =>
  postJson(`/industrial/profiles/${encodeURIComponent(key)}/approve`, payload);
export const pollIndustrialProfile = (key, payload = {}) =>
  postJson(`/industrial/profiles/${encodeURIComponent(key)}/poll`, payload);
export const browseIndustrialOpcua = (key) =>
  postJson(`/industrial/profiles/${encodeURIComponent(key)}/browse`, {});

export const fetchDailyScore = () => request("/score/daily");

export const simulateEvent = (machineKey, eventType, extras = {}) => {
  const params = new URLSearchParams({ machine_key: machineKey, event_type: eventType, ...extras });
  return request(`/events/simulate?${params}`, { method: "POST" });
};

export const SSE_URL = `${BASE}/events/stream`;
