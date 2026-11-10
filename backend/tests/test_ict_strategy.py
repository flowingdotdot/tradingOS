"""Bearish OTE Continuation on hand-designed scenarios (tests/synth.py)."""

from __future__ import annotations

import pytest
import synth
from conftest import random_walk_bars, zero_cost_config
from pydantic import ValidationError

from tradingos.backtest.engine import run_backtest
from tradingos.domain.instruments import BTC_USDT_SWAP
from tradingos.domain.timeframes import Timeframe
from tradingos.strategy.builtin.ict_ote import IctOteStrategy
from tradingos.strategy.causality import check_causality

M5, M15 = Timeframe.M5, Timeframe.M15
NO_BIAS = {"bias": {"enabled": False}}


def analyze(retrace=None, **params):
    p = {**NO_BIAS, **params}
    bars = synth.bearish_ote_scenario(retrace)
    strat = IctOteStrategy(p)
    return bars, strat, strat.analyze(bars)


def only_setup(a):
    assert len(a.setups) == 1, [s["status"] for s in a.setups]
    return a.setups[0]


def test_good_scenario_produces_exactly_one_short_signal():
    bars, strat, a = analyze()
    s = only_setup(a)
    assert s["status"] == "signal"
    assert s["break_kind"] == "MSS" and s["a"] == pytest.approx(120.6) and s["b"] == pytest.approx(89.4)
    assert s["entry_ref"] == pytest.approx(104.0)
    assert s["stop"] == pytest.approx(112.6 * 1.0005)            # retracement high + 5 bps
    assert s["target"] == pytest.approx(89.4)                     # previous confirmed low (B)
    assert s["rr"] == pytest.approx((104 - 89.4) / (112.6 * 1.0005 - 104))
    sig = a.signals[a.signals["entry"] != 0]
    assert len(sig) == 1 and sig["entry"].iloc[0] == -1
    j = sig.index[0]
    assert bars["ts"].iloc[j] == s["trigger_ts"] and bars["close"].iloc[j] == pytest.approx(104.0)
    assert any(z["fvg_ts"] is not None for z in s["zones"])      # OTE ∩ FVG zones


def test_known_times_respect_timeframe_boundaries():
    bars, strat, a = analyze()
    s = only_setup(a)
    imp_bars = a.impulse.bars
    # the impulse is known at the CLOSE of the 15m bar that confirms swing low B (k=3 right bars)
    assert s["known_ts"] == imp_bars["ts"].iloc[s["b_known_idx"]] + M15.ms
    assert s["b_known_idx"] == s["b_idx"] + 3
    assert s["known_ts"] % M15.ms == 0
    assert s["touched_ts"] >= s["known_ts"]                       # touch only checked after it is known
    assert s["trigger_known_ts"] == s["trigger_ts"] + M5.ms
    assert s["mss_ref_ts"] >= s["b5_ts"]                          # MSS broke a swing formed after B


def test_insufficient_reward_risk_is_rejected_and_threshold_is_configurable():
    _, _, a = analyze(synth.LATE_MSS_RETRACE)
    s = only_setup(a)
    assert s["status"] == "rejected_rr" and s["rr"] == pytest.approx(0.42, abs=0.01)
    assert (a.signals["entry"] == 0).all()
    _, _, a2 = analyze(synth.LATE_MSS_RETRACE, target={"min_rr": 0.4})
    assert only_setup(a2)["status"] == "signal"


def test_zone_never_reached_then_new_low():
    _, _, a = analyze(synth.NO_TOUCH_RETRACE)
    s = only_setup(a)
    assert s["status"] == "impulse_extended" and s["touched_ts"] is None


def test_retracement_above_impulse_origin_invalidates():
    _, _, a = analyze(synth.ABOVE_A_RETRACE)
    assert only_setup(a)["status"] == "invalidated"


def test_target_on_wrong_side_of_entry_is_rejected():
    _, _, a = analyze(synth.TARGET_PASSED_RETRACE)
    s = only_setup(a)
    assert s["status"] == "rejected_target" and s["entry_ref"] < s["target"]


def test_no_duplicate_signals_from_one_setup():
    # after the signal, two more MSS-like drops happen; the consumed setup must not fire again
    extra = synth.GOOD_RETRACE + [(109, 1), (106, 1), (111, 1), (103, 4), (100, 1)]
    _, _, a = analyze(extra)
    assert (a.signals["entry"] != 0).sum() == 1
    assert [s["status"] for s in a.setups].count("signal") == 1


