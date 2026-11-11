"""MA Crossover — the plugin-architecture verification strategy."""

from __future__ import annotations

import numpy as np
import pytest
import synth
from conftest import random_walk_bars
from fake_okx import FakeOkx
from fastapi.testclient import TestClient

from tradingos.api.app import create_app
from tradingos.config import BACKEND_DIR, Settings
from tradingos.domain.timeframes import Timeframe
from tradingos.marketdata.okx_public import OkxPublicClient
from tradingos.marketdata.store import CandleStore
from tradingos.strategy.builtin.ma_crossover import MaCrossoverStrategy, in_session
from tradingos.strategy.causality import check_causality
from tradingos.strategy.schema import ConfigValidationError

M15, M5 = Timeframe.M15, Timeframe.M5


def entries(params, bars):
    s = MaCrossoverStrategy(params).generate_signals(bars)
    return s[s["entry"] != 0]


@pytest.mark.parametrize("bad, path", [
    ({"fast_period": 30, "slow_period": 10}, "fast_period"),
    ({"fast_period": 1}, "fast_period"),
    ({"volatility_filter": {"enabled": True, "min_percentile": 90, "max_percentile": 10}},
     "volatility_filter.min_percentile"),
    ({"session_filter": {"enabled": True, "session": "25:00-03:00"}}, "session_filter.session"),
    ({"direction": "sideways"}, "direction"),
])
def test_parameter_validation(bad, path):
    with pytest.raises(ConfigValidationError) as exc:
        MaCrossoverStrategy(bad)
    assert any(e["path"] == path for e in exc.value.errors)


def test_basic_crossover_signals_and_stops():
    bars = random_walk_bars(3_000, seed=4)
    e = entries({}, bars)
    assert len(e) > 10
    longs, shorts = e[e["entry"] == 1], e[e["entry"] == -1]
    assert (longs["stop"] < bars.loc[longs.index, "close"]).all()
    assert (shorts["stop"] > bars.loc[shorts.index, "close"]).all()
    assert (longs["target"] > bars.loc[longs.index, "close"]).all()


def test_direction_parameter():
    bars = random_walk_bars(3_000, seed=4)
    assert (entries({"direction": "long_only"}, bars)["entry"] == 1).all()
    assert (entries({"direction": "short_only"}, bars)["entry"] == -1).all()


def test_trend_filter_on_off():
    bars = random_walk_bars(4_000, seed=6)
    base = entries({}, bars)
    filt = entries({"trend_filter": {"enabled": True, "period": 200}}, bars)
    assert 0 < len(filt) < len(base)
    trend = bars["close"].ewm(span=200, adjust=False).mean()
    c = bars["close"]
    assert (c[filt.index[filt["entry"] == 1]] > trend[filt.index[filt["entry"] == 1]]).all()
    assert (c[filt.index[filt["entry"] == -1]] < trend[filt.index[filt["entry"] == -1]]).all()


