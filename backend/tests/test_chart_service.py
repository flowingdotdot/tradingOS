"""Chart Workspace backend: windowed loading, OOS protection, overlay == strategy signals."""

from __future__ import annotations

import pytest
import synth
from fastapi.testclient import TestClient

from tradingos.api.app import create_app
from tradingos.config import BACKEND_DIR, Settings
from tradingos.domain.timeframes import Timeframe
from tradingos.features.mtf import resample
from tradingos.marketdata.store import CandleStore

M5, M15, H4 = Timeframe.M5, Timeframe.M15, Timeframe.H4
OOS_START, OOS_END = 1_727_740_800_000, 1_778_716_800_000     # 2024-10-01, 2026-05-14
DAY = 86_400_000
NO_BIAS = {"bias": {"enabled": False}}


@pytest.fixture
def api(tmp_path):
    bars = synth.bearish_ote_scenario()                                    # in-sample dated
    oos = synth.to_frame(synth.legs(100, [(104, 1), (100, 1)] * 150), M5, t0=OOS_END - 600 * M5.ms)
    store = CandleStore(tmp_path / "market")
    store.merge_save("BTC-USDT-SWAP", M5, bars)
    store.merge_save("BTC-USDT-SWAP", M5, oos)                             # 600 sealed bars + 600 observed bars
    settings = Settings(data_dir=tmp_path, strategy_config_dir=BACKEND_DIR / "config" / "strategies")
    tc = TestClient(create_app(settings))
    tc.bars = bars  # type: ignore[attr-defined]
    assert tc.post("/api/strategies/ict_bearish_ote/configs",
                   json={"version": "t-nobias", "params": NO_BIAS, "parent_version": "v1-mss-close"}).status_code == 200
    return tc


def window(api, **kw):
    ts = api.bars["ts"]
    body = {"timeframe": "5m", "start_ts": int(ts.iloc[300]), "end_ts": int(ts.iloc[-1]) + M5.ms, **kw}
    return api.post("/api/chart/window", json=body)


def test_only_the_requested_window_is_returned(api):
    r = window(api)
    assert r.status_code == 200, r.text
    d = r.json()
    c = d["candles"]
    assert c["ts"][0] == api.bars["ts"].iloc[300] and len(c["ts"]) == len(api.bars) - 300
    assert set(c) == {"ts", "open", "high", "low", "close", "volume"}
    assert d["segment"] == "IS" and d["view"] is None and d["strategy"] is None
    assert not d["clipped_to_allowed_segment"]


def test_higher_timeframe_candles_are_the_bars_the_strategy_uses(api):
    ts = api.bars["ts"]
    r = window(api, timeframe="15m", start_ts=int(ts.iloc[0]), strategy_id="ict_bearish_ote",
               strategy_version="t-nobias").json()
    expect = resample(api.bars, M5, M15)
    assert r["candles"]["ts"] == expect["ts"].tolist()
    assert r["candles"]["high"] == pytest.approx(expect["high"].tolist())
    # only items of the displayed timeframe (or timeframe-independent ones) are sent
    assert {it["tf"] for it in r["view"]["items"]} <= {None, "15m"}
    assert r["view"]["timeframes"] == {} and r["strategy"]["strategy_version"] == "t-nobias"


def test_overlay_contains_structure_labels_ote_levels_and_grouped_layers(api):
    v = window(api, start_ts=int(api.bars["ts"].iloc[0]), timeframe="15m", strategy_id="ict_bearish_ote",
               strategy_version="t-nobias").json()["view"]
    swings = [it["text"] for it in v["items"] if it["layer"] == "swings"]
    assert swings and set(swings) <= {"SH", "SL", "HH", "LH", "EH", "HL", "LL", "EL"}
    assert {"HH", "LL"} & set(swings) or {"LH", "HL"} & set(swings)
    ote_labels = [it["label"].split()[0] for it in v["items"] if it["layer"] == "ote" and it["type"] == "segment"]
    assert {"0.62", "0.705", "0.79"} <= set(ote_labels)
    groups = {x["id"]: x["group"] for x in v["layers"]}
    assert groups["swings"] == "structure" and groups["structure"] == "bos_mss" and groups["fvg_bear"] == "fvg"
    assert groups["ote"] == "ote" and groups["bias"] == "htf_bias" and groups["signals"] == "signals"
    assert all(it["known_ts"] is not None for it in v["items"])


