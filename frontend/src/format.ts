export const fmtTs = (ms: number | null | undefined): string =>
  ms == null ? "—" : new Date(ms).toISOString().slice(0, 16).replace("T", " ");

export const fmtDate = (ms: number | null | undefined): string =>
  ms == null ? "—" : new Date(ms).toISOString().slice(0, 10);

export const fmtNum = (v: number | null | undefined, digits = 2): string =>
  v == null || !Number.isFinite(v)
    ? "—"
    : v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });

export const fmtPct = (v: number | null | undefined, digits = 2): string =>
  v == null || !Number.isFinite(v) ? "—" : `${fmtNum(v, digits)}%`;

export const fmtSigned = (v: number | null | undefined, digits = 2): string =>
  v == null || !Number.isFinite(v) ? "—" : `${v > 0 ? "+" : ""}${fmtNum(v, digits)}`;

export const tone = (v: number | null | undefined): "pos" | "neg" | "" =>
  v == null || v === 0 ? "" : v > 0 ? "pos" : "neg";

export const DAY_MS = 86_400_000;
