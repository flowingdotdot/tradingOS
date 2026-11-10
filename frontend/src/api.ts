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

export interface StrategyVersion {
  strategy_id: string;
  version: string;
  status: string;
  description: string;
  params: Record<string, unknown>;
  params_hash: string;
}

export interface StrategyInfo {
  strategy_id: string;
  description: string;
  versions: StrategyVersion[];
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
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
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
  runs: () => request<RunSummary[]>("/api/backtests"),
  run: (id: string) => request<RunDetail>(`/api/backtests/${id}`),
  createRun: (body: BacktestRequest) =>
    request<RunDetail>("/api/backtests", { method: "POST", body: JSON.stringify(body) }),
  tradesCsvUrl: (id: string) => `${API_BASE}/api/backtests/${id}/trades.csv`,
};
