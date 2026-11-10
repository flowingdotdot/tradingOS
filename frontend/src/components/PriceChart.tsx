import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import type { Candles, Trade } from "../api";
import { baseChartOptions, readVar, toTime } from "./charts";

interface Props {
  candles: Candles | null;
  trades: Trade[];
  focusTrade: Trade | null;
  barMs: number;
}

export function PriceChart({ candles, trades, focusTrade, barMs }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const markers = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const lines = useRef<IPriceLine[]>([]);

  useEffect(() => {
    if (!el.current) return;
    const c = createChart(el.current, baseChartOptions());
    series.current = c.addSeries(CandlestickSeries, {
      upColor: readVar("--up"),
      downColor: readVar("--down"),
      wickUpColor: readVar("--up"),
      wickDownColor: readVar("--down"),
      borderVisible: false,
    });
    markers.current = createSeriesMarkers(series.current, []);
    chart.current = c;
    return () => {
      c.remove();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    if (!series.current || !candles) return;
    series.current.setData(
      candles.ts.map((t, i) => ({
        time: toTime(t),
        open: candles.open[i],
        high: candles.high[i],
        low: candles.low[i],
        close: candles.close[i],
      })),
    );
    chart.current?.timeScale().fitContent();
  }, [candles]);

  useEffect(() => {
    if (!markers.current) return;
    const up = readVar("--up");
    const down = readVar("--down");
    const m: SeriesMarker<Time>[] = [];
    for (const t of trades) {
      const long = t.side === 1;
      m.push({
        time: toTime(t.entry_ts),
        position: long ? "belowBar" : "aboveBar",
        shape: long ? "arrowUp" : "arrowDown",
        color: long ? up : down,
        text: `#${t.trade_id} ${long ? "L" : "S"}`,
      });
      m.push({
        time: toTime(t.exit_ts),
        position: long ? "aboveBar" : "belowBar",
        shape: "circle",
        color: t.net_pnl > 0 ? up : readVar("--warn"),
        text: `${t.exit_reason} ${t.r_multiple >= 0 ? "+" : ""}${t.r_multiple.toFixed(2)}R`,
      });
    }
    m.sort((a, b) => (a.time as number) - (b.time as number));
    markers.current.setMarkers(m);
  }, [trades, candles]);

  useEffect(() => {
    const s = series.current;
    if (!s || !chart.current) return;
    lines.current.forEach((l) => s.removePriceLine(l));
    lines.current = [];
    if (!focusTrade) return;
    const mk = (price: number, color: string, title: string, style = LineStyle.Dashed) =>
      lines.current.push(s.createPriceLine({ price, color, title, lineStyle: style, lineWidth: 1 }));
    mk(focusTrade.entry_price, readVar("--accent"), "entry", LineStyle.Solid);
    mk(focusTrade.stop, readVar("--down"), "stop");
    if (focusTrade.target != null) mk(focusTrade.target, readVar("--up"), "target");
    const pad = Math.max(40, focusTrade.bars_held * 2) * barMs;
    chart.current.timeScale().setVisibleRange({
      from: toTime(focusTrade.entry_ts - pad),
      to: toTime(focusTrade.exit_ts + pad),
    });
  }, [focusTrade, barMs]);

  return <div ref={el} className="chart chart-price" />;
}
