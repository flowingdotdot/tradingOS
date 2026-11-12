"""Risk Engine — shared by the backtester and (Phase 4) the live execution path.

Every new entry goes through `authorize_entry`. Any failed rule rejects the entry
and returns a machine-readable reason. See docs/RISK_POLICY.md §1-2.

Cost-inclusive sizing (engine 0.3.0): the risk budget covers the loss at the stop INCLUDING
the expected entry fee, exit fee and exit slippage, so a clean stop-out costs about the budget
(not budget + costs). Funding and gaps through the stop are not predictable and are excluded.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from tradingos.domain.instruments import InstrumentSpec

DAY_MS = 86_400_000


class RiskConfig(BaseModel):
    risk_per_trade_pct: float = Field(0.25, gt=0, le=2.0)      # % of equity lost if stop is hit
    daily_loss_limit_pct: float = Field(1.0, gt=0, le=10.0)    # % of start-of-day equity
    max_open_positions: int = Field(1, ge=1, le=1)             # >1 not supported yet
    max_leverage: float = Field(3.0, gt=0, le=20.0)            # notional / equity cap


@dataclass(frozen=True)
class ExecutionCosts:
    """Expected costs of one round trip, as fractions of price."""

    entry_fee_rate: float = 0.0
    exit_fee_rate: float = 0.0       # fee on a stop-out (market order)
    exit_slippage: float = 0.0       # adverse slippage on a stop-out
    target_fee_rate: float = 0.0     # fee on a take-profit fill


def unit_loss_at_stop(side: int, entry: float, stop: float, costs: ExecutionCosts | None) -> tuple[float, float]:
    """(price loss, costs) per unit of base currency if the stop is hit, entry already at its fill price."""
    if costs is None:
        return (entry - stop) * side, 0.0
    stop_fill = stop * (1 - costs.exit_slippage * side)       # long sells lower, short buys higher
    price_loss = (entry - stop_fill) * side
    fees = entry * costs.entry_fee_rate + stop_fill * costs.exit_fee_rate
    return price_loss, fees


def net_reward_risk(side: int, entry: float, stop: float, target: float, costs: ExecutionCosts | None) -> float | None:
    """Reward/risk at the expected fill, net of expected fees and stop slippage.
    reward = move to target - entry fee - target fee;  risk = loss at stop incl. fees and slippage."""
    price_loss, fees = unit_loss_at_stop(side, entry, stop, costs)
    loss = price_loss + fees
    if not loss > 0:
        return None
    c = costs or ExecutionCosts()
    reward = (target - entry) * side - entry * c.entry_fee_rate - target * c.target_fee_rate
    return reward / loss


@dataclass(frozen=True)
class EntryDecision:
    approved: bool
    reason: str
    qty: float = 0.0            # base currency (e.g. BTC)
    notional: float = 0.0
    risk_amount: float = 0.0    # qty * |entry - stop|  (price risk only)
    planned_loss: float = 0.0   # qty * (price loss + expected fees + stop slippage) = what the budget covers
    capped_by_leverage: bool = False


class RiskEngine:
    def __init__(self, config: RiskConfig, instrument: InstrumentSpec):
        self.config = config
        self.instrument = instrument
        self._day: int | None = None
        self._day_start_equity = 0.0
        self._day_realized = 0.0

    # ------------------------------------------------------- daily tracking
    def on_time(self, ts_ms: int, equity: float) -> None:
        """Call before evaluating each bar; resets the daily counter at UTC midnight."""
        day = ts_ms // DAY_MS
        if day != self._day:
            self._day = day
            self._day_start_equity = equity
            self._day_realized = 0.0

    def record_realized(self, net_pnl: float) -> None:
        """Realized PnL net of fees and funding for a closed trade."""
        self._day_realized += net_pnl

    @property
    def daily_loss_limit_hit(self) -> bool:
        limit = self._day_start_equity * self.config.daily_loss_limit_pct / 100
        return self._day_realized <= -limit

    # ------------------------------------------------------------ authorize
    def authorize_entry(
        self, *, side: int, entry_price: float, stop_price: float, equity: float, open_positions: int,
        costs: ExecutionCosts | None = None,
    ) -> EntryDecision:
        """costs=None -> legacy sizing on the stop distance only (engine <= 0.2.0)."""
        if open_positions >= self.config.max_open_positions:
            return EntryDecision(False, "max_open_positions")
        if self.daily_loss_limit_hit:
            return EntryDecision(False, "daily_loss_limit")
        if equity <= 0:
            return EntryDecision(False, "no_equity")
        if side not in (1, -1):
            return EntryDecision(False, "invalid_side")
        stop_dist = (entry_price - stop_price) * side
        if not stop_dist > 0:
            # long needs stop below entry; short needs stop above. Also catches gaps through the stop.
            return EntryDecision(False, "stop_on_wrong_side")

        price_loss, fees = unit_loss_at_stop(side, entry_price, stop_price, costs)
        per_unit = price_loss + fees
        risk_budget = equity * self.config.risk_per_trade_pct / 100
        raw_qty = risk_budget / per_unit
        capped = False
        max_qty = equity * self.config.max_leverage / entry_price
        if raw_qty > max_qty:
            raw_qty, capped = max_qty, True
        qty = self.instrument.floor_base_qty(raw_qty)          # never round up: risk must not grow
        if qty < self.instrument.min_base_qty - 1e-12:
            return EntryDecision(False, "below_min_size")
        return EntryDecision(
            True,
            "approved",
            qty=qty,
            notional=qty * entry_price,
            risk_amount=qty * stop_dist,
            planned_loss=qty * per_unit,
            capped_by_leverage=capped,
        )
