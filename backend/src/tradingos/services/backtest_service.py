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
from tradingos.marketdata.store import data_fingerprint
from tradingos.marketdata.validation import validate_candles
from tradingos.services.data_access import PeriodNotAllowedError, load_strategy_bars
from tradingos.storage.runs import RunRepository
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


MAX_EQUITY_POINTS = 20_000


def _equity_for_storage(eq) -> dict[str, Any]:
    """Metrics are computed on the full curve; long curves are thinned for storage/display only."""
    step = max(1, -(-len(eq) // MAX_EQUITY_POINTS))
    if step > 1:
        idx = sorted(set(range(0, len(eq), step)) | {len(eq) - 1})
        eq = eq.iloc[idx]
    out = eq.assign(in_position=eq["in_position"].astype(int)).to_dict("list")
    out["downsample_step"] = step
    return out


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

    strategy = build_strategy(version.strategy_id, version.params)
    # Load extra history before start_ts so indicators are warmed up; trading is only
    # allowed from start_ts onwards (signals before it are discarded). The loader enforces
    # the OOS seal and applies the data-quality rules.
    try:
        bars, data_ctx = load_strategy_bars(settings, config.inst_id, tf, config.start_ts, config.end_ts,
                                            strategy.warmup_bars(), "backtest")
    except PeriodNotAllowedError as exc:
        raise BacktestInputError(str(exc)) from exc
    if bars.empty:
        raise BacktestInputError("no stored candles for this instrument/timeframe/period — collect data first")
    test_mask = (bars["ts"] >= config.start_ts).to_numpy()
    if test_mask.sum() < 2:
        raise BacktestInputError("not enough candles inside the test period")

    # One evaluation yields both the signals and (if the plugin records it) the Signal Inspector.
    signals, inspection = strategy.evaluate(bars)
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
    dq = data_ctx["quality"]
    if dq.get("bars_removed"):
        notes.append(f"{len(dq['invalid_runs'])} zero-volume run(s) ({dq['bars_removed']} bars) treated as missing.")
    if dq.get("flagged_short_bars"):
        notes.append(f"{dq['flagged_short_bars']} zero-volume bar(s) in short runs kept but flagged.")
    notes.append("Funding is an ASSUMED fixed rate, not measured data (OKX serves ~3 months of history).")

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
        "segment": data_ctx["segment"],
        "research_split": data_ctx["research_split"],
        "oos_unsealed_run": data_ctx["oos_unsealed_run"],
        "execution_model": {
            "engine_version": ENGINE_VERSION,
            "market_entry": "next bar open +/- adverse slippage; rejected across data gaps",
            "stop_target": "stop assumed first when stop and target are inside one candle; gap through stop fills at the open",
            "sizing": "cost-inclusive (stop loss + entry fee + exit fee + stop slippage)" if config.execution.cost_inclusive_sizing else "stop distance only (legacy)",
            "min_rr_check": "net reward/risk at the expected fill" if config.execution.enforce_net_rr_at_fill else "signal bar close only (legacy)",
            "limit_orders": f"fill only if price trades through the limit by {config.execution.limit_fill_penetration_bps} bps; "
                            "fill at the limit; fill-bar stop assumed, fill-bar target not credited; "
                            "cancelled on expiry, data gap or target reached while unfilled",
            "limit_entry_fee": config.execution.limit_entry_fee,
            "funding": {"mode": config.costs.funding_mode, "rate_per_8h": config.costs.funding_rate_per_8h,
                        "basis": "ASSUMED fixed rate — not measured data"},
            "not_modelled": ["margin / liquidation", "partial fills", "intra-bar price path", "tick rounding",
                             "historical funding rates"],
            "limit_orders_placed": result.limit_orders_placed, "limit_orders_filled": result.limit_orders_filled,
        },
        "data_quality_rules": dq,
        "conditions": {
            "inst_id": config.inst_id, "timeframe": tf.value, "start_ts": config.start_ts, "end_ts": config.end_ts,
            "initial_capital": config.initial_capital, "costs": config.costs.model_dump(),
            "risk": config.risk.model_dump(), "execution": config.execution.model_dump(),
            # test-window data (what results are compared on) vs. full input incl. strategy warm-up
            "data_fingerprint": data_fingerprint(bars_t),
            "input_fingerprint": data_fingerprint(bars),
            "warmup_bars": strategy.warmup_bars(),
        },
        "metrics": metrics,
        "trades": [t.model_dump() for t in result.trades],
        "rejections": [r.model_dump() for r in result.rejections],
        "equity": _equity_for_storage(result.equity),
        "inspection": (inspection.summarize(config.start_ts, config.end_ts, compact=True)
                       if inspection is not None else None),
        "data_quality": quality.to_dict(),
        "notes": notes,
        "disclaimer": DISCLAIMER,
    }
    repo.save(record, summary, payload)
    return {**record, "summary": summary, **payload}
