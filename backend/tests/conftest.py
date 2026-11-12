from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradingos.backtest.models import BacktestConfig, CostModel
from tradingos.domain.timeframes import Timeframe
from tradingos.risk.engine import RiskConfig

T0 = 1_672_531_200_000  # 2023-01-01T00:00:00Z
TF = Timeframe.M15


def make_bars(ohlc: list[tuple[float, float, float, float]], tf: Timeframe = TF, t0: int = T0) -> pd.DataFrame:
    rows = [(t0 + i * tf.ms, o, h, l, c, 100.0, 1.0, 1000.0) for i, (o, h, l, c) in enumerate(ohlc)]
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "volume_ccy", "volume_quote"])


def random_walk_bars(n: int, seed: int = 7, tf: Timeframe = TF, t0: int = T0, start: float = 60_000.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = start * np.exp(np.cumsum(rng.normal(0, 0.003, n)))
    opens = np.concatenate([[start], closes[:-1]])
    spread = np.abs(rng.normal(0, 0.002, n)) * closes
    highs = np.maximum(opens, closes) + spread
    lows = np.minimum(opens, closes) - spread
    return make_bars(list(zip(opens, highs, lows, closes)), tf, t0)


def zero_cost_config(bars: pd.DataFrame, **overrides) -> BacktestConfig:
    step = int(bars["ts"].diff().dropna().min()) if len(bars) > 1 else TF.ms
    kw = dict(
        strategy_id="test",
        strategy_version="t",
        timeframe=next((t.value for t in Timeframe if t.ms == step), TF.value),
        start_ts=int(bars["ts"].iloc[0]),
        end_ts=int(bars["ts"].iloc[-1]) + TF.ms,
        initial_capital=10_000.0,
        costs=CostModel(taker_fee_rate=0, maker_fee_rate=0, slippage_bps=0, funding_rate_per_8h=0),
        risk=RiskConfig(max_leverage=20),
    )
    kw.update(overrides)
    return BacktestConfig(**kw)


@pytest.fixture
def bars_factory():
    return make_bars
