// Chart Workspace canvas: candles + volume, the strategy's overlay, trades.
// Zoom with the mouse wheel, pan by dragging; only the loaded window is rendered.
import { useEffect, useMemo, useRef } from "react";
import {
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import type { ChartCandles, ResearchView, ViewItem } from "../api";
import { baseChartOptions, readVar, toTime } from "./charts";
import { ShapesPrimitive } from "./ShapesPrimitive";
import { buildViewOverlay, TF_MS } from "./viewOverlay";

export interface ViewRequest {
  from: number; // ms
  to: number;   // ms
  key: number;
}

interface Props {
  candles: ChartCandles;
  view: ResearchView | null;
  extraItems: ViewItem[];
  tf: string;
  layers: Set<string>;
  viewRequest: ViewRequest | null;
  busy: boolean;
  onEdge: (side: "left" | "right", visible: { from: number; to: number } | null) => void;
  onVisibleChange: (visible: { from: number; to: number }) => void;
}

const EMPTY_VIEW: ResearchView = {
  default_tf: "", timeframes: {}, tf_roles: {}, layers: [], items: [], lines: [], bar_colors: [], tables: [], summary: [],
};
const utc = (ms: number) => new Date(ms).toISOString().slice(0, 16).replace("T", " ");
const num = (v: number, d = 1) => v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });

