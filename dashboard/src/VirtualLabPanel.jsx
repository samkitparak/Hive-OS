import { useEffect, useState } from "react";
import { FlaskConical, Play, RefreshCw, ShieldCheck } from "lucide-react";
import { fetchCommissioningLab, runCommissioningLab } from "./api";

const button = {
  border: "1px solid #374151", borderRadius: 6, padding: "8px 12px",
  color: "#f9fafb", background: "#1f2937", cursor: "pointer", fontSize: 11,
  fontWeight: 700, display: "inline-flex", alignItems: "center", gap: 7,
  justifyContent: "center", minHeight: 34,
};
const input = {
  width: 92, padding: "8px 9px", color: "#f9fafb", background: "#0d1117",
  border: "1px solid #374151", borderRadius: 5, fontSize: 11,
};
const label = { color: "#6b7280", fontSize: 9, fontWeight: 800, textTransform: "uppercase" };

const number = (value, digits = 1) => Number(value ?? 0).toFixed(digits);
const hours = seconds => `${number(Number(seconds ?? 0) / 3600, 1)} h`;

function Metric({ name, value, band }) {
  return <div style={{ minWidth: 0, padding: "12px 14px", borderRight: "1px solid #263244" }}>
    <div style={label}>{name}</div>
    <div style={{ color: "#f9fafb", fontSize: 18, fontWeight: 800, marginTop: 5 }}>{value}</div>
    <div style={{ color: "#6b7280", fontSize: 9, marginTop: 3 }}>{band}</div>
  </div>;
}

function Empty({ onRun, busy }) {
  return <div style={{ borderTop: "1px solid #263244", padding: "44px 0", textAlign: "center" }}>
    <FlaskConical size={24} color="#60a5fa" />
    <div style={{ fontSize: 12, fontWeight: 800, marginTop: 10 }}>No lab run recorded</div>
    <button onClick={onRun} disabled={busy} style={{ ...button, marginTop: 14 }}>
      <Play size={14} /> Run reference workload
    </button>
  </div>;
}