def test_session_filter_on_off():
    bars = random_walk_bars(4_000, seed=6)
    filt = entries({"session_filter": {"enabled": True, "session": "12:00-20:00"}}, bars)
    hours = ((bars.loc[filt.index, "ts"] // 3_600_000) % 24).to_numpy()
    assert len(filt) and ((hours >= 12) & (hours < 20)).all()
    assert in_session(np.array([23 * 3_600_000, 1 * 3_600_000, 5 * 3_600_000]), "22:00-02:00").tolist() == [
        True, True, False]


def test_volume_and_volatility_filters_reduce_entries_and_stay_causal():
    bars = random_walk_bars(4_000, seed=8)
    bars["volume"] = np.random.default_rng(1).lognormal(0, 0.6, len(bars))
    p = {"volume_filter": {"enabled": True, "min_ratio": 1.2},
         "volatility_filter": {"enabled": True, "lookback": 100, "min_percentile": 20, "max_percentile": 90}}
    assert len(entries(p, bars)) < len(entries({}, bars))
    check_causality(MaCrossoverStrategy(p), bars, n_cuts=20)


@pytest.fixture
def api(tmp_path):
    """API with 60 days of 15m data from the fake OKX + the ICT synthetic 5m scenario."""
    import time

    now = int(time.time() * 1000)
    last = now - now % M15.ms - M15.ms
    fake = FakeOkx(M15, last - 6_000 * M15.ms, last)
    settings = Settings(data_dir=tmp_path, strategy_config_dir=BACKEND_DIR / "config" / "strategies")
    CandleStore(tmp_path / "market").merge_save("BTC-USDT-SWAP", M5, synth.bearish_ote_scenario())
    app = create_app(settings, client_factory=lambda s: OkxPublicClient(transport=fake.transport(),
                                                                       sleep=lambda x: None))
    tc = TestClient(app)
    assert tc.post("/api/market/collect", json={"timeframe": "15m", "days": 60}).status_code == 200
    tc.last = last  # type: ignore[attr-defined]
    return tc


def test_end_to_end_without_any_strategy_specific_platform_code(api):
    # 1-3. discovered, described, parameters available for the generic UI
    s = {x["strategy_id"]: x for x in api.get("/api/strategies").json()}
    ma = s["ma_crossover"]
    specs = {p["path"]: p for p in ma["param_specs"]}
    assert specs["fast_period"]["kind"] == "int" and specs["fast_period"]["minimum"] == 2
    assert specs["session_filter.session"]["kind"] == "session"
    assert specs["trend_filter.period"]["depends_on"] == {"path": "trend_filter.enabled", "equals": True}
    assert {c["id"] for c in ma["components"] if c["role"] == "optional"} >= {"trend_filter", "volume_filter"}

    # 4-5. validate edits + toggle an optional filter
    bad = api.post("/api/strategies/ma_crossover/validate", json={"params": {"fast_period": 50, "slow_period": 20}})
    assert bad.json()["ok"] is False and bad.json()["errors"][0]["path"] == "fast_period"
    ok = api.post("/api/strategies/ma_crossover/validate",
                  json={"params": {"fast_period": 8, "trend_filter": {"enabled": True}}})
    assert ok.json()["ok"] and ok.json()["components"]["trend_filter"] is True

    # 8. save as a NEW version (clone of v1); duplicates and invalid params are refused
    new = api.post("/api/strategies/ma_crossover/configs",
                   json={"version": "v3-fast8", "parent_version": "v1", "description": "clone of v1, fast 8",
                         "params": {"fast_period": 8, "slow_period": 30}})
    assert new.status_code == 200 and new.json()["source"] == "user"
    assert api.post("/api/strategies/ma_crossover/configs",
                    json={"version": "v3-fast8", "params": {}}).status_code == 409
    assert api.post("/api/strategies/ma_crossover/configs",
                    json={"version": "v4-bad", "params": {"fast_period": 0}}).status_code == 422

    # 6. backtest on the SAME engine
    end = api.last + M15.ms
    cond = {"timeframe": "15m", "start_ts": end - 30 * 86_400_000, "end_ts": end, "initial_capital": 10_000}
    runs = {}
    for sid, ver in (("ma_crossover", "v1"), ("ma_crossover", "v3-fast8"), ("ema_cross", "v1")):
        r = api.post("/api/backtests", json={"strategy_id": sid, "strategy_version": ver, **cond})
        assert r.status_code == 200, r.text
        runs[(sid, ver)] = r.json()
    assert runs[("ma_crossover", "v3-fast8")]["strategy"]["config_source"] == "user"
    assert runs[("ma_crossover", "v3-fast8")]["params"]["fast_period"] == 8
    # different warm-up lengths, same test window -> same comparison fingerprint, different input fingerprint
    ca, cb = runs[("ma_crossover", "v1")]["conditions"], runs[("ema_cross", "v1")]["conditions"]
    assert ca["data_fingerprint"] == cb["data_fingerprint"]
    assert ca["input_fingerprint"] != cb["input_fingerprint"]

    # 7. ICT vs MA on identical 5m data and identical cost/risk assumptions
    s5 = synth.bearish_ote_scenario()
    c5 = {"timeframe": "5m", "start_ts": int(s5["ts"].iloc[0]), "end_ts": int(s5["ts"].iloc[-1]) + M5.ms,
          "require_full_warmup": False}
    ict = api.post("/api/backtests", json={"strategy_id": "ict_bearish_ote", "strategy_version": "v1-mss-close", **c5})
    ma5 = api.post("/api/backtests", json={"strategy_id": "ma_crossover", "strategy_version": "v1", **c5})
    assert ict.status_code == 200 and ma5.status_code == 200
    ci, cm = ict.json()["conditions"], ma5.json()["conditions"]
    for k in ("inst_id", "timeframe", "start_ts", "end_ts", "initial_capital", "costs", "risk", "data_fingerprint"):
        assert ci[k] == cm[k], k            # results are comparable: same data and assumptions
    listed = {(r["strategy_id"], r["strategy_version"]) for r in api.get("/api/backtests").json()}
    assert {("ict_bearish_ote", "v1-mss-close"), ("ma_crossover", "v1")} <= listed

    # research view (generic) works too
    rv = api.post("/api/research/analyze", json={"strategy_id": "ma_crossover", "strategy_version": "v2-filtered",
                                                 "timeframe": "15m", "start_ts": end - 10 * 86_400_000, "end_ts": end})
    assert rv.status_code == 200, rv.text
    assert {ln["id"] for ln in rv.json()["view"]["lines"]} == {"ma_fast", "ma_slow", "ma_trend"}
