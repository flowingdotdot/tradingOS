"""Look-ahead (causality) checker for strategies.

A causal strategy satisfies: generate_signals(bars[:k]).iloc[-1] == generate_signals(bars).iloc[k-1]
for every k. That is exactly how the live loop will use a strategy (rolling window,
last row only), so this check also proves backtest/live decision equivalence.

A look-ahead bug often only shows on the LAST row of a prefix (e.g. shift(-1) yields
NaN there), and only when the future happens to differ — so we test many random cut
points, not a handful.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from tradingos.strategy.base import Strategy, validate_signals


class LookAheadError(AssertionError):
    pass


def check_causality(
    strategy: Strategy,
    bars: pd.DataFrame,
    *,
    n_cuts: int = 80,
    full_prefix_checks: int = 3,
    seed: int = 0,
    extra_cuts: list[int] | None = None,
) -> None:
    full = strategy.generate_signals(bars)
    validate_signals(full, bars)
    rng = np.random.default_rng(seed)
    lo = max(2, min(strategy.warmup_bars(), len(bars) - 2))
    cuts = set(rng.integers(lo, len(bars), size=n_cuts).tolist()) | {len(bars) - 1}
    cuts |= {k for k in (extra_cuts or []) if 2 <= k <= len(bars)}
    cuts = sorted(cuts)
    failures: list[int] = []
    for idx, k in enumerate(cuts):
        part = strategy.generate_signals(bars.iloc[:k].copy())
        expected = full.iloc[:k]
        if idx < full_prefix_checks:
            same = part.equals(expected)
        else:
            same = part.iloc[[-1]].equals(expected.iloc[[-1]])
        if not same:
            failures.append(k)
    if failures:
        raise LookAheadError(
            f"{type(strategy).__name__}: signals change when future bars are removed "
            f"(prefix lengths {failures[:10]}{'...' if len(failures) > 10 else ''})"
        )
