"""Smoke tests against the REAL OKX public API. Deselected by default.

Run with:  pytest -m network
"""

from __future__ import annotations

import time

import pytest

from tradingos.domain.timeframes import Timeframe
from tradingos.marketdata.okx_public import OkxPublicClient

pytestmark = pytest.mark.network


def test_real_history_candles_are_confirmed_and_aligned():
    with OkxPublicClient() as c:
        df, dropped = c.get_history_candles("BTC-USDT-SWAP", Timeframe.M15, limit=100)
    assert 90 <= len(df) <= 100
    assert (df["ts"] % Timeframe.M15.ms == 0).all()
    assert df["ts"].iloc[-1] < int(time.time() * 1000)
    assert dropped <= 1


def test_real_instrument_spec():
    with OkxPublicClient() as c:
        spec = c.get_instrument("BTC-USDT-SWAP")
    assert spec.ct_val == 0.01 and spec.tick_sz > 0
