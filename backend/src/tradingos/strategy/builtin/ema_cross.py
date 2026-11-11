"""EMA crossover — EDUCATIONAL example used to validate the pipeline.

Not a research hypothesis and not expected to be profitable after costs.
See docs/STRATEGY_SPEC.md §5.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import BaseModel

from tradingos.strategy.base import Strategy, empty_signals
from tradingos.strategy.indicators import atr, ema
from tradingos.strategy.schema import ComponentSpec, DataRequirements, StrategyDefinition, param


class EmaCrossParams(BaseModel):
    fast_period: int = param(20, label="Fast EMA period", unit="bars", ge=2, le=500, step=1)
    slow_period: int = param(50, label="Slow EMA period", unit="bars", ge=3, le=1000, step=1)
    atr_period: int = param(14, label="ATR period", unit="bars", ge=2, le=200, step=1)
    atr_stop_mult: float = param(2.0, label="Stop distance", unit="× ATR", gt=0, le=20, step=0.1)
    reward_risk: float | None = param(2.0, label="Target", unit="R", gt=0, le=20, step=0.1,
                                      description="Target = reward/risk multiple of the stop distance; empty = none")
    allow_long: bool = param(True, label="Allow long entries")
    allow_short: bool = param(True, label="Allow short entries")
    exit_on_opposite_cross: bool = param(True, label="Exit on opposite cross")


class EmaCrossStrategy(Strategy):
    definition = StrategyDefinition(
        strategy_id="ema_cross",
        name="EMA Cross (Phase 1 example)",
        definition_version="1.0.0",
        category="example",
        maturity="educational",
        description="EMA crossover with ATR stop and fixed reward/risk target. Phase-1 pipeline example, kept so "
                    "that existing run records stay reproducible. New work: use ma_crossover.",
        data=DataRequirements(base_timeframes=["5m", "15m", "1H", "4H"], instruments=["BTC-USDT-SWAP"]),
        components=[
            ComponentSpec(id="crossover", label="EMA crossover entry", role="required"),
            ComponentSpec(id="opposite_exit", label="Exit on opposite cross", role="optional",
                          toggle_path="exit_on_opposite_cross"),
        ],
        docs="docs/strategies/ema_cross.md",
    )
    Params = EmaCrossParams
    params: EmaCrossParams

    def validate_config(self) -> list[dict[str, str]]:
        p, errors = self.params, []
        if p.fast_period >= p.slow_period:
            errors.append({"path": "fast_period", "message": "fast_period must be < slow_period"})
        if not (p.allow_long or p.allow_short):
            errors.append({"path": "allow_long", "message": "enable at least one direction"})
        return errors

    def indicator_lines(self, bars):
        c = bars["close"]
        return [("ema_fast", f"EMA {self.params.fast_period}", ema(c, self.params.fast_period).to_numpy(), "accent"),
                ("ema_slow", f"EMA {self.params.slow_period}", ema(c, self.params.slow_period).to_numpy(), "warn")]

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
