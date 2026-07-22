import {
  AlertTriangle, Check, ChevronDown, ChevronUp, Gauge, RefreshCw, Settings2,
  ShieldCheck, X,
} from "lucide-react";
import { useState } from "react";

const fmtMinutes = value => value == null ? "Pending" : `${Number(value).toFixed(value < 10 ? 1 : 0)}m`;
const fmtDate = value => value ? new Date(value).toLocaleString([], {
  month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
}) : "Evidence needed";

function NormRow({ item, canManage, onSave }) {
  const [minutes, setMinutes] = useState(item.workload_norm_minutes);
  const [seconds, setSeconds] = useState(item.standard_operation_seconds ?? "");
  const [verified, setVerified] = useState(item.verified);
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      await onSave(item.machine_key, {
        expected_version: item.version, workload_norm_minutes: Number(minutes),
        standard_operation_seconds: seconds === "" ? null : Number(seconds), verified,
      });
    } finally { setBusy(false); }
  };
  return <tr>
    <td className="release-norm-station"><strong>{item.machine_name}</strong><span>{item.machine_key}</span></td>
    <td className="release-norm-value" data-label="Norm minutes"><input type="number" min="1" value={minutes} disabled={!canManage}
      onChange={event => setMinutes(event.target.value)} aria-label={`${item.machine_name} workload norm minutes`} /></td>
    <td className="release-norm-value" data-label="Fallback seconds"><input type="number" min="0.1" value={seconds} disabled={!canManage}
      onChange={event => setSeconds(event.target.value)} placeholder="Model" aria-label={`${item.machine_name} fallback seconds`} /></td>
    <td className="release-norm-verified" data-label="Verified"><input type="checkbox" checked={verified} disabled={!canManage}
      onChange={event => setVerified(event.target.checked)} aria-label={`Verify ${item.machine_name} norm`} /></td>
    <td className="release-norm-save">{canManage && <button className="release-icon" onClick={save} disabled={busy} title="Save station norm">
      <Check size={13} />
    </button>}</td>
  </tr>;
}

