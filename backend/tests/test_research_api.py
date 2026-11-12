"""Generic research view + strategy APIs (any plugin) with the ICT scenario data."""

from __future__ import annotations

import shutil

import pytest
import synth
import yaml
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


def test_ict_research_view_is_expressed_with_generic_primitives(client):
    start, end = span(client)
    r = client.post("/api/research/analyze", json={"strategy_id": "ict_bearish_ote",
                                                   "strategy_version": "v1-mss-close", "start_ts": start, "end_ts": end})
    assert r.status_code == 200, r.text
    d = r.json()
    v = d["view"]
    assert set(v["timeframes"]) == {"5m", "15m", "4H"}
    assert v["tf_roles"] == {"5m": "trigger", "15m": "setup", "4H": "bias"}
    assert {it["type"] for it in v["items"]} <= {"marker", "rect", "segment", "band"}
    layer_ids = {x["id"] for x in v["layers"]}
    assert {"swings", "structure", "fvg_bear", "ote", "signals"} <= layer_ids
    # every item knows when it became knowable; swings are drawn k bars BEFORE they are known
    assert all(it["known_ts"] is not None for it in v["items"])
    swings = [it for it in v["items"] if it["layer"] == "swings"]
    assert swings and all(it["known_ts"] == it["ts"] + 4 * Timeframe(it["tf"]).ms for it in swings)
    assert {t["id"] for t in v["tables"]} >= {"setups", "signals", "swings_15m"}
    assert d["components"]["bias_filter"] is True and d["params_hash"] and d["data_fingerprint"]


def test_draft_params_are_analyzed_but_flagged(client):
    start, end = span(client)
    r = client.post("/api/research/analyze", json={"strategy_id": "ict_bearish_ote", "start_ts": start, "end_ts": end,
                                                   "draft_params": {"bias": {"enabled": False}}})
    assert r.status_code == 200 and r.json()["draft"] is True
    signals = next(t for t in r.json()["view"]["tables"] if t["id"] == "signals")
    assert len(signals["rows"]) == 1          # with the bias filter OFF the scenario produces its signal
    bad = client.post("/api/research/analyze", json={"strategy_id": "ict_bearish_ote", "start_ts": start,
                                                     "end_ts": end, "draft_params": {"ote": {"low": 2}}})
    assert bad.status_code == 422


def test_research_range_limit(client):
    r = client.post("/api/research/analyze", json={"strategy_id": "ict_bearish_ote", "strategy_version": "v1-mss-close",
                                                   "start_ts": 0, "end_ts": 100 * 86_400_000})
    assert r.status_code == 400 and "limited" in r.json()["detail"]


def test_strategies_endpoint_is_plugin_driven(client):
    s = {x["strategy_id"]: x for x in client.get("/api/strategies").json()}
    ict = s["ict_bearish_ote"]
    assert ict["required_timeframe"] == "5m" and ict["data"]["derived_timeframes"] == ["15m", "4H"]
    assert {v["version"] for v in ict["versions"]} == {"v1-mss-close", "v1-fvg-retest"}
    assert any(c["id"] == "fvg_filter" and c["role"] == "optional" for c in ict["components"])
    assert any(p["path"] == "swing.k_left" and p["kind"] == "int" and p["minimum"] == 1 for p in ict["param_specs"])


def test_backtest_api_enforces_data_requirements(client):
    start, end = span(client)
    base = {"strategy_id": "ict_bearish_ote", "start_ts": start, "end_ts": end}
    r = client.post("/api/backtests", json={**base, "strategy_version": "v1-mss-close", "timeframe": "15m"})
    assert r.status_code == 400 and "runs on 5m" in r.json()["detail"]
    r = client.post("/api/backtests", json={**base, "strategy_version": "v1-mss-close", "timeframe": "5m"})
    assert r.status_code == 400 and "insufficient history" in r.json()["detail"]   # never fabricated
    r = client.post("/api/backtests", json={**base, "strategy_version": "v1-mss-close", "timeframe": "5m",
                                            "require_full_warmup": False})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["strategy"]["definition_version"] and run["strategy"]["config_hash"]
    assert run["conditions"]["costs"]["taker_fee_rate"] == 0.0005


def test_limit_entry_variant_runs_on_the_common_engine(tmp_path):
    cfg_dir = tmp_path / "strategies" / "ict_bearish_ote"
    cfg_dir.mkdir(parents=True)
    raw = yaml.safe_load((BACKEND_DIR / "config/strategies/ict_bearish_ote/v1-fvg-retest.yaml").read_text("utf-8"))
    raw["version"] = "test-retest-nobias"
    raw["params"]["bias"] = {"enabled": False}    # so the synthetic scenario really emits a limit signal
    (cfg_dir / "test.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    shutil.copytree(BACKEND_DIR / "config/strategies/ema_cross", tmp_path / "strategies" / "ema_cross")
    bars = synth.bearish_ote_scenario()
    CandleStore(tmp_path / "market").merge_save("BTC-USDT-SWAP", M5, bars)
    tc = TestClient(create_app(Settings(data_dir=tmp_path, strategy_config_dir=tmp_path / "strategies")))
    r = tc.post("/api/backtests", json={"strategy_id": "ict_bearish_ote", "strategy_version": "test-retest-nobias",
                                        "timeframe": "5m", "start_ts": int(bars["ts"].iloc[0]),
                                        "end_ts": int(bars["ts"].iloc[-1]) + M5.ms, "require_full_warmup": False})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["execution_model"]["limit_orders_placed"] == 1 and run["segment"] == "IS"
    assert "trades through the limit" in run["execution_model"]["limit_orders"]
