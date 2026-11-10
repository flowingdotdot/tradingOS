// Builds chart markers + shapes for the Strategy Lab from a ResearchResult.
// `cutoff` (ms) = knowledge time: only things with known_ts <= cutoff are drawn, and lifecycle
// states (FVG fill/invalidation, setup end) are shown as they were known at that moment.
import type { SeriesMarker, Time, UTCTimestamp } from "lightweight-charts";
import type { ResearchResult, RTf } from "../api";
import type { Shape } from "./ShapesPrimitive";

export const LAYERS = {
  swings: "Confirmed swings",
  known: "Recognition time (✓)",
  structure: "BOS / MSS",
  sweeps: "Liquidity sweeps",
  displacement: "Displacement candles",
  fvg_bear: "Bearish FVG",
  fvg_bull: "Bullish FVG",
  bias: "4H bias background",
  dealing: "Dealing range",
  ote: "OTE zone",
  zones: "Entry zone (OTE∩FVG)",
  signals: "Entry signals",
  sltp: "Stop / target",
} as const;
export type LayerId = keyof typeof LAYERS;

export const TF_MS: Record<string, number> = { "5m": 300_000, "15m": 900_000, "1H": 3_600_000, "4H": 14_400_000 };

const C = {
  up: "#26a69a",
  down: "#ef5350",
  bearFvg: "rgba(239,83,80,0.16)",
  bearFvgLine: "rgba(239,83,80,0.55)",
  bullFvg: "rgba(38,166,154,0.16)",
  bullFvgLine: "rgba(38,166,154,0.55)",
  dealing: "rgba(160,174,192,0.7)",
  ote: "rgba(240,180,41,0.14)",
  oteLine: "rgba(240,180,41,0.8)",
  zone: "rgba(167,139,250,0.30)",
  zoneLine: "#a78bfa",
  entry: "#4ea1ff",
  bosLine: "#9aa5b1",
  mssLine: "#f0b429",
  biasBear: "rgba(239,83,80,0.06)",
  biasBull: "rgba(38,166,154,0.06)",
};

const t = (ms: number) => Math.floor(ms / 1000) as UTCTimestamp;

export interface Overlay {
  markers: SeriesMarker<Time>[];
  shapes: Shape[];
  dispTs: Set<number>;
}

