"""Candle data integrity checks. Problems are reported, never silently 'fixed'."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from tradingos.domain.timeframes import Timeframe


@dataclass
class Gap:
    start_ts: int        # open time of the first MISSING candle
    end_ts: int          # open time of the last missing candle
    missing_bars: int


@dataclass
class ValidationReport:
    rows: int
    first_ts: int | None
    last_ts: int | None
    duplicates: int = 0
    misaligned: int = 0
    unsorted: bool = False
    invalid_ohlc: int = 0
    gaps: list[Gap] = field(default_factory=list)

    @property
    def missing_bars(self) -> int:
        return sum(g.missing_bars for g in self.gaps)

    @property
    def ok(self) -> bool:
        """Structural integrity. Gaps alone don't fail validation (exchanges have real outages)
        but they are surfaced to the user in every backtest result."""
        return not (self.duplicates or self.misaligned or self.unsorted or self.invalid_ohlc)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["missing_bars"] = self.missing_bars
        d["ok"] = self.ok
        return d


def validate_candles(df: pd.DataFrame, tf: Timeframe) -> ValidationReport:
    if df.empty:
        return ValidationReport(rows=0, first_ts=None, last_ts=None)
    ts = df["ts"].to_numpy(dtype="int64")
    report = ValidationReport(rows=len(df), first_ts=int(ts.min()), last_ts=int(ts.max()))
    report.duplicates = int(pd.Series(ts).duplicated().sum())
    report.unsorted = bool(np.any(np.diff(ts) < 0))
    report.misaligned = int(np.sum(ts % tf.ms != 0))
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    bad = (h < np.maximum(o, c)) | (l > np.minimum(o, c)) | (l <= 0) | ~np.isfinite(o + h + l + c)
    report.invalid_ohlc = int(bad.sum())

    uniq = np.unique(ts)
    diffs = np.diff(uniq)
    for idx in np.nonzero(diffs > tf.ms)[0]:
        missing = int(diffs[idx] // tf.ms) - 1
        start = int(uniq[idx] + tf.ms)
        report.gaps.append(Gap(start_ts=start, end_ts=start + (missing - 1) * tf.ms, missing_bars=missing))
    return report
