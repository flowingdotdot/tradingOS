"""Phase 3 execution rules on hand-checkable synthetic data:
cost-inclusive sizing, net reward/risk at the expected fill, limit-order life cycle,
funding modes, OOS seal, zero-volume data-quality rule."""

from __future__ import annotations

import numpy as np
import pytest
import synth
from conftest import TF, make_bars, zero_cost_config
from fastapi.testclient import TestClient

from tradingos.api.app import create_app
from tradingos.backtest.engine import run_backtest
from tradingos.backtest.models import BacktestConfig, CostModel, ExecutionModel
from tradingos.config import BACKEND_DIR, Settings
from tradingos.domain.instruments import BTC_USDT_SWAP
from tradingos.domain.timeframes import Timeframe
from tradingos.marketdata.quality import apply_quality_rules, zero_volume_runs
from tradingos.marketdata.store import CandleStore
from tradingos.research.split import OosSealedError, load_split
from tradingos.risk.engine import (
    ExecutionCosts,
    RiskConfig,
    RiskEngine,
    net_reward_risk,
    unit_loss_at_stop,
)
from tradingos.services.backtest_service import BacktestInputError, run_and_store
from tradingos.storage.runs import RunRepository
from tradingos.strategy.base import empty_signals
from tradingos.strategy.builtin.ict_ote import IctOteStrategy
from tradingos.strategy.causality import check_causality

M5, M15 = Timeframe.M5, Timeframe.M15
COSTS = ExecutionCosts(entry_fee_rate=0.0005, exit_fee_rate=0.0005, exit_slippage=0.0002, target_fee_rate=0.0005)
REAL = CostModel()          # taker 0.05 %, slippage 2 bps, funding 0.01 %/8h
T = 1_672_531_200_000


def risk_engine(**kw) -> RiskEngine:
    e = RiskEngine(RiskConfig(**kw), BTC_USDT_SWAP)
    e.on_time(T, 10_000)
    return e


def sig_frame(bars, entries: dict[int, dict], exits_short=()):
    sig = empty_signals(bars.index)
    for i, e in entries.items():
        sig.loc[i, "entry"] = e["side"]
        sig.loc[i, "stop"] = e["stop"]
        sig.loc[i, "target"] = e.get("target", np.nan)
        sig.loc[i, "limit_price"] = e.get("limit", np.nan)
        sig.loc[i, "limit_expiry_bars"] = e.get("expiry", np.nan)
        sig.loc[i, "min_rr"] = e.get("min_rr", np.nan)
        sig.loc[i, "reason"] = "test"
    for i in exits_short:
        sig.loc[i, "exit_short"] = True
    return sig


def cfg(bars, costs=None, **execution):
    return zero_cost_config(bars, costs=costs or CostModel(taker_fee_rate=0, maker_fee_rate=0, slippage_bps=0,
                                                           funding_rate_per_8h=0),
                            execution=ExecutionModel(**execution))


# ============================================================ cost-inclusive sizing
def test_unit_loss_includes_fees_and_stop_slippage():
    price_loss, fees = unit_loss_at_stop(-1, 70_000, 70_700, COSTS)
    assert price_loss == pytest.approx(70_700 * 1.0002 - 70_000)            # short buys back 2 bps higher
    assert fees == pytest.approx(70_000 * 0.0005 + 70_700 * 1.0002 * 0.0005)
    assert unit_loss_at_stop(1, 100, 90, None) == (10, 0.0)                  # legacy: stop distance only


