"""Fractal swing highs / lows (docs/STRATEGY_SPEC.md §2.1).

Bar i is a swing high iff
    H_i >  max(H_{i-kL .. i-1})      (strictly higher than every left bar)
    H_i >= max(H_{i+1 .. i+kR})      (not exceeded by any right bar)
Equal highs: the EARLIEST bar of a run of equal highs is the swing (left strict, right non-strict).
Swing lows are symmetric. One bar can be both a swing high and a swing low (outside bar).

Known time: close of bar i + kR. Never earlier.
If the window i-kL .. i+kR spans a data gap the bar is NOT a swing (missing bars could have
contained a higher high / lower low).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from tradingos.domain.timeframes import Timeframe
from tradingos.features.common import empty_events, gap_counter, prepare

SWING_COLUMNS = {
    "kind": "object", "idx": "int64", "ts": "int64", "price": "float64", "known_idx": "int64", "known_ts": "int64",
}


def detect_swings(
    bars: pd.DataFrame, tf: Timeframe, k_left: int = 3, k_right: int = 3, require_contiguous: bool = True
) -> pd.DataFrame:
    bars = prepare(bars)
    n = len(bars)
    if n < k_left + k_right + 1:
        return empty_events(SWING_COLUMNS)
    h, l = bars["high"], bars["low"]
    ts = bars["ts"].to_numpy(dtype="int64")

    left_max = h.shift(1).rolling(k_left, min_periods=k_left).max()
    left_min = l.shift(1).rolling(k_left, min_periods=k_left).min()
    # Right-side windows look at bars i+1..i+kR. They are only *evaluated* for bars whose
    # known_idx = i+kR exists in the data, so the result is causal once filtered by known_idx.
    right_max = h[::-1].shift(1).rolling(k_right, min_periods=k_right).max()[::-1]
    right_min = l[::-1].shift(1).rolling(k_right, min_periods=k_right).min()[::-1]

    is_high = ((h > left_max) & (h >= right_max)).to_numpy().copy()
    is_low = ((l < left_min) & (l <= right_min)).to_numpy().copy()

    if require_contiguous:
        g = gap_counter(ts, tf)
        idx = np.arange(n)
        lo, hi = idx - k_left, idx + k_right
        valid = (lo >= 0) & (hi < n)
        contiguous = np.zeros(n, dtype=bool)
        contiguous[valid] = g[hi[valid]] == g[lo[valid]]
        is_high &= contiguous
        is_low &= contiguous

    rows = []
    for kind, mask, price in (("high", is_high, h.to_numpy()), ("low", is_low, l.to_numpy())):
        for i in np.nonzero(mask)[0]:
            k_idx = int(i + k_right)
            rows.append((kind, int(i), int(ts[i]), float(price[i]), k_idx, int(ts[k_idx] + tf.ms)))
    if not rows:
        return empty_events(SWING_COLUMNS)
    df = pd.DataFrame(rows, columns=list(SWING_COLUMNS))
    # Deterministic order: by knowledge time, then occurrence, then kind (high before low).
    return df.sort_values(["known_idx", "idx", "kind"]).reset_index(drop=True)
