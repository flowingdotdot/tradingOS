import { useEffect, useMemo, useState } from "react";
import { api, type ResearchResult, type StrategyInfo } from "../api";
import { ResearchChart } from "../components/ResearchChart";
import { LAYERS, TF_MS, type LayerId } from "../components/researchOverlays";
import { DAY_MS, fmtDate, fmtNum, fmtTs } from "../format";

const DEFAULT_LAYERS: LayerId[] = ["swings", "structure", "fvg_bear", "dealing", "ote", "zones", "signals", "sltp", "bias"];
type Tab = "setups" | "structure" | "swings" | "fvgs";

const lag = (occ: number, known: number, barMs: number) => `${Math.round((known - occ) / barMs)} bars`;

export function StrategyLab() {
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [versionKey, setVersionKey] = useState("ict_bearish_ote:v1-mss-close");
  const [start, setStart] = useState(fmtDate(Date.now() - 14 * DAY_MS));
  const [end, setEnd] = useState(fmtDate(Date.now() + DAY_MS));
  const [data, setData] = useState<ResearchResult | null>(null);
  const [tf, setTf] = useState("15m");
  const [layers, setLayers] = useState<Set<LayerId>>(new Set(DEFAULT_LAYERS));
  const [replay, setReplay] = useState(false);
  const [cutoff, setCutoff] = useState<number | null>(null);
  const [focus, setFocus] = useState<{ from: number; to: number } | null>(null);
  const [tab, setTab] = useState<Tab>("setups");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.strategies().then(setStrategies).catch((e) => setError(e.message));
  }, []);

  const versions = useMemo(
    () =>
      strategies
        .filter((s) => s.has_research_view)
        .flatMap((s) => s.versions.map((v) => ({ key: `${v.strategy_id}:${v.version}`, v }))),
    [strategies],
  );
  const selected = versions.find((x) => x.key === versionKey)?.v;

  async function analyze() {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.research(
        selected.strategy_id,
        selected.version,
        Date.parse(`${start}T00:00:00Z`),
        Math.min(Date.parse(`${end}T00:00:00Z`), Date.now()),
      );
      setData(r);
      setCutoff(null);
      setFocus(null);
      if (!r.timeframes[tf]) setTf(r.roles.impulse);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const barMs = TF_MS[tf];
  const tfd = data?.timeframes[tf];

  function onBarClick(barOpenTs: number) {
    if (replay) setCutoff(barOpenTs + barMs); // knowledge at the CLOSE of the clicked bar
  }

  function step(n: number) {
    if (!data || !tfd) return;
    const ts = tfd.candles.ts;
    const cur = cutoff ?? ts[ts.length - 1] + barMs;
    const next = cur + n * barMs;
    setCutoff(Math.max(ts[0] + barMs, Math.min(next, ts[ts.length - 1] + barMs)));
  }

  const toggle = (l: LayerId) =>
    setLayers((prev) => {
      const next = new Set(prev);
      if (next.has(l)) next.delete(l);
      else next.add(l);
      return next;
    });

  const knownOk = (k: number | null) => k != null && (cutoff == null || k <= cutoff);

  return (
    <div className="page">
      <div className="page-head">
        <h1>Strategy Lab</h1>
        <span className="env-chip backtest">RESEARCH · no orders</span>
      </div>
      {error && <div className="alert error">{error}</div>}

      <section className="panel">
        <div className="toolbar">
          <label>
            Strategy version
            <select value={versionKey} onChange={(e) => setVersionKey(e.target.value)}>
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
            End (UTC, excl.)
            <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
          </label>
          <button className="primary" onClick={analyze} disabled={busy || !selected}>
            {busy ? "Analyzing…" : "Analyze"}
          </button>
          {data && (
            <>
              <label>
                Chart timeframe
                <select value={tf} onChange={(e) => setTf(e.target.value)}>
                  {Object.keys(data.timeframes).map((k) => (
                    <option key={k} value={k}>
                      {k} ({k === data.roles.base ? "trigger" : k === data.roles.impulse ? "setup" : "bias"})
                    </option>
                  ))}
                </select>
              </label>
              <div className="replay">
                <label className="check">
                  <input
                    type="checkbox"
                    checked={replay}
                    onChange={(e) => {
                      setReplay(e.target.checked);
                      if (!e.target.checked) setCutoff(null);
                    }}
                  />
                  Knowledge replay
                </label>
                {replay && (
                  <>
                    <button onClick={() => step(-1)}>◀ bar</button>
                    <button onClick={() => step(1)}>bar ▶</button>
                    <span className="mono dim">
                      {cutoff ? `known at ${fmtTs(cutoff)} UTC` : "click a candle to set the moment"}
                    </span>
                  </>
                )}
              </div>
            </>
          )}
        </div>
        {selected && <p className="fine">{selected.description} · params #{selected.params_hash.slice(0, 10)}</p>}
        {data && (
          <div className="layers">
            {(Object.keys(LAYERS) as LayerId[]).map((l) => (
              <label key={l} className="check">
                <input type="checkbox" checked={layers.has(l)} onChange={() => toggle(l)} />
                {LAYERS[l]}
              </label>
            ))}
          </div>
        )}
      </section>

      {data && tfd && (
        <>
          <div className="disclaimer">
            Swings are <strong>drawn</strong> at the bar where they occurred, but an algorithm only{" "}
            <strong>knows</strong> them {String((data.params as { swing?: { k_right?: number } }).swing?.k_right ?? 3)} bars
            later (✓ markers). Use <em>Knowledge replay</em> to see exactly what was knowable at a given candle close.
            Research hypothesis — no profitability implied.
          </div>
          <section className="panel">
            <div className="panel-head">
              <h2>
                {data.strategy_id}:{data.strategy_version} · {tf} · {fmtDate(data.start_ts)} → {fmtDate(data.end_ts)}
              </h2>
              <span className="mono dim">
                setups {data.setups.length} · signals {data.signals.length} · code {data.code_revision} · data #
                {data.data_fingerprint.slice(0, 8)}
              </span>
            </div>
            <ResearchChart data={data} tf={tf} layers={layers} cutoff={cutoff} focus={focus} onBarClick={onBarClick} />
            <p className="fine">
              Setup outcomes:{" "}
              {Object.entries(data.setup_status_counts)
                .map(([k, v]) => `${k} ${v}`)
                .join(" · ") || "none"}
            </p>
          </section>

          <section className="panel">
            <div className="tabs">
              {(["setups", "structure", "swings", "fvgs"] as Tab[]).map((t) => (
                <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
                  {t === "setups" ? `Setups (${data.setups.length})` : t === "structure" ? `BOS / MSS / sweeps (${tfd.structure.length})` : t === "swings" ? `Swings (${tfd.swings.length})` : `FVGs (${tfd.fvgs.length})`}
                </button>
              ))}
              <span className="dim fine">{tab === "setups" ? "all timeframes" : `${tf} timeframe`} · times UTC · click a row to focus</span>
            </div>
            <div className="table-wrap tall">
              {tab === "setups" && (
                <table>
                  <thead>
                    <tr>
                      <th>#</th><th>Status</th><th>Impulse</th><th>A → B</th><th>Known (UTC)</th><th>OTE</th>
                      <th>Zones</th><th>Touched</th><th>Trigger</th><th className="r">Entry</th><th className="r">Stop</th>
                      <th className="r">Target</th><th className="r">R:R</th><th>Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.setups.map((s) => (
                      <tr key={s.id} className={knownOk(s.known_ts) ? "" : "faded"} onClick={() => setFocus({ from: s.a_ts, to: s.end_ts ?? s.known_ts })}>
                        <td>{s.id}</td>
                        <td className={s.status === "signal" ? "pos" : s.status.startsWith("rejected") || s.status === "invalidated" ? "neg" : ""}>{s.status}</td>
                        <td>{s.break_kind}</td>
                        <td className="mono">{fmtNum(s.a, 1)} → {fmtNum(s.b, 1)}</td>
                        <td>{fmtTs(s.known_ts)}</td>
                        <td className="mono">{fmtNum(s.ote_low, 1)}–{fmtNum(s.ote_high, 1)}</td>
                        <td>{s.zones.length}</td>
                        <td>{fmtTs(s.touched_ts)}</td>
                        <td>{fmtTs(s.trigger_ts)}</td>
                        <td className="r">{fmtNum(s.entry_ref, 1)}</td>
                        <td className="r">{fmtNum(s.stop, 1)}</td>
                        <td className="r">{fmtNum(s.target, 1)}</td>
                        <td className="r">{fmtNum(s.rr)}</td>
                        <td className="dim">{s.reason}{s.retest_outcome ? ` · retest(research): ${s.retest_outcome.outcome}` : ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {tab === "structure" && (
                <table>
                  <thead>
                    <tr><th>Kind</th><th>Dir</th><th>Occurred</th><th>Known</th><th>Prior trend</th><th className="r">Broken level</th><th>Level known</th><th>Displacement</th></tr>
                  </thead>
                  <tbody>
                    {tfd.structure.map((e, i) => (
                      <tr key={i} className={knownOk(e.known_ts) ? "" : "faded"} onClick={() => setFocus({ from: e.ref_ts, to: e.ts })}>
                        <td>{e.kind}</td>
                        <td className={e.direction < 0 ? "neg" : "pos"}>{e.direction < 0 ? "bear" : "bull"}</td>
                        <td>{fmtTs(e.ts)}</td>
                        <td>{fmtTs(e.known_ts)}</td>
                        <td>{e.prior_trend}</td>
                        <td className="r">{fmtNum(e.ref_price, 1)}</td>
                        <td>{fmtTs(e.ref_known_ts)}</td>
                        <td>{e.kind === "SWEEP" ? "—" : e.has_displacement ? "yes" : "no"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {tab === "swings" && (
                <table>
                  <thead>
                    <tr><th>Kind</th><th className="r">Price</th><th>Occurred (drawn here)</th><th>Known (usable from)</th><th>Recognition lag</th></tr>
                  </thead>
                  <tbody>
                    {tfd.swings.map((s, i) => (
                      <tr key={i} className={knownOk(s.known_ts) ? "" : "faded"} onClick={() => setFocus({ from: s.ts, to: s.known_ts })}>
                        <td className={s.kind === "high" ? "neg" : "pos"}>{s.kind}</td>
                        <td className="r">{fmtNum(s.price, 1)}</td>
                        <td>{fmtTs(s.ts)}</td>
                        <td>{fmtTs(s.known_ts)}</td>
                        <td>{lag(s.ts, s.known_ts, barMs)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {tab === "fvgs" && (
                <table>
                  <thead>
                    <tr><th>Dir</th><th className="r">Bottom</th><th className="r">Top</th><th>Occurred</th><th>Known</th><th>Touched</th><th>Filled</th><th>Invalidated</th></tr>
                  </thead>
                  <tbody>
                    {tfd.fvgs.map((f, i) => (
                      <tr key={i} className={knownOk(f.known_ts) ? "" : "faded"} onClick={() => setFocus({ from: f.first_ts, to: f.invalidated_ts ?? f.known_ts })}>
                        <td className={f.direction < 0 ? "neg" : "pos"}>{f.direction < 0 ? "bear" : "bull"}</td>
                        <td className="r">{fmtNum(f.bottom, 1)}</td>
                        <td className="r">{fmtNum(f.top, 1)}</td>
                        <td>{fmtTs(f.ts)}</td>
                        <td>{fmtTs(f.known_ts)}</td>
                        <td>{fmtTs(f.touched_ts)}</td>
                        <td>{fmtTs(f.filled_ts)}</td>
                        <td>{fmtTs(f.invalidated_ts)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </section>

          <section className="panel">
            <h2>Parameters (from YAML — edit the version file to experiment, then compare versions)</h2>
            <pre className="params mono">{JSON.stringify(data.params, null, 2)}</pre>
          </section>
        </>
      )}
    </div>
  );
}