def test_quantity_covers_stop_loss_fees_and_slippage_and_is_rounded_down():
    # budget 25 USDT; per-unit loss 714.14 + 70.36 = 784.50 -> 0.031867 BTC -> floor to 0.0318
    d = risk_engine().authorize_entry(side=-1, entry_price=70_000, stop_price=70_700, equity=10_000,
                                      open_positions=0, costs=COSTS)
    assert d.approved and d.qty == pytest.approx(0.0318)
    assert d.planned_loss <= 25.0 and d.planned_loss == pytest.approx(0.0318 * 784.4973, rel=1e-4)
    assert d.risk_amount == pytest.approx(0.0318 * 700)                      # price risk kept separately
    legacy = risk_engine().authorize_entry(side=-1, entry_price=70_000, stop_price=70_700, equity=10_000,
                                           open_positions=0)
    assert legacy.qty == pytest.approx(0.0357) and legacy.qty > d.qty
    assert BTC_USDT_SWAP.contracts(d.qty) == pytest.approx(3.18)             # OKX contracts (0.01 BTC each)


def test_tight_stop_no_longer_lets_costs_exceed_the_budget():
    """Trade #2 of the 150-day run: stop 0.09 % away. Legacy sizing lost 2.4x the budget."""
    kw = dict(side=-1, entry_price=63_018.3, stop_price=63_073.7, equity=10_000, open_positions=0)
    legacy, new = risk_engine().authorize_entry(**kw), risk_engine().authorize_entry(**kw, costs=COSTS)
    price_loss, fees = unit_loss_at_stop(-1, 63_018.3, 63_073.7, COSTS)
    assert legacy.qty * (price_loss + fees) > 2 * 25                         # what really happened
    assert new.planned_loss <= 25.0 and new.qty < legacy.qty / 2


def test_entry_rejected_when_rounded_quantity_is_below_the_exchange_minimum():
    d = risk_engine().authorize_entry(side=-1, entry_price=70_000, stop_price=70_700, equity=30, open_positions=0,
                                      costs=COSTS)
    assert not d.approved and d.reason == "below_min_size"                   # 0.075 / 784.5 < 0.0001 BTC


def test_stop_out_costs_about_one_r_with_cost_inclusive_sizing():
    flat = (100.0, 100.5, 99.5, 100.0)
    bars = make_bars([flat, flat, (100, 111, 99.5, 110), flat])
    sig = sig_frame(bars, {0: {"side": -1, "stop": 110.0}})
    new = run_backtest(bars, sig, cfg(bars, costs=REAL), BTC_USDT_SWAP).trades[0]
    old = run_backtest(bars, sig, cfg(bars, costs=REAL, cost_inclusive_sizing=False), BTC_USDT_SWAP).trades[0]
    assert new.exit_reason == "stop" and -1.0 <= new.r_multiple <= -0.98     # the budget was honoured
    assert new.net_pnl >= -25.0 and old.net_pnl < -25.0                      # legacy overshoots the budget
    assert new.risk_budget == pytest.approx(-new.net_pnl, rel=0.02)


# ====================================================== net reward/risk at the fill
def test_net_reward_risk_formula():
    rr = net_reward_risk(-1, 100.0, 110.0, 80.0, COSTS)
    loss = (110 * 1.0002 - 100) + 100 * 0.0005 + 110 * 1.0002 * 0.0005
    reward = 20 - 100 * 0.0005 - 80 * 0.0005
    assert rr == pytest.approx(reward / loss)
    assert net_reward_risk(-1, 100.0, 110.0, 80.0, None) == pytest.approx(2.0)   # gross
    assert net_reward_risk(-1, 100.0, 99.0, 80.0, None) is None                  # stop on the wrong side


