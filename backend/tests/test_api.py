"""End-to-end vertical slice through the HTTP API with a fake OKX exchange:
collect -> list datasets -> run backtest -> fetch run -> export CSV."""

from __future__ import annotations

import time

import pytest
from fake_okx import FakeOkx
from fastapi.testclient import TestClient

from tradingos.api.app import create_app
from tradingos.config import BACKEND_DIR, Settings
from tradingos.domain.timeframes import Timeframe
from tradingos.marketdata.okx_public import OkxPublicClient

TF = Timeframe.M15


@pytest.fixture
def client(tmp_path, monkeypatch):
    for k in ("OKX_DEMO_API_KEY", "OKX_DEMO_API_SECRET", "OKX_DEMO_PASSPHRASE"):
        monkeypatch.delenv(k, raising=False)
    now = int(time.time() * 1000)
    last_closed = now - now % TF.ms - TF.ms
    fake = FakeOkx(TF, last_closed - 6_000 * TF.ms, last_closed, missing={last_closed - 100 * TF.ms})
    settings = Settings(data_dir=tmp_path, strategy_config_dir=BACKEND_DIR / "config" / "strategies",
                        OKX_DEMO_API_KEY="demo-key-should-never-be-returned")
    app = create_app(settings, client_factory=lambda s: OkxPublicClient(transport=fake.transport(),
                                                                       sleep=lambda x: None))
    tc = TestClient(app)
    tc.fake = fake  # type: ignore[attr-defined]
    tc.last_closed = last_closed  # type: ignore[attr-defined]
    return tc


def test_system_reports_live_disabled_and_hides_secrets(client):
    r = client.get("/api/system")
    assert r.status_code == 200
    body = r.json()
    assert body["environments"]["LIVE"]["enabled"] is False
    assert body["environments"]["DEMO"]["credentials_configured"] is False  # only 1 of 3 set
    assert "demo-key-should-never-be-returned" not in r.text


def test_full_vertical_slice(client):
    r = client.post("/api/market/collect", json={"inst_id": "BTC-USDT-SWAP", "timeframe": "15m", "days": 60})
    assert r.status_code == 200, r.text
    col = r.json()
    assert col["stored_rows"] > 5_000
    assert col["validation"]["missing_bars"] == 1  # reported, not filled

    ds = client.get("/api/market/datasets").json()
    assert ds[0]["inst_id"] == "BTC-USDT-SWAP" and ds[0]["timeframe"] == "15m"

    strategies = client.get("/api/strategies").json()
    assert {v["version"] for v in strategies[0]["versions"]} >= {"v1", "v2-short-only"}

    end = client.last_closed + TF.ms
    cfg = {"strategy_id": "ema_cross", "strategy_version": "v1", "timeframe": "15m",
           "start_ts": end - 40 * 86_400_000, "end_ts": end, "initial_capital": 10_000}
    r = client.post("/api/backtests", json=cfg)
    assert r.status_code == 200, r.text
    run = r.json()
    for key in ("run_id", "params_hash", "code_revision", "data_fingerprint", "engine_version", "disclaimer"):
        assert run[key]
    assert run["environment"] == "BACKTEST"
    assert len(run["equity"]["ts"]) > 3_000

    fetched = client.get(f"/api/backtests/{run['run_id']}").json()
    assert fetched["metrics"] == run["metrics"]
    assert client.get("/api/backtests").json()[0]["run_id"] == run["run_id"]

    csv_r = client.get(f"/api/backtests/{run['run_id']}/trades.csv")
    assert csv_r.status_code == 200
    assert csv_r.text.count("\n") == run["metrics"]["total_trades"] + (1 if run["trades"] else 0)

    # Same inputs -> same results (reproducibility)
    run2 = client.post("/api/backtests", json=cfg).json()
    assert run2["data_fingerprint"] == run["data_fingerprint"]
    assert run2["trades"] == run["trades"]


def test_backtest_without_data_is_a_clear_400(client):
    r = client.post("/api/backtests", json={"strategy_id": "ema_cross", "strategy_version": "v1",
                                            "start_ts": 1_000_000, "end_ts": 2_000_000})
    assert r.status_code == 400 and "collect data first" in r.json()["detail"]


def test_unknown_strategy_version_is_400(client):
    r = client.post("/api/backtests", json={"strategy_id": "ema_cross", "strategy_version": "v999",
                                            "start_ts": 1_000_000, "end_ts": 2_000_000})
    assert r.status_code == 400
