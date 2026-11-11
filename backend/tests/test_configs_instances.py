"""Configuration immutability, instances (strategy ON/OFF), portfolio arbitration, OFF semantics."""

from __future__ import annotations

import shutil

import numpy as np
import pytest
from conftest import make_bars, zero_cost_config
from fastapi.testclient import TestClient

from tradingos.api.app import create_app
from tradingos.backtest.engine import run_backtest
from tradingos.config import BACKEND_DIR, LiveTradingDisabledError, Settings
from tradingos.domain.instruments import BTC_USDT_SWAP
from tradingos.risk.portfolio import (
    EntryIntent,
    OpenPosition,
    PortfolioLimits,
    arbitrate,
    client_order_id,
)
from tradingos.strategy.base import empty_signals
from tradingos.strategy.configs import ConfigExistsError, ConfigIntegrityError, ConfigStore
from tradingos.strategy.instances import InstanceSpec, InstanceStore


@pytest.fixture
def repo_dir(tmp_path):
    d = tmp_path / "strategies"
    shutil.copytree(BACKEND_DIR / "config" / "strategies", d)
    return d


def test_repo_config_modified_in_place_is_refused(tmp_path, repo_dir):
    store = ConfigStore(tmp_path / "db.sqlite", repo_dir)
    assert store.get("ma_crossover", "v1").config_hash
    f = repo_dir / "ma_crossover" / "v1.yaml"
    f.write_text(f.read_text(encoding="utf-8").replace("fast_period: 10", "fast_period: 12"), encoding="utf-8")
    assert {c["version"]: c["integrity"] for c in store.list("ma_crossover")}["v1"] == "modified"
    with pytest.raises(ConfigIntegrityError):
        store.get("ma_crossover", "v1")


def test_user_versions_are_immutable_and_hashes_distinguish_declared_vs_resolved(tmp_path, repo_dir):
    store = ConfigStore(tmp_path / "db.sqlite", repo_dir)
    a = store.create("ma_crossover", "mine-1", {"fast_period": 12}, parent_version="v1")
    with pytest.raises(ConfigExistsError):
        store.create("ma_crossover", "mine-1", {"fast_period": 13})
    with pytest.raises(ConfigExistsError):
        store.create("ma_crossover", "v1", {})                      # repo version names are taken too
    b = store.create("ma_crossover", "mine-2", {"fast_period": 12, "slow_period": 30})  # same RESOLVED params
    assert a.params_hash == b.params_hash and a.config_hash != b.config_hash
    assert store.get("ma_crossover", "mine-1").params["fast_period"] == 12


@pytest.fixture
def instances(tmp_path, repo_dir):
    configs = ConfigStore(tmp_path / "db.sqlite", repo_dir)
    return InstanceStore(tmp_path / "db.sqlite", configs)


def test_instance_lifecycle_and_readiness(instances, tmp_path):
    inst = instances.create(InstanceSpec(strategy_id="ma_crossover", config_version="v1", timeframe="15m",
                                         environment="DEMO", capital_allocation=500))
    assert inst.entries_enabled is False and inst.config_hash
    checks = {c["check"]: c["ok"] for c in instances.readiness(inst, Settings(data_dir=tmp_path))}
    assert checks["configuration integrity"] and checks["data requirements"]
    assert checks["lifecycle stage"] is False and checks["execution engine"] is False   # honest: not ready
    with pytest.raises(PermissionError):
        instances.set_entries_enabled(inst.instance_id, True)
    off = instances.set_entries_enabled(inst.instance_id, False, "manual stop")
    assert off.entries_enabled is False
    assert [e["kind"] for e in instances.events(inst.instance_id)] == ["created", "entries_disabled"]


def test_instance_rejects_live_and_unsupported_timeframes(instances):
    with pytest.raises(LiveTradingDisabledError):
        instances.create(InstanceSpec(strategy_id="ma_crossover", config_version="v1", timeframe="15m",
                                      environment="LIVE"))
    with pytest.raises(ValueError, match="runs on 5m"):
        instances.create(InstanceSpec(strategy_id="ict_bearish_ote", config_version="v1-mss-close",
                                      timeframe="15m"))


def test_instance_api(tmp_path, repo_dir):
    tc = TestClient(create_app(Settings(data_dir=tmp_path, strategy_config_dir=repo_dir)))
    r = tc.post("/api/instances", json={"strategy_id": "ma_crossover", "config_version": "v1", "timeframe": "1H"})
    assert r.status_code == 200
    iid = r.json()["instance_id"]
    detail = tc.get(f"/api/instances/{iid}").json()
    assert detail["ready"] is False and "Phase 4" in detail["runtime_status"]
    assert tc.post(f"/api/instances/{iid}/entries", json={"enabled": True}).status_code == 409
    assert tc.post("/api/instances", json={"strategy_id": "ma_crossover", "config_version": "v1", "timeframe": "1H",
                                           "environment": "LIVE"}).status_code == 403


# ---------------------------------------------------------------- OFF semantics in the engine
def test_strategy_off_blocks_new_entries_but_still_manages_open_position():
    flat = (100.0, 100.5, 99.5, 100.0)
    bars = make_bars([flat, flat, flat, flat, (99, 99.5, 89, 89.5), flat, flat, flat])
    sig = empty_signals(bars.index)
    for i in (0, 5):
        sig.loc[i, ["entry", "stop"]] = [1, 90.0]
    allowed = np.ones(len(bars), bool)
    allowed[2:] = False                                       # switched OFF after the first entry filled
    res = run_backtest(bars, sig, zero_cost_config(bars), BTC_USDT_SWAP, entries_allowed=allowed)
    assert len(res.trades) == 1 and res.trades[0].exit_reason == "stop"   # protective stop still honoured
    assert [r.reason for r in res.rejections] == ["entries_disabled"]


# ---------------------------------------------------------------- multi-strategy arbitration
def intent(iid, side, risk=10.0, inst="BTC-USDT-SWAP", ts=1):
    return EntryIntent(instance_id=iid, inst_id=inst, side=side, risk_amount=risk, decision_ts=ts)


def test_conflicting_signals_on_same_instrument_are_all_rejected():
    out = arbitrate([intent("a", 1), intent("b", -1)], [], equity=10_000)
    assert [d.reason for d in out] == ["conflicting_signals", "conflicting_signals"]


def test_position_ownership_and_per_instrument_limit():
    held = [OpenPosition("a", "BTC-USDT-SWAP", 1, 10)]
    out = arbitrate([intent("b", 1), intent("a", 1)], held, equity=10_000)
    reasons = {d.intent.instance_id: d.reason for d in out}
    assert reasons["b"].startswith("instrument_owned_by:a") and reasons["a"] == "max_positions_per_instrument"


def test_account_risk_limit_and_duplicates():
    out = arbitrate([intent("a", 1, risk=60, inst="X"), intent("a", 1, risk=60, inst="X"),
                     intent("b", 1, risk=60, inst="Y")], [], equity=10_000, limits=PortfolioLimits(max_total_risk_pct=1))
    assert [d.reason for d in out] == ["approved", "duplicate_intent", "account_risk_limit"]


def test_client_order_ids_are_deterministic_and_exchange_safe():
    a = client_order_id("ma_crossover-1a2b3c4d", 1_700_000_000_000, "entry")
    assert a == client_order_id("ma_crossover-1a2b3c4d", 1_700_000_000_000, "entry")
    assert a != client_order_id("ma_crossover-1a2b3c4d", 1_700_000_000_000, "stop")
    assert len(a) <= 32 and a.isalnum()
