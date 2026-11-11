// Typed client for the Trading OS API. All timestamps are UTC epoch milliseconds.

export interface CostModel {
  taker_fee_rate: number;
  maker_fee_rate: number;
  target_fill_as_maker: boolean;
  slippage_bps: number;
  funding_rate_per_8h: number;
}

export interface RiskConfig {
  risk_per_trade_pct: number;
  daily_loss_limit_pct: number;
  max_open_positions: number;
  max_leverage: number;
}

export interface EnvStatus {
  enabled: boolean;
  status: string;
  credentials_configured?: boolean;
}

export interface SystemInfo {
  version: string;
  environments: Record<"BACKTEST" | "DEMO" | "LIVE", EnvStatus>;
  defaults: { costs: CostModel; risk: RiskConfig };
}

export interface Gap {
  start_ts: number;
  end_ts: number;
  missing_bars: number;
}

export interface Dataset {
  inst_id: string;
  timeframe: string;
  rows: number;
  first_ts: number | null;
  last_ts: number | null;
  missing_bars: number;
  gaps: Gap[];
  ok: boolean;
}

// ------------------------------------------------------------------ strategy plugins
// Everything here is generic: the UI is generated from these descriptions, never from names.
export type ParamKind =
  | "int" | "float" | "bool" | "enum" | "multi_enum" | "timeframe" | "percent" | "price" | "bps" | "session"
  | "string" | "group";

export interface ParamSpec {
  path: string;
  label: string;
  description: string;
  kind: ParamKind;
  default: unknown;
  nullable: boolean;
  minimum: number | null;
  maximum: number | null;
  exclusive_minimum: number | null;
  exclusive_maximum: number | null;
  step: number | null;
  unit: string | null;
  options: unknown[] | null;
  pattern: string | null;
  depends_on: { path: string; in?: unknown[]; equals?: unknown } | null;
  advanced: boolean;
  group: string | null;
}

export interface ComponentSpec {
  id: string;
  label: string;
  description: string;
  role: "required" | "optional";
  toggle_path: string | null;
  on_value: unknown;
  off_value: unknown;
  requires: string[];
}

export interface StrategyConfig {
  strategy_id: string;
  version: string;
  status: string;
  description: string;
  declared_params: Record<string, unknown>;
  params: Record<string, unknown>;
  config_hash: string;
  params_hash: string;
  definition_version: string;
  source: "repo" | "user";
  parent_version: string | null;
  created_at: string | null;
  integrity: "ok" | "modified" | "invalid";
  error?: string;
}
/** @deprecated kept for older call sites */
export type StrategyVersion = StrategyConfig;

export interface StrategyInfo {
  strategy_id: string;
  name: string;
  definition_version: string;
  category: string;
  maturity: string;
  description: string;
  docs: string | null;
  data: { base_timeframes: string[]; derived_timeframes: string[]; fields: string[]; instruments: string[]; notes: string };
  required_timeframe: string | null;
  components: ComponentSpec[];
  component_rules: { kind: string; components: string[]; message: string }[];
  param_specs: ParamSpec[];
  component_toggle_paths: string[];
  default_params: Record<string, unknown>;
  default_warmup_bars: number;
  has_research_view: boolean;
  configs: StrategyConfig[];
  versions: StrategyConfig[];
}

export interface ValidationResult {
  ok: boolean;
  errors: { path: string; message: string }[];
  resolved_params?: Record<string, unknown>;
  params_hash?: string;
  config_hash?: string;
  warmup_bars?: number;
  components?: Record<string, boolean>;
}

// ---------------------------------------------------------------- generic research view
// Every item has `known_ts` (first moment it was knowable). t2 = null -> still open.
interface ViewItemBase {
  layer: string;
  tf: string | null;
  known_ts: number;
}
export interface ViewMarker extends ViewItemBase {
  type: "marker";
  ts: number;
  position: "above" | "below";
  shape: "circle" | "square" | "arrowUp" | "arrowDown";
  color: string;
  text: string;
  size: number;
}
export interface ViewRect extends ViewItemBase {
  type: "rect";
  t1: number;
  t2: number | null;
  end_known_ts: number | null;
  p1: number;
  p2: number;
  fill: string | null;
  stroke: string | null;
  dash: boolean;
  label: string;
}
export interface ViewSegment extends ViewItemBase {
  type: "segment";
  t1: number;
  t2: number | null;
  end_known_ts: number | null;
  price: number;
  color: string;
  width: number;
  dash: boolean;
  label: string;
}
export interface ViewBand extends ViewItemBase {
  type: "band";
  t1: number;
  t2: number | null;
  end_known_ts: number | null;
  fill: string;
}
export type ViewItem = ViewMarker | ViewRect | ViewSegment | ViewBand;

