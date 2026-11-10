"""EMA crossover — EDUCATIONAL example used to validate the pipeline.

Not a research hypothesis and not expected to be profitable after costs.
See docs/STRATEGY_SPEC.md §5.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, model_validator

from tradingos.strategy.base import Strategy, empty_signals
from tradingos.strategy.indicators import atr, ema


class EmaCrossParams(BaseModel):
    fast_period: int = Field(20, ge=2)
    slow_period: int = Field(50, ge=3)
    atr_period: int = Field(14, ge=2)
    atr_stop_mult: float = Field(2.0, gt=0)
    reward_risk: float | None = Field(2.0, gt=0)
    allow_long: bool = True
    allow_short: bool = True
    exit_on_opposite_cross: bool = True

    @model_validator(mode="after")
    def _fast_lt_slow(self) -> EmaCrossParams:
        if self.fast_period >= self.slow_period:
            raise ValueError("fast_period must be < slow_period")
        return self


class EmaCrossStrategy(Strategy):
    strategy_id = "ema_cross"
    description = "EMA crossover with ATR stop and fixed reward/risk target (educational example)."
    Params = EmaCrossParams
    params: EmaCrossParams

    def warmup_bars(self) -> int:
        return max(self.params.slow_period * 3, self.params.atr_period * 3)

    def generate_signals(self, bars: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        sig = empty_signals(bars.index)
        close = bars["close"]
        fast, slow = ema(close, p.fast_period), ema(close, p.slow_period)
        a = atr(bars, p.atr_period)

        above = fast > slow
        prev_above = above.shift(1, fill_value=False)
        valid = np.arange(len(bars)) >= self.warmup_bars()
        cross_up = (above & ~prev_above & valid).to_numpy()
        cross_dn = (~above & prev_above & valid).to_numpy()

        dist = (p.atr_stop_mult * a).to_numpy()
        c = close.to_numpy()
        rr = p.reward_risk

        if p.allow_long:
            m = cross_up
            sig.loc[m, "entry"] = 1
            sig.loc[m, "stop"] = c[m] - dist[m]
            sig.loc[m, "target"] = c[m] + rr * dist[m] if rr else np.nan
            sig.loc[m, "reason"] = f"EMA{p.fast_period} crossed above EMA{p.slow_period}"
        if p.allow_short:
            m = cross_dn
            sig.loc[m, "entry"] = -1
            sig.loc[m, "stop"] = c[m] + dist[m]
            sig.loc[m, "target"] = c[m] - rr * dist[m] if rr else np.nan
            sig.loc[m, "reason"] = f"EMA{p.fast_period} crossed below EMA{p.slow_period}"
        if p.exit_on_opposite_cross:
            sig["exit_long"] = cross_dn
            sig["exit_short"] = cross_up
        return sig
