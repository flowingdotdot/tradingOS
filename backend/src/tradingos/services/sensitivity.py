"""Funding sensitivity: funding is an ASSUMPTION (OKX serves ~3 months of history), so results
are re-computed under alternative assumptions instead of trusting one number.

The strategy is evaluated once; only the engine is re-run per scenario. Nothing is stored.
The period goes through the same guarded loader as every other evaluation (OOS seal applies).
"""

from __future__ import annotations

from typing import Any

from tradingos.backtest.engine import run_backtest
from tradingos.backtest.metrics import compute_metrics
from tradingos.backtest.models import BacktestConfig
from tradingos.config import Settings
from tradingos.domain.instruments import KNOWN_INSTRUMENTS
from tradingos.domain.timeframes import Timeframe
from tradingos.services.backtest_service import BacktestInputError
from tradingos.services.data_access import PeriodNotAllowedError, load_strategy_bars
from tradingos.strategy.registry import build_strategy

# (label, mode, rate per 8h). Measured on 2026-08-10..2026-11-12: mean +0.0058 %, max +0.01 %,
# min -0.0047 %, negative 7.8 % of the time. A short RECEIVES funding when the rate is positive.
FUNDING_SCENARIOS: list[tuple[str, str, float]] = [
    ("no funding", "cost", 0.0),
    ("base: 0.01% always a cost (assumption used in runs)", "cost", 0.0001),
    ("stress: 0.03% always a cost", "cost", 0.0003),
    ("stress: 0.05% always a cost", "cost", 0.0005),
    ("signed +0.0058% (measured recent mean; shorts receive)", "signed", 0.000058),
    ("signed +0.01% (shorts receive)", "signed", 0.0001),
    ("signed -0.01% (shorts pay)", "signed", -0.0001),
]


def funding_sensitivity(settings: Settings, config: BacktestConfig, params: dict[str, Any]) -> list[dict[str, Any]]:
    tf = Timeframe(config.timeframe)
    strategy = build_strategy(config.strategy_id, params)
    try:
        bars, ctx = load_strategy_bars(settings, config.inst_id, tf, config.start_ts, config.end_ts,
                                       strategy.warmup_bars(), "funding sensitivity")
    except PeriodNotAllowedError as exc:
        raise BacktestInputError(str(exc)) from exc
    signals, _ = strategy.evaluate(bars)
    mask = (bars["ts"] >= ctx["trade_start_ts"]).to_numpy()
    signals.loc[~mask, "entry"] = 0
    signals.loc[~mask, ["exit_long", "exit_short"]] = False
    first = int(mask.argmax())
    bars_t, sig_t = bars.iloc[first:].reset_index(drop=True), signals.iloc[first:].reset_index(drop=True)
    out = []
    for label, mode, rate in FUNDING_SCENARIOS:
        cfg = config.model_copy(update={"costs": config.costs.model_copy(
            update={"funding_mode": mode, "funding_rate_per_8h": rate})})
        m = compute_metrics(run_backtest(bars_t, sig_t, cfg, KNOWN_INSTRUMENTS[config.inst_id]))
        out.append({"scenario": label, "funding_mode": mode, "rate_per_8h": rate, "trades": m["total_trades"],
                    "net_profit": m["net_profit"], "total_funding": m["total_funding"],
                    "expectancy_r": m["expectancy_r"], "max_drawdown_pct": m["max_drawdown_pct"]})
    return out
