const BASE = import.meta.env.VITE_API_URL || "/api";

export const fetchMachines = () =>
  fetch(`${BASE}/machines`).then(r => r.json());

export const fetchMachine = (key) =>
  fetch(`${BASE}/machines/${key}`).then(r => r.json());

export const fetchOee = (windowHours = 8) =>
  fetch(`${BASE}/oee?window_hours=${windowHours}`).then(r => r.json());

export const fetchJobs = () =>
  fetch(`${BASE}/jobs?limit=20`).then(r => r.json());

export const fetchJobParts = (jobName) =>
  fetch(`${BASE}/jobs/${encodeURIComponent(jobName)}/parts`).then(r => r.json());

export const fetchActiveJobs = () =>
  fetch(`${BASE}/jobs/active`).then(r => r.json());

export const fetchSequence = () =>
  fetch(`${BASE}/sequence`).then(r => r.json());

export const fetchDailyScore = () =>
  fetch(`${BASE}/score/daily`).then(r => r.json());

export const simulateEvent = (machineKey, eventType, extras = {}) => {
  const params = new URLSearchParams({ machine_key: machineKey, event_type: eventType, ...extras });
  return fetch(`${BASE}/events/simulate?${params}`, { method: "POST" }).then(r => r.json());
};

export const SSE_URL = `${BASE}/events/stream`;