def test_entry_rejected_when_net_rr_at_expected_fill_is_below_minimum():
    # gross RR at the signal close (100): (100-85)/(110-100) = 1.50, but after 2 bps slippage
    # and fees the net RR at the expected fill (99.98) is 14.8875 / 10.147 = 1.47 < 1.5
    flat = (100.0, 100.5, 99.5, 100.0)
    bars = make_bars([flat, flat, flat, flat])
    sig = sig_frame(bars, {0: {"side": -1, "stop": 110.0, "target": 85.0, "min_rr": 1.5}})
    res = run_backtest(bars, sig, cfg(bars, costs=REAL), BTC_USDT_SWAP)
    assert res.trades == [] and res.rejections[0].reason == "net_rr_below_min"
    assert "net RR 1.4" in res.rejections[0].detail
    legacy = run_backtest(bars, sig, cfg(bars, costs=REAL, enforce_net_rr_at_fill=False), BTC_USDT_SWAP)
    assert len(legacy.trades) == 1                                           # the old engine took it
    ok = sig_frame(bars, {0: {"side": -1, "stop": 110.0, "target": 80.0, "min_rr": 1.5}})
    t = run_backtest(bars, ok, cfg(bars, costs=REAL), BTC_USDT_SWAP).trades[0]
    assert t.planned_net_rr == pytest.approx(net_reward_risk(-1, t.entry_price, 110.0, 80.0, COSTS))


def test_signals_without_min_rr_are_not_checked():
    flat = (100.0, 100.5, 99.5, 100.0)
    bars = make_bars([flat] * 4)
    sig = sig_frame(bars, {0: {"side": -1, "stop": 110.0, "target": 99.0}})   # RR 0.1, no min_rr
    assert len(run_backtest(bars, sig, cfg(bars, costs=REAL), BTC_USDT_SWAP).trades) == 1


# ============================================================== limit order model
FLAT = (100.0, 100.5, 99.5, 100.0)
SELL = {"side": -1, "limit": 105.0, "stop": 110.0, "target": 90.0, "expiry": 3}


def limit_run(ohlc, entry=None, **kw):
    bars = make_bars(ohlc)
    sig = sig_frame(bars, {0: entry or SELL})
    return bars, run_backtest(bars, sig, cfg(bars, **kw), BTC_USDT_SWAP)


def test_touching_the_limit_is_not_a_fill():
    bars, res = limit_run([FLAT, (100, 105.0, 99.5, 104), (104, 105.005, 103, 104), FLAT, FLAT, FLAT])
    assert res.limit_orders_placed == 1 and res.limit_orders_filled == 0 and res.trades == []
    assert res.rejections[-1].reason == "limit_expired"
    # with penetration 0 ("touch is a fill") the same data would fill: the assumption is explicit
    _, optimistic = limit_run([FLAT, (100, 105.0, 99.5, 104), FLAT, FLAT], limit_fill_penetration_bps=0)
    assert optimistic.limit_orders_filled == 1


def test_trade_through_fills_at_the_limit_never_better():
    bars, res = limit_run([FLAT, (106, 107, 104, 105), FLAT, (100, 100.5, 89, 90), FLAT])   # opens above the limit
    t = res.trades[0]
    assert t.entry_type == "limit" and t.entry_price == 105.0 and t.entry_ts == bars["ts"][1]
    assert t.signal_ts == bars["ts"][0]
    assert t.exit_reason == "target" and t.exit_price == 90.0 and t.exit_ts == bars["ts"][3]
    assert not t.fill_bar_ambiguous


def test_stop_inside_the_fill_bar_is_assumed_hit():
    _, res = limit_run([FLAT, (100, 111, 99.5, 104), FLAT, FLAT])
    t = res.trades[0]
    assert t.exit_reason == "stop" and t.fill_bar_ambiguous and t.bars_held == 1 and t.net_pnl < 0


def test_target_inside_the_fill_bar_is_not_credited():
    # the bar trades through the limit AND down to the target: order unknown -> filled, no profit
    bars, res = limit_run([FLAT, (100, 106, 89, 100), FLAT, FLAT])
    t = res.trades[0]
    assert t.fill_bar_ambiguous and t.exit_reason == "end_of_data" and t.exit_ts == bars["ts"][3]
    assert t.mfe == 5.5                                     # later bars (low 99.5) count; the fill bar's low 89 does not


def test_order_cancelled_when_target_is_reached_before_it_fills():
    _, res = limit_run([FLAT, (100, 100.5, 89, 90), (90, 106, 89, 100), FLAT, FLAT])
    assert res.trades == [] and res.limit_orders_filled == 0
    assert res.rejections[0].reason == "limit_cancelled_target_reached"