export interface ViewTable {
  id: string;
  label: string;
  columns: { key: string; label: string; kind: "ts" | "num" | "price" | "text" | "status" }[];
  rows: Record<string, unknown>[];
  known_key: string;
  focus_from: string | null;
  focus_to: string | null;
}

export interface ResearchView {
  default_tf: string;
  timeframes: Record<string, { ts: number[]; open: number[]; high: number[]; low: number[]; close: number[] }>;
  tf_roles: Record<string, string>;
  layers: { id: string; label: string; default_on: boolean }[];
  items: ViewItem[];
  lines: { layer: string; id: string; label: string; tf: string; color: string; bar_ms: number; ts: number[]; value: number[] }[];
  bar_colors: { layer: string; tf: string; ts: number[]; color: string; bar_ms: number }[];
  tables: ViewTable[];
  summary: { label: string; value: unknown }[];
}

export interface ResearchResult {
  strategy_id: string;
  strategy_name: string;
  definition_version: string;
  strategy_version: string;
  draft: boolean;
  params: Record<string, unknown>;
  params_hash: string;
  config_hash: string;
  components: Record<string, boolean>;
  code_revision: string;
  data_fingerprint: string;
  inst_id: string;
  timeframe: string;
  start_ts: number;
  end_ts: number;
  view: ResearchView;
}

// --------------------------------------------------------------------------- instances
export interface StrategyInstance {
  instance_id: string;
  strategy_id: string;
  config_version: string;
  config_hash: string;
  params_hash: string;
  definition_version: string;
  inst_id: string;
  timeframe: string;
  environment: "BACKTEST" | "DEMO" | "LIVE";
  capital_allocation: number;
  risk: RiskConfig;
  entries_enabled: boolean;
  created_at: string;
  note: string;
}
export interface InstanceDetail extends StrategyInstance {
  readiness: { check: string; ok: boolean; detail: string }[];
  ready: boolean;
  runtime_status: string;
  events: { ts: string; kind: string; detail: Record<string, unknown> }[];
}

export interface Trade {
  trade_id: number;
  side: 1 | -1;
  entry_ts: number;
  entry_price: number;
  exit_ts: number;
  exit_price: number;
  qty: number;
  contracts: number;
  stop: number;
  target: number | null;
  risk_amount: number;
  gross_pnl: number;
  fees: number;
  funding: number;
  slippage_cost: number;
  net_pnl: number;
  r_multiple: number;
  exit_reason: string;
  entry_reason: string;
  bars_held: number;
  ambiguous_bar: boolean;
  mae: number;
  mfe: number;
  capped_by_leverage: boolean;
}

export interface MonthRow {
  month: string;
  return_pct: number;
  equity_end: number;
  trades: number;
  net_pnl: number;
}

export interface Metrics {
  initial_capital: number;
  final_equity: number;
  net_profit: number;
  return_pct: number;
  total_trades: number;
  long_trades: number;
  short_trades: number;
  wins: number;
  losses: number;
  win_rate_pct: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  payoff_ratio: number | null;
  expectancy: number | null;
  expectancy_r: number | null;
  profit_factor: number | null;
  max_drawdown_pct: number;
  max_drawdown_abs: number;
  max_consecutive_losses: number;
  max_consecutive_wins: number;
  total_fees: number;
  total_funding: number;
  total_slippage: number;
  exposure_pct: number;
  avg_notional: number | null;
  max_notional: number | null;
  avg_bars_held: number | null;
  ambiguous_exits: number;
  leverage_capped_trades: number;
  exit_reasons: Record<string, number>;
  signals_seen: number;
  rejections: number;
  rejection_reasons: Record<string, number>;
  monthly: MonthRow[];
}

export interface RunSummary {
  run_id: string;
  created_at: string;
  environment: string;
  strategy_id: string;
  strategy_version: string;
  params_hash: string;
  code_revision: string;
  engine_version: string;
  data_fingerprint: string;
  inst_id: string;
  timeframe: string;
  start_ts: number;
  end_ts: number;
  summary: {
    total_trades: number;
    net_profit: number;
    return_pct: number;
    win_rate_pct: number | null;
    profit_factor: number | null;
    max_drawdown_pct: number;
    expectancy_r: number | null;
  };
}

