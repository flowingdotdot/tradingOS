"""Market-structure breaks (BOS / MSS) and liquidity sweeps (docs/STRATEGY_SPEC.md §2.2-2.4, §2.9).

Causal state machine over bars t = 0..n-1:
  * Reference swings: the most recently KNOWN swing high / low (known_idx < t) that has not
    been broken yet. A newly known swing replaces the previous reference of the same kind,
    even if the older one is still unbroken ("latest swing wins").
  * Bearish break at t:  C_t < ref_low.price   (close, not wick)
    Bullish break at t:  C_t > ref_high.price
    Each swing can be broken once; after a break there is no reference of that kind until a
    new swing becomes known.
  * Trend state = direction of the most recent break (0 before any break).
  * Classification of a break with direction d:
        prior trend == -d  ->  "MSS"  (shift: break against the previous direction)
        otherwise          ->  "BOS"  (continuation, or first break when trend is 0)
  * Sweep (liquidity grab): H_t > ref_high.price and C_t <= ref_high.price  (buy-side),
    L_t < ref_low.price and C_t >= ref_low.price (sell-side). Once per swing. A sweep does
    not change the trend and does not consume the swing (it can still be broken later).
  * Leg & displacement for a bearish break at t: leg_extreme = highest high in
    (broken_swing.idx, t]; has_displacement = any bearish displacement candle in
    [leg_extreme_idx, t]. Bullish is symmetric.
All events are known at the close of bar t.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from tradingos.domain.timeframes import Timeframe
from tradingos.features.common import empty_events, prepare

STRUCTURE_COLUMNS = {
    "kind": "object",            # BOS | MSS | SWEEP
    "direction": "int64",        # -1 bearish, +1 bullish (sweep: -1 = buy-side sweep -> bearish implication)
    "idx": "int64", "ts": "int64", "known_idx": "int64", "known_ts": "int64",
    "prior_trend": "int64",
    "ref_idx": "int64", "ref_ts": "int64", "ref_price": "float64", "ref_known_ts": "int64",
    "leg_extreme_idx": "int64", "leg_extreme_price": "float64",
    "has_displacement": "bool",
}


def structure_events(
    bars: pd.DataFrame,
    tf: Timeframe,
    swings: pd.DataFrame,
    disp_bear: np.ndarray | None = None,
    disp_bull: np.ndarray | None = None,
) -> tuple[pd.DataFrame, np.ndarray]:
    """Returns (events, trend) where trend[t] is the state after processing bar t's close."""
    bars = prepare(bars)
    n = len(bars)
    ts = bars["ts"].to_numpy(dtype="int64")
    h, l, c = (bars[k].to_numpy(dtype="float64") for k in ("high", "low", "close"))
    disp_bear = np.zeros(n, bool) if disp_bear is None else disp_bear
    disp_bull = np.zeros(n, bool) if disp_bull is None else disp_bull

    sw = swings.sort_values(["known_idx", "idx"]).to_dict("records")
    p = 0
    ref_high: dict | None = None
    ref_low: dict | None = None
    high_swept = low_swept = False
    trend = 0
    trend_arr = np.zeros(n, dtype=np.int64)
    rows: list[tuple] = []

    def leg(direction: int, ref: dict, t: int) -> tuple[int, float, bool]:
        a = ref["idx"] + 1
        if direction == -1:
            j = a + int(np.argmax(h[a : t + 1]))
            return j, float(h[j]), bool(disp_bear[j : t + 1].any())
        j = a + int(np.argmin(l[a : t + 1]))
        return j, float(l[j]), bool(disp_bull[j : t + 1].any())

    def emit(kind: str, direction: int, t: int, ref: dict, prior: int, with_leg: bool) -> None:
        if with_leg:
            j, px, disp = leg(direction, ref, t)
        else:
            j, px, disp = -1, np.nan, False
        rows.append((kind, direction, t, int(ts[t]), t, int(ts[t] + tf.ms), prior,
                     ref["idx"], ref["ts"], ref["price"], ref["known_ts"], j, px, disp))

    for t in range(n):
        while p < len(sw) and sw[p]["known_idx"] < t:
            s = sw[p]
            if s["kind"] == "high":
                ref_high, high_swept = s, False
            else:
                ref_low, low_swept = s, False
            p += 1

        prior = trend
        broke_down = ref_low is not None and c[t] < ref_low["price"]
        broke_up = ref_high is not None and c[t] > ref_high["price"]
        if broke_down:
            emit("MSS" if prior == 1 else "BOS", -1, t, ref_low, prior, True)
            ref_low = None
        if broke_up:
            emit("MSS" if prior == -1 else "BOS", 1, t, ref_high, prior, True)
            ref_high = None
        if broke_down and broke_up:
            trend = 0  # contradictory bar (only possible if ref_low > ref_high); stay neutral
        elif broke_down:
            trend = -1
        elif broke_up:
            trend = 1

        if ref_high is not None and not high_swept and h[t] > ref_high["price"] and c[t] <= ref_high["price"]:
            emit("SWEEP", -1, t, ref_high, prior, False)
            high_swept = True
        if ref_low is not None and not low_swept and l[t] < ref_low["price"] and c[t] >= ref_low["price"]:
            emit("SWEEP", 1, t, ref_low, prior, False)
            low_swept = True
        trend_arr[t] = trend

    if not rows:
        return empty_events(STRUCTURE_COLUMNS), trend_arr
    return pd.DataFrame(rows, columns=list(STRUCTURE_COLUMNS)), trend_arr


def swing_sequence_bias(swings: pd.DataFrame, n: int) -> np.ndarray:
    """Alternative bias: -1 if the last two KNOWN swing highs are lower-highs AND the last two
    known swing lows are lower-lows; +1 for higher-highs AND higher-lows; else 0.
    bias[t] uses swings with known_idx <= t (known at the close of bar t)."""
    out = np.zeros(n, dtype=np.int64)
    highs: list[float] = []
    lows: list[float] = []
    sw = swings.sort_values(["known_idx", "idx"]).to_dict("records")
    p = 0
    for t in range(n):
        while p < len(sw) and sw[p]["known_idx"] <= t:
            (highs if sw[p]["kind"] == "high" else lows).append(sw[p]["price"])
            p += 1
        if len(highs) >= 2 and len(lows) >= 2:
            if highs[-1] < highs[-2] and lows[-1] < lows[-2]:
                out[t] = -1
            elif highs[-1] > highs[-2] and lows[-1] > lows[-2]:
                out[t] = 1
    return out