def test_order_expires_and_cannot_fill_afterwards():
    _, res = limit_run([FLAT, FLAT, FLAT, FLAT, (100, 107, 99.5, 104), FLAT])
    assert res.trades == [] and res.rejections[0].reason == "limit_expired"


def test_order_cancelled_by_data_gap_and_by_switching_entries_off():
    bars = make_bars([FLAT, FLAT, (100, 107, 99.5, 104), FLAT])
    bars.loc[2:, "ts"] += 5 * TF.ms
    res = run_backtest(bars, sig_frame(bars, {0: SELL}), cfg(bars), BTC_USDT_SWAP)
    assert res.trades == [] and res.rejections[0].reason == "limit_cancelled_data_gap"

    bars = make_bars([FLAT, FLAT, (100, 107, 99.5, 104), FLAT])
    allowed = np.array([True, False, False, False])
    res = run_backtest(bars, sig_frame(bars, {0: SELL}), cfg(bars), BTC_USDT_SWAP, entries_allowed=allowed)
    assert res.trades == [] and res.rejections[0].reason == "limit_cancelled_entries_disabled"


def test_limit_order_checked_for_net_rr_at_the_limit_price_when_placed():
    entry = {**SELL, "target": 99.0, "min_rr": 1.5}        # (105-99)/(110-105) = 1.2 < 1.5
    _, res = limit_run([FLAT, (100, 107, 99.5, 104), FLAT], entry=entry)
    assert res.limit_orders_placed == 0 and res.rejections[0].reason == "net_rr_below_min"


def test_second_signal_while_an_order_rests_is_refused_and_filled_order_is_then_managed_normally():
    bars = make_bars([FLAT, FLAT, (100, 107, 99.5, 104), (104, 104.5, 103, 103.5), (103, 111, 102, 110), FLAT])
    sig = sig_frame(bars, {0: SELL, 1: SELL})
    res = run_backtest(bars, sig, cfg(bars), BTC_USDT_SWAP)
    assert [r.reason for r in res.rejections] == ["order_pending"]
    t = res.trades[0]
    assert t.entry_ts == bars["ts"][2] and t.exit_reason == "stop" and t.exit_ts == bars["ts"][4]
    assert not t.fill_bar_ambiguous and t.r_multiple == pytest.approx(-1.0)


def test_buy_limit_is_symmetric():
    buy = {"side": 1, "limit": 95.0, "stop": 90.0, "target": 110.0, "expiry": 3}
    _, res = limit_run([FLAT, (100, 100.5, 95.0, 96), (96, 97, 94.9, 96), (96, 111, 95.5, 110), FLAT], entry=buy)
    t = res.trades[0]
    assert t.entry_price == 95.0 and t.exit_reason == "target" and t.net_pnl > 0   # bar 1 only touched


# ===================================================================== funding modes
def test_funding_cost_mode_always_charges_and_signed_mode_pays_shorts():
    bars = make_bars([FLAT] * 41)                        # 00:00 start, 15m bars -> one funding time (08:00)
    sig = sig_frame(bars, {0: {"side": -1, "stop": 110.0}})
    base = dict(taker_fee_rate=0, maker_fee_rate=0, slippage_bps=0, funding_rate_per_8h=0.001)
    cost = run_backtest(bars, sig, cfg(bars, costs=CostModel(**base)), BTC_USDT_SWAP).trades[0]
    signed = run_backtest(bars, sig, cfg(bars, costs=CostModel(**base, funding_mode="signed")), BTC_USDT_SWAP).trades[0]
    neg = run_backtest(bars, sig, cfg(bars, costs=CostModel(**{**base, "funding_rate_per_8h": -0.001},
                                                            funding_mode="signed")), BTC_USDT_SWAP).trades[0]
    assert cost.funding == pytest.approx(0.001 * cost.qty * 100) and cost.net_pnl < 0
    assert signed.funding == pytest.approx(-cost.funding) and signed.net_pnl > 0     # short RECEIVES
    assert neg.funding == pytest.approx(cost.funding)                                 # negative rate: short pays