export function ReleaseControlPanel({ data, canManage, syncing, onSync, onSettings, onNorm, onAction }) {
  const [expanded, setExpanded] = useState(false);
  const [configure, setConfigure] = useState(false);
  const [policy, setPolicy] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState("");
  if (!data) return null;
  const review = data.current;
  const summary = review?.summary ?? { pre_shop_orders: 0, actionable: 0, release: 0, expedite: 0, hold: 0 };
  const top = review?.recommendations?.find(item => item.status === "open") ?? null;
  const runtimeTone = data.runtime.status === "healthy" ? "#22c55e"
    : data.runtime.status === "error" || data.runtime.status === "stale" ? "#ef4444" : "#f59e0b";

  const openSettings = () => {
    setPolicy({
      interval_seconds: data.settings.interval_seconds,
      overload_threshold_ratio: data.settings.overload_threshold_ratio,
      work_ahead_hours: data.settings.work_ahead_hours,
      queue_allowance_hours: data.settings.queue_allowance_hours,
      expedite_after_hours: data.settings.expedite_after_hours,
      max_releases_per_review: data.settings.max_releases_per_review,
      allow_starvation_override: data.settings.allow_starvation_override,
      auto_review: data.settings.auto_review, verified: data.settings.verified,
    });
    setConfigure(value => !value);
    setExpanded(true);
  };
  const saveSettings = async () => {
    setError("");
    try {
      await onSettings({ ...policy, expected_version: data.settings.version });
      setConfigure(false);
    } catch (reason) { setError(reason.message); }
  };
  const act = async (item, action) => {
    setBusyId(item.id); setError("");
    try {
      await onAction(item.id, {
        action, confirm_override: action === "approve" && item.requires_override,
        notes: action === "approve" ? `Reviewed ${item.recommendation} recommendation` : "Dismissed during release review",
      });
    } catch (reason) { setError(reason.message); }
    finally { setBusyId(null); }
  };

  return <section className="release-panel">
    <div className="release-summary">
      <div className="release-title">
        <div><Gauge size={14} /> Adaptive release</div>
        <strong>{review?.status?.replaceAll("_", " ") ?? "Starting"}</strong>
        <span style={{ color: runtimeTone }}>{data.runtime.status} worker · policy {data.settings.verified ? "verified" : "unverified"}</span>
      </div>
      <div className="release-metric release-pool"><span>Pre-shop pool</span><strong>{summary.pre_shop_orders}</strong><small>{summary.on_floor_orders ?? 0} orders on floor</small></div>
      <div className="release-metric release-actionable"><span>Actionable</span><strong style={{ color: summary.actionable ? "#22c55e" : "#f3f4f6" }}>{summary.actionable}</strong><small>{summary.release} release · {summary.expedite} expedite</small></div>
      <div className="release-metric release-load"><span>Corrected load</span><strong>{fmtMinutes(summary.current_corrected_load_minutes)}</strong><small>{summary.hold} orders held</small></div>
      <div className="release-decision">
        <span>{top ? top.job_name : "No pending decision"}</span>
        <strong style={{ color: top?.recommendation === "hold" ? "#f59e0b" : top ? "#22c55e" : "#6b7280" }}>
          {top?.recommendation ?? "Stable"}
        </strong>
        <small>{top ? top.reason_code.replaceAll("_", " ") : review?.evidence_gaps?.[0] ?? "Awaiting review"}</small>
      </div>
      <div className="release-actions">
        {canManage && <button onClick={onSync} disabled={syncing} title="Run release review"><RefreshCw size={14} className={syncing ? "spin" : ""} /></button>}
        {canManage && <button onClick={openSettings} title="Configure release policy"><Settings2 size={14} /></button>}
        <button onClick={() => setExpanded(value => !value)} title={expanded ? "Hide release details" : "Show release details"}>
          {expanded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
        </button>
      </div>
    </div>

    {error && <div className="release-error"><AlertTriangle size={13} />{error}</div>}

    {expanded && <div className="release-detail">
      <div className="release-note"><ShieldCheck size={13} />{review?.method_note ?? "No production order is released automatically."}</div>
      {review?.recommendations?.length > 0 && <div className="release-table-wrap"><table className="release-table">
        <thead><tr><th>Order</th><th>Decision</th><th>Planned release</th><th>Highest load</th><th>Evidence</th><th /></tr></thead>
        <tbody>{review.recommendations.map(item => {
          const highest = Math.max(0, ...item.workload.projected_stations.map(station => station.projected_ratio));
          return <tr key={item.id}>
            <td><strong>{item.job_name}</strong><span>schedule #{item.schedule_position} · score {Math.round(item.score)}</span></td>
            <td><b className={`release-${item.recommendation}`}>{item.recommendation}</b><span>{item.reason_code.replaceAll("_", " ")}</span></td>
            <td>{fmtDate(item.planned_release_at)}</td>
            <td>{Math.round(highest * 100)}%</td>
            <td style={{ color: item.evidence_ready ? "#22c55e" : "#f59e0b" }}>{item.evidence_ready ? "Decision-ready" : "Preview only"}</td>
            <td><div className="release-row-actions">
              {canManage && item.status === "open" && <>
                <button className="release-icon" onClick={() => act(item, "approve")} disabled={busyId === item.id || !item.evidence_ready} title={item.evidence_ready ? `Approve ${item.recommendation}` : "Commission evidence first"}><Check size={13} /></button>
                <button className="release-icon" onClick={() => act(item, "dismiss")} disabled={busyId === item.id} title="Dismiss recommendation"><X size={13} /></button>
              </>}
              {item.status !== "open" && <span>{item.status}</span>}
            </div></td>
          </tr>;
        })}</tbody>
      </table></div>}

      {configure && policy && <div className="release-config">
        <div className="release-config-grid">
          <label>Review interval<input type="number" min="60" value={policy.interval_seconds} onChange={event => setPolicy(value => ({ ...value, interval_seconds: Number(event.target.value) }))} /></label>
          <label>Overload ratio<input type="number" min="0.1" max="2" step="0.05" value={policy.overload_threshold_ratio} onChange={event => setPolicy(value => ({ ...value, overload_threshold_ratio: Number(event.target.value) }))} /></label>
          <label>Work-ahead hours<input type="number" min="0" value={policy.work_ahead_hours} onChange={event => setPolicy(value => ({ ...value, work_ahead_hours: Number(event.target.value) }))} /></label>
          <label>Queue allowance / operation<input type="number" min="0" step="0.5" value={policy.queue_allowance_hours} onChange={event => setPolicy(value => ({ ...value, queue_allowance_hours: Number(event.target.value) }))} /></label>
          <label>Expedite after hours<input type="number" min="0" step="0.5" value={policy.expedite_after_hours} onChange={event => setPolicy(value => ({ ...value, expedite_after_hours: Number(event.target.value) }))} /></label>
          <label>Releases per review<input type="number" min="1" max="20" value={policy.max_releases_per_review} onChange={event => setPolicy(value => ({ ...value, max_releases_per_review: Number(event.target.value) }))} /></label>
        </div>
        <div className="release-toggles">
          <label><input type="checkbox" checked={policy.auto_review} onChange={event => setPolicy(value => ({ ...value, auto_review: event.target.checked }))} /> Automatic reviews</label>
          <label><input type="checkbox" checked={policy.allow_starvation_override} onChange={event => setPolicy(value => ({ ...value, allow_starvation_override: event.target.checked }))} /> Supervised starvation override</label>
          <label><input type="checkbox" checked={policy.verified} onChange={event => setPolicy(value => ({ ...value, verified: event.target.checked }))} /> Site-verified policy</label>
          <button onClick={saveSettings}><Check size={13} /> Save policy</button>
        </div>
        <div className="release-table-wrap"><table className="release-table release-norms">
          <thead><tr><th>Station</th><th>Norm minutes</th><th>Fallback seconds</th><th>Verified</th><th /></tr></thead>
          <tbody>{data.norms.map(item => <NormRow key={`${item.machine_key}-${item.version}`} item={item} canManage={canManage} onSave={onNorm} />)}</tbody>
        </table></div>
      </div>}
    </div>}
  </section>;
}
