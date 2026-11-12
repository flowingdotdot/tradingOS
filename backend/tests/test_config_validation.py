"""Backend enforcement of parameter dependencies, required components and unknown keys.

The UI greys out dependent fields, but nothing may rely on that: every path that builds a
strategy (validate, save, research draft, backtest, repo YAML) must reject bad configurations.
"""

from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

from tradingos.api.app import create_app
from tradingos.config import BACKEND_DIR, Settings
from tradingos.strategy.builtin.ict_ote import IctOteStrategy
from tradingos.strategy.builtin.ma_crossover import MaCrossoverStrategy
from tradingos.strategy.configs import ConfigStore
from tradingos.strategy.registry import STRATEGY_CLASSES
from tradingos.strategy.schema import (
    ConfigValidationError,
    describe_params,
    effective_dependencies,
    unknown_param_paths,
    validate_dependencies,
)


def errors_for(cls, params) -> dict[str, str]:
    with pytest.raises(ConfigValidationError) as exc:
        cls(params)
    return {e["path"]: e["message"] for e in exc.value.errors}


# ------------------------------------------------------------------ explicit depends_on
def test_inert_parameter_with_explicit_dependency_is_rejected():
    # retest_expiry_bars only matters for entry.mode = fvg_retest
    e = errors_for(IctOteStrategy, {"entry": {"mode": "mss_close", "retest_expiry_bars": 30}})
    assert "entry.retest_expiry_bars" in e and "entry.mode" in e["entry.retest_expiry_bars"]
    IctOteStrategy({"entry": {"mode": "fvg_retest", "retest_expiry_bars": 30}})       # dependency met -> ok
    IctOteStrategy({"entry": {"mode": "mss_close", "retest_expiry_bars": 12}})        # default value -> ok


def test_dependency_on_enum_value():
    e = errors_for(IctOteStrategy, {"displacement": {"method": "median_body", "atr_mult": 2.0}})
    assert "displacement.atr_mult" in e
    IctOteStrategy({"displacement": {"method": "atr", "atr_mult": 2.0}})
    e = errors_for(IctOteStrategy, {"displacement": {"method": "atr", "mult": 3.0}})
    assert "displacement.mult" in e


def test_dependency_across_groups():
    # impulse.require_displacement depends on displacement.enabled
    e = errors_for(IctOteStrategy, {"displacement": {"enabled": False}, "impulse": {"require_displacement": False}})
    assert "impulse.require_displacement" in e
    IctOteStrategy({"displacement": {"enabled": False}})


# ---------------------------------------------------------- group `enabled` convention
def test_parameters_of_a_disabled_group_must_stay_default():
    e = errors_for(MaCrossoverStrategy, {"trend_filter": {"enabled": False, "period": 300}})
    assert "trend_filter.period" in e
    e = errors_for(IctOteStrategy, {"bias": {"enabled": False, "mode": "last_break"}})
    assert "bias.mode" in e
    e = errors_for(IctOteStrategy, {"fvg": {"enabled": False, "invalidation": "wick"}})
    assert "fvg.invalidation" in e
    MaCrossoverStrategy({"trend_filter": {"enabled": True, "period": 300}})


def test_group_convention_is_derived_generically():
    specs = describe_params(MaCrossoverStrategy.Params)
    by_path = {s.path: s for s in specs}
    deps = effective_dependencies(by_path["volume_filter.lookback"], by_path)
    assert {"path": "volume_filter.enabled", "equals": True} in deps
    assert effective_dependencies(by_path["volume_filter.enabled"], by_path) == []      # the flag itself is free
    assert effective_dependencies(by_path["stop.atr_mult"], by_path) == []              # group without a flag


@pytest.mark.parametrize("sid", sorted(STRATEGY_CLASSES))
def test_every_dependency_points_at_a_real_parameter_and_defaults_are_valid(sid):
    cls = STRATEGY_CLASSES[sid]
    specs = describe_params(cls.Params)
    paths = {s.path for s in specs}
    for s in specs:
        if s.depends_on:
            assert s.depends_on["path"] in paths, f"{s.path} depends on unknown {s.depends_on['path']}"
            assert "in" in s.depends_on or "equals" in s.depends_on
    assert validate_dependencies(specs, cls().params.model_dump()) == []


