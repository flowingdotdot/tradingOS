"""Strategy Lab research endpoint + ICT strategy through the backtest API."""

from __future__ import annotations

import pytest
import synth
from fastapi.testclient import TestClient

from tradingos.api.app import create_app
from tradingos.config import BACKEND_DIR, Settings
from tradingos.domain.timeframes import Timeframe
from tradingos.marketdata.store import CandleStore

M5 = Timeframe.M5


@pytest.fixture
def client(tmp_path):
    bars = synth.bearish_ote_scenario()
    CandleStore(tmp_path / "market").merge_save("BTC-USDT-SWAP", M5, bars)
    settings = Settings(data_dir=tmp_path, strategy_config_dir=BACKEND_DIR / "config" / "strategies")
    tc = TestClient(create_app(settings))
    tc.bars = bars  # type: ignore[attr-defined]
    return tc


def span(client):
    ts = client.bars["ts"]
    return int(ts.iloc[0]), int(ts.iloc[-1]) + M5.ms


def test_research_payload_has_occurrence_and_known_times(client):
    start, end = span(client)
    r = client.post("/api/research/analyze", json={"strategy_version": "v1-mss-close", "start_ts": start, "end_ts": end})
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d["timeframes"]) == {"5m", "15m", "4H"} and d["roles"] == {"base": "5m", "impulse": "15m", "bias": "4H"}
    for tf, payload in d["timeframes"].items():
        ms = Timeframe(tf).ms
        for s in payload["swings"]:
            assert s["known_ts"] == s["ts"] + (s["lag_bars"] + 1) * ms   # known at close of ts+k bar
            assert s["lag_bars"] == 3
        for e in payload["structure"]:
            assert e["ref_known_ts"] <= e["ts"] < e["known_ts"]
        for f in payload["fvgs"]:
            assert f["known_ts"] == f["ts"] + 2 * ms
    # 4H bias cannot form on ~5 days of data -> the setup is recorded but filtered
    assert d["setup_status_counts"] == {"bias_not_bearish": 1}
    assert d["params_hash"] and d["data_fingerprint"]


def test_research_range_limit(client):
    r = client.post("/api/research/analyze", json={"start_ts": 0, "end_ts": 100 * 86_400_000})
    assert r.status_code == 400 and "limited" in r.json()["detail"]


def test_strategies_endpoint_lists_ict_versions(client):
    s = {x["strategy_id"]: x for x in client.get("/api/strategies").json()}
    ict = s["ict_bearish_ote"]
    assert ict["required_timeframe"] == "5m" and ict["has_research_view"]
    assert {v["version"] for v in ict["versions"]} == {"v1-mss-close", "v1-fvg-retest"}
    assert not s["ema_cross"]["has_research_view"]


def test_backtest_api_enforces_timeframe_and_refuses_limit_variant(client):
    start, end = span(client)
    base = {"strategy_id": "ict_bearish_ote", "start_ts": start, "end_ts": end}
    r = client.post("/api/backtests", json={**base, "strategy_version": "v1-mss-close", "timeframe": "15m"})
    assert r.status_code == 400 and "runs on 5m" in r.json()["detail"]
    r = client.post("/api/backtests", json={**base, "strategy_version": "v1-mss-close", "timeframe": "5m"})
    assert r.status_code == 200, r.text
    assert r.json()["params_hash"]                    # version + params stored with the run


def test_limit_entry_variant_is_refused_by_the_backtest_service(tmp_path):
    import shutil

    import yaml

    cfg_dir = tmp_path / "strategies" / "ict_bearish_ote"
    cfg_dir.mkdir(parents=True)
    raw = yaml.safe_load((BACKEND_DIR / "config/strategies/ict_bearish_ote/v1-fvg-retest.yaml").read_text("utf-8"))
    raw["version"] = "test-retest-nobias"
    raw["params"]["bias"]["enabled"] = False      # so the synthetic scenario really emits a limit signal
    (cfg_dir / "test.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    shutil.copytree(BACKEND_DIR / "config/strategies/ema_cross", tmp_path / "strategies" / "ema_cross")
    bars = synth.bearish_ote_scenario()
    CandleStore(tmp_path / "market").merge_save("BTC-USDT-SWAP", M5, bars)
    tc = TestClient(create_app(Settings(data_dir=tmp_path, strategy_config_dir=tmp_path / "strategies")))
    r = tc.post("/api/backtests", json={"strategy_id": "ict_bearish_ote", "strategy_version": "test-retest-nobias",
                                        "timeframe": "5m", "start_ts": int(bars["ts"].iloc[0]),
                                        "end_ts": int(bars["ts"].iloc[-1]) + M5.ms})
    assert r.status_code == 400 and "LIMIT entries" in r.json()["detail"]
