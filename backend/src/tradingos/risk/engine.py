"""Risk Engine — shared by the backtester and (Phase 4) the live execution path.

Every new entry goes through `authorize_entry`. Any failed rule rejects the entry
and returns a machine-readable reason. See docs/RISK_POLICY.md §1-2.
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
class EntryDecision:
    approved: bool
    reason: str
    qty: float = 0.0          # base currency (e.g. BTC)
    notional: float = 0.0
    risk_amount: float = 0.0  # qty * |entry - stop|
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
        self, *, side: int, entry_price: float, stop_price: float, equity: float, open_positions: int
    ) -> EntryDecision:
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

        risk_budget = equity * self.config.risk_per_trade_pct / 100
        raw_qty = risk_budget / stop_dist
        capped = False
        max_qty = equity * self.config.max_leverage / entry_price
        if raw_qty > max_qty:
            raw_qty, capped = max_qty, True
        qty = self.instrument.floor_base_qty(raw_qty)
        if qty < self.instrument.min_base_qty - 1e-12:
            return EntryDecision(False, "below_min_size")
        return EntryDecision(
            True,
            "approved",
            qty=qty,
            notional=qty * entry_price,
            risk_amount=qty * stop_dist,
            capped_by_leverage=capped,
        )
