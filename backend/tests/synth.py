"""Hand-designed OHLC paths for feature and strategy tests.

Bars are built from straight legs between pivot prices. Every bar is a pure body plus a fixed
wick `w` on both sides, so on uniform legs (step <= 2w) no accidental FVGs appear, while steep
legs (step > 2w) create FVGs and displacement candles on purpose.
"""

from __future__ import annotations

import pandas as pd

from tradingos.domain.timeframes import Timeframe

T0 = 1_735_689_600_000  # 2025-01-01 00:00 UTC (aligned to 4H/15m/5m)
COLS = ["ts", "open", "high", "low", "close", "volume", "volume_ccy", "volume_quote"]


def legs(start: float, pivots: list[tuple[float, float]], w: float = 0.6) -> list[tuple[float, float, float, float]]:
    """pivots: [(target_price, step_per_bar), ...] -> list of (o, h, l, c)."""
    out = []
    px = start
    for target, step in pivots:
        direction = 1 if target > px else -1
        while abs(target - px) > 1e-9:
            nxt = px + direction * min(step, abs(target - px))
            out.append((px, max(px, nxt) + w, min(px, nxt) - w, nxt))
            px = nxt
    return out


def to_frame(ohlc: list[tuple[float, float, float, float]], tf: Timeframe, t0: int = T0) -> pd.DataFrame:
    rows = [(t0 + i * tf.ms, o, h, l, c, 1.0, 1.0, 1.0) for i, (o, h, l, c) in enumerate(ohlc)]
    return pd.DataFrame(rows, columns=COLS)


def split_15m_to_5m(ohlc15: list[tuple[float, float, float, float]]) -> list[tuple[float, float, float, float]]:
    """Split each 15m bar into 3 linear 5m bars whose aggregate is exactly the 15m bar."""
    out = []
    for o, h, l, c in ohlc15:
        pts = [o + (c - o) * k / 3 for k in range(4)]
        sub = [[pts[k], max(pts[k], pts[k + 1]), min(pts[k], pts[k + 1]), pts[k + 1]] for k in range(3)]
        if c < o:   # bearish: high wick at the start, low wick at the end
            sub[0][1], sub[2][2] = max(sub[0][1], h), min(sub[2][2], l)
        else:       # bullish (or flat): low wick first, high wick last
            sub[0][2], sub[2][1] = min(sub[0][2], l), max(sub[2][1], h)
        out += [tuple(s) for s in sub]
    return out


PRELUDE_15M = [(104, 1), (100, 1)] * 14          # ~112 quiet 15m bars (warm-up, median body = 1)
IMPULSE_15M = [(110, 1), (104, 1), (120, 1),     # swing low 104 (ref), origin A = 120
               (90, 5),                          # steep leg: displacement + FVGs, breaks 104 by close
               (93, 1)]                          # 3 rising bars confirm swing low B = 90 (k=3)


GOOD_RETRACE = [(108, 1), (105, 1), (112, 1), (104, 4), (100, 1)]
LATE_MSS_RETRACE = [(100, 1), (97, 1), (111, 1), (96, 5), (94, 1)]     # MSS far below the high: R:R ~0.42
NO_TOUCH_RETRACE = [(100, 1), (97, 1), (104, 1), (85, 2)]              # never reaches the zone, then new low
ABOVE_A_RETRACE = [(108, 1), (105, 1), (122, 1), (100, 4)]             # trades above A=120.6 -> invalidated
TARGET_PASSED_RETRACE = [(108, 1), (105, 1), (112, 1), (88, 24), (86, 1)]  # one-bar MSS closes below B


def bearish_ote_scenario(retrace_5m: list[tuple[float, float]] | None = None) -> pd.DataFrame:
    """5m bars: quiet prelude, 15m bearish impulse 120 -> 90, then a 5m retracement.

    Default retracement (designed to produce exactly one mss_close signal):
      93 -> 108 (5m swing high 108.6), -> 105 (5m swing low 104.4, formed after B), -> 112 (closes
      above 108.6: bullish 5m structure; touches the OTE∩FVG zones), then a steep drop 112 -> 104
      (step 4: displacement; closes below 104.4 -> bearish 5m MSS), then drift to 100.
      Planned: entry ref 104, stop 112.6*(1+5bps), target B=89.4 -> R:R ~1.69.
    """
    ohlc15 = legs(100, PRELUDE_15M + IMPULSE_15M)
    five = split_15m_to_5m(ohlc15)
    last = five[-1][3]
    retrace_5m = retrace_5m if retrace_5m is not None else GOOD_RETRACE
    five += legs(last, retrace_5m)
    return to_frame(five, Timeframe.M5)
