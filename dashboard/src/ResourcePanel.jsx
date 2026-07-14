import { useMemo, useState } from "react";

const line = { borderTop: "1px solid #263244", padding: "10px 0" };
const label = { color: "#6b7280", fontSize: 9, fontWeight: 800, textTransform: "uppercase" };
const input = { background: "#0d1117", border: "1px solid #374151", borderRadius: 5,
  color: "#e5e7eb", padding: "7px 8px", fontSize: 11, minWidth: 0, width: "100%" };
const button = { background: "#1f2937", border: "1px solid #374151", color: "#e5e7eb",
  padding: "7px 10px", borderRadius: 5, fontSize: 11, fontWeight: 700, cursor: "pointer" };
const primary = { ...button, background: "#1d4ed8", borderColor: "#3b82f6" };

function Verification({ checked, onChange }) {
  return <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 10, color: checked ? "#86efac" : "#9ca3af" }}>
    <input type="checkbox" checked={checked} onChange={event => onChange(event.target.checked)} /> Verified
  </label>;
}

function MaterialRow({ item, actor, onSave, showOpenDemand }) {
  const [stock, setStock] = useState(item.on_hand_sheets);
  const [verified, setVerified] = useState(Boolean(item.verified && item.stock_verified));
  const [busy, setBusy] = useState(false);
  const required = showOpenDemand ? item.open_required_sheets : item.required_sheets;
  const shortage = showOpenDemand ? item.open_shortage_sheets : item.shortage_sheets;
  const save = async () => {
    setBusy(true);
    try { await onSave(item.material_key, { on_hand_sheets: Number(stock), sheet_length_mm: item.sheet_length_mm,
      sheet_width_mm: item.sheet_width_mm, yield_factor: item.yield_factor, verified, actor }); }
    finally { setBusy(false); }
  };
  return <div className="resource-material-row" style={{ ...line, display: "grid", gridTemplateColumns: "minmax(180px,1fr) 90px 90px 110px auto", gap: 8, alignItems: "center" }}>
    <div style={{ minWidth: 0 }}><div style={{ fontSize: 11, fontWeight: 800, overflowWrap: "anywhere" }}>{item.name}</div>
      <div style={{ color: shortage > 0 ? "#f87171" : "#6b7280", fontSize: 9, marginTop: 3 }}>
        {Number(required || 0).toFixed(0)} {showOpenDemand ? "open demand" : "required"} - {Number(item.reserved_sheets || 0).toFixed(0)} reserved
        {item.unknown_part_count ? ` - ${item.unknown_part_count} unknown-size parts` : ""}
      </div></div>
    <div><div style={label}>On hand</div><input type="number" min="0" step="1" value={stock} onChange={event => setStock(event.target.value)} style={input} /></div>
    <div><div style={label}>Shortage</div><div style={{ color: shortage > 0 ? "#f87171" : "#22c55e", fontSize: 12, fontWeight: 800, paddingTop: 7 }}>{Number(shortage || 0).toFixed(0)}</div></div>
    <Verification checked={verified} onChange={setVerified} />
    <button disabled={busy} onClick={save} style={primary}>Save</button>
  </div>;
}

function LaborRow({ item, actor, onSave }) {
  const [headcount, setHeadcount] = useState(item.headcount);
  const [verified, setVerified] = useState(Boolean(item.verified));
  return <div className="resource-compact-row" style={{ ...line, display: "grid", gridTemplateColumns: "1fr 100px 100px auto", gap: 8, alignItems: "center" }}>
    <div><div style={{ fontSize: 11, fontWeight: 800 }}>{item.name}</div><div style={{ color: "#6b7280", fontSize: 9 }}>{item.role_key}</div></div>
    <input type="number" min="0" value={headcount} onChange={event => setHeadcount(event.target.value)} style={input} />
    <Verification checked={verified} onChange={setVerified} />
    <button onClick={() => onSave(item.role_key, { headcount: Number(headcount), verified, actor })} style={primary}>Save</button>
  </div>;
}

