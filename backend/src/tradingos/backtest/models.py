"""Backtest configuration and result records."""

from __future__ import annotations

from pydantic import BaseModel, Field

from tradingos.risk.engine import RiskConfig

ENGINE_VERSION = "0.2.0"


class CostModel(BaseModel):
    """Transaction-cost assumptions. Defaults approximate OKX regular-tier USDT swaps
    (taker 0.05%, maker 0.02%). Check your actual fee tier; these are assumptions."""

    taker_fee_rate: float = Field(0.0005, ge=0, le=0.01)
    maker_fee_rate: float = Field(0.0002, ge=-0.001, le=0.01)
    # Take-profit as a resting limit order could pay maker fees, but fills are not
    # guaranteed; default charges taker on every fill (conservative).
    target_fill_as_maker: bool = False
    slippage_bps: float = Field(2.0, ge=0, le=500)  # adverse, applied to market-type fills
    # Charged as a COST regardless of position side (conservative; real funding can be
    # received). Historical funding rates are Phase 3.
    funding_rate_per_8h: float = Field(0.0001, ge=0, le=0.01)


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
    costs: CostModel = CostModel()
    risk: RiskConfig = RiskConfig()


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
    risk_amount: float        # planned loss at stop (before costs)
    gross_pnl: float
    fees: float
    funding: float
    slippage_cost: float      # vs reference price (already inside gross_pnl)
    net_pnl: float
    r_multiple: float         # net_pnl / risk_amount
    exit_reason: str          # stop | target | signal_exit | end_of_data
    entry_reason: str
    bars_held: int
    ambiguous_bar: bool       # stop and target both inside one candle -> stop assumed
    mae: float                # max adverse excursion, price units
    mfe: float                # max favourable excursion, price units
    capped_by_leverage: bool


class Rejection(BaseModel):
    ts: int
    side: int
    reason: str