# ========================================================================= OOS seal
def test_split_file_matches_the_approved_boundaries():
    s = load_split(BACKEND_DIR / "config" / "research_split.yaml")
    p = s.public()
    assert (p["is"]["start"], p["is"]["end_exclusive"]) == ("2020-01-01", "2024-10-01")
    assert (p["oos"]["start"], p["oos"]["end_exclusive"]) == ("2024-10-01", "2026-05-14")
    assert s.oos_sealed is True and s.zero_volume_invalid_minutes == 30 and len(s.fingerprint) == 16
    day = 86_400_000
    assert s.segment(s.is_start, s.is_start + day) == "IS"
    assert s.segment(s.oos_end, s.oos_end + day) == "OBSERVED"
    assert s.segment(s.is_start - 5 * day, s.is_start) == "WARMUP_ONLY"
    assert s.segment(s.is_end - day, s.is_end + day) == "MIXED"
    for a, b in ((s.oos_start, s.oos_start + 1), (s.is_end - day, s.oos_start + 1), (s.oos_end - 1, s.oos_end + day),
                 (s.is_start, s.oos_end + day)):
        with pytest.raises(OosSealedError):
            s.guard(a, b, "test")
    s.guard(s.is_start, s.is_end, "test")                 # IS up to the boundary is allowed
    s.guard(s.oos_end, s.oos_end + day, "test")           # observed window is allowed


OOS_START, OOS_END = 1_727_740_800_000, 1_778_716_800_000     # 2024-10-01, 2026-05-14


@pytest.fixture
def sealed_api(tmp_path):
    # data INSIDE the sealed window exists on purpose: the seal, not missing data, must block
    bars = synth.to_frame(synth.legs(100, [(104, 1), (100, 1)] * 200), M5, t0=OOS_START)
    CandleStore(tmp_path / "market").merge_save("BTC-USDT-SWAP", M5, bars)
    settings = Settings(data_dir=tmp_path, strategy_config_dir=BACKEND_DIR / "config" / "strategies")
    tc = TestClient(create_app(settings))
    tc.settings = settings  # type: ignore[attr-defined]
    tc.span = {"start_ts": OOS_START + 86_400_000, "end_ts": OOS_START + 2 * 86_400_000}  # type: ignore[attr-defined]
    return tc


def test_every_strategy_evaluation_path_refuses_the_sealed_oos_window(sealed_api):
    span = sealed_api.span
    body = {"strategy_id": "ma_crossover", "strategy_version": "v1", "timeframe": "5m", **span}
    for path in ("/api/backtests", "/api/research/inspect", "/api/research/analyze"):
        r = sealed_api.post(path, json=body)
        assert r.status_code == 400 and "SEALED" in r.json()["detail"], path
    draft = sealed_api.post("/api/research/analyze", json={"strategy_id": "ma_crossover", "draft_params": {},
                                                           "timeframe": "5m", **span})
    assert draft.status_code == 400 and "SEALED" in draft.json()["detail"]
    assert sealed_api.get("/api/backtests").json() == []                      # nothing was run or stored
    split = sealed_api.get("/api/research/split").json()
    assert split["defined"] and split["oos_sealed"] and split["oos"]["start"] == "2024-10-01"
    # the service used by the CLI is the same guarded function
    with pytest.raises(BacktestInputError, match="SEALED"):
        run_and_store(BacktestConfig(strategy_id="ma_crossover", strategy_version="v1", timeframe="5m", **span),
                      sealed_api.settings, RunRepository(sealed_api.settings.db_path))
    # a period that merely touches the window from the IS side is refused too
    straddle = {**body, "start_ts": OOS_START - 86_400_000, "end_ts": OOS_START + 86_400_000}
    assert sealed_api.post("/api/backtests", json=straddle).status_code == 400


