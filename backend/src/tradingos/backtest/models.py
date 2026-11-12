"""Backtest configuration and result records."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from tradingos.risk.engine import RiskConfig

ENGINE_VERSION = "0.3.0"


class CostModel(BaseModel):
    """Transaction-cost assumptions. Defaults approximate OKX regular-tier USDT swaps
    (taker 0.05%, maker 0.02%). Check your actual fee tier; these are assumptions."""

    taker_fee_rate: float = Field(0.0005, ge=0, le=0.01)
    maker_fee_rate: float = Field(0.0002, ge=-0.001, le=0.01)
    # Take-profit as a resting limit order could pay maker fees, but fills are not
    # guaranteed; default charges taker on every fill (conservative).
    target_fill_as_maker: bool = False
    slippage_bps: float = Field(2.0, ge=0, le=500)  # adverse, applied to market-type fills
    # ASSUMPTION, not measured data: OKX only serves ~3 months of funding history.
    #   cost   : |rate| charged on every funding time regardless of side (conservative default)
    #   signed : rate > 0 means longs pay and shorts RECEIVE (rate may be negative)
    funding_rate_per_8h: float = Field(0.0001, ge=-0.01, le=0.01)
    funding_mode: Literal["cost", "signed"] = "cost"


class ExecutionModel(BaseModel):
    """Execution-model switches. Defaults are the approved Phase-3 rules; the flags exist so
    that the effect of each rule can be measured (before/after) and are recorded with every run."""

    cost_inclusive_sizing: bool = True      # risk budget covers stop loss + fees + stop slippage
    enforce_net_rr_at_fill: bool = True     # re-check the signal's min_rr at the expected fill, net of costs
    # A limit order is filled only if price trades THROUGH the limit by at least this much.
    # 0 = "touch is a fill" (optimistic: a touch does not guarantee queue priority).
    limit_fill_penetration_bps: float = Field(1.0, ge=0, le=100)
    limit_entry_fee: Literal["taker", "maker"] = "taker"   # taker = conservative


class BacktestConfig(BaseModel):
    strategy_id: str
    strategy_version: str
    inst_id: str = "BTC-USDT-SWAP"
    timeframe: str = "15m"
    start_ts: int
    end_ts: int
    initial_capital: float = Field(10_000.0, gt=0)
    # Refuse to run when the stored history before start_ts is shorter than the strategy's
    # warm-up (default). False = run anyway; early signals are suppressed and a note is recorded.
    require_full_warmup: bool = True
    # Research bookkeeping: which registered experiment (docs/EXPERIMENT_LOG.md) this run belongs
    # to. Every run on the in-sample window counts as a trial of its experiment.
    experiment_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
    costs: CostModel = CostModel()
    risk: RiskConfig = RiskConfig()
    execution: ExecutionModel = ExecutionModel()


class Trade(BaseModel):
    trade_id: int
    side: int                 # +1 long, -1 short
    entry_ts: int
    entry_price: float
    exit_ts: int
    exit_price: float
    qty: float                # base currency
    contracts: float
    stop: float
    target: float | None
    risk_amount: float        # price risk at the stop (qty * |entry - stop|)
    gross_pnl: float
    fees: float
    funding: float
    slippage_cost: float      # vs reference price (already inside gross_pnl)
    net_pnl: float
    r_multiple: float         # net_pnl / risk_budget
    exit_reason: str          # stop | target | signal_exit | end_of_data
    entry_reason: str
    bars_held: int
    ambiguous_bar: bool       # stop and target both inside one candle -> stop assumed
    mae: float                # max adverse excursion, price units
    mfe: float                # max favourable excursion, price units
    capped_by_leverage: bool
    # --- engine 0.3.0
    entry_type: str = "market"          # market | limit
    signal_ts: int | None = None        # open time of the bar whose close produced the signal
    risk_budget: float = 0.0            # planned loss at the stop incl. expected fees + slippage
    planned_net_rr: float | None = None  # net reward/risk at the expected fill (None = no target)
    fill_bar_ambiguous: bool = False    # limit fill and stop/target inside the same candle


class Rejection(BaseModel):
    ts: int
    side: int
    reason: str
    detail: str = ""
