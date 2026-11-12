"""Signal Inspector (funnel vs. per-condition), trade audit, history probe, MTF consistency."""

from __future__ import annotations

import pandas as pd
import pytest
import synth
from conftest import random_walk_bars, zero_cost_config
from fake_okx import FakeOkx
from fastapi.testclient import TestClient

from tradingos.api.app import create_app
from tradingos.backtest.engine import run_backtest
from tradingos.config import BACKEND_DIR, Settings
from tradingos.domain.instruments import BTC_USDT_SWAP
from tradingos.domain.timeframes import Timeframe
from tradingos.marketdata.collector import check_mtf_consistency, find_earliest_candle
from tradingos.marketdata.okx_public import OkxPublicClient
from tradingos.marketdata.store import CandleStore
from tradingos.services.audit_service import audit_run
from tradingos.strategy import registry
from tradingos.strategy.base import Strategy, empty_signals
from tradingos.strategy.builtin.ict_ote import IctOteStrategy
from tradingos.strategy.inspect import (
    FAIL,
    NA,
    NOT_EVALUATED,
    PASS,
    PENDING,
    Inspection,
    StageDef,
    audit_evidence,
)
from tradingos.strategy.schema import DataRequirements, StrategyDefinition

M5, M15, H4 = Timeframe.M5, Timeframe.M15, Timeframe.H4
NO_BIAS = {"bias": {"enabled": False}}


# ------------------------------------------------------------------ generic aggregation
def test_funnel_is_sequential_and_conditions_are_independent():
    insp = Inspection([StageDef("a", "A"), StageDef("b", "B", independent=True), StageDef("c", "C")])
    rows = [  # a, b, c
        (PASS, PASS, PASS),
        (PASS, FAIL, NOT_EVALUATED),
        (FAIL, PASS, NOT_EVALUATED),     # b evaluated although a failed (independent condition)
        (FAIL, FAIL, NOT_EVALUATED),
        (PASS, NA, PENDING),
    ]
    for i, (a, b, c) in enumerate(rows):
        cand = insp.candidate(i, i + 1, f"c{i}")
        for stage, status in zip("abc", (a, b, c)):
            insp.mark(cand, stage, status, reason="why: detail" if status == FAIL else "")
        cand["outcome"] = "signal" if c == PASS else "x"
    r = insp.summarize()
    f = {x["stage"]: x for x in r["funnel"]}
    assert (f["a"]["entered"], f["a"]["continue"]) == (5, 3)
    assert (f["b"]["entered"], f["b"]["failed"], f["b"]["not_applicable"], f["b"]["continue"]) == (3, 1, 1, 2)
    assert (f["c"]["entered"], f["c"]["passed"], f["c"]["pending"], f["c"]["continue"]) == (2, 1, 1, 1)
    assert f["a"]["fail_reasons"] == {"why": 2}
    c = {x["stage"]: x for x in r["conditions"]}
    # independent rate of b counts ALL candidates where b was evaluated: 2 pass / 4
    assert (c["b"]["evaluated"], c["b"]["passed"], c["b"]["pass_rate_pct"]) == (4, 2, 50.0)
    assert c["c"]["evaluated"] == 1 and c["c"]["not_evaluated"] == 3
    assert r["n_signals"] == 1
    # time window + compact form
    assert insp.summarize(start=1, end=3)["n_candidates"] == 2
    compact = insp.summarize(compact=True)["candidates"][1]["stages"]
    assert "c" not in compact and compact["b"]["status"] == FAIL


# ----------------------------------------------------------------------- ICT inspector
def evaluate(retrace=None, **params):
    bars = synth.bearish_ote_scenario(retrace)
    strat = IctOteStrategy({**NO_BIAS, **params})
    sig, insp = strat.evaluate(bars)
    return bars, strat, sig, insp


def signal_candidates(insp):
    return [c for c in insp.candidates if c["outcome"] == "signal"]


def test_inspector_comes_from_the_same_evaluation_and_does_not_change_signals():
    bars, strat, sig, insp = evaluate()
    pd.testing.assert_frame_equal(sig, strat.generate_signals(bars))
    assert len(signal_candidates(insp)) == int((sig["entry"] != 0).sum()) == 1


