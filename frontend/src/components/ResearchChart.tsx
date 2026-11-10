import { useEffect, useMemo, useRef } from "react";
import {
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import type { ResearchResult } from "../api";
import { baseChartOptions, readVar, toTime } from "./charts";
import { buildOverlay, TF_MS, type LayerId } from "./researchOverlays";
import { ShapesPrimitive } from "./ShapesPrimitive";

interface Props {
  data: ResearchResult;
  tf: string;
  layers: Set<LayerId>;
  cutoff: number | null; // knowledge time (ms) or null = show everything
  focus: { from: number; to: number } | null;
  onBarClick: (barOpenTs: number) => void;
}

export function ResearchChart({ data, tf, layers, cutoff, focus, onBarClick }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const markers = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const shapes = useRef(new ShapesPrimitive());
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
    return () => {
      c.remove();
      chart.current = null;
    };
  }, []);

  const overlay = useMemo(() => buildOverlay(data, tf, layers, cutoff), [data, tf, layers, cutoff]);

  useEffect(() => {
    const s = series.current;
    if (!s) return;
    const cd = data.timeframes[tf].candles;
    const barMs = TF_MS[tf];
    const rows = [];
    const times: number[] = [];
    for (let i = 0; i < cd.ts.length; i++) {
      // knowledge replay: only candles that had CLOSED by the cutoff are visible
      if (cutoff != null && cd.ts[i] + barMs > cutoff) break;
      const disp = overlay.dispTs.has(cd.ts[i]);
      rows.push({
        time: toTime(cd.ts[i]),
        open: cd.open[i],
        high: cd.high[i],
        low: cd.low[i],
        close: cd.close[i],
        ...(disp ? { color: cd.close[i] < cd.open[i] ? "#ff1744" : "#00e676", wickColor: "#e0e0e0" } : {}),
      });
      times.push(cd.ts[i]);
    }
    s.setData(rows);
    markers.current?.setMarkers(overlay.markers.filter((m) => (m.time as number) * 1000 <= (times[times.length - 1] ?? 0)));
    shapes.current.setShapes(overlay.shapes, times, barMs);
    const key = `${data.strategy_version}|${data.start_ts}|${data.end_ts}|${tf}`;
    if (fitKey.current !== key) {
      chart.current?.timeScale().fitContent();
      fitKey.current = key;
    }
  }, [data, tf, overlay, cutoff]);

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
