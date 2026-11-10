"""Displacement candles (docs/STRATEGY_SPEC.md §2.5).

method = "median_body" (default, v1):
    body_t = |C_t - O_t|
    ref_t  = median(body_{t-N .. t-1})          N = lookback (20) prior CLOSED bars (rows, not clock time)
    displacement iff body_t >= mult (1.5) * ref_t  and ref_t > 0
method = "atr" (alternative, original proposal):
    body_t >= atr_mult (1.2) * ATR_p(t-1)        (ATR up to the PREVIOUS bar)
Optional for both: body_t / (H_t - L_t) >= min_body_ratio.
Direction: bearish if C < O, bullish if C > O. Known at the close of bar t.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

from tradingos.features.common import atr, prepare


def displacement(
    bars: pd.DataFrame,
    method: Literal["median_body", "atr"] = "median_body",
    lookback: int = 20,
    mult: float = 1.5,
    atr_period: int = 14,
    atr_mult: float = 1.2,
    min_body_ratio: float | None = None,
) -> pd.DataFrame:
    """Returns per-bar columns: body, ref, bear (bool), bull (bool)."""
    bars = prepare(bars)
    o, h, l, c = (bars[k].to_numpy(dtype="float64") for k in ("open", "high", "low", "close"))
    body = np.abs(c - o)
    if method == "median_body":
        ref = pd.Series(body).shift(1).rolling(lookback, min_periods=lookback).median().to_numpy()
        big = (ref > 0) & (body >= mult * ref)
    elif method == "atr":
        ref = np.concatenate([[np.nan], atr(bars, atr_period)[:-1]])
        big = body >= atr_mult * ref
    else:
        raise ValueError(f"unknown displacement method {method!r}")
    big = np.nan_to_num(big, nan=False).astype(bool)
    if min_body_ratio is not None:
        rng = h - l
        big &= (rng > 0) & (body >= min_body_ratio * np.where(rng > 0, rng, 1))
    return pd.DataFrame({"body": body, "ref": ref, "bear": big & (c < o), "bull": big & (c > o)})