def test_signal_candidate_passes_every_stage_with_recorded_values():
    bars, strat, sig, insp = evaluate()
    c = signal_candidates(insp)[0]
    st = c["stages"]
    assert st["structure_break"]["status"] == PASS and st["structure_break"]["value"]["kind"] == "MSS"
    assert st["htf_bias"]["status"] == NA and st["session_filter"]["status"] == NA
    for stage in ("displacement", "impulse_confirmed", "distinct_impulse", "fvg_detection", "ote_intersection",
                  "zone_touch", "entry_confirmation", "reward_risk", "final_decision"):
        assert st[stage]["status"] == PASS, stage
    rr = st["reward_risk"]["value"]
    assert rr["entry_ref"] == pytest.approx(104.0) and rr["rr"] == pytest.approx(1.69, abs=0.01)
    assert st["impulse_confirmed"]["value"]["a"] == pytest.approx(120.6)
    assert st["fvg_detection"]["value"]["fvg_valid"] >= 1
    assert "retest_fvg" not in st                                  # only present for the retest variant
    j = int(sig.index[sig["entry"] != 0][0])
    assert c["decision_ts"] == bars["ts"].iloc[j] + M5.ms


@pytest.mark.parametrize("retrace, stage, reason_prefix", [
    (synth.LATE_MSS_RETRACE, "reward_risk", "rejected_rr"),
    (synth.NO_TOUCH_RETRACE, "zone_touch", "impulse_extended"),
    (synth.ABOVE_A_RETRACE, "entry_confirmation", "invalidated"),
    (synth.TARGET_PASSED_RETRACE, "reward_risk", "rejected_target"),
])
def test_failure_stage_and_reason_are_recorded(retrace, stage, reason_prefix):
    _, _, sig, insp = evaluate(retrace)
    assert (sig["entry"] == 0).all()
    main = [c for c in insp.candidates if c["stages"]["impulse_confirmed"]["status"] == PASS
            and c["stages"]["distinct_impulse"]["status"] == PASS][-1]
    s = main["stages"][stage]
    assert s["status"] == FAIL and s["reason"].startswith(reason_prefix), s
    assert main["stages"]["final_decision"]["status"] == FAIL
    order = [x.id for x in insp.stages]
    for later in order[order.index(stage) + 1 : -1]:
        assert main["stages"][later]["status"] in (NOT_EVALUATED, NA), later


def test_reward_risk_failure_keeps_the_computed_values():
    _, _, _, insp = evaluate(synth.LATE_MSS_RETRACE)
    v = next(c for c in insp.candidates if c["outcome"] == "rejected_rr")["stages"]["reward_risk"]["value"]
    assert v["rr"] == pytest.approx(0.42, abs=0.01) and v["min_rr"] == 1.5 and v["stop"] > v["entry_ref"] > v["target"]


def test_independent_conditions_are_evaluated_even_when_the_bias_fails():
    bars = synth.bearish_ote_scenario()
    _, insp = IctOteStrategy({}).evaluate(bars)       # bias ON; ~5 days of data -> 4H bias is neutral
    blocked = [c for c in insp.candidates if c["outcome"] == "bias_not_bearish"]
    assert blocked
    c = blocked[-1]["stages"]
    assert c["htf_bias"]["status"] == FAIL and c["htf_bias"]["reason"].startswith("bias_neutral")
    assert c["fvg_detection"]["status"] == PASS and c["ote_intersection"]["status"] == PASS
    assert c["zone_touch"]["status"] == NOT_EVALUATED
    r = insp.summarize()
    f = {x["stage"]: x for x in r["funnel"]}
    cond = {x["stage"]: x for x in r["conditions"]}
    assert f["fvg_detection"]["entered"] == 0                    # funnel: nothing got past the bias
    assert cond["fvg_detection"]["evaluated"] >= 1                # condition: still evaluated independently


def test_retest_variant_adds_its_stage():
    _, _, _, insp = evaluate(entry={"mode": "fvg_retest"})
    c = signal_candidates(insp)[0]
    assert c["stages"]["retest_fvg"]["status"] == PASS and c["stages"]["retest_fvg"]["value"]["limit_price"] > 0


@pytest.mark.parametrize("params", [{}, {"target": {"min_rr": 0.5}}, {"bias": {"enabled": False}}])
def test_inspection_invariants_on_random_data(params):
    bars = random_walk_bars(8_000, seed=21, tf=M5)
    sig, insp = IctOteStrategy(params).evaluate(bars)
    r = insp.summarize()
    assert r["n_signals"] == int((sig["entry"] != 0).sum())
    assert sum(r["outcomes"].values()) == r["n_candidates"] == len(insp.candidates)
    f = r["funnel"]
    for a, b in zip(f, f[1:]):
        assert b["entered"] == a["continue"]                      # sequential by construction
    assert f[-1]["passed"] == r["n_signals"]
    for c in insp.candidates:
        assert c["outcome"], c
        assert c["stages"]["final_decision"]["status"] in (PASS, FAIL, PENDING)
        if c["outcome"] == "signal":
            assert all(e["ok"] for e in audit_evidence(c)) and len(c["evidence"]) >= 6


