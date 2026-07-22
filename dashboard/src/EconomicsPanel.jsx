import { useState } from "react";
import {
  AlertTriangle, Check, ChevronDown, ChevronUp, CircleDollarSign,
  RefreshCw, Settings2, ShieldCheck,
} from "lucide-react";

function money(value, currency) {
  if (value == null) return "Pending";
  try {
    return new Intl.NumberFormat("en-IN", {
      style: "currency", currency, maximumFractionDigits: 0,
    }).format(value);
  } catch {
    return `${currency} ${Number(value).toFixed(0)}`;
  }
}

function RateRow({ rateKey, meta, current, canManage, onSave }) {
  const [amount, setAmount] = useState(current?.amount ?? "");
  const [verified, setVerified] = useState(Boolean(current?.verified));
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      await onSave(rateKey, {
        amount: Number(amount), scope_type: "factory", scope_key: "factory",
        verified, expected_version: current?.version ?? 0,
      });
    } finally { setBusy(false); }
  };
  return <tr>
    <td className="economics-rate-name"><strong>{meta.name}</strong><span>{meta.unit}</span></td>
    <td className="economics-rate-value" data-label="Amount"><input type="number" min="0" step="any"
      value={amount} disabled={!canManage} onChange={event => setAmount(event.target.value)}
      aria-label={`${meta.name} amount`} placeholder="Not commissioned" /></td>
    <td className="economics-rate-verified" data-label="Verified"><input type="checkbox"
      checked={verified} disabled={!canManage} onChange={event => setVerified(event.target.checked)}
      aria-label={`Verify ${meta.name}`} /></td>
    <td className="economics-rate-save">{canManage && <button className="economics-icon"
      disabled={busy || amount === ""} onClick={save} title={`Save ${meta.name}`}><Check size={13} /></button>}</td>
  </tr>;
}

function AdjustmentRow({ experimentId, window, currency, canManage, onSave }) {
  const current = window.adjustment;
  const [amount, setAmount] = useState(current?.adjustment_amount ?? 0);
  const [reason, setReason] = useState(current?.reason ?? "");
  const [verified, setVerified] = useState(Boolean(current?.verified));
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      await onSave(experimentId, {
        expected_version: current?.version ?? 0,
        window_start: window.window_start, window_end: window.window_end,
        adjustment_amount: Number(amount), reason, verified,
      });
    } finally { setBusy(false); }
  };
  const start = new Date(window.window_start).toLocaleDateString();
  const end = new Date(window.window_end).toLocaleDateString();
  return <tr>
    <td><strong>{start} - {end}</strong><span>{window.sample_count} samples · {window.effect_pct ?? "Pending"}% effect</span></td>
    <td data-label="Raw benefit">{money(window.raw_amount, currency)}</td>
    <td data-label="Adjustment"><input type="number" step="any" value={amount}
      disabled={!canManage} onChange={event => setAmount(event.target.value)}
      aria-label={`Adjustment for ${start} to ${end}`} /></td>
    <td data-label="Review reason"><input type="text" value={reason} disabled={!canManage}
      onChange={event => setReason(event.target.value)} placeholder="Operating conditions reviewed"
      aria-label={`Review reason for ${start} to ${end}`} /></td>
    <td data-label="Verified"><input type="checkbox" checked={verified} disabled={!canManage}
      onChange={event => setVerified(event.target.checked)} aria-label={`Verify ${start} to ${end}`} /></td>
    <td className={`economics-status economics-${window.status}`}><strong>{window.status.replaceAll("_", " ")}</strong></td>
    <td>{canManage && <button className="economics-icon" disabled={busy || reason.trim().length < 3}
      onClick={save} title={`Save review for ${start} to ${end}`}><Check size={13} /></button>}</td>
  </tr>;
}

