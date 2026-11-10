"""Feature modules on hand-built OHLC with known expected results."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from conftest import random_walk_bars
from synth import T0, legs, to_frame

from tradingos.domain.timeframes import Timeframe
from tradingos.features.displacement import displacement
from tradingos.features.fvg import detect_fvgs
from tradingos.features.mtf import map_series_to_ltf, resample
from tradingos.features.ote import DealingRange, intersect
from tradingos.features.structure import structure_events, swing_sequence_bias
from tradingos.features.swings import detect_swings

TF = Timeframe.M15


def hl_bars(highs: list[float], lows: list[float] | None = None, tf: Timeframe = TF) -> pd.DataFrame:
    lows = lows or [x - 1 for x in highs]
    ohlc = [((h + l) / 2, h, l, (h + l) / 2) for h, l in zip(highs, lows)]
    return to_frame(ohlc, tf)


def ohlc_bars(rows: list[tuple[float, float, float, float]], tf: Timeframe = TF) -> pd.DataFrame:
    return to_frame(rows, tf)


# ------------------------------------------------------------------------------- swings
def test_swing_high_occurrence_vs_known_time():
    bars = hl_bars([1, 2, 3, 5, 3, 2, 1, 1])
    sw = detect_swings(bars, TF, 3, 3)
    hi = sw[sw["kind"] == "high"]
    assert hi["idx"].tolist() == [3]
    row = hi.iloc[0]
    assert row["ts"] == T0 + 3 * TF.ms             # where it is drawn
    assert row["known_idx"] == 6                   # only after 3 right bars have CLOSED
    assert row["known_ts"] == T0 + 7 * TF.ms       # = close time of bar 6


def test_equal_highs_earliest_bar_is_the_swing():
    sw = detect_swings(hl_bars([1, 2, 3, 5, 5, 3, 2, 1, 1]), TF, 3, 3)
    assert sw[sw["kind"] == "high"]["idx"].tolist() == [3]


def test_unconfirmed_swing_is_not_reported():
    # only 2 right-hand bars exist after the peak -> not yet a swing (k_right = 3)
    sw = detect_swings(hl_bars([1, 2, 3, 5, 4, 3]), TF, 3, 3)
    assert sw[sw["kind"] == "high"].empty


def test_data_gap_inside_window_invalidates_swing():
    bars = hl_bars([1, 2, 3, 5, 3, 2, 1, 1])
    gapped = bars.drop(index=5).reset_index(drop=True)          # one missing candle right of the peak
    gapped.loc[len(gapped)] = [T0 + 8 * TF.ms, 0.5, 1, 0, 0.5, 1, 1, 1]
    assert detect_swings(gapped, TF, 3, 3).query("kind == 'high'").empty
    assert detect_swings(gapped, TF, 3, 3, require_contiguous=False).query("kind == 'high'")["idx"].tolist() == [3]


def test_outside_bar_can_be_both_swing_high_and_low():
    highs = [5, 5.5, 6, 9, 6, 5.5, 5]
    lows = [4, 3.5, 3, 0, 3, 3.5, 4]
    sw = detect_swings(hl_bars(highs, lows), TF, 3, 3)
    assert sorted(sw["kind"].tolist()) == ["high", "low"] and set(sw["idx"]) == {3}


def test_swings_are_prefix_consistent():
    bars = random_walk_bars(800, seed=3)
    full = detect_swings(bars, TF, 3, 3)
    for k in (100, 333, 512, 799):
        part = detect_swings(bars.iloc[:k], TF, 3, 3)
        expected = full[full["known_idx"] < k].sort_values(["known_idx", "idx", "kind"]).reset_index(drop=True)
        pd.testing.assert_frame_equal(part, expected, check_dtype=False)


# ---------------------------------------------------------------------------- structure
def run_structure(bars, k=3, disp=None):
    sw = detect_swings(bars, TF, k, k)
    return structure_events(bars, TF, sw, disp, None)


def test_consecutive_bearish_bos():
    bars = to_frame(legs(100, [(95, 1), (98, 1), (90, 1), (93, 1), (85, 1)]), TF)
    ev, trend = run_structure(bars)
    br = ev[ev["kind"] != "SWEEP"]
    assert br["kind"].tolist() == ["BOS", "BOS"]
    assert br["direction"].tolist() == [-1, -1]
    assert br["ref_price"].tolist() == pytest.approx([94.4, 89.4])
    assert trend[-1] == -1


def test_mss_is_a_break_against_the_prior_direction():
    bars = to_frame(legs(100, [(110, 1), (105, 1), (115, 1), (100, 1)]), TF)
    ev, _ = run_structure(bars)
    br = ev[ev["kind"] != "SWEEP"]
    assert list(zip(br["kind"], br["direction"])) == [("BOS", 1), ("MSS", -1)]
    mss = br.iloc[-1]
    assert mss["prior_trend"] == 1 and mss["ref_price"] == pytest.approx(104.4)
    assert mss["leg_extreme_price"] == pytest.approx(115.6)   # top of the leg that broke structure
    # reference swing must have been KNOWN before the breaking bar opened
    assert mss["ref_known_ts"] <= mss["ts"]


def test_break_requires_close_and_sweep_then_return_is_not_a_break():
    base = legs(100, [(110, 1), (105, 1), (108, 1)])            # swing high 110.6, swing low 104.4
    sweep_bar = [(108, 112.0, 107.5, 109.0)]                    # wick above 110.6, close back below
    after = legs(109.0, [(111.5, 1)])                           # later genuine close above 110.6
    bars = to_frame(base + sweep_bar + after, TF)
    ev, trend = run_structure(bars)
    sweep_idx = len(base)
    sw = ev[ev["kind"] == "SWEEP"]
    assert sw["idx"].tolist()[0] == sweep_idx and sw["direction"].tolist()[0] == -1   # buy-side sweep
    assert trend[sweep_idx] == trend[sweep_idx - 1]                                   # no structure change
    breaks = ev[(ev["kind"] != "SWEEP") & (ev["idx"] >= sweep_idx)]
    assert breaks.iloc[0]["direction"] == 1 and breaks.iloc[0]["idx"] > sweep_idx       # swing not consumed


def test_displacement_flag_on_break_leg():
    bars = to_frame(legs(100, [(110, 1), (105, 1), (115, 1), (100, 5)]), TF)
    disp = displacement(bars, lookback=5, mult=1.5)["bear"].to_numpy()
    ev, _ = run_structure(bars, disp=disp)
    assert ev[ev["kind"] == "MSS"]["has_displacement"].tolist() == [True]


def test_swing_sequence_bias_lower_highs_and_lows():
    bars = to_frame(legs(120, [(110, 1), (116, 1), (104, 1), (112, 1), (98, 1), (101, 1)]), TF)
    sw = detect_swings(bars, TF, 3, 3)
    bias = swing_sequence_bias(sw, len(bars))
    assert bias[-1] == -1
    first_known = sw.groupby("kind")["known_idx"].nth(1).max()   # need two of each kind
    assert (bias[:first_known] == 0).all()


# ------------------------------------------------------------------------- displacement
def test_median_body_displacement():
    rows = [(100, 101.5, 99.5, 101)] * 20 + [(101, 101.2, 99.4, 99.5), (99.5, 99.6, 98, 98.1)]
    d = displacement(ohlc_bars(rows), method="median_body", lookback=20, mult=1.5)
    assert not d["bear"].iloc[:20].any() and not d["bull"].iloc[:20].any()   # not enough history
    assert d["bear"].iloc[20]          # body 1.5 >= 1.5 * median(1)
    assert not d["bear"].iloc[21]      # body 1.4 < 1.5


def test_atr_mode_and_body_ratio_alternatives():
    rows = [(100, 101, 99, 100.5)] * 30 + [(100.5, 104, 96, 97)]
    bars = ohlc_bars(rows)
    assert displacement(bars, method="atr", atr_mult=1.2)["bear"].iloc[-1]
    # body 3.5 / range 8 = 0.44 -> filtered out by a 0.6 body-ratio requirement
    assert not displacement(bars, method="atr", atr_mult=1.2, min_body_ratio=0.6)["bear"].iloc[-1]


# --------------------------------------------------------------------------------- FVG
OVERLAP = [
    (112, 112.5, 110, 110.5),
    (110.5, 110.6, 107, 107.5),
    (106, 106.5, 103, 103.5),
    (103.5, 104, 99, 99.5),
]


def test_bearish_fvg_and_overlapping_fvgs_kept_separately():
    f = detect_fvgs(ohlc_bars(OVERLAP), TF, min_size_mode="none")
    bear = f[f["direction"] == -1].sort_values("known_idx")
    assert bear[["bottom", "top"]].values.tolist() == [[106.5, 110.0], [104.0, 107.0]]
    assert bear["idx"].tolist() == [1, 2] and bear["known_idx"].tolist() == [2, 3]
    assert bear["known_ts"].tolist() == [T0 + 3 * TF.ms, T0 + 4 * TF.ms]


def test_bullish_fvg():
    rows = [(100, 101, 99, 100.8), (100.8, 104, 100.7, 103.9), (103.9, 106, 102, 105.5)]
    f = detect_fvgs(ohlc_bars(rows), TF, min_size_mode="none")
    assert f[["direction", "bottom", "top"]].values.tolist() == [[1, 101.0, 102.0]]


def test_fvg_min_size_filter():
    bars = ohlc_bars([(100, 100.5, 99.5, 100)] * 20 + OVERLAP)
    assert len(detect_fvgs(bars, TF, min_size_mode="atr", min_size_atr=50)) == 0
    assert len(detect_fvgs(bars, TF, min_size_mode="atr", min_size_atr=0.1)) >= 2


def test_fvg_not_formed_across_missing_candle():
    bars = ohlc_bars(OVERLAP)
    bars.loc[2:, "ts"] += TF.ms           # a candle is missing between bar 1 and bar 2
    f = detect_fvgs(bars, TF, min_size_mode="none")
    assert f.empty


def test_fvg_lifecycle_and_invalidation_modes():
    rows = OVERLAP + [(99.5, 106.6, 99, 105), (105, 111, 104, 109)]
    for mode, expect_inval in (("close", -1), ("wick", 5)):
        f = detect_fvgs(ohlc_bars(rows), TF, min_size_mode="none", invalidation=mode)
        upper = f[(f["direction"] == -1) & (f["top"] == 110.0)].iloc[0]
        assert upper["touched_idx"] == 4 and upper["filled_idx"] == 5
        assert upper["invalidated_idx"] == expect_inval
    lower = f[(f["direction"] == -1) & (f["top"] == 107.0)].iloc[0]
    assert lower["touched_idx"] == 4 and lower["filled_idx"] == 5   # 106.6 < 107 -> not filled at bar 4


# --------------------------------------------------------------------------------- MTF
def test_resample_only_complete_buckets():
    m5 = Timeframe.M5
    bars = to_frame([(100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(8)], m5)  # 2 full 15m + 2 bars
    r = resample(bars, m5, TF)
    assert len(r) == 2
    assert r.iloc[0][["open", "high", "low", "close"]].tolist() == [100, 103, 99, 102.5]
    missing = bars.drop(index=4).reset_index(drop=True)
    assert len(resample(missing, m5, TF)) == 1          # bucket with a missing 5m bar is dropped
    shifted = to_frame([(100, 101, 99, 100)] * 5, m5, t0=T0 + m5.ms)
    assert resample(shifted, m5, TF)["ts"].tolist() == [T0 + TF.ms]   # partial first bucket dropped


def test_htf_value_usable_only_at_close_of_its_last_ltf_bar():
    m5 = Timeframe.M5
    ltf = to_frame([(100, 101, 99, 100)] * 6, m5)
    htf = resample(ltf, m5, TF)
    mapped = map_series_to_ltf(htf, TF, np.array([7, 8]), ltf, m5, fill=0)
    # 15m bar 0 closes at T0+15m = close of 5m bar 2 -> usable from bar 2's decision, not bar 1's
    assert mapped.tolist() == [0, 0, 7, 7, 7, 8]


# --------------------------------------------------------------------------------- OTE
def test_dealing_range_and_ote():
    dr = DealingRange(a_high=120, b_low=90)
    assert dr.equilibrium == 105
    assert dr.ote() == pytest.approx((108.6, 113.7))
    assert dr.level(0.705) == pytest.approx(111.15)
    assert dr.ratio(111.15) == pytest.approx(0.705)
    assert intersect((100, 110), dr.ote()) == pytest.approx((108.6, 110))
    assert intersect((100, 105), dr.ote()) is None