export function WorkspaceChart({ candles, view, extraItems, tf, layers, viewRequest, busy, onEdge, onVisibleChange }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const legend = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volume = useRef<ISeriesApi<"Histogram"> | null>(null);
  const markers = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const shapes = useRef(new ShapesPrimitive());
  const lineSeries = useRef<Map<string, ISeriesApi<"Line">>>(new Map());
  const state = useRef({ n: 0, suppressUntil: 0, busy: false, appliedKey: -1, firstData: true,
    byTime: new Map<number, number>(), candles });
  const cb = useRef({ onEdge, onVisibleChange });
  cb.current = { onEdge, onVisibleChange };
  state.current.busy = busy;
  state.current.candles = candles;

  const showLegend = (i: number) => {
    const c = state.current.candles;
    if (!legend.current || i < 0 || i >= c.ts.length) return;
    const up = c.close[i] >= c.open[i];
    const chg = ((c.close[i] - c.open[i]) / c.open[i]) * 100;
    legend.current.innerHTML =
      `<span class="lg-time">${utc(c.ts[i])} UTC</span>` +
      `<span>시 <b>${num(c.open[i])}</b></span><span>고 <b>${num(c.high[i])}</b></span>` +
      `<span>저 <b>${num(c.low[i])}</b></span><span>종 <b class="${up ? "pos" : "neg"}">${num(c.close[i])}</b></span>` +
      `<span class="${up ? "pos" : "neg"}">${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%</span>` +
      `<span>거래량 <b>${num(c.volume[i], 0)}</b></span>`;
  };

  useEffect(() => {
    if (!el.current) return;
    const c = createChart(el.current, {
      ...baseChartOptions(),
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
    });
    const s = c.addSeries(CandlestickSeries, {
      upColor: readVar("--up"), downColor: readVar("--down"), wickUpColor: readVar("--up"),
      wickDownColor: readVar("--down"), borderVisible: false,
    });
    const v = c.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false }, 1);
    s.attachPrimitive(shapes.current);
    markers.current = createSeriesMarkers(s, []);
    chart.current = c;
    series.current = s;
    volume.current = v;

    c.subscribeCrosshairMove((p) => {
      const t = p.time as number | undefined;
      const i = t != null ? state.current.byTime.get(t) : undefined;
      showLegend(i ?? state.current.candles.ts.length - 1);
    });

    let timer: ReturnType<typeof setTimeout> | null = null;
    c.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (!range) return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        const st = state.current;
        const vr = c.timeScale().getVisibleRange();
        const vis = vr ? { from: (vr.from as number) * 1000, to: (vr.to as number) * 1000 } : null;
        if (vis) cb.current.onVisibleChange(vis);
        if (st.busy || performance.now() < st.suppressUntil || st.n === 0) return;
        if (range.from < 15) cb.current.onEdge("left", vis);
        else if (range.to > st.n - 1 + 15) cb.current.onEdge("right", vis);
      }, 350);
    });
    const ls = lineSeries.current;
    return () => {
      if (timer) clearTimeout(timer);
      c.remove();
      ls.clear();
      chart.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const overlay = useMemo(() => {
    const base = view ?? EMPTY_VIEW;
    const merged = { ...base, items: [...base.items, ...extraItems] };
    const last = candles.ts.length ? candles.ts[candles.ts.length - 1] : 0;
    return buildViewOverlay(merged, tf, layers, null, last);
  }, [view, extraItems, tf, layers, candles]);

  // data
  useEffect(() => {
    const s = series.current;
    const c = chart.current;
    if (!s || !c || !volume.current) return;
    const st = state.current;
    st.suppressUntil = performance.now() + 900;
    const rows = [];
    const vols = [];
    st.byTime = new Map();
    for (let i = 0; i < candles.ts.length; i++) {
      const time = toTime(candles.ts[i]);
      st.byTime.set(time as number, i);
      const hl = overlay.barColors.get(candles.ts[i]);
      rows.push({ time, open: candles.open[i], high: candles.high[i], low: candles.low[i], close: candles.close[i],
        ...(hl ? { color: hl, wickColor: hl } : {}) });
      vols.push({ time, value: candles.volume[i],
        color: candles.close[i] >= candles.open[i] ? "rgba(38,166,154,0.45)" : "rgba(239,83,80,0.45)" });
    }
    s.setData(rows);
    volume.current.setData(vols);
    st.n = rows.length;
    const panes = c.panes();
    if (panes[1]) panes[1].setHeight(84);
    showLegend(rows.length - 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candles, overlay.barColors]);

  // overlay (does not touch the visible range)
  useEffect(() => {
    const c = chart.current;
    if (!c || !series.current) return;
    const last = candles.ts.length ? candles.ts[candles.ts.length - 1] : 0;
    markers.current?.setMarkers(overlay.markers.filter((m) => (m.time as number) * 1000 <= last && (m.time as number) * 1000 >= (candles.ts[0] ?? 0)));
    shapes.current.setShapes(overlay.shapes, candles.ts, TF_MS[tf]);
    const wanted = new Set(overlay.lines.map((l) => l.id));
    for (const [id, ls] of lineSeries.current) {
      if (!wanted.has(id)) {
        c.removeSeries(ls);
        lineSeries.current.delete(id);
      }
    }
    for (const l of overlay.lines) {
      let ls = lineSeries.current.get(l.id);
      if (!ls) {
        ls = c.addSeries(LineSeries, { color: l.color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: l.label });
        lineSeries.current.set(l.id, ls);
      }
      ls.setData(l.points);
    }
  }, [overlay, candles, tf]);

  // visible range: explicit request (trade / date jump / window shift) or the latest bars on first load
  useEffect(() => {
    const c = chart.current;
    const st = state.current;
    if (!c || !candles.ts.length) return;
    st.suppressUntil = performance.now() + 900;
    if (viewRequest && viewRequest.key !== st.appliedKey) {
      st.appliedKey = viewRequest.key;
      st.firstData = false;
      const first = candles.ts[0];
      const last = candles.ts[candles.ts.length - 1];
      const from = Math.max(first, Math.min(viewRequest.from, last));
      const to = Math.min(last, Math.max(viewRequest.to, from + 20 * TF_MS[tf]));
      c.timeScale().setVisibleRange({ from: Math.floor(from / 1000) as UTCTimestamp, to: Math.floor(to / 1000) as UTCTimestamp });
    } else if (st.firstData) {
      st.firstData = false;
      c.timeScale().setVisibleLogicalRange({ from: Math.max(0, candles.ts.length - 220), to: candles.ts.length + 6 });
    }
  }, [candles, viewRequest, tf]);

  return (
    <div className="ws-chart-wrap">
      <div ref={legend} className="ws-legend" />
      <div ref={el} className="ws-chart" />
    </div>
  );
}
