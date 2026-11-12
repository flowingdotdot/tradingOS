"""Data-quality rules applied before any strategy evaluation.

OKX fills exchange outages with flat, zero-volume candles instead of leaving gaps, so "no missing
bars" does not mean "no outage". Rule (approved 2026-11-12):
  * a run of zero-volume bars lasting >= `invalid_minutes` (30 min = 6 x 5m bars) is treated as
    MISSING: the bars are removed, so the existing gap rules apply (higher-timeframe buckets
    containing them are dropped, swings/FVGs cannot span them, no entry right after them);
  * shorter runs are kept but FLAGGED (their OHLC is the previous close repeated, which is why
    higher-timeframe bars built from them can differ from the exchange's own aggregates).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from tradingos.domain.timeframes import Timeframe


def zero_volume_runs(bars: pd.DataFrame) -> list[tuple[int, int]]:
    """[(start_index, length)] of consecutive zero-volume bars (positional indices)."""
    zero = (bars["volume"].to_numpy() == 0)
    if not zero.any():
        return []
    edges = np.diff(np.concatenate([[0], zero.astype(np.int8), [0]]))
    starts, ends = np.nonzero(edges == 1)[0], np.nonzero(edges == -1)[0]
    return [(int(s), int(e - s)) for s, e in zip(starts, ends)]


def invalid_run_bars(tf: Timeframe, invalid_minutes: int) -> int:
    return max(1, math.ceil(invalid_minutes / tf.minutes)) if invalid_minutes > 0 else 0


def apply_quality_rules(bars: pd.DataFrame, tf: Timeframe, invalid_minutes: int = 30) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Returns (bars with invalid runs removed, report). Never fabricates or alters candle values."""
    bars = bars.reset_index(drop=True)
    ts = bars["ts"].to_numpy(dtype="int64") if len(bars) else np.array([], dtype="int64")
    min_bars = invalid_run_bars(tf, invalid_minutes)
    runs = zero_volume_runs(bars) if len(bars) else []
    invalid = [(s, n) for s, n in runs if min_bars and n >= min_bars]
    short = [(s, n) for s, n in runs if not (min_bars and n >= min_bars)]
    keep = np.ones(len(bars), dtype=bool)
    for s, n in invalid:
        keep[s : s + n] = False
    report = {
        "rule": f"zero-volume run >= {invalid_minutes} min ({min_bars} x {tf.value}) is treated as missing"
                if min_bars else "zero-volume rule disabled",
        "invalid_minutes": invalid_minutes,
        "zero_volume_bars": int(sum(n for _, n in runs)),
        "invalid_runs": [{"start_ts": int(ts[s]), "end_ts": int(ts[s + n - 1]), "bars": n} for s, n in invalid],
        "bars_removed": int((~keep).sum()),
        "flagged_short_runs": [{"start_ts": int(ts[s]), "bars": n} for s, n in short],
        "flagged_short_bars": int(sum(n for _, n in short)),
    }
    return (bars[keep].reset_index(drop=True) if invalid else bars), report
