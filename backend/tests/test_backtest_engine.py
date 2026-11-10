"""Hand-checkable scenarios for the fill model (docs/ARCHITECTURE.md §3.3)."""

from __future__ import annotations

import numpy as np
import pytest
from conftest import TF, make_bars, random_walk_bars, zero_cost_config

from tradingos.backtest.engine import run_backtest
from tradingos.backtest.models import CostModel
from tradingos.domain.instruments import BTC_USDT_SWAP
from tradingos.risk.engine import RiskConfig
from tradingos.strategy.base import empty_signals


def signals_for(bars, entries: dict[int, tuple[int, float, float | None]], exits_long=(), exits_short=()):
    sig = empty_signals(bars.index)
    for i, (side, stop, target) in entries.items():
        sig.loc[i, "entry"] = side
        sig.loc[i, "stop"] = stop
        sig.loc[i, "target"] = np.nan if target is None else target
        sig.loc[i, "reason"] = "test"
    for i in exits_long:
        sig.loc[i, "exit_long"] = True
    for i in exits_short:
        sig.loc[i, "exit_short"] = True
    return sig


FLAT = (100.0, 100.5, 99.5, 100.0)


def test_entry_fills_at_next_open_not_signal_close():
    bars = make_bars([FLAT, (101, 101.5, 100.5, 101), (102, 102.5, 101.5, 102), FLAT])
    sig = signals_for(bars, {0: (1, 90.0, None)})
    res = run_backtest(bars, sig, zero_cost_config(bars), BTC_USDT_SWAP)
    t = res.trades[0]
    assert t.entry_ts == bars["ts"][1]
    assert t.entry_price == 101.0  # open of bar 1, not close of bar 0
    assert t.exit_reason == "end_of_data"


def test_long_stop_hit_and_risk_sized_to_quarter_percent():
    # entry 100, stop 90 -> risk 10/unit; 0.25% of 10k = 25 -> qty 2.5
    bars = make_bars([FLAT, FLAT, (99, 99.5, 89, 89.5), FLAT])
    sig = signals_for(bars, {0: (1, 90.0, 130.0)})
    res = run_backtest(bars, sig, zero_cost_config(bars), BTC_USDT_SWAP)
    t = res.trades[0]
    assert t.qty == pytest.approx(2.5)
    assert t.exit_reason == "stop" and t.exit_price == 90.0
    assert t.net_pnl == pytest.approx(-25.0)
    assert t.r_multiple == pytest.approx(-1.0)


def test_short_target_hit():
    bars = make_bars([FLAT, FLAT, (99, 99.5, 79, 80), FLAT])
    sig = signals_for(bars, {0: (-1, 110.0, 80.0)})
    res = run_backtest(bars, sig, zero_cost_config(bars), BTC_USDT_SWAP)
    t = res.trades[0]
    assert t.exit_reason == "target" and t.exit_price == 80.0
    assert t.net_pnl == pytest.approx(2.5 * 20)  # qty 25/10 = 2.5, +20 per unit
    assert t.r_multiple == pytest.approx(2.0)


def test_stop_and_target_in_same_candle_assumes_stop():
    bars = make_bars([FLAT, FLAT, (100, 125, 85, 100), FLAT])
    sig = signals_for(bars, {0: (1, 90.0, 120.0)})
    res = run_backtest(bars, sig, zero_cost_config(bars), BTC_USDT_SWAP)
    t = res.trades[0]
    assert t.exit_reason == "stop" and t.ambiguous_bar
    assert t.net_pnl < 0


def test_gap_through_stop_fills_at_worse_open():
    bars = make_bars([FLAT, FLAT, (85, 86, 80, 82), FLAT])
    sig = signals_for(bars, {0: (1, 90.0, None)})
    res = run_backtest(bars, sig, zero_cost_config(bars), BTC_USDT_SWAP)
    t = res.trades[0]
    assert t.exit_price == 85.0  # open, not the 90 stop
    assert t.r_multiple == pytest.approx(-1.5)


def test_entry_rejected_when_open_gaps_past_stop():
    bars = make_bars([FLAT, (88, 89, 87, 88), FLAT])
    sig = signals_for(bars, {0: (1, 90.0, None)})
    res = run_backtest(bars, sig, zero_cost_config(bars), BTC_USDT_SWAP)
    assert res.trades == []
    assert res.rejections[0].reason == "stop_on_wrong_side"


def test_signal_on_last_bar_is_never_executed():
    bars = make_bars([FLAT, FLAT])
    sig = signals_for(bars, {1: (1, 90.0, None)})
    res = run_backtest(bars, sig, zero_cost_config(bars), BTC_USDT_SWAP)
    assert res.trades == [] and res.signals_seen == 0