export interface RunDetail extends RunSummary {
  strategy?: {
    strategy_id: string;
    name: string;
    definition_version: string;
    config_version: string;
    config_source: string;
    config_hash: string;
    params_hash: string;
    components: Record<string, boolean>;
  };
  conditions?: {
    inst_id: string;
    timeframe: string;
    start_ts: number;
    end_ts: number;
    initial_capital: number;
    costs: CostModel;
    risk: RiskConfig;
    data_fingerprint: string;
    warmup_bars: number;
  };
  config: {
    initial_capital: number;
    costs: CostModel;
    risk: RiskConfig;
  };
  params: Record<string, unknown>;
  metrics: Metrics;
  trades: Trade[];
  rejections: { ts: number; side: number; reason: string }[];
  equity: { ts: number[]; equity: number[]; drawdown_pct: number[]; in_position: number[] };
  data_quality: Dataset;
  notes: string[];
  disclaimer: string;
}

export interface Candles {
  truncated: boolean;
  ts: number[];
  open: number[];
  high: number[];
  low: number[];
  close: number[];
  volume: number[];
}

export interface BacktestRequest {
  strategy_id: string;
  strategy_version: string;
  inst_id: string;
  timeframe: string;
  start_ts: number;
  end_ts: number;
  initial_capital: number;
  costs: CostModel;
  risk: RiskConfig;
}

// In dev the browser talks to the API directly (CORS allows the Vite origin); the Vite dev
// proxy stalls on large (~0.6 MB+) responses. Production/preview builds use same-origin "/api".
const API_BASE: string = import.meta.env.VITE_API_BASE ?? (import.meta.env.DEV ? "http://127.0.0.1:8000" : "");

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(API_BASE + path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      const d = body.detail;
      detail =
        typeof d === "string"
          ? d
          : d && Array.isArray(d.errors)
            ? `${d.message}: ${d.errors.map((e: { path: string; message: string }) => `${e.path}: ${e.message}`).join("; ")}`
            : JSON.stringify(d);
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  system: () => request<SystemInfo>("/api/system"),
  datasets: () => request<Dataset[]>("/api/market/datasets"),
  candles: (inst: string, tf: string, start: number, end: number) =>
    request<Candles>(
      `/api/market/candles?inst_id=${encodeURIComponent(inst)}&timeframe=${tf}&start_ts=${start}&end_ts=${end}`,
    ),
  collect: (inst_id: string, timeframe: string, days: number) =>
    request<{ fetched: number; stored_rows: number; validation: Dataset }>("/api/market/collect", {
      method: "POST",
      body: JSON.stringify({ inst_id, timeframe, days }),
    }),
  strategies: () => request<StrategyInfo[]>("/api/strategies"),
  strategy: (id: string) => request<StrategyInfo>(`/api/strategies/${id}`),
  validate: (id: string, params: Record<string, unknown>) =>
    request<ValidationResult>(`/api/strategies/${id}/validate`, { method: "POST", body: JSON.stringify({ params }) }),
  saveConfig: (id: string, body: { version: string; params: Record<string, unknown>; description: string; parent_version: string | null }) =>
    request<StrategyConfig>(`/api/strategies/${id}/configs`, { method: "POST", body: JSON.stringify(body) }),
  research: (body: {
    strategy_id: string;
    strategy_version?: string | null;
    draft_params?: Record<string, unknown> | null;
    timeframe?: string | null;
    start_ts: number;
    end_ts: number;
  }) => request<ResearchResult>("/api/research/analyze", { method: "POST", body: JSON.stringify({ inst_id: "BTC-USDT-SWAP", ...body }) }),
  instances: () => request<StrategyInstance[]>("/api/instances"),
  instance: (id: string) => request<InstanceDetail>(`/api/instances/${id}`),
  createInstance: (body: { strategy_id: string; config_version: string; timeframe: string; environment: string; capital_allocation: number }) =>
    request<StrategyInstance>("/api/instances", { method: "POST", body: JSON.stringify(body) }),
  setEntries: (id: string, enabled: boolean, reason = "") =>
    request<StrategyInstance>(`/api/instances/${id}/entries`, { method: "POST", body: JSON.stringify({ enabled, reason }) }),
  runs: () => request<RunSummary[]>("/api/backtests"),
  run: (id: string) => request<RunDetail>(`/api/backtests/${id}`),
  createRun: (body: BacktestRequest) =>
    request<RunDetail>("/api/backtests", { method: "POST", body: JSON.stringify(body) }),
  tradesCsvUrl: (id: string) => `${API_BASE}/api/backtests/${id}/trades.csv`,
};
