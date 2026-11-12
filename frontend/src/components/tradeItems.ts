// Backtest trades as generic view items (layers "fills" and "trade_sltp").
// These are the engine's actual fills and exits — nothing is recomputed here.
import type { Trade, ViewItem } from "../api";

export type ChartTrade = Pick<
  Trade,
  "trade_id" | "side" | "entry_ts" | "entry_price" | "exit_ts" | "exit_price" | "stop" | "target" | "net_pnl" |
  "r_multiple" | "exit_reason"
>;

export function tradeItems(t: ChartTrade, barMs: number, selected = true): ViewItem[] {
  const long = t.side === 1;
  const r = `${t.r_multiple >= 0 ? "+" : ""}${t.r_multiple.toFixed(2)}R`;
  const pnl = `${t.net_pnl >= 0 ? "+" : ""}${t.net_pnl.toFixed(2)}`;
  const fills = { layer: "fills", tf: null };
  const sltp = { layer: "trade_sltp", tf: null };
  const w = selected ? 2 : 1;
  const items: ViewItem[] = [
    { ...fills, type: "marker", ts: t.entry_ts, known_ts: t.entry_ts, position: long ? "below" : "above",
      shape: long ? "arrowUp" : "arrowDown", color: "accent",
      text: `#${t.trade_id} 체결 ${t.entry_price.toFixed(1)}`, size: selected ? 1.5 : 1 },
    { ...fills, type: "marker", ts: t.exit_ts, known_ts: t.exit_ts + barMs, position: long ? "above" : "below",
      shape: "circle", color: t.net_pnl > 0 ? "bull" : "warn",
      text: `#${t.trade_id} 청산(${t.exit_reason}) ${t.exit_price.toFixed(1)} · ${r} · ${pnl}`, size: selected ? 1.5 : 1 },
    { ...fills, type: "segment", t1: t.entry_ts, t2: t.exit_ts, end_known_ts: t.entry_ts, known_ts: t.entry_ts,
      price: t.entry_price, color: "accent", width: w, dash: false, label: selected ? "진입" : "" },
    { ...sltp, type: "segment", t1: t.entry_ts, t2: t.exit_ts, end_known_ts: t.entry_ts, known_ts: t.entry_ts,
      price: t.stop, color: "bear", width: w, dash: true, label: selected ? "손절" : "" },
  ];
  if (t.target != null)
    items.push({ ...sltp, type: "segment", t1: t.entry_ts, t2: t.exit_ts, end_known_ts: t.entry_ts,
      known_ts: t.entry_ts, price: t.target, color: "bull", width: w, dash: true, label: selected ? "목표" : "" });
  return items;
}

export function holdingTime(t: { entry_ts: number; exit_ts: number }): string {
  const min = Math.round((t.exit_ts - t.entry_ts) / 60_000);
  const d = Math.floor(min / 1440);
  const h = Math.floor((min % 1440) / 60);
  const m = min % 60;
  return `${d ? `${d}일 ` : ""}${h ? `${h}시간 ` : ""}${m}분`;
}
