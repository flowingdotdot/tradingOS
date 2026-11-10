"""Shared conventions for feature modules.

Every feature event carries TWO times (docs/STRATEGY_SPEC.md §0):
  idx / ts             — where the structure OCCURRED on the chart (bar index, bar open time)
  known_idx / known_ts — when an algorithm could first KNOW it (the bar at whose close it is
                         determined; known_ts = that bar's close time)
Consumers must only use an event at decision time T if known_ts <= T.

Input bars: DataFrame with columns ts, open, high, low, close (ascending, closed candles,
positional RangeIndex 0..n-1).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from tradingos.domain.timeframes import Timeframe


def prepare(bars: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(bars.index, pd.RangeIndex) or bars.index.start != 0:
        bars = bars.reset_index(drop=True)
    return bars


def gap_counter(ts: np.ndarray, tf: Timeframe) -> np.ndarray:
    """G[i] = number of data gaps between bar 0 and bar i. Bars a..b are contiguous iff G[b]==G[a]."""
    gaps = np.zeros(len(ts), dtype=np.int64)
    if len(ts) > 1:
        gaps[1:] = np.diff(ts) != tf.ms
    return np.cumsum(gaps)


def close_ts(ts: np.ndarray | int, tf: Timeframe):
    return ts + tf.ms


def atr(bars: pd.DataFrame, period: int) -> np.ndarray:
    """Wilder ATR; value at i uses bars 0..i."""
    prev_close = bars["close"].shift(1)
    tr = pd.concat(
        [bars["high"] - bars["low"], (bars["high"] - prev_close).abs(), (bars["low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean().to_numpy()


def empty_events(columns: dict[str, str]) -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=t) for c, t in columns.items()})
