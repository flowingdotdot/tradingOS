// Chart Workspace — explore the BTC chart with the strategy's own overlay and the backtest's trades.
// Data is loaded one window at a time; the sealed OOS period is never requested or shown.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type ChartWindow, type RunSummary, type RunTrades, type StrategyInfo, type ViewItem } from "../api";
import { holdingTime, tradeItems } from "../components/tradeItems";
import { WorkspaceChart, type ViewRequest } from "../components/WorkspaceChart";
import { TF_MS } from "../components/viewOverlay";
import { fmtDate, fmtNum, fmtSigned, fmtTs } from "../format";

export interface WorkspacePreset {
  run_id: string;
  trade_id: number;
  strategy_id: string;
  version: string;
  nonce: number;
}

const TFS = ["5m", "15m", "4H"];
const SPAN_BARS: Record<string, number> = { "5m": 1500, "15m": 1500, "4H": 900 };
const INST = "BTC-USDT-SWAP";

// Layer groups of the workspace -> layer ids delivered by the strategy view (matched by `group`)
// plus the two trade layers drawn from the backtest's fills.
const GROUPS: { id: string; label: string }[] = [
  { id: "structure", label: "시장 구조 (스윙)" },
  { id: "bos_mss", label: "BOS / MSS" },
  { id: "fvg", label: "FVG" },
  { id: "ote", label: "OTE / Dealing Range" },
  { id: "htf_bias", label: "상위 TF 바이어스" },
  { id: "signals", label: "진입 신호" },
  { id: "fills", label: "주문 / 체결 (백테스트)" },
  { id: "sltp", label: "손절 / 목표" },
];
const TRADE_LAYERS = [
  { id: "fills", label: "체결·청산 (백테스트)", default_on: true, group: "fills" },
  { id: "trade_sltp", label: "거래의 손절 / 목표", default_on: true, group: "sltp" },
];