def test_unsealing_is_explicit_and_recorded(tmp_path, sealed_api):
    text = (BACKEND_DIR / "config" / "research_split.yaml").read_text(encoding="utf-8")
    f = tmp_path / "split.yaml"
    f.write_text(text.replace("oos_sealed: true", "oos_sealed: false"), encoding="utf-8")
    settings = Settings(data_dir=tmp_path, strategy_config_dir=BACKEND_DIR / "config" / "strategies",
                        research_split_file=f)
    run = run_and_store(BacktestConfig(strategy_id="ma_crossover", strategy_version="v1", timeframe="5m",
                                       require_full_warmup=False, **sealed_api.span),
                        settings, RunRepository(settings.db_path))
    assert run["segment"] == "OOS" and run["oos_unsealed_run"] is True
    assert run["research_split"]["oos_sealed"] is False and run["research_split"]["fingerprint"]


# ============================================================ zero-volume data rule
def with_zero_volume(bars, start, n):
    out = bars.copy()
    prev_close = out["close"].iloc[start - 1]
    out.loc[start : start + n - 1, ["open", "high", "low", "close"]] = prev_close   # exchange filler candles
    out.loc[start : start + n - 1, "volume"] = 0.0
    return out


def test_zero_volume_runs_are_detected_and_classified():
    bars = make_bars([(100, 101, 99, 100)] * 40, tf=M5)
    bars = with_zero_volume(with_zero_volume(bars, 5, 5), 20, 6)
    assert zero_volume_runs(bars) == [(5, 5), (20, 6)]
    clean, rep = apply_quality_rules(bars, M5, 30)
    assert len(clean) == 34 and rep["bars_removed"] == 6 and rep["zero_volume_bars"] == 11
    assert rep["invalid_runs"] == [{"start_ts": int(bars["ts"][20]), "end_ts": int(bars["ts"][25]), "bars": 6}]
    assert rep["flagged_short_runs"] == [{"start_ts": int(bars["ts"][5]), "bars": 5}]     # kept, flagged
    assert clean["ts"].diff().max() == 7 * M5.ms                                           # a real gap now
    assert apply_quality_rules(bars, M15, 30)[1]["bars_removed"] == 11     # 30 min = 2 x 15m: both runs invalid
    assert apply_quality_rules(bars, M5, 0)[1]["bars_removed"] == 0        # rule can be disabled explicitly


def test_invalid_run_invalidates_higher_timeframe_bars_and_blocks_the_next_entry():
    bars = synth.bearish_ote_scenario()
    start = 300                                             # 6 filler candles inside the quiet prelude
    dirty = with_zero_volume(bars, start, 6)
    clean, rep = apply_quality_rules(dirty, M5, 30)
    assert rep["bars_removed"] == 6
    from tradingos.features.mtf import resample

    removed = set(dirty["ts"].iloc[start : start + 6])
    for htf in (M15, Timeframe.H4):
        full, cut = resample(bars, M5, htf), resample(clean, M5, htf)
        dropped = set(full["ts"]) - set(cut["ts"])
        expect = {t - t % htf.ms for t in removed}
        assert dropped == expect, htf                       # exactly the buckets containing removed bars
        # every surviving bar is complete and becomes known at the close of its last 5m bar
        assert ((cut["ts"] + htf.ms) % M5.ms == 0).all() and not (set(cut["ts"]) & expect)
    # an entry signalled on the last bar before the removed run is never executed after it
    j = start - 1
    sig = empty_signals(clean.index)
    sig.loc[j, ["entry", "stop"]] = [-1, clean["close"].iloc[j] * 1.5]
    res = run_backtest(clean, sig, zero_cost_config(clean), BTC_USDT_SWAP)
    assert res.trades == [] and res.rejections[0].reason == "stale_signal_data_gap"