export function buildOverlay(
  data: ResearchResult,
  tf: string,
  layers: Set<LayerId>,
  cutoff: number | null,
): Overlay {
  const tfd: RTf = data.timeframes[tf];
  const barMs = TF_MS[tf];
  const floor = (ms: number) => Math.floor(ms / barMs) * barMs;
  const known = (k: number | null | undefined) => k != null && (cutoff == null || k <= cutoff);
  const lastTs = tfd.candles.ts.length ? tfd.candles.ts[tfd.candles.ts.length - 1] : data.end_ts;
  const horizon = cutoff != null ? Math.min(cutoff - barMs, lastTs) : lastTs;
  const markers: SeriesMarker<Time>[] = [];
  const shapes: Shape[] = [];
  const on = (l: LayerId) => layers.has(l);

  // ------------------------------------------------------------------ swings
  for (const s of tfd.swings) {
    if (!known(s.known_ts)) continue;
    const high = s.kind === "high";
    if (on("swings"))
      markers.push({
        time: t(s.ts),
        position: high ? "aboveBar" : "belowBar",
        shape: "circle",
        size: 0.6,
        color: high ? C.down : C.up,
        text: high ? "SH" : "SL",
      });
    if (on("known"))
      markers.push({
        time: t(s.known_ts - barMs), // the bar whose close confirmed it
        position: high ? "aboveBar" : "belowBar",
        shape: "square",
        size: 0.4,
        color: "#7d8a99",
        text: `✓${high ? "SH" : "SL"}+${s.lag_bars}`,
      });
  }

  // --------------------------------------------------------------- structure
  for (const e of tfd.structure) {
    if (!known(e.known_ts)) continue;
    if (e.kind === "SWEEP") {
      if (on("sweeps"))
        markers.push({
          time: t(e.ts),
          position: e.direction === -1 ? "aboveBar" : "belowBar",
          shape: "circle",
          size: 0.8,
          color: "#c084fc",
          text: "sweep",
        });
      continue;
    }
    if (!on("structure")) continue;
    const color = e.kind === "MSS" ? C.mssLine : C.bosLine;
    shapes.push({ kind: "segment", t1: e.ref_ts, t2: e.ts, price: e.ref_price, color, dash: e.kind === "BOS",
      label: `${e.kind}${e.direction === -1 ? "↓" : "↑"}` });
    if (on("known"))
      markers.push({ time: t(e.ts), position: e.direction === -1 ? "belowBar" : "aboveBar", shape: "square",
        size: 0.4, color, text: `${e.kind}${e.has_displacement ? "+D" : ""}` });
  }

  // ------------------------------------------------------------------- FVGs
  for (const f of tfd.fvgs) {
    if (!known(f.known_ts)) continue;
    const bear = f.direction === -1;
    if ((bear && !on("fvg_bear")) || (!bear && !on("fvg_bull"))) continue;
    const ends = [f.invalidated_known_ts, f.filled_known_ts].filter((x): x is number => known(x));
    const end = ends.length ? Math.min(...ends) - barMs : horizon;
    shapes.push({ kind: "rect", t1: f.first_ts, t2: end, p1: f.bottom, p2: f.top,
      fill: bear ? C.bearFvg : C.bullFvg, stroke: bear ? C.bearFvgLine : C.bullFvgLine });
  }

  // ------------------------------------------------------------------- bias
  if (on("bias")) {
    const pts = data.bias.filter((b) => known(b.known_ts));
    for (let i = 0; i < pts.length; i++) {
      const end = i + 1 < pts.length ? pts[i + 1].known_ts : horizon + barMs;
      if (pts[i].bias === 0) continue;
      shapes.push({ kind: "band", t1: pts[i].known_ts, t2: end - 1, fill: pts[i].bias < 0 ? C.biasBear : C.biasBull });
    }
  }

  // ----------------------------------------------------------------- setups
  for (const s of data.setups) {
    if (!known(s.known_ts)) continue;
    const ended = s.end_ts != null && known(s.end_ts);
    const end = ended ? (s.end_ts as number) - 1 : horizon;
    const status = ended || cutoff == null ? s.status : "active";
    if (on("dealing")) {
      shapes.push({ kind: "rect", t1: s.a_ts, t2: end, p1: s.b, p2: s.a, stroke: C.dealing, dash: true,
        label: `#${s.id} ${status}` });
      shapes.push({ kind: "segment", t1: s.b_ts, t2: end, price: s.equilibrium, color: C.dealing, dash: true, label: "EQ" });
      markers.push({ time: t(floor(s.a_ts)), position: "aboveBar", shape: "arrowDown", size: 0.6, color: C.dealing, text: `A#${s.id}` });
      markers.push({ time: t(floor(s.b_ts)), position: "belowBar", shape: "arrowUp", size: 0.6, color: C.dealing, text: `B#${s.id}` });
    }
    if (on("ote")) {
      shapes.push({ kind: "rect", t1: s.known_ts, t2: end, p1: s.ote_low, p2: s.ote_high, fill: C.ote });
      shapes.push({ kind: "segment", t1: s.known_ts, t2: end, price: s.ote_ref, color: C.oteLine, dash: true, label: "0.705" });
    }
    if (on("zones"))
      for (const z of s.zones)
        shapes.push({ kind: "rect", t1: s.known_ts, t2: end, p1: z.bottom, p2: z.top, fill: C.zone, stroke: C.zoneLine });
  }

  // ---------------------------------------------------------------- signals
  for (const g of data.signals) {
    if (!known(g.known_ts)) continue;
    if (on("signals"))
      markers.push({ time: t(floor(g.ts)), position: "aboveBar", shape: "arrowDown", color: C.entry,
        text: g.limit_price != null ? `SHORT limit ${g.limit_price.toFixed(1)}` : "SHORT (next open)" });
    if (on("sltp")) {
      const t2 = Math.min(g.ts + 60 * TF_MS["5m"], horizon);
      shapes.push({ kind: "segment", t1: g.ts, t2, price: g.stop, color: C.down, width: 2, label: "SL" });
      shapes.push({ kind: "segment", t1: g.ts, t2, price: g.target, color: C.up, width: 2, label: "TP" });
      if (g.limit_price != null)
        shapes.push({ kind: "segment", t1: g.ts, t2, price: g.limit_price, color: C.entry, dash: true, label: "limit" });
    }
  }

  markers.sort((a, b) => (a.time as number) - (b.time as number));
  const dispTs = new Set<number>(
    on("displacement") ? tfd.displacement.filter((d) => known(d.known_ts)).map((d) => d.ts) : [],
  );
  return { markers, shapes, dispTs };
}