export function ChartWorkspace({ preset }: { preset: WorkspacePreset | null }) {
  const [catalog, setCatalog] = useState<StrategyInfo[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [sid, setSid] = useState<string>("");
  const [version, setVersion] = useState<string>("");
  const [runId, setRunId] = useState<string>("");
  const [runTrades, setRunTrades] = useState<RunTrades | null>(null);
  const [tradeId, setTradeId] = useState<number | null>(null);
  const [tf, setTf] = useState("15m");
  const [data, setData] = useState<ChartWindow | null>(null);
  const [viewReq, setViewReq] = useState<ViewRequest | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [layerOverride, setLayerOverride] = useState<Record<string, boolean>>({}); // user choices; display only
  const [jump, setJump] = useState("");
  const [full, setFull] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const visible = useRef<{ from: number; to: number } | null>(null);
  const reqSeq = useRef(0);
  const presetNonce = useRef(0);

  const strategy = catalog.find((s) => s.strategy_id === sid) ?? null;
  const versions = (strategy?.configs ?? []).filter((c) => c.integrity === "ok");
  const overlayOk = !!strategy && strategy.data.base_timeframes.includes("5m");

  // ------------------------------------------------------------------ loading
  const load = useCallback(async (start: number, end: number, req: ViewRequest | null, opts?: { tf?: string; sid?: string; version?: string }) => {
    const seq = ++reqSeq.current;
    setBusy(true);
    setError(null);
    try {
      const useSid = opts?.sid ?? sid;
      const useVer = opts?.version ?? version;
      const d = await api.chartWindow({
        inst_id: INST, timeframe: opts?.tf ?? tf, start_ts: Math.floor(start), end_ts: Math.floor(end),
        strategy_id: useSid || null, strategy_version: useSid ? useVer : null,
      });
      if (seq !== reqSeq.current) return; // a newer request superseded this one
      setData(d);
      if (req) setViewReq(req);
      setNotice(d.clipped_to_allowed_segment
        ? "요청한 구간이 봉인된 OOS 기간 또는 저장된 데이터 범위를 벗어나, 허용된 구간까지만 표시합니다."
        : null);
    } catch (e) {
      if (seq === reqSeq.current) setError((e as Error).message);
    } finally {
      if (seq === reqSeq.current) setBusy(false);
    }
  }, [sid, version, tf]);

  // initial: catalog, runs, default strategy (the first multi-timeframe plugin), latest allowed data
  useEffect(() => {
    (async () => {
      try {
        const [cat, rs, segs] = await Promise.all([api.strategies(), api.runs(), api.chartSegments()]);
        setCatalog(cat);
        setRuns(rs);
        const def = cat.find((s) => s.data.derived_timeframes.length > 0 && s.data.base_timeframes.includes("5m")) ?? null;
        const ver = def?.configs.find((c) => c.integrity === "ok")?.version ?? "";
        if (def) {
          setSid(def.strategy_id);
          setVersion(ver);
        }
        const last = segs.segments[segs.segments.length - 1];
        if (!last) {
          setError("저장된 5m 데이터가 없습니다. 먼저 데이터를 수집하세요 (tradingos collect --tf 5m).");
          return;
        }
        if (presetNonce.current === 0)
          await load(last.end_ts - SPAN_BARS["15m"] * TF_MS["15m"], last.end_ts, null,
            { tf: "15m", sid: def?.strategy_id ?? "", version: ver });
      } catch (e) {
        setError((e as Error).message);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // runs of the selected strategy version
  const myRuns = useMemo(
    () => runs.filter((r) => r.strategy_id === sid && r.strategy_version === version && r.summary.total_trades > 0),
    [runs, sid, version],
  );
  useEffect(() => {
    if (!myRuns.some((r) => r.run_id === runId)) setRunId(myRuns[0]?.run_id ?? "");
  }, [myRuns, runId]);
  useEffect(() => {
    if (!runId) {
      setRunTrades(null);
      return;
    }
    api.runTrades(runId).then(setRunTrades).catch((e) => setError(e.message));
  }, [runId]);

  const trades = runTrades?.trades ?? [];
  const selected = trades.find((t) => t.trade_id === tradeId) ?? null;

  // ------------------------------------------------------------------ navigation
  const span = () => SPAN_BARS[tf] * TF_MS[tf];
  const segOf = (t: number) => data?.allowed_segments.find((s) => t >= s.start_ts && t < s.end_ts) ?? null;

  function goTime(center: number, useTf = tf, extra?: { sid?: string; version?: string }) {
    const ms = TF_MS[useTf];
    const half = (SPAN_BARS[useTf] * ms) / 2;
    const seg = segOf(center);
    if (data && !seg) {
      setNotice(`${fmtDate(center)}은(는) 봉인된 OOS 기간이거나 데이터 범위 밖이라 표시할 수 없습니다.`);
      return;
    }
    let start = center - half;
    let end = center + half;
    if (seg) {
      if (end > seg.end_ts) { end = seg.end_ts; start = end - 2 * half; }
      if (start < seg.start_ts) { start = seg.start_ts; end = Math.min(seg.end_ts, start + 2 * half); }
    }
    void load(start, end, { from: center - 110 * ms, to: center + 110 * ms, key: Date.now() }, { tf: useTf, ...extra });
  }

  function goTrade(id: number, list = trades, extra?: { sid?: string; version?: string }) {
    const t = list.find((x) => x.trade_id === id);
    if (!t) return;
    setTradeId(id);
    const ms = TF_MS[tf];
    let start = t.entry_ts - 260 * ms;
    let end = Math.max(t.exit_ts + 160 * ms, start + 420 * ms);
    if (end - start > 2400 * ms) end = start + 2400 * ms;
    const seg = segOf(t.entry_ts);
    if (seg) {
      start = Math.max(start, seg.start_ts);
      end = Math.min(end, seg.end_ts);
    }
    void load(start, end, { from: t.entry_ts - 70 * ms, to: Math.min(t.exit_ts, t.entry_ts + 300 * ms) + 45 * ms, key: Date.now() }, extra);
  }

  function stepTrade(dir: -1 | 1) {
    if (!trades.length) return;
    const i = trades.findIndex((t) => t.trade_id === tradeId);
    const next = i < 0 ? (dir === 1 ? 0 : trades.length - 1) : Math.min(trades.length - 1, Math.max(0, i + dir));
    goTrade(trades[next].trade_id);
  }

  function onEdge(side: "left" | "right", vis: { from: number; to: number } | null) {
    if (!data || busy) return;
    const seg = segOf((data.start_ts + data.end_ts) / 2);
    if (!seg) return;
    const shift = span() / 2;
    let start = side === "left" ? data.start_ts - shift : data.start_ts + shift;
    let end = start + span();
    if (start < seg.start_ts) { start = seg.start_ts; end = Math.min(seg.end_ts, start + span()); }
    if (end > seg.end_ts) { end = seg.end_ts; start = Math.max(seg.start_ts, end - span()); }
    if (Math.abs(start - data.start_ts) < TF_MS[tf] && Math.abs(end - data.end_ts) < TF_MS[tf]) {
      setNotice(side === "left"
        ? `${seg.name} 구간의 시작(${fmtDate(seg.start_ts)})입니다. 그 이전은 ${seg.name === "OBSERVED" ? "봉인된 OOS 기간" : "데이터가 없는 기간"}이라 표시하지 않습니다.`
        : `${seg.name} 구간의 끝(${fmtDate(seg.end_ts)})입니다.${seg.name === "IS" ? " 그 이후는 봉인된 OOS 기간입니다." : ""}`);
      return;
    }
    void load(start, end, vis ? { ...vis, key: Date.now() } : null);
  }

  function changeTf(next: string) {
    const center = visible.current ? (visible.current.from + visible.current.to) / 2 : data ? (data.start_ts + data.end_ts) / 2 : Date.now();
    setTf(next);
    goTime(center, next);
  }

  function changeStrategy(nextSid: string, nextVer?: string) {
    const s = catalog.find((x) => x.strategy_id === nextSid);
    const ver = nextVer ?? s?.configs.find((c) => c.integrity === "ok")?.version ?? "";
    setSid(nextSid);
    setVersion(ver);
    setTradeId(null);
    if (data) void load(data.start_ts, data.end_ts, visible.current ? { ...visible.current, key: Date.now() } : null, { sid: nextSid, version: ver });
  }

  // opened from Backtest Lab on one trade
  useEffect(() => {
    if (!preset || preset.nonce === presetNonce.current || !catalog.length) return;
    presetNonce.current = preset.nonce;
    setSid(preset.strategy_id);
    setVersion(preset.version);
    setRunId(preset.run_id);
    api.runTrades(preset.run_id)
      .then((rt) => {
        setRunTrades(rt);
        goTrade(preset.trade_id, rt.trades, { sid: preset.strategy_id, version: preset.version });
      })
      .catch((e) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preset, catalog]);

  // ------------------------------------------------------------------ layers & trade items
  const allLayers = useMemo(() => [...(data?.view?.layers ?? []), ...(runTrades ? TRADE_LAYERS : [])], [data, runTrades]);
  const layers = useMemo(
    () => new Set(allLayers.filter((l) => layerOverride[l.id] ?? l.default_on).map((l) => l.id)),
    [allLayers, layerOverride],
  );
  const isOn = (id: string) => layers.has(id);
  const toggleLayer = (id: string) => setLayerOverride((cur) => ({ ...cur, [id]: !isOn(id) }));
  const toggleGroup = (g: string) => {
    const members = allLayers.filter((l) => l.group === g);
    const next = !members.some((l) => isOn(l.id)); // any on -> all off; all off -> all on
    setLayerOverride((cur) => ({ ...cur, ...Object.fromEntries(members.map((l) => [l.id, next])) }));
  };

  const extraItems = useMemo<ViewItem[]>(() => {
    if (!data || !trades.length) return [];
    const ms = TF_MS[tf];
    return trades
      .filter((t) => t.exit_ts >= data.start_ts && t.entry_ts < data.end_ts)
      .flatMap((t) => tradeItems(t, ms, t.trade_id === tradeId));
  }, [data, trades, tf, tradeId]);

  // ------------------------------------------------------------------ fullscreen
  useEffect(() => {
    const h = () => { if (!document.fullscreenElement) setFull(false); };
    document.addEventListener("fullscreenchange", h);
    return () => document.removeEventListener("fullscreenchange", h);
  }, []);
  function toggleFull() {
    const next = !full;
    setFull(next);
    if (next) wrap.current?.requestFullscreen?.().catch(() => undefined);
    else if (document.fullscreenElement) void document.exitFullscreen();
  }

  const warmupNote = data && data.strategy && data.signals_valid_from_ts > data.start_ts
    ? `이 구간의 ${fmtTs(data.signals_valid_from_ts)} UTC 이전은 전략 워밍업 구간입니다 (봉인된 OOS 데이터를 워밍업에 쓰지 않음).` : null;
  const tradesInWindow = data ? trades.filter((t) => t.exit_ts >= data.start_ts && t.entry_ts < data.end_ts).length : 0;

  return (
    <div className={`workspace ${full ? "ws-full" : ""}`} ref={wrap}>
      <div className="ws-top">
        <strong className="ws-title">{INST}</strong>
        <div className="seg">
          {TFS.map((t) => (
            <button key={t} className={t === tf ? "active" : ""} onClick={() => changeTf(t)} disabled={busy}>{t}</button>
          ))}
        </div>
        <label>
          전략
          <select value={sid} onChange={(e) => changeStrategy(e.target.value)}>
            <option value="">(오버레이 없음)</option>
            {catalog.filter((s) => s.data.base_timeframes.includes("5m")).map((s) => (
              <option key={s.strategy_id} value={s.strategy_id}>{s.name}</option>
            ))}
          </select>
        </label>
        {sid && (
          <label>
            설정 버전
            <select value={version} onChange={(e) => changeStrategy(sid, e.target.value)}>
              {versions.map((c) => <option key={c.version} value={c.version}>{c.version} [{c.status}]</option>)}
            </select>
          </label>
        )}
        {sid && (
          <label>
            백테스트 실행 (거래 표시)
            <select value={runId} onChange={(e) => { setRunId(e.target.value); setTradeId(null); }}>
              <option value="">(없음)</option>
              {myRuns.map((r) => (
                <option key={r.run_id} value={r.run_id}>
                  {fmtDate(r.start_ts)}~{fmtDate(r.end_ts)} · 거래 {r.summary.total_trades}건 · {r.run_id.slice(0, 15)}
                </option>
              ))}
            </select>
          </label>
        )}
        <label>
          날짜로 이동 (UTC)
          <span className="row-inline">
            <input type="date" value={jump} onChange={(e) => setJump(e.target.value)} />
            <button disabled={!jump || busy} onClick={() => goTime(Date.parse(`${jump}T12:00:00Z`))}>이동</button>
          </span>
        </label>
        <div className="seg">
          {data?.allowed_segments.map((s) => (
            <button key={s.name} disabled={busy} title={`${fmtDate(s.start_ts)} ~ ${fmtDate(s.end_ts)}`}
              onClick={() => goTime(s.end_ts - (SPAN_BARS[tf] * TF_MS[tf]) / 2)}>
              {s.name === "IS" ? "IS 끝으로" : s.name === "OBSERVED" ? "최신(관찰 구간)" : "최신"}
            </button>
          ))}
        </div>
        <button onClick={toggleFull}>{full ? "전체 화면 종료" : "전체 화면"}</button>
        {busy && <span className="dim">불러오는 중…</span>}
      </div>

      {error && <div className="alert error">{error}</div>}
      {notice && <div className="alert info">{notice}</div>}
      {sid && !overlayOk && <div className="alert info">이 전략은 5m 기준 데이터로 실행되지 않아 오버레이를 표시할 수 없습니다.</div>}

      <div className="ws-body">
        <div className="ws-main">
          {data ? (
            <WorkspaceChart candles={data.candles} view={data.view} extraItems={extraItems} tf={tf} layers={layers}
              viewRequest={viewReq} busy={busy} onEdge={onEdge} onVisibleChange={(v) => { visible.current = v; }} />
          ) : (
            <div className="ws-empty">{error ? "차트를 불러오지 못했습니다." : "차트를 불러오는 중…"}</div>
          )}
          <div className="ws-status">
            {data && (
              <>
                <span>불러온 구간: {fmtTs(data.start_ts)} ~ {fmtTs(data.end_ts)} UTC · {data.candles.ts.length}봉 · 구간 {data.segment}</span>
                {data.strategy && <span> · 오버레이: {data.strategy.name} {data.strategy.strategy_version} (설정 #{data.strategy.config_hash.slice(0, 8)})</span>}
                {runTrades && <span> · 이 구간의 거래 {tradesInWindow}건</span>}
                {warmupNote && <div className="warn-text">{warmupNote}</div>}
              </>
            )}
            <div className="fine">
              마우스 휠: 확대·축소 · 드래그: 이동 · 구간 끝에 닿으면 이웃 구간을 자동으로 불러옵니다. 구조 표시는 발생 위치에 그려지며, 전략은 인식 시각 이후에만 사용합니다.
              가상의 과거 시뮬레이션이며 미래 성과를 보장하지 않습니다. 차트: TradingView Lightweight Charts™ (https://www.tradingview.com/)
            </div>
          </div>
        </div>

        <aside className="ws-side">
          <h3>레이어 (표시만 변경, 전략 판단에는 영향 없음)</h3>
          <div className="ws-layers">
            {GROUPS.filter((g) => allLayers.some((l) => l.group === g.id)).map((g) => {
              const members = allLayers.filter((l) => l.group === g.id);
              const on = members.filter((l) => isOn(l.id)).length;
              return (
                <div key={g.id} className="ws-group">
                  <label className="check">
                    <input type="checkbox" checked={on > 0} onChange={() => toggleGroup(g.id)} />
                    <b>{g.label}</b>
                    <span className="dim">{on}/{members.length}</span>
                  </label>
                  {members.length > 1 && members.map((l) => (
                    <label key={l.id} className="check sub">
                      <input type="checkbox" checked={isOn(l.id)} onChange={() => toggleLayer(l.id)} />
                      {l.label}
                    </label>
                  ))}
                </div>
              );
            })}
            {!allLayers.length && <p className="dim fine">전략을 선택하면 오버레이 레이어가 나타납니다.</p>}
          </div>

          <h3>거래 {trades.length ? `(${trades.length}건)` : ""}</h3>
          {runTrades ? (
            <>
              <div className="row-inline">
                <button onClick={() => stepTrade(-1)} disabled={busy || !trades.length}>◀ 이전 거래</button>
                <button onClick={() => stepTrade(1)} disabled={busy || !trades.length}>다음 거래 ▶</button>
              </div>
              {selected && (
                <div className="ws-trade">
                  <div><b>#{selected.trade_id} {selected.side === 1 ? "롱" : "숏"}</b> · {selected.entry_type === "limit" ? "지정가" : "시장가"} · <span className={selected.net_pnl > 0 ? "pos" : "neg"}>{fmtSigned(selected.r_multiple)}R ({fmtSigned(selected.net_pnl)} USDT)</span></div>
                  <table>
                    <tbody>
                      <tr><td>신호 봉</td><td>{fmtTs(selected.signal_ts)}</td></tr>
                      <tr><td>체결</td><td>{fmtTs(selected.entry_ts)} @ {fmtNum(selected.entry_price, 1)}</td></tr>
                      <tr><td>손절 / 목표</td><td>{fmtNum(selected.stop, 1)} / {selected.target == null ? "—" : fmtNum(selected.target, 1)}</td></tr>
                      <tr><td>청산</td><td>{fmtTs(selected.exit_ts)} @ {fmtNum(selected.exit_price, 1)} ({selected.exit_reason})</td></tr>
                      <tr><td>보유 시간</td><td>{holdingTime(selected)} ({selected.bars_held}봉)</td></tr>
                      <tr><td>계획 순손익비</td><td>{fmtNum(selected.planned_net_rr)}</td></tr>
                      <tr><td>수량 / 비용</td><td>{selected.qty} BTC / {fmtNum(selected.fees + selected.funding + selected.slippage_cost)} USDT</td></tr>
                    </tbody>
                  </table>
                  <div className="fine">{selected.entry_reason}</div>
                </div>
              )}
              <div className="table-wrap ws-tradelist">
                <table>
                  <thead><tr><th>#</th><th>체결 (UTC)</th><th>보유</th><th>청산</th><th className="r">R</th></tr></thead>
                  <tbody>
                    {trades.map((t) => (
                      <tr key={t.trade_id} className={t.trade_id === tradeId ? "sel" : ""} onClick={() => goTrade(t.trade_id)}>
                        <td>{t.trade_id}</td>
                        <td>{fmtTs(t.entry_ts)}</td>
                        <td>{holdingTime(t)}</td>
                        <td>{t.exit_reason}</td>
                        <td className={`r ${t.net_pnl > 0 ? "pos" : "neg"}`}>{fmtSigned(t.r_multiple)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <p className="dim fine">선택한 전략 버전의 백테스트 실행을 고르면 체결·손절·목표·청산이 차트에 표시됩니다. Backtest Lab의 거래 목록에서 거래를 클릭해도 이 화면으로 이동합니다.</p>
          )}
        </aside>
      </div>
    </div>
  );
}