def test_strategy_stays_causal_and_warm_around_an_invalid_run():
    dirty = with_zero_volume(synth.bearish_ote_scenario(), 430, 7)      # inside the steep impulse leg
    clean, _ = apply_quality_rules(dirty, M5, 30)
    strat = IctOteStrategy({"bias": {"enabled": False}})
    a = strat.analyze(clean)
    gap_at = int(np.nonzero(np.diff(clean["ts"]) != M5.ms)[0][0])
    gap_ts = int(clean["ts"].iloc[gap_at + 1])
    imp = a.impulse
    its = imp.bars["ts"].to_numpy()
    # no swing / FVG uses bars from both sides of the removed run
    for r in imp.swings.itertuples():
        lo, hi = its[r.idx - 3], its[r.known_idx]
        assert not (lo < gap_ts <= hi) or hi - lo == 6 * M15.ms
    for r in imp.fvgs.itertuples():
        assert its[r.known_idx] - its[r.first_idx] == 2 * M15.ms
    # displacement reference after the run still uses 20 CLOSED prior bars (rows), never NaN-poisoned
    d = a.base.disp
    assert np.isfinite(d["ref"].iloc[gap_at + 1 :]).all()
    check_causality(strat, clean, n_cuts=20, extra_cuts=[gap_at, gap_at + 1, gap_at + 2, gap_at + 10])


def test_quality_rule_is_recorded_with_the_run(tmp_path):
    bars = with_zero_volume(synth.bearish_ote_scenario(), 300, 6)
    bars = with_zero_volume(bars, 100, 2)
    CandleStore(tmp_path / "market").merge_save("BTC-USDT-SWAP", M5, bars)
    settings = Settings(data_dir=tmp_path, strategy_config_dir=BACKEND_DIR / "config" / "strategies")
    run = run_and_store(BacktestConfig(strategy_id="ma_crossover", strategy_version="v1", timeframe="5m",
                                       start_ts=int(bars["ts"].iloc[150]), end_ts=int(bars["ts"].iloc[-1]) + M5.ms),
                        settings, RunRepository(settings.db_path))
    dq = run["data_quality_rules"]
    assert dq["bars_removed"] == 6 and len(dq["invalid_runs"]) == 1 and dq["flagged_short_bars"] == 2
    assert run["data_quality"]["missing_bars"] == 6 and run["segment"] == "IS"
    assert any("treated as missing" in n for n in run["notes"]) and any("ASSUMED" in n for n in run["notes"])
    em = run["execution_model"]
    assert em["engine_version"] == "0.3.0" and em["funding"]["basis"].startswith("ASSUMED")
    assert "cost-inclusive" in em["sizing"] and "margin / liquidation" in em["not_modelled"]
    assert run["conditions"]["execution"]["limit_fill_penetration_bps"] == 1.0