def test_overlay_signal_is_the_signal_the_backtest_traded(api):
    ts = api.bars["ts"]
    span = {"start_ts": int(ts.iloc[0]), "end_ts": int(ts.iloc[-1]) + M5.ms}
    run = api.post("/api/backtests", json={"strategy_id": "ict_bearish_ote", "strategy_version": "t-nobias",
                                           "timeframe": "5m", "require_full_warmup": False, **span}).json()
    assert len(run["trades"]) == 1
    trade = run["trades"][0]
    v = api.post("/api/chart/window", json={"timeframe": "5m", "strategy_id": "ict_bearish_ote",
                                            "strategy_version": "t-nobias", **span}).json()["view"]
    sig = [it for it in v["items"] if it["layer"] == "signals" and it["type"] == "marker"]
    assert [it["ts"] for it in sig] == [trade["signal_ts"]]                 # same bar the engine acted on
    sl = [it["price"] for it in v["items"] if it["layer"] == "sltp" and it["label"] == "SL"]
    tp = [it["price"] for it in v["items"] if it["layer"] == "sltp" and it["label"] == "TP"]
    assert sl == [pytest.approx(trade["stop"])] and tp == [pytest.approx(trade["target"])]

    listed = api.get(f"/api/backtests/{run['run_id']}/trades").json()
    assert listed["trades"][0]["entry_ts"] == trade["entry_ts"] and "equity" not in listed
    assert listed["trades"][0]["bars_held"] == trade["bars_held"]
    assert api.get("/api/backtests/nope/trades").status_code == 404


def test_sealed_oos_is_never_shown(api):
    inside = api.post("/api/chart/window", json={"timeframe": "5m", "start_ts": OOS_END - 500 * M5.ms,
                                                 "end_ts": OOS_END - 100 * M5.ms})
    assert inside.status_code == 400 and "SEALED" in inside.json()["detail"]

    # a window straddling the OOS end with its centre in the observed segment is clipped to it
    r = api.post("/api/chart/window", json={"timeframe": "5m", "start_ts": OOS_END - 100 * M5.ms,
                                            "end_ts": OOS_END + 400 * M5.ms})
    assert r.status_code == 200
    d = r.json()
    assert d["clipped_to_allowed_segment"] and d["segment"] == "OBSERVED"
    assert min(d["candles"]["ts"]) == OOS_END                                # no sealed bar
    # with a strategy: the warm-up may not come from the sealed window either
    s = api.post("/api/chart/window", json={"timeframe": "5m", "start_ts": OOS_END, "end_ts": OOS_END + 400 * M5.ms,
                                            "strategy_id": "ma_crossover", "strategy_version": "v1"}).json()
    assert min(s["candles"]["ts"]) == OOS_END and s["signals_valid_from_ts"] == OOS_END + 90 * M5.ms

    segs = api.get("/api/chart/segments").json()
    names = [x["name"] for x in segs["segments"]]
    assert names == ["IS", "OBSERVED"] and segs["segments"][1]["start_ts"] == OOS_END
    assert segs["split"]["oos_sealed"] is True

    # raw candle endpoint obeys the same policy
    raw = api.get("/api/market/candles", params={"timeframe": "5m", "start_ts": OOS_END - 50 * M5.ms,
                                                 "end_ts": OOS_END + 50 * M5.ms})
    assert raw.status_code == 400 and "SEALED" in raw.json()["detail"]
    assert api.get("/api/market/candles", params={"timeframe": "5m"}).status_code == 400      # "everything" too
    ok = api.get("/api/market/candles", params={"timeframe": "5m", "start_ts": OOS_END, "end_ts": OOS_END + DAY})
    assert ok.status_code == 200 and min(ok.json()["ts"]) == OOS_END


def test_window_size_is_capped_and_bad_requests_are_clear(api):
    ts = api.bars["ts"]
    d = api.post("/api/chart/window", json={"timeframe": "5m", "start_ts": int(ts.iloc[0]),
                                            "end_ts": int(ts.iloc[0]) + 400 * DAY}).json()
    assert d["end_ts"] - d["start_ts"] <= d["max_window_bars"] * M5.ms
    assert api.post("/api/chart/window", json={"timeframe": "5m", "start_ts": 10, "end_ts": 5}).status_code == 400
    bad = api.post("/api/chart/window", json={"timeframe": "5m", "start_ts": int(ts.iloc[0]),
                                              "end_ts": int(ts.iloc[50]), "strategy_id": "nope"})
    assert bad.status_code == 400
    far = api.post("/api/chart/window", json={"timeframe": "5m", "start_ts": 1_000_000_000_000,
                                              "end_ts": 1_000_000_000_000 + DAY})
    assert far.status_code == 400                                             # before the stored data


def test_generic_strategy_gets_indicator_lines(api):
    ts = api.bars["ts"]
    v = api.post("/api/chart/window", json={"timeframe": "5m", "start_ts": int(ts.iloc[200]),
                                            "end_ts": int(ts.iloc[-1]) + M5.ms, "strategy_id": "ma_crossover",
                                            "strategy_version": "v1"}).json()["view"]
    assert {ln["id"] for ln in v["lines"]} == {"ma_fast", "ma_slow"}
