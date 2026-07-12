import { useMemo, useState } from "react";

const DOWNTIME_REASONS = [
  ["setup", "Setup / changeover"],
  ["breakdown", "Machine breakdown"],
  ["waiting_material", "Waiting material"],
  ["tool_change", "Tool change"],
  ["no_operator", "No operator"],
  ["quality_issue", "Quality issue"],
  ["no_job", "No job"],
  ["unknown", "Unknown"],
];

const DEFECT_TYPES = [
  ["edge_band", "Edge band"],
  ["drilling", "Drilling"],
  ["cut_size", "Cut size"],
  ["sanding", "Sanding"],
  ["paint", "Paint"],
  ["material_damage", "Material damage"],
  ["missing_part", "Missing part"],
  ["other", "Other"],
];

function Stat({ label, value, color = "#f9fafb" }) {
  return (
    <div style={{ border: "1px solid #1f2937", borderRadius: 6, padding: 12 }}>
      <div style={eyebrowStyle}>{label}</div>
      <div style={{ color, fontSize: 22, fontWeight: 800, marginTop: 4 }}>{value}</div>
    </div>
  );
}

function Empty({ children }) {
  return <div style={{ color: "#4b5563", fontSize: 11 }}>{children}</div>;
}

function Field({ label, children }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 5, minWidth: 0 }}>
      <span style={labelStyle}>{label}</span>
      {children}
    </label>
  );
}

function MiniForm({ title, children, onSubmit, disabled }) {
  return (
    <form onSubmit={onSubmit} style={formStyle}>
      <div style={{ ...eyebrowStyle, marginBottom: 8 }}>{title}</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 8 }}>
        {children}
      </div>
      <button disabled={disabled} style={{ ...buttonStyle, marginTop: 10, opacity: disabled ? 0.55 : 1 }}>
        Save
      </button>
    </form>
  );
}

function List({ title, rows, render }) {
  return (
    <div style={{ border: "1px solid #1f2937", borderRadius: 6, padding: 12, minHeight: 150 }}>
      <div style={{ ...eyebrowStyle, marginBottom: 10 }}>{title}</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
        {rows?.length ? rows.slice(0, 6).map(render) : <Empty>No records yet</Empty>}
      </div>
    </div>
  );
}

function machineOptions(machines) {
  return machines.map(machine => (
    <option key={machine.machine_key} value={machine.machine_key}>
      {machine.name}
    </option>
  ));
}

function jobOptions(jobs) {
  return jobs.map(job => (
    <option key={job.job_name} value={job.job_name}>
      {job.job_name}
    </option>
  ));
}

