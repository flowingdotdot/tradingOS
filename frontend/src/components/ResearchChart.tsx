import { useEffect, useMemo, useRef } from "react";
import {
  CandlestickSeries,
  LineSeries,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import type { ResearchView } from "../api";
import { baseChartOptions, readVar, toTime } from "./charts";
import { ShapesPrimitive } from "./ShapesPrimitive";
import { buildViewOverlay, TF_MS } from "./viewOverlay";

interface Props {
  view: ResearchView;
  viewKey: string;           // changes when a new analysis is loaded (re-fit the chart)
  tf: string;
  layers: Set<string>;
  cutoff: number | null;     // knowledge time (ms) or null = show everything
  focus: { from: number; to: number } | null;
  onBarClick: (barOpenTs: number) => void;
}

/** Strategy-agnostic research chart: renders any plugin's ResearchView. */
export function ResearchChart({ view, viewKey, tf, layers, cutoff, focus, onBarClick }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const markers = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const shapes = useRef(new ShapesPrimitive());
  const lineSeries = useRef<Map<string, ISeriesApi<"Line">>>(new Map());
  const clickRef = useRef(onBarClick);
  clickRef.current = onBarClick;
  const fitKey = useRef("");

  useEffect(() => {
    if (!el.current) return;
    const c = createChart(el.current, baseChartOptions());
    const s = c.addSeries(CandlestickSeries, {
      upColor: readVar("--up"),
      downColor: readVar("--down"),
      wickUpColor: readVar("--up"),
      wickDownColor: readVar("--down"),
      borderVisible: false,
    });
    s.attachPrimitive(shapes.current);
    markers.current = createSeriesMarkers(s, []);
    c.subscribeClick((p) => {
      if (p.time != null) clickRef.current((p.time as number) * 1000);
    });
    chart.current = c;
    series.current = s;
    const ls = lineSeries.current;
    return () => {
      c.remove();
      ls.clear();
      chart.current = null;
    };
  }, []);

  const overlay = useMemo(() => buildViewOverlay(view, tf, layers, cutoff), [view, tf, layers, cutoff]);

  useEffect(() => {
    const s = series.current;
    const c = chart.current;
    if (!s || !c) return;
    const cd = view.timeframes[tf];
    if (!cd) return;
    const barMs = TF_MS[tf];
    const rows = [];
    const times: number[] = [];
    for (let i = 0; i < cd.ts.length; i++) {
      // knowledge replay: only candles that had CLOSED by the cutoff are visible
      if (cutoff != null && cd.ts[i] + barMs > cutoff) break;
      const hl = overlay.barColors.get(cd.ts[i]);
      rows.push({
        time: toTime(cd.ts[i]),
        open: cd.open[i],
        high: cd.high[i],
        low: cd.low[i],
        close: cd.close[i],
        ...(hl ? { color: hl, wickColor: hl } : {}),
      });
      times.push(cd.ts[i]);
    }
    s.setData(rows);
    const lastTime = times[times.length - 1] ?? 0;
    markers.current?.setMarkers(overlay.markers.filter((m) => (m.time as number) * 1000 <= lastTime));
    shapes.current.setShapes(overlay.shapes, times, barMs);

    // indicator lines (generic)
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

    const key = `${viewKey}|${tf}`;
    if (fitKey.current !== key) {
      c.timeScale().fitContent();
      fitKey.current = key;
    }
  }, [view, viewKey, tf, overlay, cutoff]);

  useEffect(() => {
    if (!focus || !chart.current) return;
    const pad = 60 * TF_MS[tf];
    chart.current.timeScale().setVisibleRange({
      from: Math.floor((focus.from - pad) / 1000) as UTCTimestamp,
      to: Math.floor((focus.to + pad) / 1000) as UTCTimestamp,
    });
  }, [focus, tf]);

  return <div ref={el} className="chart chart-research" />;
}
