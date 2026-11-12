"""The single way strategy code obtains market data: OOS seal + data-quality rules.

Every strategy evaluation path (backtest, inspection, research chart, audit) loads bars through
`load_strategy_bars`, so the seal and the zero-volume rule cannot be bypassed by one of them.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from tradingos.config import Settings
from tradingos.domain.timeframes import Timeframe
from tradingos.marketdata.quality import apply_quality_rules
from tradingos.marketdata.store import CandleStore
from tradingos.research.split import OosSealedError, ResearchSplit, guard_period, load_split


class PeriodNotAllowedError(ValueError):
    """The requested period overlaps the sealed out-of-sample window."""


def research_split(settings: Settings) -> ResearchSplit | None:
    return load_split(settings.research_split_file)


def load_strategy_bars(settings: Settings, inst_id: str, tf: Timeframe, start_ts: int, end_ts: int,
                       warmup_bars: int, purpose: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Bars for [start - warmup, end) after the quality rules, plus a context record that is
    stored with the run: split, segment label, quality report."""
    split = research_split(settings)
    try:
        segment = guard_period(split, start_ts, end_ts, purpose)
    except OosSealedError as exc:
        raise PeriodNotAllowedError(str(exc)) from exc
    # Sealed OOS bars are never read — not even as indicator warm-up. A period that starts right
    # after the sealed window therefore warms up INSIDE its own window and trades only afterwards.
    load_from, trade_start = start_ts - warmup_bars * tf.ms, start_ts
    clamped = False
    if split is not None and split.oos_sealed and load_from < split.oos_end <= start_ts:
        load_from = split.oos_end
        trade_start = max(start_ts, split.oos_end + warmup_bars * tf.ms)
        clamped = True
    raw = CandleStore(settings.market_data_dir).load(inst_id, tf, load_from, end_ts)
    minutes = split.zero_volume_invalid_minutes if split is not None else 30
    bars, quality = apply_quality_rules(raw, tf, minutes) if len(raw) else (raw, {})
    context = {
        "segment": segment,
        "research_split": split.public() if split is not None else None,
        "oos_unsealed_run": bool(split is not None and not split.oos_sealed and split.overlaps_oos(start_ts, end_ts)),
        "warmup_data_segment": split.segment(load_from, max(trade_start, load_from + 1)) if split is not None else None,
        # first instant at which trading / signal evaluation is allowed (>= requested start)
        "trade_start_ts": trade_start,
        "warmup_clamped_to_oos_end": clamped,
        "quality": quality,
    }
    return bars, context
