import { useEffect, useRef } from "react";
import { AreaSeries, LineSeries, createChart, type IChartApi } from "lightweight-charts";
import type { RunDetail } from "../api";
import { baseChartOptions, readVar, toTime } from "./charts";

/** Equity curve (pane 0) and drawdown % (pane 1), mark-to-market at each bar close. */
export function EquityChart({ run }: { run: RunDetail | null }) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!el.current || !run) return;
    const c = createChart(el.current, baseChartOptions());
    chart.current = c;
    const eq = run.equity;
    // Downsample very long curves for rendering only (keeps every Nth point + the last).
    const step = Math.max(1, Math.floor(eq.ts.length / 4000));
    const idx: number[] = [];
    for (let i = 0; i < eq.ts.length; i += step) idx.push(i);
    if (idx[idx.length - 1] !== eq.ts.length - 1) idx.push(eq.ts.length - 1);

    const equity = c.addSeries(LineSeries, { color: readVar("--accent"), lineWidth: 2, priceLineVisible: false });
    equity.setData(idx.map((i) => ({ time: toTime(eq.ts[i]), value: eq.equity[i] })));
    equity.createPriceLine({
      price: run.config.initial_capital,
      color: readVar("--text-dim"),
      lineStyle: 2,
      lineWidth: 1,
      title: "initial",
    });

    const dd = c.addSeries(
      AreaSeries,
      {
        lineColor: readVar("--down"),
        topColor: "rgba(0,0,0,0)",
        bottomColor: readVar("--down-fill"),
        lineWidth: 1,
        priceLineVisible: false,
        priceFormat: { type: "custom", formatter: (v: number) => `${v.toFixed(1)}%` },
        invertFilledArea: true,
      },
      1,
    );
    dd.setData(idx.map((i) => ({ time: toTime(eq.ts[i]), value: eq.drawdown_pct[i] })));
    const panes = c.panes();
    if (panes[1]) panes[1].setHeight(90);
    c.timeScale().fitContent();
    return () => {
      c.remove();
      chart.current = null;
    };
  }, [run]);

  return <div ref={el} className="chart chart-equity" />;
}