def test_trigger_touch_variant_signals_at_the_touch_bar():
    _, _, a = analyze(entry={"trigger": "touch"})
    s = only_setup(a)
    assert s["status"] in ("signal", "rejected_rr")
    assert s["trigger_ts"] == s["touched_ts"]


def test_fvg_retest_variant_emits_limit_entry_which_phase1_engine_refuses():
    bars, strat, a = analyze(entry={"mode": "fvg_retest"})
    s = only_setup(a)
    assert s["status"] == "signal" and s["limit_price"] == pytest.approx(108.6)
    assert s["retest_outcome"]["outcome"] in ("would_fill", "expired_unfilled", "cancelled_target_first")
    sig = a.signals[a.signals["entry"] != 0]
    assert sig["limit_price"].iloc[0] == pytest.approx(108.6)
    with pytest.raises(NotImplementedError):
        run_backtest(bars, a.signals, zero_cost_config(bars), BTC_USDT_SWAP)


def test_component_toggles():
    _, _, a = analyze(fvg={"enabled": False})
    s = only_setup(a)
    assert s["zones"] == [pytest.approx(s["zones"][0])] and s["zones"][0]["fvg_ts"] is None
    assert (s["zones"][0]["bottom"], s["zones"][0]["top"]) == pytest.approx((s["ote_low"], s["ote_high"]))

    _, _, a = analyze(ote={"enabled": False})
    zs = only_setup(a)["zones"]
    assert zs and all((z["bottom"], z["top"]) == (z["fvg_bottom"], z["fvg_top"]) for z in zs)

    with pytest.raises(ValidationError):
        IctOteStrategy({"fvg": {"enabled": False}, "ote": {"enabled": False}})

    _, _, a = analyze(displacement={"enabled": False})
    assert only_setup(a)["status"] in ("signal", "rejected_rr")


def test_bias_filter_blocks_setups_without_bearish_4h_structure():
    bars = synth.bearish_ote_scenario()
    a = IctOteStrategy({}).analyze(bars)          # bias enabled: ~5 days of data -> no 4H structure
    assert {s["status"] for s in a.setups} == {"bias_not_bearish"}
    assert (a.signals["entry"] == 0).all()


def test_missing_5m_bar_drops_the_15m_bucket_and_no_fvg_spans_the_gap():
    bars = synth.bearish_ote_scenario()
    gap_at = 430                                   # inside the steep 15m impulse leg
    gapped = bars.drop(index=gap_at).reset_index(drop=True)
    a = IctOteStrategy(NO_BIAS).analyze(gapped)
    full = IctOteStrategy(NO_BIAS).analyze(bars)
    assert len(a.impulse.bars) == len(full.impulse.bars) - 1
    f, ts = a.impulse.fvgs, a.impulse.bars["ts"].to_numpy()
    for r in f.itertuples():
        assert ts[r.known_idx] - ts[r.first_idx] == 2 * M15.ms


def test_scenario_signals_are_causal_around_the_trigger():
    bars, strat, a = analyze()
    j = int(a.signals.index[a.signals["entry"] != 0][0])
    check_causality(strat, bars, n_cuts=30, extra_cuts=[j - 1, j, j + 1, j + 2])


@pytest.mark.parametrize("mode", ["mss_close", "fvg_retest"])
def test_random_walk_signals_are_causal_with_all_filters_on(mode):
    # Causality does not depend on parameter values, so min_rr is relaxed to make sure the
    # signal path is actually exercised (a causality test with zero signals proves nothing).
    bars = random_walk_bars(8_000, seed=21, tf=M5)
    strat = IctOteStrategy({"entry": {"mode": mode}, "target": {"min_rr": 0.5}})
    sig = strat.generate_signals(bars)
    hits = list(sig.index[sig["entry"] != 0])
    assert len(hits) >= 3
    check_causality(strat, bars, n_cuts=25, extra_cuts=[k for j in hits for k in (j, j + 1, j + 2)])


def test_real_okx_data_is_causal_if_available():
    from tradingos.config import get_settings
    from tradingos.marketdata.store import CandleStore

    bars = CandleStore(get_settings().market_data_dir).load("BTC-USDT-SWAP", M5)
    if len(bars) < 20_000:
        pytest.skip("no local 5m OKX data (run: tradingos collect --tf 5m --days 180)")
    bars = bars.iloc[-20_000:].reset_index(drop=True)
    strat = IctOteStrategy({"target": {"min_rr": 0.5}})
    sig = strat.generate_signals(bars)
    hits = list(sig.index[sig["entry"] != 0])
    assert hits, "expected at least one signal on ~70 days of real data with relaxed min_rr"
    check_causality(strat, bars, n_cuts=10, extra_cuts=[k for j in hits for k in (j, j + 1)])
