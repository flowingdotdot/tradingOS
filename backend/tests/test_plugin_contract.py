"""Contract tests applied automatically to EVERY discovered strategy plugin.

Adding a plugin = these tests cover it with no extra test code: metadata, schema, configs,
component validation, causality, incremental==batch equivalence, default research view,
generated documentation freshness.
"""

from __future__ import annotations

import copy

import pandas as pd
import pytest
from conftest import random_walk_bars

from tradingos.config import BACKEND_DIR, PROJECT_DIR
from tradingos.domain.timeframes import Timeframe
from tradingos.strategy.base import validate_signals
from tradingos.strategy.causality import check_causality
from tradingos.strategy.docgen import GENERATED_DIR, render
from tradingos.strategy.registry import (
    STRATEGY_CLASSES,
    describe_strategy,
    discover_plugins,
    load_versions,
)
from tradingos.strategy.schema import ConfigValidationError, get_path, set_path

PLUGINS = sorted(STRATEGY_CLASSES)
CONFIGS = load_versions(BACKEND_DIR / "config" / "strategies")


def sample_bars(cls, n_extra: int = 1_500, seed: int = 3):
    tf = Timeframe(cls.definition.data.base_timeframes[0])
    return random_walk_bars(cls().warmup_bars() + n_extra, seed=seed, tf=tf), tf


def test_discovery_is_deterministic_and_complete():
    assert discover_plugins() .keys() == STRATEGY_CLASSES.keys()
    assert {"ema_cross", "ict_bearish_ote", "ma_crossover"} <= set(PLUGINS)


@pytest.mark.parametrize("sid", PLUGINS)
def test_definition_metadata_and_docs(sid):
    cls = STRATEGY_CLASSES[sid]
    d = cls.definition
    assert d.strategy_id == sid and d.name and d.description and d.data.base_timeframes and d.data.instruments
    assert d.docs and (PROJECT_DIR / d.docs).exists(), f"missing human-written doc {d.docs}"
    ids = [c.id for c in d.components]
    assert len(ids) == len(set(ids)) and any(c.role == "required" for c in d.components)
    for c in d.components:
        assert set(c.requires) <= set(ids)
        if c.toggle_path:
            get_path(cls().params.model_dump(), c.toggle_path)   # toggle must point at a real parameter


@pytest.mark.parametrize("sid", PLUGINS)
def test_param_schema_is_fully_described(sid):
    d = describe_strategy(sid)
    specs = [p for p in d["param_specs"] if p["kind"] != "group"]
    assert specs and all(p["label"] for p in specs)
    defaults = d["default_params"]
    for p in specs:
        assert get_path(defaults, p["path"]) == p["default"]
        if p["kind"] in ("enum", "timeframe"):
            assert p["default"] in p["options"]


@pytest.mark.parametrize("sid", PLUGINS)
def test_required_components_cannot_be_disabled_and_optional_ones_can(sid):
    cls = STRATEGY_CLASSES[sid]
    base = cls().params.model_dump()
    for c in cls.definition.components:
        if not c.toggle_path:
            continue
        params = copy.deepcopy(base)          # nested dicts: a shallow copy would leak toggles
        set_path(params, c.toggle_path, c.off_value)
        if c.role == "required":
            with pytest.raises(ConfigValidationError):
                cls(params)
        else:
            try:
                cls(params)
            except ConfigValidationError:
                # refusing to switch an optional component off is only legitimate when a declared
                # rule or another component depends on it
                needed = any(c.id in r.components for r in cls.definition.component_rules) or any(
                    c.id in o.requires for o in cls.definition.components)
                assert needed, f"optional component {c.id} cannot be disabled and nothing declares why"


@pytest.mark.parametrize("cfg", CONFIGS, ids=lambda c: f"{c.strategy_id}:{c.version}")
def test_repository_configs_resolve(cfg):
    assert cfg.strategy_id in STRATEGY_CLASSES
    assert len(cfg.config_hash) == 64 and len(cfg.params_hash) == 64


@pytest.mark.parametrize("sid", PLUGINS)
def test_default_config_is_causal(sid):
    cls = STRATEGY_CLASSES[sid]
    bars, _ = sample_bars(cls)
    check_causality(cls(), bars, n_cuts=15)


@pytest.mark.parametrize("sid", PLUGINS)
def test_incremental_update_equals_batch(sid):
    """The live path (one closed bar at a time) must give exactly the backtest signals."""
    cls = STRATEGY_CLASSES[sid]
    bars, _ = sample_bars(cls, n_extra=400)
    strat = cls()
    batch = strat.generate_signals(bars)
    validate_signals(batch, bars)
    k = len(bars) - 25
    state = strat.create_state(bars.iloc[:k])
    for i in range(k, len(bars)):
        row = strat.update(state, bars.iloc[i].to_dict())
        pd.testing.assert_series_equal(row, batch.iloc[i], check_names=False)


@pytest.mark.parametrize("sid", PLUGINS)
def test_research_view_uses_generic_primitives(sid):
    cls = STRATEGY_CLASSES[sid]
    bars, tf = sample_bars(cls, n_extra=600)
    start, end = int(bars["ts"].iloc[-500]), int(bars["ts"].iloc[-1]) + tf.ms
    v = cls().research_view(bars, tf, start, end).to_dict()
    assert v["default_tf"] in v["timeframes"]
    assert {it["type"] for it in v["items"]} <= {"marker", "rect", "segment", "band"}
    assert all(it["known_ts"] is not None and it["layer"] in {x["id"] for x in v["layers"]} for it in v["items"])
    assert any(t["id"] == "signals" for t in v["tables"])


@pytest.mark.parametrize("sid", PLUGINS)
def test_generated_parameter_reference_is_up_to_date(sid):
    path = GENERATED_DIR / f"{sid}.md"
    assert path.exists(), "run `tradingos docs`"
    assert path.read_text(encoding="utf-8") == render(sid), "stale generated doc: run `tradingos docs`"
