"""Strategy interface shared by backtesting and (Phase 4) live execution.

CAUSALITY CONTRACT: row i of `generate_signals(bars)` may depend only on rows 0..i
of `bars`. The backtest computes all rows at once; the live loop will call it on a
rolling window and use only the last row. tests/test_strategy_causality.py checks
the contract for every registered strategy.

Strategies express *intent* (side, stop, target). Quantity and authorization are
the Risk Engine's job.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

import numpy as np
import pandas as pd
from pydantic import BaseModel

SIGNAL_COLUMNS = ["entry", "stop", "target", "exit_long", "exit_short", "reason"]
# Optional order details. NaN limit_price = market order at the next open (Phase-1 engine).
# A finite limit_price requests a limit entry, which the engine supports from Phase 3.
OPTIONAL_SIGNAL_COLUMNS = ["limit_price", "limit_expiry_bars"]


def empty_signals(index: pd.Index) -> pd.DataFrame:
    n = len(index)
    return pd.DataFrame(
        {
            "entry": np.zeros(n, dtype="int8"),
            "stop": np.full(n, np.nan),
            "target": np.full(n, np.nan),
            "exit_long": np.zeros(n, dtype=bool),
            "exit_short": np.zeros(n, dtype=bool),
            "reason": np.full(n, "", dtype=object),
            "limit_price": np.full(n, np.nan),
            "limit_expiry_bars": np.full(n, np.nan),
        },
        index=index,
    )


class Strategy(ABC):
    strategy_id: ClassVar[str]
    # If set, the strategy only runs on this bar timeframe (e.g. "5m"); higher timeframes are
    # built internally from it so that timeframe boundaries are handled in one place.
    required_timeframe: ClassVar[str | None] = None
    description: ClassVar[str] = ""
    Params: ClassVar[type[BaseModel]]

    def __init__(self, params: dict[str, Any] | None = None):
        self.params = self.Params(**(params or {}))

    @abstractmethod
    def warmup_bars(self) -> int:
        """Bars needed before signals are meaningful; no entries are produced before this."""

    @abstractmethod
    def generate_signals(self, bars: pd.DataFrame) -> pd.DataFrame:
        """bars: columns ts, open, high, low, close, volume (ascending, closed candles only)."""


def has_limit_entries(signals: pd.DataFrame) -> bool:
    return "limit_price" in signals.columns and bool(
        np.isfinite(signals.loc[signals["entry"] != 0, "limit_price"].to_numpy(dtype=float)).any()
    )


def validate_signals(signals: pd.DataFrame, bars: pd.DataFrame) -> None:
    """Structural checks applied by the engine to every strategy's output."""
    missing = set(SIGNAL_COLUMNS) - set(signals.columns)
    if missing:
        raise ValueError(f"signals missing columns: {sorted(missing)}")
    if len(signals) != len(bars):
        raise ValueError("signals must have one row per bar")
    entries = signals["entry"] != 0
    if not signals.loc[entries, "entry"].isin([1, -1]).all():
        raise ValueError("entry must be -1, 0 or +1")
    if signals.loc[entries, "stop"].isna().any():
        raise ValueError("every entry signal requires a stop price")