function ToolRow({ item, actor, onSave }) {
  const [total, setTotal] = useState(item.total_qty);
  const [available, setAvailable] = useState(item.available_qty);
  const [verified, setVerified] = useState(Boolean(item.verified));
  return <div className="resource-tool-row" style={{ ...line, display: "grid", gridTemplateColumns: "1fr 74px 74px 100px auto", gap: 8, alignItems: "center" }}>
    <div><div style={{ fontSize: 11, fontWeight: 800 }}>{item.name}</div><div style={{ color: "#6b7280", fontSize: 9 }}>{item.pool_key}</div></div>
    <div><div style={label}>Total</div><input type="number" min="0" value={total} onChange={event => setTotal(event.target.value)} style={input} /></div>
    <div><div style={label}>Ready</div><input type="number" min="0" value={available} onChange={event => setAvailable(event.target.value)} style={input} /></div>
    <Verification checked={verified} onChange={setVerified} />
    <button onClick={() => onSave(item.pool_key, { total_qty: Number(total), available_qty: Number(available), verified, actor })} style={primary}>Save</button>
  </div>;
}

function ProfileRow({ item, roles, pools, actor, onSave }) {
  const [roleKey, setRoleKey] = useState(item.role_key);
  const [laborQty, setLaborQty] = useState(item.labor_qty);
  const [poolKey, setPoolKey] = useState(item.pool_key);
  const [toolQty, setToolQty] = useState(item.tool_qty);
  const [capacity, setCapacity] = useState(item.machine_capacity);
  const [verified, setVerified] = useState(Boolean(item.verified));
  return <div style={line}>
    <div style={{ display: "flex", justifyContent: "space-between", gap: 8, marginBottom: 7 }}><div style={{ fontSize: 11, fontWeight: 800 }}>{item.machine_name}</div><Verification checked={verified} onChange={setVerified} /></div>
    <div className="resource-profile-grid" style={{ display: "grid", gridTemplateColumns: "1fr 64px 1fr 64px 64px auto", gap: 7, alignItems: "end" }}>
      <div><div style={label}>Labor role</div><select value={roleKey} onChange={event => setRoleKey(event.target.value)} style={input}>{roles.map(role => <option key={role.role_key} value={role.role_key}>{role.name}</option>)}</select></div>
      <div><div style={label}>People</div><input type="number" min="0" value={laborQty} onChange={event => setLaborQty(event.target.value)} style={input} /></div>
      <div><div style={label}>Tool pool</div><select value={poolKey} onChange={event => setPoolKey(event.target.value)} style={input}>{pools.map(pool => <option key={pool.pool_key} value={pool.pool_key}>{pool.name}</option>)}</select></div>
      <div><div style={label}>Tools</div><input type="number" min="0" value={toolQty} onChange={event => setToolQty(event.target.value)} style={input} /></div>
      <div><div style={label}>Slots</div><input type="number" min="1" value={capacity} onChange={event => setCapacity(event.target.value)} style={input} /></div>
      <button onClick={() => onSave(item.machine_key, { role_key: roleKey, labor_qty: Number(laborQty), pool_key: poolKey,
        tool_qty: Number(toolQty), machine_capacity: Number(capacity), verified, actor })} style={primary}>Save</button>
    </div>
  </div>;
}

function BufferRow({ item, actor, onSave }) {
  const [capacity, setCapacity] = useState(item.capacity_qty);
  const [current, setCurrent] = useState(item.current_qty);
  const [verified, setVerified] = useState(Boolean(item.verified));
  return <div className="resource-tool-row" style={{ ...line, display: "grid", gridTemplateColumns: "1fr 80px 80px 100px auto", gap: 8, alignItems: "center" }}>
    <div><div style={{ fontSize: 11, fontWeight: 800 }}>{item.machine_name}</div><div style={{ color: "#6b7280", fontSize: 9 }}>Input buffer</div></div>
    <div><div style={label}>Capacity</div><input type="number" min="1" value={capacity} onChange={event => setCapacity(event.target.value)} style={input} /></div>
    <div><div style={label}>Current</div><input type="number" min="0" value={current} onChange={event => setCurrent(event.target.value)} style={input} /></div>
    <Verification checked={verified} onChange={setVerified} />
    <button onClick={() => onSave(item.machine_key, { capacity_qty: Number(capacity), current_qty: Number(current), verified, actor })} style={primary}>Save</button>
  </div>;
}

