"""Moving-Average Crossover — plugin-system verification example.

Purpose: prove that a strategy unrelated to ICT can be added WITHOUT touching the common engine,
registry, API or UI. It is NOT a validated or recommended strategy. docs/strategies/ma_crossover.md
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel

from tradingos.strategy.base import Strategy, empty_signals
from tradingos.strategy.indicators import atr
from tradingos.strategy.schema import (
    ComponentSpec,
    DataRequirements,
    Session,
    StrategyDefinition,
    group,
    param,
)

MaType = Literal["sma", "ema"]


def moving_average(series: pd.Series, period: int, kind: MaType) -> pd.Series:
    if kind == "sma":
        return series.rolling(period, min_periods=period).mean()
    return series.ewm(span=period, adjust=False).mean()


def in_session(ts_ms: np.ndarray, session: str) -> np.ndarray:
    """True where the UTC time of day of `ts_ms` is inside [start, end); the window may wrap midnight."""
    a, b = session.split("-")
    start = int(a[:2]) * 60 + int(a[3:])
    end = int(b[:2]) * 60 + int(b[3:])
    minute = (ts_ms // 60_000) % 1440
    return (minute >= start) & (minute < end) if start < end else (minute >= start) | (minute < end)


# ----------------------------------------------------------------------------- params
class StopParams(BaseModel):
    atr_period: int = param(14, label="ATR period", unit="bars", ge=2, le=200, step=1)
    atr_mult: float = param(2.0, label="Stop distance", unit="x ATR", gt=0, le=20, step=0.1)


class TargetParams(BaseModel):
    reward_risk: float | None = param(2.0, label="Target", unit="R", gt=0, le=20, step=0.1,
                                      description="Target as a multiple of the stop distance; empty = no target")


class TrendFilter(BaseModel):
    enabled: bool = param(False, label="Use trend filter",
                          description="Longs only above, shorts only below a long moving average")
    period: int = param(200, label="Trend MA period", unit="bars", ge=10, le=2000, step=1,
                        depends_on={"path": "trend_filter.enabled", "equals": True})
    ma_type: MaType = param("ema", label="Trend MA type", depends_on={"path": "trend_filter.enabled", "equals": True})


class VolatilityFilter(BaseModel):
    enabled: bool = param(False, label="Use volatility filter",
                          description="Enter only when ATR's percentile rank over the lookback is inside the band")
    lookback: int = param(100, label="Percentile lookback", unit="bars", ge=20, le=5000, step=1,
                          depends_on={"path": "volatility_filter.enabled", "equals": True})
    min_percentile: float = param(20.0, label="Min ATR percentile", unit="%", kind="percent", ge=0, le=100, step=1,
                                  depends_on={"path": "volatility_filter.enabled", "equals": True})
    max_percentile: float = param(90.0, label="Max ATR percentile", unit="%", kind="percent", ge=0, le=100, step=1,
                                  depends_on={"path": "volatility_filter.enabled", "equals": True})


class VolumeFilter(BaseModel):
    enabled: bool = param(False, label="Use volume filter")
    lookback: int = param(20, label="Volume median lookback", unit="bars", ge=3, le=1000, step=1,
                          depends_on={"path": "volume_filter.enabled", "equals": True})
    min_ratio: float = param(1.2, label="Volume >= median x", unit="x", gt=0, le=20, step=0.1,
                             depends_on={"path": "volume_filter.enabled", "equals": True})


class SessionFilter(BaseModel):
    enabled: bool = param(False, label="Use session filter")
    session: Session = param("12:00-20:00", label="Session (UTC)", kind="session",
                             description="Entries only from signal bars that OPEN inside this UTC window",
                             depends_on={"path": "session_filter.enabled", "equals": True})


class MaCrossoverParams(BaseModel):
    ma_type: MaType = param("ema", label="MA type")
    fast_period: int = param(10, label="Fast MA period", unit="bars", ge=2, le=500, step=1)
    slow_period: int = param(30, label="Slow MA period", unit="bars", ge=3, le=2000, step=1)
    direction: Literal["both", "long_only", "short_only"] = param("both", label="Direction")
    exit_on_opposite_cross: bool = param(True, label="Exit on opposite cross")
    stop: StopParams = group(StopParams, label="Stop loss")
    target: TargetParams = group(TargetParams, label="Target")
    trend_filter: TrendFilter = group(TrendFilter, label="Trend filter")
    volatility_filter: VolatilityFilter = group(VolatilityFilter, label="Volatility filter")
    volume_filter: VolumeFilter = group(VolumeFilter, label="Volume filter")
    session_filter: SessionFilter = group(SessionFilter, label="Session filter")


# --------------------------------------------------------------------------- strategy
class MaCrossoverStrategy(Strategy):
    definition = StrategyDefinition(
        strategy_id="ma_crossover",
        name="Moving Average Crossover",
        definition_version="1.0.0",
        category="trend_following",
        maturity="educational",
        description="Fast/slow moving-average crossover with ATR stop and optional trend, volatility, volume and "
                    "session filters. Plugin-architecture verification example — not a validated strategy.",
        data=DataRequirements(base_timeframes=["5m", "15m", "1H", "4H"], instruments=["BTC-USDT-SWAP"],
                              fields=["open", "high", "low", "close", "volume"]),
        components=[
            ComponentSpec(id="crossover", label="MA crossover entry", role="required"),
            ComponentSpec(id="atr_stop", label="ATR protective stop", role="required"),
            ComponentSpec(id="trend_filter", label="Trend filter", role="optional", toggle_path="trend_filter.enabled"),
            ComponentSpec(id="volatility_filter", label="Volatility filter", role="optional",
                          toggle_path="volatility_filter.enabled"),
            ComponentSpec(id="volume_filter", label="Volume filter", role="optional",
                          toggle_path="volume_filter.enabled"),
            ComponentSpec(id="session_filter", label="Session filter", role="optional",
                          toggle_path="session_filter.enabled"),
            ComponentSpec(id="opposite_exit", label="Exit on opposite cross", role="optional",
                          toggle_path="exit_on_opposite_cross"),
        ],
        docs="docs/strategies/ma_crossover.md",
    )
    Params = MaCrossoverParams
    params: MaCrossoverParams

    def validate_config(self) -> list[dict[str, str]]:
        p, errors = self.params, []
        if p.fast_period >= p.slow_period:
            errors.append({"path": "fast_period", "message": "fast_period must be smaller than slow_period"})
        v = p.volatility_filter
        if v.enabled and not v.min_percentile < v.max_percentile:
            errors.append({"path": "volatility_filter.min_percentile", "message": "min must be < max percentile"})
        return errors

    def warmup_bars(self) -> int:
        p = self.params
        mult = 3 if p.ma_type == "ema" else 1
        need = [p.slow_period * mult, p.stop.atr_period * 3]
        if p.trend_filter.enabled:
            need.append(p.trend_filter.period * (3 if p.trend_filter.ma_type == "ema" else 1))
        if p.volatility_filter.enabled:
            need.append(p.volatility_filter.lookback + p.stop.atr_period * 3)
        if p.volume_filter.enabled:
            need.append(p.volume_filter.lookback + 1)
        return max(need)

    def _mas(self, bars: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series | None]:
        p, c = self.params, bars["close"]
        trend = moving_average(c, p.trend_filter.period, p.trend_filter.ma_type) if p.trend_filter.enabled else None
        return moving_average(c, p.fast_period, p.ma_type), moving_average(c, p.slow_period, p.ma_type), trend

    def indicator_lines(self, bars):
        p = self.params
        fast, slow, trend = self._mas(bars)
        lines = [("ma_fast", f"{p.ma_type.upper()} {p.fast_period}", fast.to_numpy(), "accent"),
                 ("ma_slow", f"{p.ma_type.upper()} {p.slow_period}", slow.to_numpy(), "warn")]
        if trend is not None:
            lines.append(("ma_trend", f"Trend {p.trend_filter.ma_type.upper()} {p.trend_filter.period}",
                          trend.to_numpy(), "muted"))
        return lines

    def generate_signals(self, bars: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        bars = bars.reset_index(drop=True)
        n = len(bars)
        sig = empty_signals(bars.index)
        c = bars["close"].to_numpy()
        fast, slow, trend = self._mas(bars)
        above = (fast > slow).to_numpy()
        valid = np.isfinite(fast.to_numpy()) & np.isfinite(slow.to_numpy())
        prev_above = np.concatenate([[False], above[:-1]])
        prev_valid = np.concatenate([[False], valid[:-1]])
        warm = np.arange(n) >= self.warmup_bars()
        cross_up = above & ~prev_above & valid & prev_valid
        cross_dn = ~above & prev_above & valid & prev_valid

        a = atr(bars, p.stop.atr_period).to_numpy()
        allowed = warm.copy()
        reasons: list[str] = []
        if p.volatility_filter.enabled:
            v = p.volatility_filter
            # percentile rank of the current ATR among the last `lookback` values (inclusive, causal)
            rank = pd.Series(a).rolling(v.lookback, min_periods=v.lookback).rank(pct=True).to_numpy() * 100
            allowed &= (rank >= v.min_percentile) & (rank <= v.max_percentile)
            reasons.append("vol")
        if p.volume_filter.enabled:
            vf = p.volume_filter
            vol = bars["volume"]
            med = vol.shift(1).rolling(vf.lookback, min_periods=vf.lookback).median().to_numpy()
            allowed &= vol.to_numpy() >= vf.min_ratio * med
            reasons.append("volume")
        if p.session_filter.enabled:
            # bar OPEN time (a per-bar fact; deriving the close from neighbouring bars could leak)
            allowed &= in_session(bars["ts"].to_numpy(dtype="int64"), p.session_filter.session)
            reasons.append("session")
        long_ok = allowed & (p.direction != "short_only")
        short_ok = allowed & (p.direction != "long_only")
        if trend is not None:
            t = trend.to_numpy()
            long_ok &= c > t
            short_ok &= c < t
            reasons.append("trend")

        dist = p.stop.atr_mult * a
        rr = p.target.reward_risk
        suffix = f" [{'+'.join(reasons)}]" if reasons else ""
        for side, mask, name in ((1, cross_up & long_ok, "above"), (-1, cross_dn & short_ok, "below")):
            m = mask & np.isfinite(dist)
            sig.loc[m, "entry"] = side
            sig.loc[m, "stop"] = c[m] - side * dist[m]
            sig.loc[m, "target"] = c[m] + side * rr * dist[m] if rr else np.nan
            sig.loc[m, "reason"] = f"{p.ma_type.upper()}{p.fast_period} crossed {name} {p.ma_type.upper()}{p.slow_period}{suffix}"
        if p.exit_on_opposite_cross:
            sig["exit_long"] = cross_dn & warm
            sig["exit_short"] = cross_up & warm
        return sig