def test_evidence_separates_occurrence_from_recognition_time():
    _, strat, _, insp = evaluate()
    ev = audit_evidence(signal_candidates(insp)[0])
    assert all(e["ok"] for e in ev)
    b = next(e for e in ev if e["what"].startswith("impulse end B"))
    assert b["known_ts"] - b["ts"] == 4 * M15.ms                 # swing low: drawn at ts, known k+1 bars later
    mss_swing = next(e for e in ev if "internal swing low" in e["what"])
    assert mss_swing["known_ts"] - mss_swing["ts"] == 4 * M5.ms
    assert max(e["known_ts"] for e in ev) == ev[-1]["decision_ts"]


# --------------------------------------------------------------------------- API + audit
@pytest.fixture
def api(tmp_path):
    bars = synth.bearish_ote_scenario()
    CandleStore(tmp_path / "market").merge_save("BTC-USDT-SWAP", M5, bars)
    settings = Settings(data_dir=tmp_path, strategy_config_dir=BACKEND_DIR / "config" / "strategies")
    tc = TestClient(create_app(settings))
    tc.settings, tc.bars = settings, bars  # type: ignore[attr-defined]
    tc.span = {"start_ts": int(bars["ts"].iloc[0]), "end_ts": int(bars["ts"].iloc[-1]) + M5.ms}  # type: ignore
    return tc


def test_inspect_endpoint_and_run_record(api):
    r = api.post("/api/research/inspect", json={"strategy_id": "ict_bearish_ote", "draft_params": NO_BIAS, **api.span})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["supported"] and d["inspection"]["n_signals"] == 1 and d["warmup_available_bars"] == 0
    assert [s["id"] for s in d["inspection"]["stages"]][0] == "structure_break"
    # a strategy without an inspector answers honestly instead of failing
    ma = api.post("/api/research/inspect", json={"strategy_id": "ma_crossover", "strategy_version": "v1", **api.span})
    assert ma.status_code == 200 and ma.json()["supported"] is False

    saved = api.post("/api/strategies/ict_bearish_ote/configs",
                     json={"version": "t-nobias", "params": NO_BIAS, "parent_version": "v1-mss-close"})
    assert saved.status_code == 200
    run = api.post("/api/backtests", json={"strategy_id": "ict_bearish_ote", "strategy_version": "t-nobias",
                                           "timeframe": "5m", "require_full_warmup": False, **api.span}).json()
    assert run["engine_version"] == "0.3.0"
    assert run["inspection"]["n_signals"] == 1 and len(run["trades"]) == 1
    stored = api.get(f"/api/backtests/{run['run_id']}").json()
    assert stored["inspection"]["funnel"] == run["inspection"]["funnel"]          # recorded with the run

    audit = api.get(f"/api/backtests/{run['run_id']}/audit").json()
    assert audit["all_checks_passed"] and audit["same_market_data_as_run"]
    t = audit["trades"][0]
    assert t["signal_reproduced"] and t["prefix_replay_ok"] and t["evidence_ok"]
    assert t["decision_ts"] == t["entry_ts"] and t["signal_bar_ts"] == t["entry_ts"] - M5.ms
    assert t["planned_rr_at_signal"] == pytest.approx(1.69, abs=0.01)
    assert t["candidate"]["stages"]["reward_risk"]["status"] == PASS
    assert api.get("/api/backtests/nope/audit").status_code == 404