function CalendarEditor({ rows, machines, unavailability, actor, onAction }) {
  const factoryRows = rows.filter(row => row.resource_type === "factory" && row.resource_key === "factory");
  const first = factoryRows[0] ?? { start_time: "09:00", end_time: "18:00", timezone: "Asia/Kolkata", verified: 0 };
  const [weekdays, setWeekdays] = useState(factoryRows.map(row => row.weekday));
  const [start, setStart] = useState(first.start_time);
  const [end, setEnd] = useState(first.end_time);
  const [zone, setZone] = useState(first.timezone);
  const [verified, setVerified] = useState(factoryRows.length > 0 && factoryRows.every(row => row.verified));
  const [machineKey, setMachineKey] = useState(machines[0]?.machine_key ?? "");
  const [startsAt, setStartsAt] = useState("");
  const [endsAt, setEndsAt] = useState("");
  const [reason, setReason] = useState("");
  const toggleDay = day => setWeekdays(current => current.includes(day) ? current.filter(value => value !== day) : [...current, day]);
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  return <div>
    <div style={label}>Factory calendar</div>
    <div style={{ display: "flex", gap: 5, margin: "8px 0", flexWrap: "wrap" }}>{days.map((name, day) => <button key={name} onClick={() => toggleDay(day)}
      style={{ ...button, background: weekdays.includes(day) ? "#1d4ed8" : "#1f2937", minWidth: 42 }}>{name}</button>)}</div>
    <div className="resource-calendar-grid" style={{ display: "grid", gridTemplateColumns: "110px 110px minmax(150px,1fr) 110px auto", gap: 8, alignItems: "end" }}>
      <div><div style={label}>Start</div><input type="time" value={start} onChange={event => setStart(event.target.value)} style={input} /></div>
      <div><div style={label}>End</div><input type="time" value={end} onChange={event => setEnd(event.target.value)} style={input} /></div>
      <div><div style={label}>Timezone</div><input value={zone} onChange={event => setZone(event.target.value)} style={input} /></div>
      <Verification checked={verified} onChange={setVerified} />
      <button onClick={() => onAction("calendar", { weekdays, start_time: start, end_time: end, timezone: zone, verified, actor })} style={primary}>Save</button>
    </div>
    <div style={{ ...label, marginTop: 20 }}>Planned unavailability</div>
    <div className="resource-unavailable-grid" style={{ display: "grid", gridTemplateColumns: "minmax(140px,1fr) 170px 170px minmax(160px,1fr) auto", gap: 8, alignItems: "end", marginTop: 8 }}>
      <div><div style={label}>Machine</div><select value={machineKey} onChange={event => setMachineKey(event.target.value)} style={input}>{machines.map(machine => <option key={machine.machine_key} value={machine.machine_key}>{machine.machine_name}</option>)}</select></div>
      <div><div style={label}>Starts</div><input type="datetime-local" value={startsAt} onChange={event => setStartsAt(event.target.value)} style={input} /></div>
      <div><div style={label}>Ends</div><input type="datetime-local" value={endsAt} onChange={event => setEndsAt(event.target.value)} style={input} /></div>
      <div><div style={label}>Reason</div><input value={reason} onChange={event => setReason(event.target.value)} style={input} /></div>
      <button disabled={!machineKey || !startsAt || !endsAt || !reason} onClick={() => onAction("unavailability", { resource_type: "machine", resource_key: machineKey,
        starts_at: new Date(startsAt).toISOString(), ends_at: new Date(endsAt).toISOString(), reason, actor })} style={primary}>Add</button>
    </div>
    {unavailability.map(item => <div key={item.id} style={{ ...line, display: "flex", gap: 8, alignItems: "center", fontSize: 10, flexWrap: "wrap" }}>
      <span style={{ color: "#f59e0b", fontWeight: 800 }}>{item.resource_key}</span><span style={{ flex: 1 }}>{new Date(item.starts_at).toLocaleString()} - {new Date(item.ends_at).toLocaleString()} - {item.reason}</span>
      <button onClick={() => onAction("deleteUnavailability", { id: item.id, actor })} style={button}>Remove</button>
    </div>)}
  </div>;
}

