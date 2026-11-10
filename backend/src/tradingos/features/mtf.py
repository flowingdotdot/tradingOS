"""Multi-timeframe construction and alignment (docs/STRATEGY_SPEC.md §0).

Higher-timeframe (HTF) bars are BUILT from the base (LTF) bars:
  * bucket = floor(ts / htf) * htf  (UTC-aligned; OKX 15m/4H boundaries are UTC-aligned)
  * an HTF bar exists only if ALL its LTF bars are present (count == htf/ltf). A bucket with a
    missing LTF bar — or the still-forming last bucket — is dropped, never partially used.
  * the HTF bar becomes usable at its close = bucket + htf, which equals the close of its last
    LTF bar. A decision at the close of LTF bar j (ts_j + ltf) may use HTF information whose
    known_ts <= ts_j + ltf.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from tradingos.domain.timeframes import Timeframe


def resample(bars: pd.DataFrame, ltf: Timeframe, htf: Timeframe) -> pd.DataFrame:
    if htf.ms % ltf.ms:
        raise ValueError(f"{htf} is not a multiple of {ltf}")
    ratio = htf.ms // ltf.ms
    b = bars.assign(bucket=bars["ts"] - bars["ts"] % htf.ms)
    agg = b.groupby("bucket", sort=True).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
        volume=("volume", "sum"), n=("ts", "size"), first_ts=("ts", "min"),
    )
    complete = (agg["n"] == ratio) & (agg["first_ts"] == agg.index)
    out = agg[complete].drop(columns=["n", "first_ts"]).reset_index().rename(columns={"bucket": "ts"})
    out["ts"] = out["ts"].astype("int64")
    return out


def asof_index(known_ts: np.ndarray, decision_ts: np.ndarray) -> np.ndarray:
    """For each decision time, index of the last item with known_ts <= decision time (-1 if none).
    known_ts must be sorted ascending."""
    return np.searchsorted(known_ts, decision_ts, side="right") - 1


def map_series_to_ltf(htf_bars: pd.DataFrame, htf: Timeframe, values: np.ndarray,
                      ltf_bars: pd.DataFrame, ltf: Timeframe, fill: float = 0) -> np.ndarray:
    """Value of an HTF per-bar series as known at each LTF bar's close."""
    htf_close = htf_bars["ts"].to_numpy(dtype="int64") + htf.ms
    decision = ltf_bars["ts"].to_numpy(dtype="int64") + ltf.ms
    k = asof_index(htf_close, decision)
    out = np.where(k >= 0, np.asarray(values)[np.clip(k, 0, None)], fill)
    return out