export function OperationsPanel({ data, machines = [], jobs = [], onClose, onAction, onDemo }) {
  const { summary, downtime, workOrders, rework, barcodeEvents } = data;
  const defaultMachine = machines[0]?.machine_key ?? "";
  const defaultJob = jobs[0]?.job_name ?? "";
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [showDev, setShowDev] = useState(false);
  const [downtimeForm, setDowntimeForm] = useState({
    machine_key: defaultMachine,
    reason_code: "setup",
    notes: "",
  });
  const [qualityForm, setQualityForm] = useState({
    result: "fail",
    job_name: defaultJob,
    part_name: "",
    defect_code: "edge_band",
    assigned_area: "edge_banding",
    inspector: "",
    notes: "",
  });
  const [workOrderForm, setWorkOrderForm] = useState({
    machine_key: defaultMachine,
    title: "",
    priority: "medium",
    description: "",
  });
  const [closeDowntimeId, setCloseDowntimeId] = useState("");
  const [closeReworkId, setCloseReworkId] = useState("");
  const [closeNotes, setCloseNotes] = useState("");

  const openDowntime = useMemo(() => downtime ?? [], [downtime]);
  const openRework = useMemo(() => rework ?? [], [rework]);
  const activeDowntimeId = openDowntime.some(row => String(row.id) === String(closeDowntimeId))
    ? closeDowntimeId
    : openDowntime[0]?.id ?? "";
  const activeReworkId = openRework.some(row => String(row.id) === String(closeReworkId))
    ? closeReworkId
    : openRework[0]?.id ?? "";

  const run = async (kind, payload, success) => {
    setBusy(true);
    setMessage("");
    try {
      await onAction(kind, payload);
      setMessage(success);
    } catch (error) {
      setMessage(error.message || "Action failed");
    } finally {
      setBusy(false);
    }
  };

  const update = setter => event => {
    const { name, value } = event.target;
    setter(prev => ({ ...prev, [name]: value }));
  };

  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 125, background: "rgba(0,0,0,.78)",
                  display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}
         onClick={onClose}>
      <div style={{ width: "min(1180px, 100%)", maxHeight: "90vh", overflowY: "auto",
                    background: "#0d1117", border: "1px solid #374151", borderRadius: 8,
                    padding: 20 }} onClick={event => event.stopPropagation()}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 16,
                      alignItems: "center", marginBottom: 18 }}>
          <div>
            <div style={{ fontSize: 18, fontWeight: 800 }}>Phase 1 Operations</div>
            <div style={{ color: "#6b7280", fontSize: 11, marginTop: 3 }}>
              Floor workflows backed by replaceable placeholder integrations.
            </div>
          </div>
          <button onClick={onClose} style={{ background: "none", border: 0, color: "#9ca3af",
                                            fontSize: 20, cursor: "pointer" }}>x</button>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
                      gap: 8, marginBottom: 14 }}>
          <Stat label="Open downtime" value={summary?.open_downtime ?? 0} color="#f59e0b" />
          <Stat label="Work orders" value={summary?.open_work_orders ?? 0} color="#60a5fa" />
          <Stat label="Open rework" value={summary?.open_rework ?? 0} color="#ef4444" />
          <Stat label="Defects today" value={summary?.defects_today ?? 0} color="#ef4444" />
          <Stat label="Scans today" value={summary?.scans_today ?? 0} color="#22c55e" />
        </div>

        {message && (
          <div style={{ color: message.includes("failed") ? "#ef4444" : "#22c55e",
                        fontSize: 11, marginBottom: 12 }}>
            {message}
          </div>
        )}

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
                      gap: 12, marginBottom: 16 }}>
          <MiniForm title="START DOWNTIME" disabled={busy} onSubmit={event => {
            event.preventDefault();
            run("downtime", downtimeForm, "Downtime opened");
          }}>
            <Field label="Machine">
              <select name="machine_key" value={downtimeForm.machine_key} onChange={update(setDowntimeForm)}
                      style={inputStyle}>
                {machineOptions(machines)}
              </select>
            </Field>
            <Field label="Reason">
              <select name="reason_code" value={downtimeForm.reason_code} onChange={update(setDowntimeForm)}
                      style={inputStyle}>
                {DOWNTIME_REASONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </Field>
            <Field label="Notes">
              <input name="notes" value={downtimeForm.notes} onChange={update(setDowntimeForm)}
                     placeholder="brief note" style={inputStyle} />
            </Field>
          </MiniForm>

          <MiniForm title="LOG QUALITY" disabled={busy} onSubmit={event => {
            event.preventDefault();
            run("quality", qualityForm, "Quality check saved");
          }}>
            <Field label="Result">
              <select name="result" value={qualityForm.result} onChange={update(setQualityForm)}
                      style={inputStyle}>
                <option value="pass">Pass</option>
                <option value="fail">Fail</option>
                <option value="rework">Rework</option>
              </select>
            </Field>
            <Field label="Job">
              <select name="job_name" value={qualityForm.job_name} onChange={update(setQualityForm)}
                      style={inputStyle}>
                <option value="">Unlinked</option>
                {jobOptions(jobs)}
              </select>
            </Field>
            <Field label="Part">
              <input name="part_name" value={qualityForm.part_name} onChange={update(setQualityForm)}
                     placeholder="part name / barcode text" style={inputStyle} />
            </Field>
            <Field label="Defect">
              <select name="defect_code" value={qualityForm.defect_code} onChange={update(setQualityForm)}
                      style={inputStyle}>
                {DEFECT_TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </Field>
            <Field label="Inspector">
              <input name="inspector" value={qualityForm.inspector} onChange={update(setQualityForm)}
                     placeholder="name" style={inputStyle} />
            </Field>
            <Field label="Notes">
              <input name="notes" value={qualityForm.notes} onChange={update(setQualityForm)}
                     placeholder="what happened" style={inputStyle} />
            </Field>
          </MiniForm>

          <MiniForm title="CREATE WORK ORDER" disabled={busy} onSubmit={event => {
            event.preventDefault();
            if (!workOrderForm.title.trim()) return setMessage("Work order title is required");
            run("workOrder", workOrderForm, "Work order created");
          }}>
            <Field label="Machine">
              <select name="machine_key" value={workOrderForm.machine_key} onChange={update(setWorkOrderForm)}
                      style={inputStyle}>
                {machineOptions(machines)}
              </select>
            </Field>
            <Field label="Priority">
              <select name="priority" value={workOrderForm.priority} onChange={update(setWorkOrderForm)}
                      style={inputStyle}>
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
                <option value="urgent">Urgent</option>
              </select>
            </Field>
            <Field label="Title">
              <input name="title" value={workOrderForm.title} onChange={update(setWorkOrderForm)}
                     placeholder="what needs fixing" style={inputStyle} />
            </Field>
            <Field label="Description">
              <input name="description" value={workOrderForm.description} onChange={update(setWorkOrderForm)}
                     placeholder="extra detail" style={inputStyle} />
            </Field>
          </MiniForm>

          <div style={formStyle}>
            <div style={{ ...eyebrowStyle, marginBottom: 8 }}>CLOSE OPEN ITEMS</div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 8 }}>
              <Field label="Close downtime">
                <select value={activeDowntimeId} disabled={!openDowntime.length} style={inputStyle}
                        onChange={event => setCloseDowntimeId(event.target.value)}>
                  {openDowntime.length
                    ? openDowntime.map(row => <option key={row.id} value={row.id}>{row.machine_name}</option>)
                    : <option>No open downtime</option>}
                </select>
              </Field>
              <Field label="Close rework">
                <select value={activeReworkId} disabled={!openRework.length} style={inputStyle}
                        onChange={event => setCloseReworkId(event.target.value)}>
                  {openRework.length
                    ? openRework.map(row => <option key={row.id} value={row.id}>{row.part_name ?? row.job_name ?? "Unlinked"}</option>)
                    : <option>No open rework</option>}
                </select>
              </Field>
              <Field label="Close notes">
                <input value={closeNotes} onChange={event => setCloseNotes(event.target.value)}
                       placeholder="optional note" style={inputStyle} />
              </Field>
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
              <button disabled={busy || !activeDowntimeId} style={buttonStyle}
                      onClick={() => run("closeDowntime", { id: activeDowntimeId, notes: closeNotes }, "Downtime closed")}>
                Close downtime
              </button>
              <button disabled={busy || !activeReworkId} style={buttonStyle}
                      onClick={() => run("closeRework", { id: activeReworkId, notes: closeNotes }, "Rework closed")}>
                Close rework
              </button>
            </div>
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
                      gap: 12 }}>
          <List title="OPEN DOWNTIME" rows={downtime} render={row => (
            <div key={row.id} style={rowStyle}>
              <b>{row.machine_name ?? "Machine"}</b>
              <span>{row.reason_label ?? "Unassigned"} · {row.notes ?? "no notes"}</span>
            </div>
          )} />
          <List title="OPEN REWORK" rows={rework} render={row => (
            <div key={row.id} style={rowStyle}>
              <b>{row.job_name ?? "Unlinked job"}</b>
              <span>{row.part_name ?? "Unlinked part"} · {row.assigned_area ?? "unassigned"}</span>
            </div>
          )} />
          <List title="WORK ORDERS" rows={workOrders} render={row => (
            <div key={row.id} style={rowStyle}>
              <b>{row.title}</b>
              <span>{row.priority} · {row.status}</span>
            </div>
          )} />
          <List title="RECENT BARCODE EVENTS" rows={barcodeEvents} render={row => (
            <div key={row.id} style={rowStyle}>
              <b>{row.event_type}</b>
              <span>{row.barcode} · {row.station ?? "unknown station"}</span>
            </div>
          )} />
        </div>

        <div style={{ borderTop: "1px solid #1f2937", marginTop: 16, paddingTop: 12 }}>
          <button onClick={() => setShowDev(value => !value)} style={buttonStyle}>
            {showDev ? "Hide integration tests" : "Show integration tests"}
          </button>
          {showDev && (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
              <button onClick={() => onDemo("downtime")} style={buttonStyle}>Demo downtime</button>
              <button onClick={() => onDemo("quality")} style={buttonStyle}>Demo QC fail</button>
              <button onClick={() => onDemo("ottimo")} style={buttonStyle}>Demo Ottimo scan</button>
              <button onClick={() => onDemo("cvsql")} style={buttonStyle}>Demo CV SQL row</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

const eyebrowStyle = {
  color: "#6b7280",
  fontSize: 10,
  fontWeight: 800,
  letterSpacing: 1,
  textTransform: "uppercase",
};

const labelStyle = {
  color: "#6b7280",
  fontSize: 9,
  fontWeight: 800,
  textTransform: "uppercase",
};

const formStyle = {
  border: "1px solid #1f2937",
  borderRadius: 6,
  padding: 12,
  background: "#0f1623",
};

const inputStyle = {
  width: "100%",
  minHeight: 32,
  background: "#111827",
  border: "1px solid #374151",
  color: "#f9fafb",
  padding: "6px 8px",
  borderRadius: 6,
  fontSize: 11,
};

const buttonStyle = {
  background: "#1f2937",
  border: "1px solid #374151",
  color: "#f9fafb",
  padding: "7px 12px",
  borderRadius: 6,
  fontSize: 11,
  cursor: "pointer",
};

const rowStyle = {
  display: "flex",
  flexDirection: "column",
  gap: 2,
  color: "#d1d5db",
  fontSize: 11,
  borderBottom: "1px solid #111827",
  paddingBottom: 6,
};
