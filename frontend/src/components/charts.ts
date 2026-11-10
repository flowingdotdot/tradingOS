import { ColorType, CrosshairMode, type DeepPartial, type ChartOptions, type UTCTimestamp } from "lightweight-charts";

export const toTime = (ms: number) => Math.floor(ms / 1000) as UTCTimestamp;

export function readVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function baseChartOptions(): DeepPartial<ChartOptions> {
  return {
    autoSize: true,
    layout: {
      background: { type: ColorType.Solid, color: readVar("--panel") },
      textColor: readVar("--text-dim"),
      fontSize: 11,
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
      attributionLogo: false,
    },
    grid: {
      vertLines: { color: readVar("--grid") },
      horzLines: { color: readVar("--grid") },
    },
    crosshair: { mode: CrosshairMode.Normal },
    rightPriceScale: { borderColor: readVar("--border") },
    timeScale: { borderColor: readVar("--border"), timeVisible: true, secondsVisible: false },
    localization: {
      // Always show UTC: all trading logic is UTC; mixing local time causes errors.
      timeFormatter: (t: number) => new Date(t * 1000).toISOString().slice(0, 16).replace("T", " "),
    },
  };
}
