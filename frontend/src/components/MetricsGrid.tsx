import type { Metrics } from "../api";
import { fmtNum, fmtPct, fmtSigned, tone } from "../format";

interface Tile {
  label: string;
  value: string;
  tone?: string;
  hint: string;
}

export function MetricsGrid({ m }: { m: Metrics }) {
  const tiles: Tile[] = [
    { label: "Net profit (after costs)", value: fmtSigned(m.net_profit), tone: tone(m.net_profit),
      hint: "Final equity − initial capital. Fees, slippage and funding included." },
    { label: "Return", value: fmtPct(m.return_pct), tone: tone(m.return_pct), hint: "Net profit / initial capital." },
    { label: "Max drawdown", value: fmtPct(-m.max_drawdown_pct), tone: m.max_drawdown_pct > 0 ? "neg" : "",
      hint: `Largest peak-to-trough equity decline (${fmtNum(m.max_drawdown_abs)} USDT).` },
    { label: "Trades", value: `${m.total_trades}`, hint: `${m.long_trades} long / ${m.short_trades} short` },
    { label: "Win rate", value: fmtPct(m.win_rate_pct, 1), hint: `${m.wins} wins / ${m.losses} losses (net PnL > 0 = win)` },
    { label: "Avg win / avg loss", value: `${fmtNum(m.avg_win)} / ${fmtNum(m.avg_loss)}`,
      hint: `Payoff ratio ${fmtNum(m.payoff_ratio)} — the realised reward/risk.` },
    { label: "Expectancy", value: `${fmtSigned(m.expectancy)} (${fmtSigned(m.expectancy_r)}R)`,
      tone: tone(m.expectancy), hint: "Average net PnL per trade, also in multiples of planned risk (R)." },
    { label: "Profit factor", value: fmtNum(m.profit_factor),
      tone: m.profit_factor == null ? "" : m.profit_factor >= 1 ? "pos" : "neg",
      hint: "Gross wins / gross losses. < 1 means losing overall. Undefined with no losses." },
    { label: "Max consecutive losses", value: `${m.max_consecutive_losses}`,
      hint: `Longest losing streak (wins: ${m.max_consecutive_wins}). Plan psychology and limits for this.` },
    { label: "Costs", value: fmtNum(m.total_fees + m.total_funding + m.total_slippage),
      hint: `Fees ${fmtNum(m.total_fees)} · slippage ${fmtNum(m.total_slippage)} · funding ${fmtNum(m.total_funding)}` },
    { label: "Exposure", value: fmtPct(m.exposure_pct, 1),
      hint: `Share of bars with an open position. Avg notional ${fmtNum(m.avg_notional, 0)}, max ${fmtNum(m.max_notional, 0)}.` },
    { label: "Signals / rejected", value: `${m.signals_seen} / ${m.rejections}`,
      hint: Object.entries(m.rejection_reasons).map(([k, v]) => `${k}: ${v}`).join(", ") || "No rejections" },
  ];
  return (
    <div className="metrics">
      {tiles.map((t) => (
        <div className="metric" key={t.label} title={t.hint}>
          <div className="metric-label">{t.label}</div>
          <div className={`metric-value ${t.tone ?? ""}`}>{t.value}</div>
          <div className="metric-hint">{t.hint}</div>
        </div>
      ))}
    </div>
  );
}