def test_fees_and_slippage_are_charged_adversely():
    bars = make_bars([FLAT, FLAT, FLAT, FLAT])
    costs = CostModel(taker_fee_rate=0.001, maker_fee_rate=0, slippage_bps=10, funding_rate_per_8h=0)
    sig = signals_for(bars, {0: (1, 90.0, None)}, exits_long=[1])
    res = run_backtest(bars, sig, zero_cost_config(bars, costs=costs), BTC_USDT_SWAP)
    t = res.trades[0]
    assert t.entry_price == pytest.approx(100.1)   # bought 10 bps higher
    assert t.exit_price == pytest.approx(99.9)     # sold 10 bps lower
    assert t.exit_reason == "signal_exit"
    expected_fees = (100.1 + 99.9) * t.qty * 0.001
    assert t.fees == pytest.approx(expected_fees)
    assert t.net_pnl == pytest.approx((99.9 - 100.1) * t.qty - expected_fees)
    assert res.equity["equity"].iloc[-1] == pytest.approx(10_000 + t.net_pnl)


def test_funding_charged_when_position_carried_across_funding_time():
    # T0 is 00:00 UTC; 15m bars -> 08:00 is bar 32. Hold from bar 1 to the end (bar 40).
    bars = make_bars([FLAT] * 41)
    costs = CostModel(taker_fee_rate=0, maker_fee_rate=0, slippage_bps=0, funding_rate_per_8h=0.001)
    sig = signals_for(bars, {0: (1, 90.0, None)})
    res = run_backtest(bars, sig, zero_cost_config(bars, costs=costs), BTC_USDT_SWAP)
    t = res.trades[0]
    assert t.funding == pytest.approx(0.001 * t.qty * 100.0)  # exactly one funding event
    assert t.net_pnl == pytest.approx(-t.funding)


def test_daily_loss_limit_blocks_new_entries_same_day():
    # risk 0.6%/trade, daily limit 1%: two losing stops (-60, -59.64) exceed -100 -> third entry blocked.
    # Signals at bars 0, 2, 4 fill at the open (100) of bars 1, 3, 5, which then hit the stop at 90.
    ohlc = []
    for _ in range(3):
        ohlc += [FLAT, (100, 100.5, 89, 89.5)]
    ohlc += [FLAT, FLAT]
    bars = make_bars(ohlc)
    entries = {0: (1, 90.0, None), 2: (1, 90.0, None), 4: (1, 90.0, None)}
    sig = signals_for(bars, entries)
    cfg = zero_cost_config(bars, risk=RiskConfig(risk_per_trade_pct=0.6, daily_loss_limit_pct=1.0, max_leverage=20))
    res = run_backtest(bars, sig, cfg, BTC_USDT_SWAP)
    assert len(res.trades) == 2
    assert [r.reason for r in res.rejections] == ["daily_loss_limit"]


def test_no_pyramiding_while_in_position():
    bars = make_bars([FLAT] * 5)
    sig = signals_for(bars, {0: (1, 90.0, None), 1: (1, 90.0, None)})
    res = run_backtest(bars, sig, zero_cost_config(bars), BTC_USDT_SWAP)
    assert len(res.trades) == 1
    assert res.rejections[0].reason == "already_in_position"


def test_reversal_exit_then_entry_on_same_open():
    bars = make_bars([FLAT] * 5)
    sig = signals_for(bars, {0: (1, 90.0, None), 2: (-1, 110.0, None)}, exits_long=[2])
    res = run_backtest(bars, sig, zero_cost_config(bars), BTC_USDT_SWAP)
    assert [t.side for t in res.trades] == [1, -1]
    assert res.trades[0].exit_ts == res.trades[1].entry_ts


def test_deterministic_on_random_data():
    bars = random_walk_bars(2_000, seed=3)
    rng = np.random.default_rng(1)
    entries = {}
    for i in rng.choice(np.arange(10, 1990), 60, replace=False):
        side = int(rng.choice([1, -1]))
        c = bars["close"][i]
        entries[int(i)] = (side, c * (1 - 0.01 * side), c * (1 + 0.02 * side))
    sig = signals_for(bars, entries)
    cfg = zero_cost_config(bars, costs=CostModel())
    a = run_backtest(bars, sig, cfg, BTC_USDT_SWAP)
    b = run_backtest(bars, sig, cfg, BTC_USDT_SWAP)
    assert [t.model_dump() for t in a.trades] == [t.model_dump() for t in b.trades]
    assert a.equity.equals(b.equity)
    # accounting identity: final equity = initial + sum of net PnL
    assert a.equity["equity"].iloc[-1] == pytest.approx(10_000 + sum(t.net_pnl for t in a.trades))
    assert len(bars) == a.bars and TF.ms == 900_000
