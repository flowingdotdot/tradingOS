import { useEffect, useState } from "react";
import { api, type RunDetail } from "../api";
import { fmtDate, fmtNum, fmtPct, fmtSigned } from "../format";

// Experiment conditions that must match for a fair comparison. Differences are highlighted.
function conditions(r: RunDetail): Record<string, string> {
  const c = r.conditions;
  const costs = c?.costs ?? r.config.costs;
  const risk = c?.risk ?? r.config.risk;
  return {
    Instrument: c?.inst_id ?? r.inst_id,
    Timeframe: r.timeframe,
    Period: `${fmtDate(r.start_ts)} → ${fmtDate(r.end_ts)}`,
    // test-window data; the warm-up history legitimately differs between strategies
    "Test data fingerprint": (c?.data_fingerprint ?? r.data_fingerprint).slice(0, 10),
    "Initial capital": fmtNum(c?.initial_capital ?? r.config.initial_capital, 0),
    "Taker fee": String(costs.taker_fee_rate),
    "Slippage (bps)": String(costs.slippage_bps),
    "Funding / 8h": String(costs.funding_rate_per_8h),
    "Risk / trade %": String(risk.risk_per_trade_pct),
    "Daily loss %": String(risk.daily_loss_limit_pct),
    "Max leverage": String(risk.max_leverage),
  };
}

export function ComparePanel({ ids, onError }: { ids: string[]; onError: (m: string) => void }) {
  const [runs, setRuns] = useState<RunDetail[]>([]);
  useEffect(() => {
    Promise.all(ids.map((id) => api.run(id))).then(setRuns).catch((e) => onError(e.message));
  }, [ids, onError]);
  if (runs.length < 2) return null;

  const conds = runs.map(conditions);
  const condKeys = Object.keys(conds[0]);
  const differs = (k: string) => new Set(conds.map((c) => c[k])).size > 1;
  const anyDiff = condKeys.some(differs);
  const metricRows: [string, (r: RunDetail) => string][] = [
    ["Strategy", (r) => `${r.strategy_id}:${r.strategy_version}`],
    ["Definition", (r) => (r.strategy ? `v${r.strategy.definition_version}` : "—")],
    ["Params", (r) => `#${r.params_hash.slice(0, 8)}`],
    ["Trades", (r) => String(r.metrics.total_trades)],
    ["Net profit", (r) => fmtSigned(r.metrics.net_profit)],
    ["Return", (r) => fmtPct(r.metrics.return_pct)],
    ["Win rate", (r) => fmtPct(r.metrics.win_rate_pct, 1)],
    ["Profit factor", (r) => fmtNum(r.metrics.profit_factor)],
    ["Expectancy (R)", (r) => fmtSigned(r.metrics.expectancy_r)],
    ["Max drawdown", (r) => fmtPct(-r.metrics.max_drawdown_pct)],
    ["Max consec. losses", (r) => String(r.metrics.max_consecutive_losses)],
    ["Costs", (r) => fmtNum(r.metrics.total_fees + r.metrics.total_funding + r.metrics.total_slippage)],
    ["Exposure", (r) => fmtPct(r.metrics.exposure_pct, 1)],
  ];
  return (
    <section className="panel">
      <h2>Comparison ({runs.length} runs)</h2>
      {anyDiff ? (
        <div className="alert error">
          Experiment conditions differ (highlighted). Results are not directly comparable — rerun with identical data
          period, costs, capital and risk settings.
        </div>
      ) : (
        <div className="alert info">Identical data, period, costs, capital and risk settings — only the strategy differs.</div>
      )}
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th></th>{runs.map((r) => <th key={r.run_id} className="mono">{r.run_id}</th>)}</tr>
          </thead>
          <tbody>
            {metricRows.map(([label, f]) => (
              <tr key={label}><td className="dim">{label}</td>{runs.map((r) => <td key={r.run_id}>{f(r)}</td>)}</tr>
            ))}
            <tr><td colSpan={runs.length + 1} className="dim fine">Experiment conditions</td></tr>
            {condKeys.map((k) => (
              <tr key={k}>
                <td className={differs(k) ? "compare-diff" : "dim"}>{k}</td>
                {conds.map((c, i) => <td key={i} className={differs(k) ? "compare-diff" : ""}>{c[k]}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="fine">Few trades or a single market regime make any of these comparisons statistically fragile.</p>
    </section>
  );
}
