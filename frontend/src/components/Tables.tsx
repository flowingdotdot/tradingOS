import type { MonthRow, RunSummary, Trade } from "../api";
import { fmtDate, fmtNum, fmtPct, fmtSigned, fmtTs, tone } from "../format";

export function TradeTable({
  trades,
  selected,
  onSelect,
}: {
  trades: Trade[];
  selected: number | null;
  onSelect: (t: Trade) => void;
}) {
  if (!trades.length) return <p className="empty">No trades in this run.</p>;
  return (
    <div className="table-wrap tall">
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Side</th>
            <th>Entry (UTC)</th>
            <th className="r">Entry</th>
            <th className="r">Stop</th>
            <th className="r">Target</th>
            <th>Exit (UTC)</th>
            <th className="r">Exit</th>
            <th>Reason</th>
            <th className="r">Qty BTC</th>
            <th className="r">Costs</th>
            <th className="r">Net PnL</th>
            <th className="r">R</th>
            <th className="r">Bars</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => (
            <tr
              key={t.trade_id}
              className={selected === t.trade_id ? "sel" : ""}
              onClick={() => onSelect(t)}
              title={t.entry_reason}
            >
              <td>{t.trade_id}</td>
              <td className={t.side === 1 ? "pos" : "neg"}>{t.side === 1 ? "LONG" : "SHORT"}</td>
              <td>{fmtTs(t.entry_ts)}</td>
              <td className="r">{fmtNum(t.entry_price, 1)}</td>
              <td className="r">{fmtNum(t.stop, 1)}</td>
              <td className="r">{t.target == null ? "—" : fmtNum(t.target, 1)}</td>
              <td>{fmtTs(t.exit_ts)}</td>
              <td className="r">{fmtNum(t.exit_price, 1)}</td>
              <td>
                {t.exit_reason}
                {t.ambiguous_bar && <span className="tag warn" title="Stop and target inside one candle; stop assumed">amb</span>}
              </td>
              <td className="r">{fmtNum(t.qty, 4)}</td>
              <td className="r">{fmtNum(t.fees + t.funding + t.slippage_cost)}</td>
              <td className={`r ${tone(t.net_pnl)}`}>{fmtSigned(t.net_pnl)}</td>
              <td className={`r ${tone(t.r_multiple)}`}>{fmtSigned(t.r_multiple)}</td>
              <td className="r">{t.bars_held}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function MonthlyTable({ rows }: { rows: MonthRow[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Month (UTC)</th>
            <th className="r">Return</th>
            <th className="r">Trades</th>
            <th className="r">Net PnL</th>
            <th className="r">Equity end</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.month}>
              <td>{r.month}</td>
              <td className={`r ${tone(r.return_pct)}`}>{fmtPct(r.return_pct)}</td>
              <td className="r">{r.trades}</td>
              <td className={`r ${tone(r.net_pnl)}`}>{fmtSigned(r.net_pnl)}</td>
              <td className="r">{fmtNum(r.equity_end)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function RunsTable({
  runs,
  selected,
  onSelect,
}: {
  runs: RunSummary[];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  if (!runs.length) return <p className="empty">No runs yet.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Run</th>
            <th>Strategy</th>
            <th>TF</th>
            <th>Period (UTC)</th>
            <th className="r">Trades</th>
            <th className="r">Net</th>
            <th className="r">Return</th>
            <th className="r">PF</th>
            <th className="r">Exp. R</th>
            <th className="r">Max DD</th>
            <th>Code</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.run_id} className={selected === r.run_id ? "sel" : ""} onClick={() => onSelect(r.run_id)}>
              <td className="mono" title={r.created_at}>{r.run_id}</td>
              <td>
                {r.strategy_id}:{r.strategy_version}
                <span className="mono dim" title={`params_hash ${r.params_hash}`}> #{r.params_hash.slice(0, 6)}</span>
              </td>
              <td>{r.timeframe}</td>
              <td>{fmtDate(r.start_ts)} → {fmtDate(r.end_ts)}</td>
              <td className="r">{r.summary.total_trades}</td>
              <td className={`r ${tone(r.summary.net_profit)}`}>{fmtSigned(r.summary.net_profit)}</td>
              <td className={`r ${tone(r.summary.return_pct)}`}>{fmtPct(r.summary.return_pct)}</td>
              <td className="r">{fmtNum(r.summary.profit_factor)}</td>
              <td className={`r ${tone(r.summary.expectancy_r)}`}>{fmtSigned(r.summary.expectancy_r)}</td>
              <td className="r neg">{fmtPct(-r.summary.max_drawdown_pct)}</td>
              <td className="mono dim" title={`engine ${r.engine_version}, data ${r.data_fingerprint}`}>{r.code_revision}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