export function ResourcePanel({ data, actor, onAction }) {
  const [tab, setTab] = useState("materials");
  const [error, setError] = useState("");
  const run = async (kind, payload) => { setError(""); try { await onAction(kind, payload); } catch (err) { setError(err.message); } };
  const machineNames = useMemo(() => data.machine_profiles.map(item => ({ machine_key: item.machine_key, machine_name: item.machine_name })), [data.machine_profiles]);
  return <div style={{ borderTop: "1px solid #374151", marginTop: 12, paddingTop: 12 }}>
    <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
      <div><div style={label}>Factory resources</div><div style={{ color: data.resource_ready ? "#22c55e" : "#f59e0b", fontSize: 13, fontWeight: 800, marginTop: 3 }}>{data.status}</div></div>
      <div style={{ display: "flex", gap: 4 }}>{[["materials", "Materials"], ["capacity", "Capacity"], ["calendar", "Calendar"]].map(([key, name]) =>
        <button key={key} onClick={() => setTab(key)} style={{ ...button, background: tab === key ? "#1d4ed8" : "#1f2937" }}>{name}</button>)}</div>
    </div>
    <div style={{ display: "flex", gap: 10, flexWrap: "wrap", margin: "10px 0" }}>{data.checks.map(check => <span key={check.key} title={check.detail}
      style={{ color: check.passed ? "#22c55e" : "#6b7280", fontSize: 9, fontWeight: 800 }}>{check.passed ? "OK" : "--"} {check.label}</span>)}</div>
    {error && <div style={{ color: "#f87171", fontSize: 10, marginBottom: 8 }}>{error}</div>}
    {tab === "materials" && <div>{data.materials.map(item => <MaterialRow key={`${item.material_key}-${item.updated_at}-${item.on_hand_sheets}`} item={item} actor={actor} showOpenDemand={!data.applicable} onSave={(key, payload) => run("material", { key, payload })} />)}
      {!data.materials.length && <div style={{ color: "#6b7280", fontSize: 10, padding: 12 }}>No open-order material demand.</div>}</div>}
    {tab === "capacity" && <div className="resource-capacity-grid" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
      <div><div style={label}>Labor roles</div>{data.labor_roles.map(item => <LaborRow key={`${item.role_key}-${item.updated_at}`} item={item} actor={actor} onSave={(key, payload) => run("labor", { key, payload })} />)}
        <div style={{ ...label, marginTop: 14 }}>Tool pools</div>{data.tool_pools.map(item => <ToolRow key={`${item.pool_key}-${item.updated_at}`} item={item} actor={actor} onSave={(key, payload) => run("tool", { key, payload })} />)}</div>
      <div><div style={label}>Machine requirements</div>{data.machine_profiles.map(item => <ProfileRow key={`${item.machine_key}-${item.updated_at}`} item={item} roles={data.labor_roles} pools={data.tool_pools} actor={actor} onSave={(key, payload) => run("machineResource", { key, payload })} />)}
        <div style={{ ...label, marginTop: 14 }}>Input WIP buffers</div>{data.wip_buffers.map(item => <BufferRow key={`${item.machine_key}-${item.updated_at}`} item={item} actor={actor} onSave={(key, payload) => run("wip", { key, payload })} />)}</div>
    </div>}
    {tab === "calendar" && <CalendarEditor key={data.calendar.map(row => row.updated_at).join("-")} rows={data.calendar} machines={machineNames}
      unavailability={data.unavailability} actor={actor} onAction={run} />}
    <style>{`@media (max-width: 760px) { .resource-capacity-grid { grid-template-columns: 1fr !important; } .resource-material-row, .resource-tool-row, .resource-compact-row { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) !important; } .resource-material-row > div:first-child, .resource-tool-row > div:first-child, .resource-compact-row > div:first-child { grid-column: 1 / -1; } .resource-profile-grid, .resource-calendar-grid { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) !important; } .resource-unavailable-grid { grid-template-columns: minmax(0, 1fr) !important; } }`}</style>
  </div>;
}
