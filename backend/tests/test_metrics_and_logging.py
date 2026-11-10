from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd
import pytest

from tradingos.backtest.engine import BacktestResult
from tradingos.backtest.metrics import compute_metrics
from tradingos.backtest.models import BacktestConfig, Trade
from tradingos.logging_setup import JsonFormatter, RedactingFilter

T0 = 1_735_689_600_000
DAY = 86_400_000


def trade(i: int, net: float, exit_ts: int) -> Trade:
    return Trade(trade_id=i, side=1, entry_ts=exit_ts - 1, entry_price=100, exit_ts=exit_ts, exit_price=100,
                 qty=1, contracts=100, stop=90, target=None, risk_amount=10, gross_pnl=net, fees=0, funding=0,
                 slippage_cost=0, net_pnl=net, r_multiple=net / 10, exit_reason="stop", entry_reason="",
                 bars_held=1, ambiguous_bar=False, mae=0, mfe=0, capped_by_leverage=False)


def test_metrics_on_known_trade_list():
    pnls = [20, -10, -10, -10, 30, -10]
    trades = [trade(i + 1, p, T0 + i * DAY) for i, p in enumerate(pnls)]
    equity_vals = 1_000 + np.cumsum(pnls)
    eq = pd.DataFrame({"ts": [T0 + i * DAY for i in range(6)], "equity": equity_vals,
                       "in_position": [True, False, True, False, False, False]})
    eq["drawdown_pct"] = (eq["equity"] / eq["equity"].cummax() - 1) * 100
    cfg = BacktestConfig(strategy_id="t", strategy_version="t", start_ts=T0, end_ts=T0 + 7 * DAY,
                         initial_capital=1_000)
    m = compute_metrics(BacktestResult(config=cfg, trades=trades, rejections=[], equity=eq, bars=6))

    assert m["total_trades"] == 6 and m["wins"] == 2 and m["losses"] == 4
    assert m["win_rate_pct"] == pytest.approx(100 / 3)
    assert m["avg_win"] == pytest.approx(25) and m["avg_loss"] == pytest.approx(10)
    assert m["payoff_ratio"] == pytest.approx(2.5)
    assert m["expectancy"] == pytest.approx(10 / 6)
    assert m["profit_factor"] == pytest.approx(50 / 40)
    assert m["max_consecutive_losses"] == 3
    assert m["net_profit"] == pytest.approx(10)
    # peak 1020 -> trough 990: 30 abs, 2.941%
    assert m["max_drawdown_abs"] == pytest.approx(30)
    assert m["max_drawdown_pct"] == pytest.approx(30 / 1020 * 100)
    assert m["exposure_pct"] == pytest.approx(100 / 3)
    assert sum(r["trades"] for r in m["monthly"]) == 6


def test_profit_factor_none_when_no_losses():
    cfg = BacktestConfig(strategy_id="t", strategy_version="t", start_ts=T0, end_ts=T0 + DAY, initial_capital=100)
    eq = pd.DataFrame({"ts": [T0], "equity": [110.0], "drawdown_pct": [0.0], "in_position": [False]})
    m = compute_metrics(BacktestResult(config=cfg, trades=[trade(1, 10, T0)], rejections=[], equity=eq, bars=1))
    assert m["profit_factor"] is None  # undefined, not "infinite"


def test_log_redaction_masks_secrets():
    rec = logging.LogRecord("x", logging.INFO, "f", 1, "login api_key=ABC123 secret: 'zzz' ok", None, None)
    rec.passphrase = "hunter2"
    rec.headers = {"OK-ACCESS-SIGN": "sig", "Accept": "json"}
    RedactingFilter().filter(rec)
    out = JsonFormatter().format(rec)
    for leaked in ("ABC123", "zzz", "hunter2", '"sig"'):
        assert leaked not in out
    payload = json.loads(out)
    assert payload["headers"]["Accept"] == "json"
