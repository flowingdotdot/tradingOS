import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  type InstanceDetail,
  type ResearchResult,
  type StrategyConfig,
  type StrategyInfo,
  type StrategyInstance,
  type ValidationResult,
  type ViewTable,
} from "../api";
import { ComponentToggles, ParamForm, setPath, type Params } from "../components/ParamForm";
import { ResearchChart } from "../components/ResearchChart";
import { TF_MS } from "../components/viewOverlay";
import { DAY_MS, fmtDate, fmtNum, fmtTs } from "../format";

export interface BacktestPreset {
  strategy_id: string;
  version: string;
  timeframe: string;
}

const statusTone = (s: unknown) => {
  const v = String(s);
  return v === "signal" ? "pos" : v.startsWith("rejected") || v === "invalidated" ? "neg" : "";
};

function GenericTable({ table, cutoff, onFocus }: {
  table: ViewTable;
  cutoff: number | null;
  onFocus: (r: { from: number; to: number }) => void;
}) {
  const cell = (kind: string, v: unknown) => {
    if (v === null || v === undefined || (typeof v === "number" && !Number.isFinite(v))) return "—";
    if (kind === "ts") return fmtTs(v as number);
    if (kind === "price") return fmtNum(v as number, 1);
    if (kind === "num") return typeof v === "number" && !Number.isInteger(v) ? fmtNum(v, 2) : String(v);
    return String(v);
  };
  return (
    <div className="table-wrap tall">
      <table>
        <thead>
          <tr>{table.columns.map((c) => <th key={c.key} className={c.kind === "price" || c.kind === "num" ? "r" : ""}>{c.label}</th>)}</tr>
        </thead>
        <tbody>
          {table.rows.map((r, i) => {
            const k = r[table.known_key] as number | undefined;
            const faded = cutoff != null && k != null && k > cutoff;
            return (
              <tr key={i} className={faded ? "faded" : ""} onClick={() => {
                const from = table.focus_from ? (r[table.focus_from] as number) : null;
                const to = table.focus_to ? ((r[table.focus_to] as number) ?? from) : from;
                if (from != null) onFocus({ from, to: to ?? from });
              }}>
                {table.columns.map((c) => (
                  <td key={c.key} className={`${c.kind === "price" || c.kind === "num" ? "r" : ""} ${c.kind === "status" ? statusTone(r[c.key]) : ""} ${c.kind === "text" && c.key === "reason" ? "dim" : ""}`}>
                    {cell(c.kind, r[c.key])}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function StrategyLab({ onBacktest }: { onBacktest: (p: BacktestPreset) => void }) {
  const [catalog, setCatalog] = useState<StrategyInfo[]>([]);
  const [sid, setSid] = useState<string | null>(null);
  const info = catalog.find((s) => s.strategy_id === sid) ?? null;
  const [baseVersion, setBaseVersion] = useState<string | null>(null);
  const [values, setValues] = useState<Params>({});
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [newVersion, setNewVersion] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // research
  const [start, setStart] = useState(fmtDate(Date.now() - 14 * DAY_MS));
  const [end, setEnd] = useState(fmtDate(Date.now() + DAY_MS));
  const [runTf, setRunTf] = useState<string>("");
  const [research, setResearch] = useState<ResearchResult | null>(null);
  const [chartTf, setChartTf] = useState("");
  const [layers, setLayers] = useState<Set<string>>(new Set());
  const [replay, setReplay] = useState(false);
  const [cutoff, setCutoff] = useState<number | null>(null);
  const [focus, setFocus] = useState<{ from: number; to: number } | null>(null);
  const [tableId, setTableId] = useState<string>("");
  const [busy, setBusy] = useState(false);

  // deployment
  const [instances, setInstances] = useState<StrategyInstance[]>([]);
  const [instance, setInstance] = useState<InstanceDetail | null>(null);
  const [capital, setCapital] = useState(1000);

  const reload = useCallback(async () => {
    const [cat, inst] = await Promise.all([api.strategies(), api.instances()]);
    setCatalog(cat);
    setInstances(inst);
    return cat;
  }, []);

  useEffect(() => {
    reload()
      .then((cat) => setSid((cur) => cur ?? cat[0]?.strategy_id ?? null))
      .catch((e) => setError(e.message));
  }, [reload]);

  const configs: StrategyConfig[] = info?.configs ?? [];
  const usable = configs.filter((c) => c.integrity === "ok");
  const base = usable.find((c) => c.version === baseVersion) ?? null;

  // selecting a strategy -> first config, its params, its default timeframe
  useEffect(() => {
    if (!info) return;
    const first = info.configs.find((c) => c.integrity === "ok");
    setBaseVersion(first?.version ?? null);
    setValues((first?.params as Params) ?? info.default_params);
    setRunTf(info.data.base_timeframes[0]);
    setResearch(null);
    setInstance(null);
    setNewVersion("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [info?.strategy_id]);

  function loadVersion(v: string) {
    const c = usable.find((x) => x.version === v);
    if (!c) return;
    setBaseVersion(v);
    setValues(c.params as Params);
    setNotice(null);
  }

  // live validation against the backend schema + component rules + strategy rules
  useEffect(() => {
    if (!info) return;
    const h = setTimeout(() => {
      api.validate(info.strategy_id, values).then(setValidation).catch((e) => setError(e.message));
    }, 250);
    return () => clearTimeout(h);
  }, [info, values]);

  const errorMap = useMemo(() => {
    const m: Record<string, string> = {};
    for (const e of validation?.errors ?? []) for (const p of e.path.split(",")) {
      const comp = info?.components.find((c) => c.id === p);
      m[comp?.toggle_path ?? p] = e.message;
    }
    return m;
  }, [validation, info]);

  const dirty = base ? JSON.stringify(validation?.resolved_params ?? values) !== JSON.stringify(base.params) : true;

  async function saveVersion() {
    if (!info) return;
    setError(null);
    try {
      const cfg = await api.saveConfig(info.strategy_id, {
        version: newVersion.trim(),
        params: values,
        description: newDesc,
        parent_version: baseVersion,
      });
      await reload();
      setBaseVersion(cfg.version);
      setNotice(`Saved ${info.strategy_id}:${cfg.version} (config #${cfg.config_hash.slice(0, 10)}). Versions are immutable.`);
      setNewVersion("");
      setNewDesc("");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function analyze(draft: boolean) {
    if (!info) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.research({
        strategy_id: info.strategy_id,
        strategy_version: draft ? null : baseVersion,
        draft_params: draft ? values : null,
        timeframe: runTf,
        start_ts: Date.parse(`${start}T00:00:00Z`),
        end_ts: Math.min(Date.parse(`${end}T00:00:00Z`), Date.now()),
      });
      setResearch(r);
      setChartTf(r.view.default_tf);
      setLayers(new Set(r.view.layers.filter((l) => l.default_on).map((l) => l.id)));
      setTableId(r.view.tables[0]?.id ?? "");
      setCutoff(null);
      setFocus(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function createInstance() {
    if (!info || !baseVersion) return;
    setError(null);
    try {
      const inst = await api.createInstance({ strategy_id: info.strategy_id, config_version: baseVersion,
        timeframe: runTf, environment: "DEMO", capital_allocation: capital });
      await reload();
      setInstance(await api.instance(inst.instance_id));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function toggleEntries(enabled: boolean) {
    if (!instance) return;
    setError(null);
    try {
      await api.setEntries(instance.instance_id, enabled, enabled ? "requested from Strategy Lab" : "stopped from Strategy Lab");
    } catch (e) {
      setError((e as Error).message);
    }
    setInstance(await api.instance(instance.instance_id));
    setInstances(await api.instances());
  }

  const barMs = TF_MS[chartTf] ?? 60_000;
  const candleTs = research?.view.timeframes[chartTf]?.ts ?? [];
  function step(n: number) {
    if (!candleTs.length) return;
    const cur = cutoff ?? candleTs[candleTs.length - 1] + barMs;
    setCutoff(Math.max(candleTs[0] + barMs, Math.min(cur + n * barMs, candleTs[candleTs.length - 1] + barMs)));
  }
  const hidden = new Set(info?.component_toggle_paths ?? []);
  const myInstances = instances.filter((i) => i.strategy_id === sid);
  const table = research?.view.tables.find((t) => t.id === tableId);

  return (
    <div className="page">
      <div className="page-head">
        <h1>Strategy Lab</h1>
        <span className="env-chip backtest">RESEARCH · no orders</span>
      </div>
      {error && <div className="alert error">{error}</div>}
      {notice && <div className="alert info">{notice}</div>}

      <section className="panel">
        <h2>Strategies ({catalog.length}) — discovered from installed plugins</h2>
        <div className="catalog">
          {catalog.map((s) => (
            <button key={s.strategy_id} className={`card ${s.strategy_id === sid ? "active" : ""}`} onClick={() => setSid(s.strategy_id)}>
              <div className="card-title">{s.name}</div>
              <div className="card-meta">
                <span className="tag">{s.category}</span>
                <span className={`tag ${s.maturity === "validated" ? "" : "warn"}`}>{s.maturity}</span>
                <span className="mono dim">v{s.definition_version}</span>
              </div>
              <div className="card-desc">{s.description}</div>
              <div className="fine">runs on {s.data.base_timeframes.join(", ")}{s.data.derived_timeframes.length ? ` · builds ${s.data.derived_timeframes.join(", ")}` : ""} · {s.configs.length} config(s)</div>
            </button>
          ))}
        </div>
      </section>

      {info && (
        <div className="grid-lab">
          <section className="panel">
            <div className="panel-head">
              <h2>Configuration</h2>
              <label className="check"><input type="checkbox" checked={showAdvanced} onChange={(e) => setShowAdvanced(e.target.checked)} />advanced</label>
            </div>
            <div className="toolbar">
              <label>
                Base version
                <select value={baseVersion ?? ""} onChange={(e) => loadVersion(e.target.value)}>
                  {configs.map((c) => (
                    <option key={c.version} value={c.version} disabled={c.integrity !== "ok"}>
                      {c.version} [{c.status ?? "?"} · {c.source}{c.integrity !== "ok" ? ` · ${c.integrity.toUpperCase()}` : ""}]
                    </option>
                  ))}
                </select>
              </label>
              <button onClick={() => base && setValues(base.params as Params)} disabled={!dirty}>Reset</button>
              <button onClick={() => setNewVersion(`${baseVersion ?? "v"}-copy`)}>Clone as new version</button>
            </div>
            {base && (
              <p className="fine mono">
                {base.description || "—"} · config #{base.config_hash.slice(0, 10)} · params #{base.params_hash.slice(0, 10)} · def v{base.definition_version}
                {base.parent_version ? ` · parent ${base.parent_version}` : ""}
              </p>
            )}
            <div className={`validation ${validation?.ok ? "ok" : "bad"}`}>
              {validation?.ok
                ? `✓ valid · warm-up ${validation.warmup_bars} bars · params #${validation.params_hash?.slice(0, 10)}${dirty ? " · UNSAVED CHANGES" : ""}`
                : `✗ ${validation?.errors.length ?? 0} problem(s): ${validation?.errors.map((e) => `${e.path}: ${e.message}`).join(" | ")}`}
            </div>

            <h3>Components</h3>
            <ComponentToggles components={info.components} values={values} errors={errorMap}
              onChange={(p, v) => setValues((cur) => setPath(cur, p, v))} />
            {info.component_rules.map((r) => <p key={r.message} className="fine">Rule: {r.message}</p>)}

            <h3>Parameters</h3>
            <ParamForm specs={info.param_specs} values={values} errors={errorMap} hiddenPaths={hidden}
              showAdvanced={showAdvanced} onChange={(p, v) => setValues((cur) => setPath(cur, p, v))} />

            <div className="save-row">
              <label>
                New version name
                <input value={newVersion} placeholder="e.g. v2-slower" onChange={(e) => setNewVersion(e.target.value)} />
              </label>
              <label className="grow">
                Description / hypothesis
                <input value={newDesc} onChange={(e) => setNewDesc(e.target.value)} />
              </label>
              <button className="primary" disabled={!validation?.ok || !newVersion.trim()} onClick={saveVersion}>
                Save as new version
              </button>
            </div>
            <p className="fine">Existing versions are never overwritten. Backtests and instances always reference a saved version.</p>
          </section>

          <section className="panel">
            <h2>Data requirements & deployment</h2>
            <div className="kv">
              <span>Instruments</span><span className="mono">{info.data.instruments.join(", ")}</span>
              <span>Run timeframe</span>
              <select value={runTf} onChange={(e) => setRunTf(e.target.value)} disabled={info.data.base_timeframes.length < 2}>
                {info.data.base_timeframes.map((t) => <option key={t}>{t}</option>)}
              </select>
              <span>Fields</span><span className="mono">{info.data.fields.join(", ")}</span>
              <span>Warm-up</span><span>{validation?.warmup_bars ?? info.default_warmup_bars} bars</span>
              {info.data.notes && (<><span>Notes</span><span className="fine">{info.data.notes}</span></>)}
              {info.docs && (<><span>Docs</span><span className="mono">{info.docs}</span></>)}
            </div>
            <div className="row wrap">
              <button disabled={!baseVersion || dirty} onClick={() => baseVersion && onBacktest({ strategy_id: info.strategy_id, version: baseVersion, timeframe: runTf })}>
                Backtest {baseVersion} →
              </button>
              {dirty && <span className="fine">save the edits as a version to backtest them</span>}
            </div>

            <h3>DEMO deployment preparation</h3>
            <div className="row wrap">
              <label>Capital (USDT)<input type="number" value={capital} onChange={(e) => setCapital(Number(e.target.value))} /></label>
              <button disabled={!baseVersion || dirty} onClick={createInstance}>Create DEMO instance from {baseVersion}</button>
            </div>
            <p className="fine">An instance = saved version + instrument + timeframe + environment + limits. Entries start OFF. LIVE cannot be created.</p>
            {myInstances.length > 0 && (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Instance</th><th>Version</th><th>TF</th><th>Env</th><th>Entries</th></tr></thead>
                  <tbody>
                    {myInstances.map((i) => (
                      <tr key={i.instance_id} className={instance?.instance_id === i.instance_id ? "sel" : ""}
                        onClick={() => api.instance(i.instance_id).then(setInstance).catch((e) => setError(e.message))}>
                        <td className="mono">{i.instance_id}</td><td>{i.config_version}</td><td>{i.timeframe}</td>
                        <td>{i.environment}</td><td className={i.entries_enabled ? "pos" : "dim"}>{i.entries_enabled ? "ON" : "OFF"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {instance && (
              <div className="instance">
                <div className="row wrap">
                  <strong className="mono">{instance.instance_id}</strong>
                  <button onClick={() => toggleEntries(true)} disabled={instance.entries_enabled}>Entries ON</button>
                  <button onClick={() => toggleEntries(false)} disabled={!instance.entries_enabled}>Entries OFF</button>
                </div>
                <p className="fine">Status: {instance.runtime_status}. OFF stops new entries only; open positions stay protected.</p>
                <ul className="checklist">
                  {instance.readiness.map((c) => (
                    <li key={c.check} className={c.ok ? "pos" : "neg"}>{c.ok ? "✓" : "✗"} {c.check} <span className="dim">— {c.detail}</span></li>
                  ))}
                </ul>
                <h3>Event log</h3>
                <ul className="events mono">
                  {instance.events.map((e, k) => <li key={k}>{e.ts} {e.kind} {JSON.stringify(e.detail)}</li>)}
                </ul>
              </div>
            )}
          </section>
        </div>
      )}

      {info && (
        <section className="panel">
          <div className="toolbar">
            <label>Start (UTC)<input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
            <label>End (UTC, excl.)<input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
            <button className="primary" disabled={busy || !baseVersion} onClick={() => analyze(false)}>
              {busy ? "Analyzing…" : `Analyze ${baseVersion ?? ""}`}
            </button>
            <button disabled={busy || !validation?.ok} onClick={() => analyze(true)}>Analyze unsaved draft</button>
            {research && (
              <>
                <label>
                  Chart timeframe
                  <select value={chartTf} onChange={(e) => setChartTf(e.target.value)}>
                    {Object.keys(research.view.timeframes).map((k) => (
                      <option key={k} value={k}>{k}{research.view.tf_roles[k] ? ` (${research.view.tf_roles[k]})` : ""}</option>
                    ))}
                  </select>
                </label>
                <div className="replay">
                  <label className="check">
                    <input type="checkbox" checked={replay} onChange={(e) => { setReplay(e.target.checked); if (!e.target.checked) setCutoff(null); }} />
                    Knowledge replay
                  </label>
                  {replay && (
                    <>
                      <button onClick={() => step(-1)}>◀ bar</button>
                      <button onClick={() => step(1)}>bar ▶</button>
                      <span className="mono dim">{cutoff ? `known at ${fmtTs(cutoff)} UTC` : "click a candle"}</span>
                    </>
                  )}
                </div>
              </>
            )}
          </div>
          {research && (
            <>
              <div className="layers">
                {research.view.layers.map((l) => (
                  <label key={l.id} className="check">
                    <input type="checkbox" checked={layers.has(l.id)} onChange={() => setLayers((cur) => {
                      const n = new Set(cur);
                      if (n.has(l.id)) n.delete(l.id);
                      else n.add(l.id);
                      return n;
                    })} />
                    {l.label}
                  </label>
                ))}
              </div>
              <div className={`disclaimer ${research.draft ? "draft" : ""}`}>
                {research.draft ? <strong>UNSAVED DRAFT — exploration only, not reproducible. </strong> : null}
                {research.strategy_name} · {research.strategy_version} · params #{research.params_hash.slice(0, 10)} · code {research.code_revision} · data #{research.data_fingerprint.slice(0, 8)}.
                Structures are drawn where they occurred but only usable from their known time — use Knowledge replay. Research only; no profitability implied.
              </div>
              <ResearchChart view={research.view} viewKey={`${research.strategy_id}|${research.strategy_version}|${research.start_ts}|${research.end_ts}|${research.params_hash}`}
                tf={chartTf} layers={layers} cutoff={replay ? cutoff : null} focus={focus}
                onBarClick={(ts) => replay && setCutoff(ts + barMs)} />
              <p className="fine">{research.view.summary.map((s) => `${s.label}: ${String(s.value)}`).join(" · ")}</p>
              <div className="tabs">
                {research.view.tables.map((t) => (
                  <button key={t.id} className={t.id === tableId ? "active" : ""} onClick={() => setTableId(t.id)}>{t.label}</button>
                ))}
              </div>
              {table && <GenericTable table={table} cutoff={replay ? cutoff : null} onFocus={setFocus} />}
            </>
          )}
        </section>
      )}
    </div>
  );
}