# ================================================= OOS isolation: no sealed bar as warm-up
def test_observed_window_never_reads_sealed_oos_bars_even_for_warmup(tmp_path):
    """Data exists on both sides of the OOS end. A run starting at the first observed bar must
    warm up INSIDE the observed window and may only trade after that."""
    from tradingos.services.data_access import load_strategy_bars

    n_before, n_after = 400, 1_200
    bars = synth.to_frame(synth.legs(100, [(104, 1), (100, 1)] * 200), M5, t0=OOS_END - n_before * M5.ms)
    assert len(bars) == n_before + n_after
    CandleStore(tmp_path / "market").merge_save("BTC-USDT-SWAP", M5, bars)
    settings = Settings(data_dir=tmp_path, strategy_config_dir=BACKEND_DIR / "config" / "strategies")
    end = int(bars["ts"].iloc[-1]) + M5.ms

    loaded, ctx = load_strategy_bars(settings, "BTC-USDT-SWAP", M5, OOS_END, end, 90, "test")
    assert int(loaded["ts"].iloc[0]) == OOS_END                       # nothing from the sealed window
    assert ctx["warmup_clamped_to_oos_end"] and ctx["trade_start_ts"] == OOS_END + 90 * M5.ms
    assert ctx["segment"] == "OBSERVED" and ctx["warmup_data_segment"] == "OBSERVED"

    # far enough inside the observed window the normal warm-up applies, still without OOS bars
    later = OOS_END + 200 * M5.ms
    loaded, ctx = load_strategy_bars(settings, "BTC-USDT-SWAP", M5, later, end, 90, "test")
    assert int(loaded["ts"].iloc[0]) == later - 90 * M5.ms >= OOS_END and not ctx["warmup_clamped_to_oos_end"]
    assert ctx["trade_start_ts"] == later

    run = run_and_store(BacktestConfig(strategy_id="ma_crossover", strategy_version="v1", timeframe="5m",
                                       start_ts=OOS_END, end_ts=end), settings, RunRepository(settings.db_path))
    eff = run["conditions"]["effective_start_ts"]
    assert eff == OOS_END + 90 * M5.ms                                 # ma_crossover v1 warm-up = 90 bars
    assert run["equity"]["ts"][0] == eff and all(t["entry_ts"] > eff for t in run["trades"])
    assert run["conditions"]["warmup_data_segment"] == "OBSERVED"
    assert any("sealed OOS window" in n for n in run["notes"])
    # the same strategy given the sealed bars as warm-up would have produced different early signals:
    # prove the bars really were excluded by comparing fingerprints of what was loaded
    from tradingos.marketdata.store import data_fingerprint

    assert run["conditions"]["input_fingerprint"] == data_fingerprint(bars[bars["ts"] >= OOS_END])


def test_in_sample_warmup_before_is_start_is_unchanged(tmp_path):
    from tradingos.research.split import load_split
    from tradingos.services.data_access import load_strategy_bars

    split = load_split(BACKEND_DIR / "config" / "research_split.yaml")
    bars = synth.to_frame(synth.legs(100, [(104, 1), (100, 1)] * 100), M5, t0=split.is_start - 300 * M5.ms)
    CandleStore(tmp_path / "market").merge_save("BTC-USDT-SWAP", M5, bars)
    settings = Settings(data_dir=tmp_path, strategy_config_dir=BACKEND_DIR / "config" / "strategies")
    loaded, ctx = load_strategy_bars(settings, "BTC-USDT-SWAP", M5, split.is_start, split.is_start + 86_400_000, 90, "t")
    assert int(loaded["ts"].iloc[0]) == split.is_start - 90 * M5.ms and ctx["trade_start_ts"] == split.is_start
    assert ctx["warmup_data_segment"] == "WARMUP_ONLY" and not ctx["warmup_clamped_to_oos_end"]


def test_experiment_id_and_segment_are_recorded_for_trial_counting(tmp_path):
    bars = synth.bearish_ote_scenario()
    CandleStore(tmp_path / "market").merge_save("BTC-USDT-SWAP", M5, bars)
    settings = Settings(data_dir=tmp_path, strategy_config_dir=BACKEND_DIR / "config" / "strategies")
    repo = RunRepository(settings.db_path)
    base = dict(strategy_id="ma_crossover", strategy_version="v1", timeframe="5m",
                start_ts=int(bars["ts"].iloc[150]), end_ts=int(bars["ts"].iloc[-1]) + M5.ms)
    run = run_and_store(BacktestConfig(**base, experiment_id="H1-entry-timing"), settings, repo)
    assert run["experiment_id"] == "H1-entry-timing"
    run_and_store(BacktestConfig(**base), settings, repo)
    listed = {r["summary"]["experiment_id"] for r in repo.list()}
    assert listed == {"H1-entry-timing", None} and all(r["summary"]["segment"] == "IS" for r in repo.list())
    with pytest.raises(ValueError):
        BacktestConfig(**base, experiment_id="bad id with spaces")