export function EconomicsPanel({ data, canManage, syncing, onSync, onSettings, onRate, onAdjustment }) {
  const [expanded, setExpanded] = useState(false);
  const [configure, setConfigure] = useState(false);
  const [policy, setPolicy] = useState(null);
  const [error, setError] = useState(null);
  if (!data) return null;
  const current = data.current;
  const summary = current?.summary ?? {
    direct_cost_exposure: 0, constraint_capacity_opportunity: 0,
    measured_improvement_benefit: 0, sustained_claims: 0,
    blocked_claims: 0, verified_rates: 0,
  };
  const currency = current?.currency ?? data.settings.currency;
  const openSettings = () => {
    setPolicy({ ...data.settings });
    setConfigure(value => !value);
    setExpanded(true);
  };
  const saveSettings = async () => {
    setError(null);
    try {
      await onSettings({
        expected_version: policy.version,
        auto_review: policy.auto_review,
        interval_seconds: Number(policy.interval_seconds),
        window_hours: Number(policy.window_hours),
        persistence_window_days: Number(policy.persistence_window_days),
        minimum_persistence_reviews: Number(policy.minimum_persistence_reviews),
        currency: policy.currency,
        verified: policy.verified,
      });
    } catch (reason) { setError(reason.message); }
  };
  const runtimeTone = data.runtime.status === "healthy" ? "#22c55e" :
    data.runtime.status === "error" ? "#ef4444" : "#f59e0b";
  const topClaim = current?.claims?.find(item => ["measured", "sustained"].includes(item.status))
    ?? current?.claims?.find(item => item.status === "decision_ready")
    ?? current?.claims?.[0];
  const activeRates = new Map(data.rates.filter(item => item.scope_type === "factory")
    .map(item => [item.rate_key, item]));

  return <section className="economics-panel">
    <div className="economics-summary">
      <div className="economics-title">
        <div><CircleDollarSign size={14} /> Value assurance</div>
        <strong>{current?.status?.replaceAll("_", " ") ?? "Starting"}</strong>
        <span style={{ color: runtimeTone }}>{data.runtime.status} worker · {summary.verified_rates} verified rates</span>
      </div>
      <div className="economics-metric economics-benefit"><span>Measured benefit</span>
        <strong>{money(summary.measured_improvement_benefit, currency)}</strong>
        <small>{summary.sustained_claims} sustained claims</small></div>
      <div className="economics-metric economics-direct"><span>Direct loss exposure</span>
        <strong>{money(summary.direct_cost_exposure, currency)}</strong>
        <small>cash-like measured waste</small></div>
      <div className="economics-metric economics-opportunity"><span>Constraint opportunity</span>
        <strong>{money(summary.constraint_capacity_opportunity, currency)}</strong>
        <small>not booked savings</small></div>
      <div className="economics-decision"><span>{topClaim ? topClaim.category.replaceAll("_", " ") : "No value claim"}</span>
        <strong style={{ color: topClaim?.status === "sustained" ? "#22c55e" : topClaim?.blocked_by?.length ? "#f59e0b" : "#9ca3af" }}>
          {topClaim?.status?.replaceAll("_", " ") ?? "Waiting"}
        </strong><small>{topClaim?.blocked_by?.[0] ?? current?.evidence_gaps?.[0] ?? "Awaiting operational evidence"}</small></div>
      <div className="economics-actions">
        {canManage && <button onClick={onSync} disabled={syncing} title="Run value review"><RefreshCw size={14} className={syncing ? "spin" : ""} /></button>}
        {canManage && <button onClick={openSettings} title="Configure production economics"><Settings2 size={14} /></button>}
        <button onClick={() => setExpanded(value => !value)} title={expanded ? "Hide value details" : "Show value details"}>
          {expanded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
        </button>
      </div>
    </div>

    {error && <div className="economics-error"><AlertTriangle size={13} />{error}</div>}

    {expanded && <div className="economics-detail">
      <div className="economics-note"><ShieldCheck size={13} />
        Direct exposure, capacity opportunity, and measured benefit remain separate.</div>
      {current?.claims?.length ? <div className="economics-table-wrap"><table className="economics-table economics-claims">
        <thead><tr><th>Claim</th><th>Target</th><th>Quantity</th><th>Value</th><th>Status</th></tr></thead>
        <tbody>{current.claims.map(item => <tr key={item.id}>
          <td><strong>{item.category.replaceAll("_", " ")}</strong><span>{item.claim_type.replaceAll("_", " ")}</span></td>
          <td>{item.target_key.replaceAll("_", " ")}</td>
          <td>{item.quantity == null ? "Pending" : `${Number(item.quantity).toFixed(2)} ${item.quantity_unit}`}</td>
          <td>{money(item.amount, item.currency)}</td>
          <td className={`economics-status economics-${item.status}`}><strong>{item.status.replaceAll("_", " ")}</strong>
            <span>{item.blocked_by?.[0] ?? item.evidence?.[0]}</span></td>
        </tr>)}</tbody>
      </table></div> : <div className="economics-empty">No financial claim has operational evidence in this window.</div>}

      {current?.claims?.filter(item => item.source_type === "improvement_experiment" && item.persistence?.length)
        .map(item => <div className="economics-persistence" key={`persistence-${item.claim_key}`}>
          <div className="economics-subtitle"><strong>Persistence verification</strong>
            <span>{item.category.replaceAll("_", " ")} · experiment #{item.source_key}</span></div>
          <div className="economics-table-wrap"><table className="economics-table economics-adjustments">
            <thead><tr><th>Window</th><th>Raw benefit</th><th>Adjustment ({currency})</th><th>Review reason</th><th>Verified</th><th>Status</th><th /></tr></thead>
            <tbody>{item.persistence.map(window => <AdjustmentRow
              key={`${window.window_start}-${window.adjustment?.version ?? 0}`}
              experimentId={Number(item.source_key)} window={window} currency={currency}
              canManage={canManage} onSave={onAdjustment} />)}</tbody>
          </table></div>
        </div>)}

      {configure && policy && <div className="economics-config">
        <div className="economics-config-grid">
          <label>Currency<input maxLength="3" value={policy.currency}
            onChange={event => setPolicy(value => ({ ...value, currency: event.target.value.toUpperCase() }))} /></label>
          <label>Review interval<input type="number" min="60" value={policy.interval_seconds}
            onChange={event => setPolicy(value => ({ ...value, interval_seconds: Number(event.target.value) }))} /></label>
          <label>Exposure window hours<input type="number" min="1" value={policy.window_hours}
            onChange={event => setPolicy(value => ({ ...value, window_hours: Number(event.target.value) }))} /></label>
          <label>Persistence window days<input type="number" min="1" value={policy.persistence_window_days}
            onChange={event => setPolicy(value => ({ ...value, persistence_window_days: Number(event.target.value) }))} /></label>
          <label>Required follow-ups<input type="number" min="1" max="12" value={policy.minimum_persistence_reviews}
            onChange={event => setPolicy(value => ({ ...value, minimum_persistence_reviews: Number(event.target.value) }))} /></label>
        </div>
        <div className="economics-toggles">
          <label><input type="checkbox" checked={policy.auto_review}
            onChange={event => setPolicy(value => ({ ...value, auto_review: event.target.checked }))} /> Automatic reviews</label>
          <label><input type="checkbox" checked={policy.verified}
            onChange={event => setPolicy(value => ({ ...value, verified: event.target.checked }))} /> Site-verified policy</label>
          <button onClick={saveSettings}><Check size={13} /> Save policy</button>
        </div>
        <div className="economics-table-wrap"><table className="economics-table economics-rates">
          <thead><tr><th>Factory rate</th><th>Amount ({policy.currency})</th><th>Verified</th><th /></tr></thead>
          <tbody>{Object.entries(data.rate_catalog).map(([rateKey, meta]) => {
            const active = activeRates.get(rateKey);
            return <RateRow key={`${rateKey}-${active?.version ?? 0}`} rateKey={rateKey}
              meta={meta} current={active} canManage={canManage} onSave={onRate} />;
          })}</tbody>
        </table></div>
      </div>}
    </div>}
  </section>;
}