# ------------------------------------------------------------------------ unknown keys
def test_unknown_and_misspelled_parameters_are_rejected_not_ignored():
    e = errors_for(MaCrossoverStrategy, {"fast_perod": 5})
    assert e == {"fast_perod": "unknown parameter (not defined by this strategy)"}
    e = errors_for(MaCrossoverStrategy, {"trend_filter": {"enabled": True, "perod": 100}})
    assert "trend_filter.perod" in e
    e = errors_for(IctOteStrategy, {"swing": 3})                    # a group given a scalar
    assert "swing" in e
    specs = describe_params(MaCrossoverStrategy.Params)
    assert unknown_param_paths(specs, {"stop": {"atr_mult": 3}, "ma_type": "sma"}) == []


# -------------------------------------------------------------- required components
def test_required_and_dependent_components_are_enforced():
    e = errors_for(IctOteStrategy, {"fvg": {"enabled": False}, "ote": {"enabled": False}})
    assert any("entry zone" in m for m in e.values())
    e = errors_for(IctOteStrategy, {"entry": {"trigger": "touch", "mode": "fvg_retest"}})
    assert "entry.mode" in e


# --------------------------------------------------- API requests that bypass the UI
@pytest.fixture
def api(tmp_path):
    repo = tmp_path / "strategies"
    shutil.copytree(BACKEND_DIR / "config" / "strategies", repo)
    tc = TestClient(create_app(Settings(data_dir=tmp_path, strategy_config_dir=repo)))
    tc.repo, tc.tmp = repo, tmp_path  # type: ignore[attr-defined]
    return tc


BAD_CONFIGS = [
    ("ict_bearish_ote", {"entry": {"mode": "mss_close", "retest_expiry_bars": 99}}, "entry.retest_expiry_bars"),
    ("ma_crossover", {"volume_filter": {"enabled": False, "min_ratio": 5}}, "volume_filter.min_ratio"),
    ("ma_crossover", {"fast_perod": 5}, "fast_perod"),
    ("ict_bearish_ote", {"fvg": {"enabled": False}, "ote": {"enabled": False}}, "fvg_filter,ote_filter"),
]


@pytest.mark.parametrize("sid, params, path", BAD_CONFIGS)
def test_api_validate_save_and_draft_all_reject(api, sid, params, path):
    v = api.post(f"/api/strategies/{sid}/validate", json={"params": params}).json()
    assert v["ok"] is False and any(e["path"] == path for e in v["errors"])

    save = api.post(f"/api/strategies/{sid}/configs", json={"version": "bad-1", "params": params})
    assert save.status_code == 422
    assert all(c["version"] != "bad-1" for c in api.get(f"/api/strategies/{sid}").json()["configs"])  # not stored

    draft = api.post("/api/research/analyze", json={"strategy_id": sid, "draft_params": params,
                                                    "start_ts": 0, "end_ts": 86_400_000})
    assert draft.status_code == 422


def test_invalid_repo_yaml_is_flagged_and_cannot_run(api):
    f = api.repo / "ma_crossover" / "v9-bad.yaml"
    f.write_text("strategy_id: ma_crossover\nversion: v9-bad\nparams:\n  trend_filter: {enabled: false, period: 500}\n",
                 encoding="utf-8")
    configs = {c["version"]: c for c in api.get("/api/strategies/ma_crossover").json()["configs"]}
    assert configs["v9-bad"]["integrity"] == "invalid" and "trend_filter.period" in configs["v9-bad"]["error"]
    assert configs["v1"]["integrity"] == "ok"                       # one bad file does not break the others
    r = api.post("/api/backtests", json={"strategy_id": "ma_crossover", "strategy_version": "v9-bad",
                                         "timeframe": "15m", "start_ts": 0, "end_ts": 86_400_000})
    assert r.status_code == 400 and "no longer valid" in r.json()["detail"]


def test_stored_user_config_that_becomes_invalid_is_not_runnable(tmp_path):
    """Defence in depth: even if an invalid row reaches the store, get() refuses it."""
    import json
    import sqlite3

    repo = tmp_path / "strategies"
    shutil.copytree(BACKEND_DIR / "config" / "strategies", repo)
    store = ConfigStore(tmp_path / "db.sqlite", repo)
    store.list()
    con = sqlite3.connect(tmp_path / "db.sqlite")
    con.execute("INSERT INTO strategy_configs VALUES (?,?,?,?,?,?,?,?,?)",
                ("ma_crossover", "tampered", "user", "x", json.dumps({"fast_perod": 3}), "DRAFT", "", None, "2026-01-01"))
    con.commit()
    con.close()
    assert {c["version"]: c["integrity"] for c in store.list("ma_crossover")}["tampered"] == "invalid"
    with pytest.raises(Exception, match="no longer valid"):
        store.get("ma_crossover", "tampered")
