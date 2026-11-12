"""Strategy Lab research use case — strategy-agnostic.

Loads data per the plugin's declared requirements, calls `strategy.research_view()` and returns
generic primitives. Works for any registered plugin; no strategy names appear here.
"""

from __future__ import annotations

from typing import Any

from tradingos.config import Settings
from tradingos.domain.instruments import KNOWN_INSTRUMENTS
from tradingos.domain.timeframes import Timeframe
from tradingos.marketdata.store import data_fingerprint
from tradingos.services.data_access import PeriodNotAllowedError, load_strategy_bars
from tradingos.services.backtest_service import BacktestInputError, code_revision
from tradingos.strategy.configs import ConfigIntegrityError, ConfigStore
from tradingos.strategy.instances import check_data_requirements
from tradingos.strategy.registry import STRATEGY_CLASSES, resolve_config

MAX_RESEARCH_DAYS = 60
DAY_MS = 86_400_000


def analyze_for_chart(settings: Settings, configs: ConfigStore, strategy_id: str, version: str | None,
                      inst_id: str, start_ts: int, end_ts: int, timeframe: str | None = None,
                      draft_params: dict[str, Any] | None = None) -> dict[str, Any]:
    """version=None + draft_params -> unsaved draft (exploration only; never used for backtests)."""
    if end_ts <= start_ts:
        raise BacktestInputError("end_ts must be after start_ts")
    if end_ts - start_ts > MAX_RESEARCH_DAYS * DAY_MS:
        raise BacktestInputError(f"research view is limited to {MAX_RESEARCH_DAYS} days per request")
    if strategy_id not in STRATEGY_CLASSES:
        raise BacktestInputError(f"unknown strategy {strategy_id!r}")
    cls = STRATEGY_CLASSES[strategy_id]
    try:
        cfg = (resolve_config(strategy_id, "(draft)", draft_params) if draft_params is not None
               else configs.get(strategy_id, version or ""))
    except ConfigIntegrityError as exc:
        raise BacktestInputError(str(exc)) from exc
    tf = Timeframe(timeframe or cls.definition.data.base_timeframes[0])
    errors = check_data_requirements(strategy_id, inst_id, tf.value)
    if inst_id not in KNOWN_INSTRUMENTS:
        errors.append(f"unknown instrument {inst_id}")
    if errors:
        raise BacktestInputError("; ".join(errors))

    strategy = cls(cfg.params)
    try:
        bars, data_ctx = load_strategy_bars(settings, inst_id, tf, start_ts, end_ts, strategy.warmup_bars(),
                                            "research chart")
    except PeriodNotAllowedError as exc:
        raise BacktestInputError(str(exc)) from exc
    if bars.empty:
        raise BacktestInputError(f"no stored {tf.value} candles for {inst_id} — collect data first")
    view = strategy.research_view(bars, tf, start_ts, end_ts)
    return {
        "strategy_id": strategy_id,
        "strategy_name": cls.definition.name,
        "definition_version": cls.definition.definition_version,
        "strategy_version": cfg.version,
        "draft": draft_params is not None,
        "params": cfg.params,
        "params_hash": cfg.params_hash,
        "config_hash": cfg.config_hash,
        "components": strategy.component_states(),
        "code_revision": code_revision(),
        "data_fingerprint": data_fingerprint(bars),
        "inst_id": inst_id,
        "timeframe": tf.value,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "warmup_from_ts": int(bars["ts"].iloc[0]),
        "segment": data_ctx["segment"],
        "data_quality_rules": data_ctx["quality"],
        "view": view.to_dict(),
    }
