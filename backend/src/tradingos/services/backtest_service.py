"""Use cases shared by the API and CLI: run & persist a backtest."""

from __future__ import annotations

import subprocess
import uuid
from datetime import UTC, datetime
from typing import Any

from tradingos.backtest.engine import run_backtest
from tradingos.backtest.metrics import compute_metrics
from tradingos.backtest.models import ENGINE_VERSION, BacktestConfig
from tradingos.config import BACKEND_DIR, ExecutionEnvironment, Settings, assert_environment_allowed
from tradingos.domain.instruments import KNOWN_INSTRUMENTS
from tradingos.domain.timeframes import Timeframe
from tradingos.marketdata.store import CandleStore, data_fingerprint
from tradingos.marketdata.validation import validate_candles
from tradingos.storage.runs import RunRepository
from tradingos.strategy.base import has_limit_entries
from tradingos.strategy.configs import ConfigIntegrityError, ConfigStore
from tradingos.strategy.instances import check_data_requirements
from tradingos.strategy.registry import build_strategy

DISCLAIMER = (
    "Hypothetical simulation on historical data with assumed costs. "
    "Past simulated results do not guarantee future performance."
)


class BacktestInputError(ValueError):
    pass


def code_revision() -> str:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"], cwd=BACKEND_DIR, capture_output=True, text=True, timeout=5, check=False
        )
        if sha.returncode != 0:
            return "uncommitted"
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--", "."], cwd=BACKEND_DIR, capture_output=True, text=True, timeout=5, check=False
        )
        return sha.stdout.strip() + ("-dirty" if dirty.stdout.strip() else "")
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def run_and_store(config: BacktestConfig, settings: Settings, repo: RunRepository,
                  configs: ConfigStore | None = None) -> dict[str, Any]:
    """One engine for every strategy: the plugin only supplies signals."""
    assert_environment_allowed(ExecutionEnvironment.BACKTEST)
    configs = configs or ConfigStore(settings.db_path, settings.strategy_config_dir)
    try:
        version = configs.get(config.strategy_id, config.strategy_version)
    except ConfigIntegrityError as exc:
        raise BacktestInputError(str(exc)) from exc
    tf = Timeframe(config.timeframe)
    instrument = KNOWN_INSTRUMENTS.get(config.inst_id)
    if instrument is None:
        raise BacktestInputError(f"unknown instrument {config.inst_id}")
    req_errors = check_data_requirements(version.strategy_id, config.inst_id, tf.value)
    if req_errors:
        raise BacktestInputError("; ".join(req_errors))

    store = CandleStore(settings.market_data_dir)
    strategy = build_strategy(version.strategy_id, version.params)
    # Load extra history before start_ts so indicators are warmed up; trading is only
    # allowed from start_ts onwards (signals before it are discarded).
    warm_start = config.start_ts - strategy.warmup_bars() * tf.ms
    bars = store.load(config.inst_id, tf, warm_start, config.end_ts)
    if bars.empty:
        raise BacktestInputError("no stored candles for this instrument/timeframe/period — collect data first")
    test_mask = (bars["ts"] >= config.start_ts).to_numpy()
    if test_mask.sum() < 2:
        raise BacktestInputError("not enough candles inside the test period")

    signals = strategy.generate_signals(bars)
    signals.loc[~test_mask, ["entry"]] = 0
    signals.loc[~test_mask, ["exit_long", "exit_short"]] = False
    first = int(test_mask.argmax())
    bars_t, signals_t = bars.iloc[first:].reset_index(drop=True), signals.iloc[first:].reset_index(drop=True)

    quality = validate_candles(bars_t, tf)
    if not quality.ok:
        raise BacktestInputError(f"stored data failed validation: {quality.to_dict()}")
    warmup_available = first
    notes: list[str] = []
    if warmup_available < strategy.warmup_bars() and config.require_full_warmup:
        raise BacktestInputError(
            f"insufficient history: {strategy.warmup_bars()} {tf.value} bars of warm-up are required before the "
            f"start date but only {warmup_available} are stored. Collect more data or move the start later "
            "(missing data is never generated)."
        )
    if warmup_available < strategy.warmup_bars():
        notes.append(
            f"Only {warmup_available} warm-up bars available before start (strategy wants "
            f"{strategy.warmup_bars()}); early signals are suppressed by the strategy's own warm-up."
        )
    if quality.gaps:
        notes.append(f"Data has {len(quality.gaps)} gap(s), {quality.missing_bars} missing bar(s). Not filled.")

    if has_limit_entries(signals_t):
        raise BacktestInputError(
            "this strategy version emits LIMIT entries (e.g. fvg_retest); the backtest engine supports "
            "market entries only until Phase 3. Use it in Strategy Lab for research."
        )
    result = run_backtest(bars_t, signals_t, config, instrument)
    metrics = compute_metrics(result)
    notes += result.notes

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    record = {
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "environment": ExecutionEnvironment.BACKTEST.value,
        "strategy_id": version.strategy_id,
        "strategy_version": version.version,
        "params_hash": version.params_hash,
        "code_revision": code_revision(),
        "engine_version": ENGINE_VERSION,
        "data_fingerprint": data_fingerprint(bars),
        "inst_id": config.inst_id,
        "timeframe": tf.value,
        "start_ts": config.start_ts,
        "end_ts": config.end_ts,
    }
    summary = {k: metrics[k] for k in ("total_trades", "net_profit", "return_pct", "win_rate_pct",
                                       "profit_factor", "max_drawdown_pct", "expectancy_r")}
    definition = strategy.definition
    payload = {
        "config": config.model_dump(),
        "params": version.params,
        "strategy": {
            "strategy_id": definition.strategy_id,
            "name": definition.name,
            "definition_version": definition.definition_version,
            "config_version": version.version,
            "config_source": version.source,
            "config_hash": version.config_hash,
            "params_hash": version.params_hash,
            "declared_params": version.declared_params,
            "resolved_params": version.params,
            "components": strategy.component_states(),
        },
        "conditions": {
            "inst_id": config.inst_id, "timeframe": tf.value, "start_ts": config.start_ts, "end_ts": config.end_ts,
            "initial_capital": config.initial_capital, "costs": config.costs.model_dump(),
            "risk": config.risk.model_dump(),
            # test-window data (what results are compared on) vs. full input incl. strategy warm-up
            "data_fingerprint": data_fingerprint(bars_t),
            "input_fingerprint": data_fingerprint(bars),
            "warmup_bars": strategy.warmup_bars(),
        },
        "metrics": metrics,
        "trades": [t.model_dump() for t in result.trades],
        "rejections": [r.model_dump() for r in result.rejections],
        "equity": result.equity.assign(in_position=result.equity["in_position"].astype(int)).to_dict("list"),
        "data_quality": quality.to_dict(),
        "notes": notes,
        "disclaimer": DISCLAIMER,
    }
    repo.save(record, summary, payload)
    return {**record, "summary": summary, **payload}
