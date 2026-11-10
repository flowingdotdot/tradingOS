"""Performance metrics. All PnL figures are NET of fees, slippage and funding.

These describe a hypothetical simulation; they are not predictions.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from tradingos.backtest.engine import BacktestResult


def _max_streak(flags: list[bool]) -> int:
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


def _clean(x: float | None) -> float | None:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return None
    return float(x)


def compute_metrics(result: BacktestResult) -> dict[str, Any]:
    cfg = result.config
    trades = result.trades
    eq = result.equity
    initial = cfg.initial_capital
    final = float(eq["equity"].iloc[-1]) if len(eq) else initial

    net = np.array([t.net_pnl for t in trades], dtype=float)
    wins, losses = net[net > 0], net[net <= 0]
    gross_win, gross_loss = float(wins.sum()), float(-losses.sum())
    avg_win = float(wins.mean()) if len(wins) else None
    avg_loss = float(-losses.mean()) if len(losses) else None
    r = np.array([t.r_multiple for t in trades], dtype=float)
    notionals = np.array([t.qty * t.entry_price for t in trades], dtype=float)

    exit_reasons: dict[str, int] = {}
    for t in trades:
        exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1
    rejection_reasons: dict[str, int] = {}
    for rj in result.rejections:
        rejection_reasons[rj.reason] = rejection_reasons.get(rj.reason, 0) + 1

    return {
        "initial_capital": initial,
        "final_equity": final,
        "net_profit": final - initial,
        "return_pct": (final / initial - 1) * 100,
        "total_trades": len(trades),
        "long_trades": sum(1 for t in trades if t.side == 1),
        "short_trades": sum(1 for t in trades if t.side == -1),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": _clean(len(wins) / len(trades) * 100) if trades else None,
        "avg_win": _clean(avg_win),
        "avg_loss": _clean(avg_loss),
        "payoff_ratio": _clean(avg_win / avg_loss) if avg_win and avg_loss else None,
        "expectancy": _clean(float(net.mean())) if trades else None,
        "expectancy_r": _clean(float(r.mean())) if trades else None,
        "profit_factor": _clean(gross_win / gross_loss) if gross_loss > 0 else None,
        "max_drawdown_pct": float(-eq["drawdown_pct"].min()) if len(eq) else 0.0,
        "max_drawdown_abs": float((eq["equity"].cummax() - eq["equity"]).max()) if len(eq) else 0.0,
        "max_consecutive_losses": _max_streak([x <= 0 for x in net]),
        "max_consecutive_wins": _max_streak([x > 0 for x in net]),
        "total_fees": float(sum(t.fees for t in trades)),
        "total_funding": float(sum(t.funding for t in trades)),
        "total_slippage": float(sum(t.slippage_cost for t in trades)),
        "exposure_pct": float(eq["in_position"].mean() * 100) if len(eq) else 0.0,
        "avg_notional": _clean(float(notionals.mean())) if trades else None,
        "max_notional": _clean(float(notionals.max())) if trades else None,
        "avg_bars_held": _clean(float(np.mean([t.bars_held for t in trades]))) if trades else None,
        "ambiguous_exits": sum(1 for t in trades if t.ambiguous_bar),
        "leverage_capped_trades": sum(1 for t in trades if t.capped_by_leverage),
        "exit_reasons": exit_reasons,
        "signals_seen": result.signals_seen,
        "rejections": len(result.rejections),
        "rejection_reasons": rejection_reasons,
        "monthly": monthly_performance(result),
    }


def monthly_performance(result: BacktestResult) -> list[dict[str, Any]]:
    eq = result.equity
    if eq.empty:
        return []
    # naive UTC timestamps (ts is epoch-ms UTC) so month bucketing is exactly UTC months
    s = pd.Series(eq["equity"].to_numpy(), index=pd.to_datetime(eq["ts"], unit="ms"))
    month_end = s.groupby(s.index.to_period("M")).last()
    prev = month_end.shift(1)
    prev.iloc[0] = result.config.initial_capital
    trades_by_month: dict[str, list[float]] = {}
    for t in result.trades:
        key = pd.Timestamp(t.exit_ts, unit="ms", tz="UTC").strftime("%Y-%m")
        trades_by_month.setdefault(key, []).append(t.net_pnl)
    rows = []
    for period, end_val in month_end.items():
        key = str(period)
        pnl = trades_by_month.get(key, [])
        rows.append(
            {
                "month": key,
                "return_pct": float((end_val / prev[period] - 1) * 100),
                "equity_end": float(end_val),
                "trades": len(pnl),
                "net_pnl": float(sum(pnl)),
            }
        )
    return rows