export function VirtualLabPanel() {
  const [data, setData] = useState(null);
  const [samples, setSamples] = useState(20);
  const [seed, setSeed] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = async () => {
    setError("");
    try { setData(await fetchCommissioningLab()); }
    catch (reason) { setError(reason.message); }
  };
  useEffect(() => {
    let active = true;
    fetchCommissioningLab()
      .then(result => { if (active) setData(result); })
      .catch(reason => { if (active) setError(reason.message); });
    return () => { active = false; };
  }, []);

  const run = async () => {
    setBusy(true); setError("");
    try {
      await runCommissioningLab({ samples: Number(samples), seed: Number(seed), actor: "commissioning-lab" });
      await load();
    } catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  };

  const latest = data?.latest?.result;
  const baseline = latest?.baseline;
  const throughput = baseline?.throughput_parts_per_hour;
  const completed = baseline?.completed_within_shift;
  const makespan = baseline?.makespan_s;

  return <div>
    <div style={{ display: "flex", justifyContent: "space-between", gap: 14, alignItems: "flex-start", flexWrap: "wrap" }}>
      <div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <div style={{ fontSize: 13, fontWeight: 800 }}>Reference factory model</div>
          <span style={{ color: "#93c5fd", background: "#172554", border: "1px solid #1e40af", borderRadius: 4, padding: "3px 6px", fontSize: 9, fontWeight: 800, textTransform: "uppercase" }}>
            Assumption only
          </span>
        </div>
        <div style={{ color: "#6b7280", fontSize: 10, marginTop: 5 }}>
          {data?.assumptions?.machine_count ?? 0} machines · {data?.assumptions?.reference_units_per_shift ?? 0} units · {data?.assumptions?.shift_hours ?? 0} hour shift
        </div>
      </div>
      <div style={{ display: "flex", alignItems: "end", gap: 8, flexWrap: "wrap" }}>
        <label style={label}>Samples<br /><input type="number" min="10" max="100" value={samples} onChange={event => setSamples(event.target.value)} style={{ ...input, marginTop: 5 }} /></label>
        <label style={label}>Seed<br /><input type="number" min="0" max="2147483647" value={seed} onChange={event => setSeed(event.target.value)} style={{ ...input, marginTop: 5 }} /></label>
        <button onClick={run} disabled={busy || samples < 10 || samples > 100} style={{ ...button, opacity: busy ? .55 : 1 }}>
          {busy ? <RefreshCw size={14} className="spin" /> : <Play size={14} />} {busy ? "Running" : "Run lab"}
        </button>
      </div>
    </div>

    {error && <div style={{ color: "#fca5a5", fontSize: 10, marginTop: 12 }}>{error}</div>}
    {!latest ? <Empty onRun={run} busy={busy} /> : <>
      <div className="lab-metrics" style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", marginTop: 18, border: "1px solid #263244", borderRadius: 6, overflow: "hidden" }}>
        <Metric name="P50 throughput" value={`${number(throughput?.p50)} / h`} band={`P10 ${number(throughput?.p10)} · P90 ${number(throughput?.p90)}`} />
        <Metric name="Shift completion" value={`${number(completed?.p50, 0)} / ${latest.reference_workload.units}`} band={`P10 ${number(completed?.p10, 0)} · P90 ${number(completed?.p90, 0)}`} />
        <Metric name="P50 makespan" value={hours(makespan?.p50)} band={`P10 ${hours(makespan?.p10)} · P90 ${hours(makespan?.p90)}`} />
      </div>

      <div className="lab-columns" style={{ display: "grid", gridTemplateColumns: ".8fr 1.2fr", gap: 22, marginTop: 22 }}>
        <section>
          <div style={{ fontSize: 11, fontWeight: 800, marginBottom: 8 }}>Likely constraints</div>
          {baseline.constraints.slice(0, 6).map(item => <div key={item.machine_key} style={{ padding: "9px 0", borderTop: "1px solid #263244" }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 10, fontSize: 10 }}>
              <span style={{ color: "#d1d5db", fontWeight: 700 }}>{item.machine_name}</span>
              <span style={{ color: item.bottleneck_probability ? "#fbbf24" : "#6b7280", fontWeight: 800 }}>{number(item.bottleneck_probability * 100, 0)}%</span>
            </div>
            <div style={{ height: 3, background: "#1f2937", marginTop: 7 }}><div style={{ height: "100%", width: `${item.bottleneck_probability * 100}%`, background: "#f59e0b" }} /></div>
          </div>)}
        </section>
        <section>
          <div style={{ fontSize: 11, fontWeight: 800, marginBottom: 8 }}>Measure first on site</div>
          {latest.measurement_priorities.slice(0, 5).map((item, index) => <div key={item.machine_key} style={{ display: "grid", gridTemplateColumns: "24px minmax(0,1fr) auto", gap: 8, padding: "9px 0", borderTop: "1px solid #263244" }}>
            <span style={{ color: "#60a5fa", fontSize: 10, fontWeight: 800 }}>{index + 1}</span>
            <div><div style={{ color: "#d1d5db", fontSize: 10, fontWeight: 700 }}>{item.machine_name}</div>
              <div style={{ color: "#6b7280", fontSize: 9, lineHeight: 1.45, marginTop: 3 }}>{item.measure_on_site}</div></div>
            <span style={{ color: "#93c5fd", fontSize: 9, fontWeight: 800 }}>{number(item.impact_span_pct)}% span</span>
          </div>)}
        </section>
      </div>

      <section style={{ marginTop: 22 }}>
        <div style={{ fontSize: 11, fontWeight: 800, marginBottom: 8 }}>Intervention screening</div>
        <div className="lab-interventions" style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: "0 16px" }}>
          {latest.interventions.slice(0, 6).map(item => <div key={item.key} style={{ padding: "9px 0", borderTop: "1px solid #263244" }}>
            <div style={{ color: "#d1d5db", fontSize: 10, fontWeight: 700 }}>{item.label}</div>
            <div style={{ color: item.modeled_throughput_uplift_pct > 0 ? "#86efac" : "#6b7280", fontSize: 10, fontWeight: 800, marginTop: 4 }}>
              {item.modeled_throughput_uplift_pct > 0 ? "+" : ""}{number(item.modeled_throughput_uplift_pct)}% modeled throughput
            </div>
          </div>)}
        </div>
      </section>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "center", borderTop: "1px solid #263244", marginTop: 16, paddingTop: 12, color: "#6b7280", fontSize: 9, flexWrap: "wrap" }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}><ShieldCheck size={12} color="#60a5fa" /> Not eligible for production scheduling or machine control</span>
        <span>Run {data.latest.id} · {data.latest.sample_count} samples · SHA-256 {data.latest.assumptions_sha256.slice(0, 12)}</span>
      </div>
    </>}
  </div>;
}
