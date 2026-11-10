import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  type Candles,
  type CostModel,
  type Dataset,
  type RiskConfig,
  type RunDetail,
  type RunSummary,
  type StrategyInfo,
  type SystemInfo,
  type Trade,
} from "../api";
import { EquityChart } from "../components/EquityChart";
import { MetricsGrid } from "../components/MetricsGrid";
import { PriceChart } from "../components/PriceChart";
import { MonthlyTable, RunsTable, TradeTable } from "../components/Tables";
import { DAY_MS, fmtDate, fmtNum, fmtTs } from "../format";

const TF_MS: Record<string, number> = { "1m": 60e3, "5m": 300e3, "15m": 900e3, "1H": 3600e3, "4H": 14400e3 };
const INST = "BTC-USDT-SWAP";

export function BacktestLab({ system }: { system: SystemInfo | null }) {
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [run, setRun] = useState<RunDetail | null>(null);
  const [candles, setCandles] = useState<Candles | null>(null);
  const [focus, setFocus] = useState<Trade | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // form state
  const [strategyKey, setStrategyKey] = useState("ema_cross:v1");
  const [tf, setTf] = useState("15m");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [capital, setCapital] = useState(10_000);
  const [costs, setCosts] = useState<CostModel | null>(null);
  const [risk, setRisk] = useState<RiskConfig | null>(null);
  const [collectDays, setCollectDays] = useState(180);

  useEffect(() => {
    if (system && !costs) {
      setCosts(system.defaults.costs);
      setRisk(system.defaults.risk);
    }
  }, [system, costs]);

  const refresh = useCallback(async () => {
    const [s, d, r] = await Promise.all([api.strategies(), api.datasets(), api.runs()]);
    setStrategies(s);
    setDatasets(d);
    setRuns(r);
    return r;
  }, []);

  const openRun = useCallback(async (id: string) => {
    setError(null);
    setFocus(null);
    const detail = await api.run(id);
    setRun(detail);
    setCandles(await api.candles(detail.inst_id, detail.timeframe, detail.start_ts, detail.end_ts));
  }, []);

  useEffect(() => {
    refresh()
      .then((r) => (r.length ? openRun(r[0].run_id) : undefined))
      .catch((e) => setError(String(e.message ?? e)));
  }, [refresh, openRun]);

  const dataset = datasets.find((d) => d.inst_id === INST && d.timeframe === tf);

  // Default the period to the whole stored dataset for the chosen timeframe.
  useEffect(() => {
    if (dataset?.first_ts != null && dataset.last_ts != null) {
      setStart(fmtDate(dataset.first_ts));
      setEnd(fmtDate(dataset.last_ts + TF_MS[tf]));
    }
  }, [dataset?.first_ts, dataset?.last_ts, tf]);

  const versions = useMemo(
    () => strategies.flatMap((s) => s.versions.map((v) => ({ key: `${v.strategy_id}:${v.version}`, v }))),
    [strategies],
  );
  const selectedVersion = versions.find((x) => x.key === strategyKey)?.v;
  const requiredTf = strategies.find((s) => s.strategy_id === selectedVersion?.strategy_id)?.required_timeframe;
  useEffect(() => {
    if (requiredTf) setTf(requiredTf); // e.g. ICT strategy runs on 5m and builds 15m/4H itself
  }, [requiredTf]);

  async function collect() {
    setBusy(`Collecting ${collectDays}d of ${tf} candles from OKX…`);
    setError(null);
    try {
      await api.collect(INST, tf, collectDays);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function runBacktest() {
    if (!selectedVersion || !costs || !risk) return;
    setBusy("Running backtest…");
    setError(null);
    try {
      const detail = await api.createRun({
        strategy_id: selectedVersion.strategy_id,
        strategy_version: selectedVersion.version,
        inst_id: INST,
        timeframe: tf,
        start_ts: Date.parse(`${start}T00:00:00Z`),
        end_ts: Date.parse(`${end}T00:00:00Z`),
        initial_capital: capital,
        costs,
        risk,
      });
      setRun(detail);
      setFocus(null);
      setCandles(await api.candles(detail.inst_id, detail.timeframe, detail.start_ts, detail.end_ts));
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  const numInput = (value: number, set: (v: number) => void, step = "any") => (
    <input type="number" step={step} value={value} onChange={(e) => set(Number(e.target.value))} />
  );

  return (
    <div className="page">
      <div className="page-head">
        <h1>Backtest Lab</h1>
        <span className="env-chip backtest">SIMULATED · historical data · assumed costs</span>
      </div>

      {error && <div className="alert error">{error}</div>}
      {busy && <div className="alert info">{busy}</div>}

      <div className="grid-config">
        <section className="panel">
          <h2>Market data</h2>
          <div className="kv">
            <span>Instrument</span>
            <span className="mono">{INST}</span>
            <span>Timeframe</span>
            <select value={tf} onChange={(e) => setTf(e.target.value)} disabled={!!requiredTf}>
              {Object.keys(TF_MS).map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
            <span>Stored</span>
            <span>
              {dataset ? (
                <>
                  {dataset.rows.toLocaleString()} closed candles
                  <br />
                  <span className="dim">
                    {fmtTs(dataset.first_ts)} → {fmtTs(dataset.last_ts)}
                  </span>
                </>
              ) : (
                <span className="dim">none — collect first</span>
              )}
            </span>
            <span>Integrity</span>
            <span>
              {dataset ? (
                dataset.missing_bars === 0 && dataset.ok ? (
                  <span className="pos">OK · no gaps</span>
                ) : (
                  <span className="warn-text">
                    {dataset.gaps.length} gap(s), {dataset.missing_bars} missing bar(s)
                  </span>
                )
              ) : (
                "—"
              )}
            </span>
          </div>
          <div className="row">
            <label>
              Days {numInput(collectDays, setCollectDays, "1")}
            </label>
            <button onClick={collect} disabled={!!busy}>
              Collect / update from OKX
            </button>
          </div>
          <p className="fine">Public OKX endpoint. Forming (unconfirmed) candles are discarded; gaps are reported, never filled.</p>
        </section>

        <section className="panel">
          <h2>Test configuration</h2>
          <div className="form">
            <label>
              Strategy version
              <select value={strategyKey} onChange={(e) => setStrategyKey(e.target.value)}>
                {versions.map(({ key, v }) => (
                  <option key={key} value={key}>
                    {key} [{v.status}]
                  </option>
                ))}
              </select>
            </label>
            <label>
              Start (UTC)
              <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
            </label>
            <label>
              End (UTC, exclusive)
              <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
            </label>
            <label>
              Initial capital (USDT)
              {numInput(capital, setCapital)}
            </label>
            {costs && (
              <>
                <label>
                  Taker fee (rate)
                  {numInput(costs.taker_fee_rate, (v) => setCosts({ ...costs, taker_fee_rate: v }))}
                </label>
                <label>
                  Slippage (bps)
                  {numInput(costs.slippage_bps, (v) => setCosts({ ...costs, slippage_bps: v }))}
                </label>
                <label>
                  Funding / 8h (cost)
                  {numInput(costs.funding_rate_per_8h, (v) => setCosts({ ...costs, funding_rate_per_8h: v }))}
                </label>
              </>
            )}
            {risk && (
              <>
                <label>
                  Risk / trade (%)
                  {numInput(risk.risk_per_trade_pct, (v) => setRisk({ ...risk, risk_per_trade_pct: v }))}
                </label>
                <label>
                  Daily loss limit (%)
                  {numInput(risk.daily_loss_limit_pct, (v) => setRisk({ ...risk, daily_loss_limit_pct: v }))}
                </label>
                <label>
                  Max leverage (x)
                  {numInput(risk.max_leverage, (v) => setRisk({ ...risk, max_leverage: v }))}
                </label>
              </>
            )}
          </div>
          {selectedVersion && (
            <p className="fine">
              {selectedVersion.description} · params <span className="mono">#{selectedVersion.params_hash.slice(0, 10)}</span>
            </p>
          )}
          <button className="primary" onClick={runBacktest} disabled={!!busy || !dataset || !start || !end}>
            Run backtest
          </button>
        </section>
      </div>

      <section className="panel">
        <h2>Runs — compare versions</h2>
        <RunsTable runs={runs} selected={run?.run_id ?? null} onSelect={(id) => openRun(id).catch((e) => setError(e.message))} />
      </section>

      {run && (
        <>
          <div className="disclaimer">
            <strong>Hypothetical result.</strong> {run.disclaimer} Fills: next-bar open with adverse slippage; stop
            assumed before target when both are inside one candle; funding charged as a cost on both sides.
          </div>

          <section className="panel">
            <div className="panel-head">
              <h2>
                {run.strategy_id}:{run.strategy_version} · {run.inst_id} {run.timeframe} · {fmtDate(run.start_ts)} →{" "}
                {fmtDate(run.end_ts)}
              </h2>
              <a className="button" href={api.tradesCsvUrl(run.run_id)}>
                Export trades CSV
              </a>
            </div>
            <div className="provenance mono">
              run {run.run_id} · code {run.code_revision} · engine {run.engine_version} · params #
              {run.params_hash.slice(0, 10)} · data #{run.data_fingerprint.slice(0, 10)} · capital{" "}
              {fmtNum(run.config.initial_capital, 0)} · fee {run.config.costs.taker_fee_rate} · slip{" "}
              {run.config.costs.slippage_bps}bps · risk {run.config.risk.risk_per_trade_pct}%
            </div>
            {run.notes.length > 0 && (
              <ul className="notes">
                {run.notes.map((n) => (
                  <li key={n}>{n}</li>
                ))}
              </ul>
            )}
            <MetricsGrid m={run.metrics} />
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>Price & trades {focus && <span className="dim">— trade #{focus.trade_id}</span>}</h2>
              {focus && <button onClick={() => setFocus(null)}>Clear focus</button>}
            </div>
            <PriceChart candles={candles} trades={run.trades} focusTrade={focus} barMs={TF_MS[run.timeframe]} />
          </section>

          <section className="panel">
            <h2>Equity & drawdown (mark-to-market)</h2>
            <EquityChart run={run} />
          </section>

          <div className="grid-2">
            <section className="panel">
              <h2>Trades ({run.trades.length}) — click to inspect on chart</h2>
              <TradeTable trades={run.trades} selected={focus?.trade_id ?? null} onSelect={setFocus} />
            </section>
            <section className="panel">
              <h2>Monthly performance</h2>
              <MonthlyTable rows={run.metrics.monthly} />
              <h3>Exit reasons</h3>
              <p className="mono">
                {Object.entries(run.metrics.exit_reasons)
                  .map(([k, v]) => `${k}: ${v}`)
                  .join(" · ") || "—"}
              </p>
              <p className="fine">
                Test period ≈ {Math.round((run.end_ts - run.start_ts) / DAY_MS)} days. Few trades or a single market
                regime make every metric here statistically fragile.
              </p>
            </section>
          </div>
        </>
      )}
    </div>
  );
}
