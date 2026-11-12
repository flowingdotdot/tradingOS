// Generic renderer for a strategy's ResearchView (any plugin). No strategy-specific code here.
// cutoff (ms) = knowledge time: items are drawn only if known_ts <= cutoff, and items whose end
// was not yet known are drawn as still open (extended to the cutoff).
import type { SeriesMarker, Time, UTCTimestamp } from "lightweight-charts";
import type { ResearchView } from "../api";
import type { Shape } from "./ShapesPrimitive";

export const TF_MS: Record<string, number> = {
  "1m": 60_000,
  "5m": 300_000,
  "15m": 900_000,
  "1H": 3_600_000,
  "4H": 14_400_000,
};

// Semantic color tokens used by plugins -> theme colors. Unknown values pass through as CSS.
const TOKENS: Record<string, string> = {
  bear: "#ef5350",
  bull: "#26a69a",
  neutral: "rgba(160,174,192,0.8)",
  muted: "#7d8a99",
  accent: "#4ea1ff",
  warn: "#f0b429",
  sweep: "#c084fc",
  ote: "rgba(240,180,41,0.85)",
  zone: "#a78bfa",
  highlight: "#ffd54f",
  bear_fill: "rgba(239,83,80,0.16)",
  bear_line: "rgba(239,83,80,0.55)",
  bull_fill: "rgba(38,166,154,0.16)",
  bull_line: "rgba(38,166,154,0.55)",
  ote_fill: "rgba(240,180,41,0.14)",
  zone_fill: "rgba(167,139,250,0.30)",
  bear_bg: "rgba(239,83,80,0.06)",
  bull_bg: "rgba(38,166,154,0.06)",
};
export const color = (c: string | null | undefined) => (c ? TOKENS[c] ?? c : undefined);

const t = (ms: number) => Math.floor(ms / 1000) as UTCTimestamp;

export interface Overlay {
  markers: SeriesMarker<Time>[];
  shapes: Shape[];
  lines: { id: string; label: string; color: string; points: { time: UTCTimestamp; value: number }[] }[];
  barColors: Map<number, string>;
}

export function buildViewOverlay(
  view: ResearchView,
  tf: string,
  layers: Set<string>,
  cutoff: number | null,
  lastCandleTs?: number, // when the candles are supplied separately (Chart Workspace windows)
): Overlay {
  const barMs = TF_MS[tf];
  const cd = view.timeframes[tf];
  const lastTs = lastCandleTs ?? (cd?.ts.length ? cd.ts[cd.ts.length - 1] : 0);
  const horizon = cutoff != null ? Math.min(cutoff - barMs, lastTs) : lastTs;
  const known = (k: number | null | undefined) => k != null && (cutoff == null || k <= cutoff);
  const floor = (ms: number) => Math.floor(ms / barMs) * barMs;
  const visible = (it: { layer: string; tf: string | null; max_tf?: string | null; known_ts: number }) =>
    layers.has(it.layer) && (it.tf == null || it.tf === tf) && (!it.max_tf || barMs <= TF_MS[it.max_tf]) &&
    known(it.known_ts);
  const endOf = (t2: number | null, endKnown: number | null) =>
    t2 != null && (endKnown == null || known(endKnown)) ? t2 : horizon;

  const markers: SeriesMarker<Time>[] = [];
  const shapes: Shape[] = [];
  for (const it of view.items) {
    if (!visible(it)) continue;
    if (it.type === "marker") {
      markers.push({
        time: t(floor(it.ts)),
        position: it.position === "above" ? "aboveBar" : "belowBar",
        shape: it.shape,
        color: color(it.color) ?? "#aaa",
        text: it.text,
        size: it.size,
      });
    } else if (it.type === "rect") {
      const open = !(it.t2 != null && (it.end_known_ts == null || known(it.end_known_ts)));
      shapes.push({
        kind: "rect", t1: it.t1, t2: endOf(it.t2, it.end_known_ts), p1: it.p1, p2: it.p2,
        fill: color(it.fill), stroke: color(it.stroke), dash: it.dash,
        label: it.label && open && cutoff != null ? `${it.label.split(" ")[0]} (open)` : it.label,
      });
    } else if (it.type === "segment") {
      shapes.push({ kind: "segment", t1: it.t1, t2: endOf(it.t2, it.end_known_ts), price: it.price,
        color: color(it.color) ?? "#aaa", width: it.width, dash: it.dash, label: it.label });
    } else {
      shapes.push({ kind: "band", t1: it.t1, t2: endOf(it.t2, it.end_known_ts), fill: color(it.fill) ?? "transparent" });
    }
  }
  markers.sort((a, b) => (a.time as number) - (b.time as number));

  const lines = view.lines
    .filter((l) => layers.has(l.layer) && l.tf === tf)
    .map((l) => {
      const points: { time: UTCTimestamp; value: number }[] = [];
      for (let i = 0; i < l.ts.length; i++) {
        if (cutoff != null && l.ts[i] + l.bar_ms > cutoff) break; // value known at bar close
        points.push({ time: t(l.ts[i]), value: l.value[i] });
      }
      return { id: l.id, label: l.label, color: color(l.color) ?? "#4ea1ff", points };
    });

  const barColors = new Map<number, string>();
  for (const b of view.bar_colors) {
    if (!layers.has(b.layer) || b.tf !== tf) continue;
    for (const ts of b.ts) if (cutoff == null || ts + b.bar_ms <= cutoff) barColors.set(ts, color(b.color) ?? b.color);
  }
  return { markers, shapes, lines, barColors };
}
