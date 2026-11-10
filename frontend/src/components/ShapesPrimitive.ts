// Generic overlay primitive for lightweight-charts v5: price/time rectangles, horizontal
// segments and full-height time bands. All times are UTC epoch ms; they are floored to the
// displayed timeframe and clamped to the loaded candle range.
import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  SeriesType,
  Time,
  UTCTimestamp,
} from "lightweight-charts";

export interface RectShape {
  kind: "rect";
  t1: number;
  t2: number;
  p1: number;
  p2: number;
  fill?: string;
  stroke?: string;
  dash?: boolean;
  label?: string;
}
export interface SegmentShape {
  kind: "segment";
  t1: number;
  t2: number;
  price: number;
  color: string;
  width?: number;
  dash?: boolean;
  label?: string;
}
export interface BandShape {
  kind: "band";
  t1: number;
  t2: number;
  fill: string;
}
export type Shape = RectShape | SegmentShape | BandShape;

interface Ctx {
  chart: IChartApi;
  series: ISeriesApi<SeriesType>;
  shapes: Shape[];
  times: number[]; // sorted candle open times (ms) of the displayed series
  barMs: number;
}

function xOf(c: Ctx, tMs: number, edge: "start" | "end"): number | null {
  if (!c.times.length) return null;
  const first = c.times[0];
  const last = c.times[c.times.length - 1];
  let t = Math.floor(tMs / c.barMs) * c.barMs;
  t = Math.min(Math.max(t, first), last);
  // exact bar if present, otherwise nearest earlier bar (data gaps)
  let lo = 0;
  let hi = c.times.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (c.times[mid] <= t) lo = mid;
    else hi = mid - 1;
  }
  const x = c.chart.timeScale().timeToCoordinate((c.times[lo] / 1000) as UTCTimestamp);
  if (x == null) return null;
  const half = c.chart.timeScale().options().barSpacing / 2;
  return edge === "start" ? x - half : x + half;
}

class Renderer implements IPrimitivePaneRenderer {
  constructor(private c: Ctx, private background: boolean) {}

  draw(target: Parameters<IPrimitivePaneRenderer["draw"]>[0]) {
    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      const hr = scope.horizontalPixelRatio;
      const vr = scope.verticalPixelRatio;
      const H = scope.bitmapSize.height;
      ctx.save();
      ctx.font = `${Math.round(10 * vr)}px ui-monospace, Consolas, monospace`;
      for (const s of this.c.shapes) {
        if ((s.kind === "band") !== this.background) continue;
        const x1 = xOf(this.c, s.t1, "start");
        const x2 = xOf(this.c, s.t2, "end");
        if (x1 == null || x2 == null || x2 < x1) continue;
        if (s.kind === "band") {
          ctx.fillStyle = s.fill;
          ctx.fillRect(x1 * hr, 0, (x2 - x1) * hr, H);
          continue;
        }
        if (s.kind === "rect") {
          const y1 = this.c.series.priceToCoordinate(Math.max(s.p1, s.p2));
          const y2 = this.c.series.priceToCoordinate(Math.min(s.p1, s.p2));
          if (y1 == null || y2 == null) continue;
          const [X, Y, W, Hh] = [x1 * hr, y1 * vr, (x2 - x1) * hr, Math.max(1, (y2 - y1) * vr)];
          if (s.fill) {
            ctx.fillStyle = s.fill;
            ctx.fillRect(X, Y, W, Hh);
          }
          if (s.stroke) {
            ctx.strokeStyle = s.stroke;
            ctx.lineWidth = Math.max(1, hr);
            ctx.setLineDash(s.dash ? [4 * hr, 3 * hr] : []);
            ctx.strokeRect(X, Y, W, Hh);
          }
          if (s.label) {
            ctx.fillStyle = s.stroke ?? "#aaa";
            ctx.fillText(s.label, X + 3 * hr, Y + 11 * vr);
          }
        } else {
          const y = this.c.series.priceToCoordinate(s.price);
          if (y == null) continue;
          ctx.strokeStyle = s.color;
          ctx.lineWidth = (s.width ?? 1) * hr;
          ctx.setLineDash(s.dash ? [5 * hr, 4 * hr] : []);
          ctx.beginPath();
          ctx.moveTo(x1 * hr, y * vr);
          ctx.lineTo(x2 * hr, y * vr);
          ctx.stroke();
          if (s.label) {
            ctx.fillStyle = s.color;
            ctx.fillText(s.label, x1 * hr + 2 * hr, y * vr - 3 * vr);
          }
        }
      }
      ctx.restore();
    });
  }
}

class View implements IPrimitivePaneView {
  constructor(private c: Ctx, private background: boolean) {}
  zOrder() {
    return this.background ? ("bottom" as const) : ("normal" as const);
  }
  renderer() {
    return new Renderer(this.c, this.background);
  }
}

export class ShapesPrimitive implements ISeriesPrimitive<Time> {
  private ctx: Ctx | null = null;
  private requestUpdate: (() => void) | null = null;
  private pending: { shapes: Shape[]; times: number[]; barMs: number } = { shapes: [], times: [], barMs: 60_000 };

  attached(param: SeriesAttachedParameter<Time>) {
    this.ctx = {
      chart: param.chart as IChartApi,
      series: param.series,
      ...this.pending,
    };
    this.requestUpdate = param.requestUpdate;
  }

  detached() {
    this.ctx = null;
    this.requestUpdate = null;
  }

  setShapes(shapes: Shape[], times: number[], barMs: number) {
    this.pending = { shapes, times, barMs };
    if (this.ctx) Object.assign(this.ctx, this.pending);
    this.requestUpdate?.();
  }

  updateAllViews() {}

  paneViews() {
    if (!this.ctx) return [];
    return [new View(this.ctx, true), new View(this.ctx, false)];
  }
}