def test_audit_flags_a_strategy_that_looks_ahead(tmp_path, monkeypatch):
    from pydantic import BaseModel

    class P(BaseModel):
        pass

    class Peek(Strategy):
        definition = StrategyDefinition(
            strategy_id="peek_audit", name="Peek", definition_version="0.0.1", category="example",
            description="cheats: uses the next bar", data=DataRequirements(base_timeframes=["15m"], instruments=["*"]))
        Params = P

        def warmup_bars(self) -> int:
            return 0

        def generate_signals(self, bars):
            sig = empty_signals(bars.index)
            up_next = (bars["close"].shift(-1) > bars["close"] * 1.004).to_numpy()
            sig.loc[up_next, "entry"] = 1
            sig.loc[up_next, "stop"] = bars.loc[up_next, "close"] * 0.97
            return sig

    monkeypatch.setitem(registry.STRATEGY_CLASSES, "peek_audit", Peek)
    bars = random_walk_bars(600, seed=2)
    CandleStore(tmp_path / "market").merge_save("BTC-USDT-SWAP", M15, bars)
    cfg = zero_cost_config(bars)
    res = run_backtest(bars, Peek().generate_signals(bars), cfg, BTC_USDT_SWAP)
    assert res.trades
    run = {"run_id": "r", "strategy_id": "peek_audit", "strategy_version": "t", "params": {}, "params_hash": "x",
           "code_revision": "x", "engine_version": "x", "timeframe": "15m", "inst_id": "BTC-USDT-SWAP",
           "config": cfg.model_dump(), "trades": [t.model_dump() for t in res.trades]}
    audit = audit_run(Settings(data_dir=tmp_path), run)
    assert audit["all_checks_passed"] is False
    assert any(t["prefix_replay_ok"] is False for t in audit["trades"])


# ------------------------------------------------------------------ data range + consistency
def test_find_earliest_candle_by_probing():
    first = synth.T0 + 1_234 * M15.ms
    last = first + 5_000 * M15.ms
    fake = FakeOkx(M15, first, last)
    with OkxPublicClient(transport=fake.transport(), sleep=lambda s: None) as c:
        r = find_earliest_candle(c, "BTC-USDT-SWAP", M15, hi_ts=last + 3 * M15.ms)
    assert r["earliest_ts"] == first and r["first_page_contiguous"] and r["requests"] < 60


def test_mtf_consistency_detects_a_mismatch(tmp_path):
    from tradingos.features.mtf import resample

    store = CandleStore(tmp_path)
    five = synth.bearish_ote_scenario()
    store.merge_save("X", M5, five)
    native = resample(five, M5, M15)
    for col in ("volume_ccy", "volume_quote"):
        native[col] = 0.0
    store.merge_save("X", M15, native)
    assert check_mtf_consistency(store, "X", M5, M15)["consistent"] is True
    native.loc[10, "high"] += 5
    store.merge_save("X", M15, native)
    r = check_mtf_consistency(store, "X", M5, M15)
    assert r["consistent"] is False and r["max_abs_price_diff"] == pytest.approx(5)
    assert check_mtf_consistency(store, "X", M5, H4)["compared"] == 0


def test_audit_uses_the_recorded_signal_bar_for_limit_orders(tmp_path):
    """A limit order fills bars after its signal; the audit must check the SIGNAL bar, not fill-1."""
    import yaml

    cfg_dir = tmp_path / "strategies" / "ict_bearish_ote"
    cfg_dir.mkdir(parents=True)
    raw = yaml.safe_load((BACKEND_DIR / "config/strategies/ict_bearish_ote/v1-fvg-retest.yaml").read_text("utf-8"))
    raw["version"], raw["params"]["bias"] = "retest-nobias", {"enabled": False}
    (cfg_dir / "t.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    # TARGET_PASSED scenario + a bounce back up through the 5m FVG so that the limit really fills late
    bars = synth.bearish_ote_scenario(synth.GOOD_RETRACE[:-1] + [(103, 1), (110, 1.5), (100, 2)])
    CandleStore(tmp_path / "market").merge_save("BTC-USDT-SWAP", M5, bars)
    settings = Settings(data_dir=tmp_path, strategy_config_dir=tmp_path / "strategies")
    tc = TestClient(create_app(settings))
    run = tc.post("/api/backtests", json={"strategy_id": "ict_bearish_ote", "strategy_version": "retest-nobias",
                                          "timeframe": "5m", "require_full_warmup": False,
                                          "start_ts": int(bars["ts"].iloc[0]),
                                          "end_ts": int(bars["ts"].iloc[-1]) + M5.ms}).json()
    assert len(run["trades"]) == 1 and run["trades"][0]["entry_type"] == "limit"
    t = tc.get(f"/api/backtests/{run['run_id']}/audit").json()["trades"][0]
    assert t["bars_from_signal_to_fill"] > 1                          # filled later than the next bar
    assert t["signal_reproduced"] and t["prefix_replay_ok"] and t["evidence_ok"]
    assert t["decision_ts"] == t["signal_bar_ts"] + M5.ms < t["entry_ts"]
