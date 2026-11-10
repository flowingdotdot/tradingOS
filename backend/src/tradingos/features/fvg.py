"""Fair Value Gaps — 3-candle imbalance (docs/STRATEGY_SPEC.md §2.6).

Candles i-2, i-1, i (must be contiguous — no missing bar inside):
  bearish FVG:  L_{i-2} > H_i   zone [bottom, top] = [H_i, L_{i-2}]
  bullish FVG:  H_{i-2} < L_i   zone [bottom, top] = [H_{i-2}, L_i]
Minimum size (min_size_mode):
  "atr"  : top - bottom >= min_size_atr (0.1) * ATR_p(i)
  "none" : top - bottom > 0
Optional: middle candle i-1 must be a displacement candle in the FVG direction.
Occurrence: idx = i-1 (middle candle, where the imbalance is drawn); known at close of bar i.

Lifecycle after creation (bars t > i), each with its own known time (close of that bar):
  bearish: touched  H_t >= bottom | filled H_t >= top | invalidated C_t > top ("close") or H_t > top ("wick")
  bullish: touched  L_t <= top    | filled L_t <= bottom | invalidated C_t < bottom / L_t < bottom
Lifecycle is tracked for at most `track_bars` bars (a computation horizon, not a strategy rule);
-1 means "not happened within the data / horizon".
Overlapping FVGs are kept as separate zones; consumers decide how to combine them.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

from tradingos.domain.timeframes import Timeframe
from tradingos.features.common import atr, empty_events, gap_counter, prepare

FVG_COLUMNS = {
    "direction": "int64", "idx": "int64", "ts": "int64", "first_idx": "int64", "known_idx": "int64",
    "known_ts": "int64", "bottom": "float64", "top": "float64", "size": "float64",
    "touched_idx": "int64", "filled_idx": "int64", "invalidated_idx": "int64",
}


def _first(mask: np.ndarray) -> int:
    return int(np.argmax(mask)) if mask.any() else -1


def detect_fvgs(
    bars: pd.DataFrame,
    tf: Timeframe,
    min_size_mode: Literal["atr", "none"] = "atr",
    min_size_atr: float = 0.1,
    atr_period: int = 14,
    require_displacement_middle: bool = False,
    disp_bear: np.ndarray | None = None,
    disp_bull: np.ndarray | None = None,
    invalidation: Literal["close", "wick"] = "close",
    track_bars: int = 2000,
) -> pd.DataFrame:
    bars = prepare(bars)
    n = len(bars)
    if n < 3:
        return empty_events(FVG_COLUMNS)
    ts = bars["ts"].to_numpy(dtype="int64")
    h, l, c = (bars[k].to_numpy(dtype="float64") for k in ("high", "low", "close"))
    g = gap_counter(ts, tf)
    a = atr(bars, atr_period)

    i = np.arange(2, n)
    contiguous = g[i] == g[i - 2]
    bear = contiguous & (l[i - 2] > h[i])
    bull = contiguous & (h[i - 2] < l[i])
    if require_displacement_middle:
        bear &= np.asarray(disp_bear, bool)[i - 1]
        bull &= np.asarray(disp_bull, bool)[i - 1]

    rows = []
    for direction, mask in ((-1, bear), (1, bull)):
        for k in i[mask]:
            bottom, top = (h[k], l[k - 2]) if direction == -1 else (h[k - 2], l[k])
            size = top - bottom
            if min_size_mode == "atr" and not size >= min_size_atr * a[k]:
                continue
            if not size > 0:
                continue
            s, e = k + 1, min(n, k + 1 + track_bars)
            hh, ll, cc = h[s:e], l[s:e], c[s:e]
            if direction == -1:
                touched, filled = _first(hh >= bottom), _first(hh >= top)
                inval = _first(cc > top) if invalidation == "close" else _first(hh > top)
            else:
                touched, filled = _first(ll <= top), _first(ll <= bottom)
                inval = _first(cc < bottom) if invalidation == "close" else _first(ll < bottom)
            rows.append((direction, int(k - 1), int(ts[k - 1]), int(k - 2), int(k), int(ts[k] + tf.ms),
                         float(bottom), float(top), float(size),
                         *(x + s if x >= 0 else -1 for x in (touched, filled, inval))))
    if not rows:
        return empty_events(FVG_COLUMNS)
    return pd.DataFrame(rows, columns=list(FVG_COLUMNS)).sort_values(["known_idx", "direction"]).reset_index(drop=True)
