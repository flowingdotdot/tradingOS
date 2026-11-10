"""Look-ahead guard: every registered strategy version must be prefix-invariant.

If generate_signals(bars[:k]) differs from generate_signals(bars)[:k] on any row,
the strategy used information from the future.
"""

from __future__ import annotations

import numpy as np
import pytest
from conftest import random_walk_bars
from pydantic import BaseModel

from tradingos.config import BACKEND_DIR
from tradingos.strategy.base import Strategy, empty_signals
from tradingos.strategy.causality import LookAheadError, check_causality
from tradingos.strategy.registry import build_strategy, load_versions

VERSIONS = load_versions(BACKEND_DIR / "config" / "strategies")


@pytest.mark.parametrize("version", VERSIONS, ids=lambda v: f"{v.strategy_id}:{v.version}")
def test_registered_strategies_are_causal(version):
    bars = random_walk_bars(3_000, seed=11)
    strategy = build_strategy(version.strategy_id, version.params)
    full = strategy.generate_signals(bars)
    assert (full["entry"] != 0).sum() > 5, "test data should produce some signals"
    check_causality(strategy, bars)


@pytest.mark.parametrize("version", VERSIONS, ids=lambda v: f"{v.strategy_id}:{v.version}")
def test_stops_on_correct_side_and_no_signal_in_warmup(version):
    bars = random_walk_bars(3_000, seed=5)
    strategy = build_strategy(version.strategy_id, version.params)
    sig = strategy.generate_signals(bars)
    longs, shorts = sig["entry"] == 1, sig["entry"] == -1
    assert (sig.loc[longs, "stop"] < bars.loc[longs, "close"]).all()
    assert (sig.loc[shorts, "stop"] > bars.loc[shorts, "close"]).all()
    assert (sig["entry"].iloc[: strategy.warmup_bars()] == 0).all()


class _NoParams(BaseModel):
    pass


class _PeekingStrategy(Strategy):
    """Deliberately cheats by looking one bar ahead — the guard must catch it."""

    strategy_id = "peek"
    Params = _NoParams

    def warmup_bars(self) -> int:
        return 0

    def generate_signals(self, bars):
        sig = empty_signals(bars.index)
        up_next = (bars["close"].shift(-1) > bars["close"]).to_numpy()
        sig.loc[up_next, "entry"] = 1
        sig.loc[up_next, "stop"] = bars.loc[up_next, "close"] * 0.99
        return sig


def test_guard_detects_look_ahead():
    bars = random_walk_bars(500, seed=2)
    with pytest.raises(LookAheadError):
        check_causality(_PeekingStrategy(), bars)


def test_guard_detects_whole_series_normalisation():
    """Subtler leak: normalising by the max of the WHOLE series uses future data."""

    class _Normaliser(_PeekingStrategy):
        def generate_signals(self, bars):
            sig = empty_signals(bars.index)
            hot = (bars["close"] / bars["close"].max() > 0.97).to_numpy()
            sig.loc[hot, "entry"] = -1
            sig.loc[hot, "stop"] = bars.loc[hot, "high"] * 1.01
            return sig

    with pytest.raises(LookAheadError):
        check_causality(_Normaliser(), random_walk_bars(1_000, seed=4))


def test_ema_cross_shorts_only_when_configured():
    v2 = next(v for v in VERSIONS if v.version == "v2-short-only")
    sig = build_strategy(v2.strategy_id, v2.params).generate_signals(random_walk_bars(3_000, seed=9))
    assert not (sig["entry"] == 1).any()
    assert np.isfinite(sig.loc[sig["entry"] == -1, "target"]).all()
