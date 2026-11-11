"""Bar-by-bar backtest simulator.

Timeline for bar i (open time ts_i):
  1. At ts_i open: execute actions decided at the close of bar i-1
     (signal exit first, then entry). Market fills at open +/- adverse slippage.
  2. Funding: a position carried into bar i pays for each funding time in (ts_{i-1}, ts_i].
  3. Intra-bar: check stop / target against high/low. If both are touched inside
     the same candle the STOP is assumed first (path unknown -> conservative).
     If the open already gapped through the stop, fill at the (worse) open.
  4. At bar i close: read signals[i] -> schedule actions for bar i+1.
  5. Mark-to-market equity at close.

Signals on the last bar are never executed (there is no next open).
See docs/ARCHITECTURE.md §3.3.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from tradingos.backtest.models import BacktestConfig, Rejection, Trade
from tradingos.domain.instruments import InstrumentSpec
from tradingos.risk.engine import RiskEngine
from tradingos.strategy.base import has_limit_entries, validate_signals

FUNDING_INTERVAL_MS = 8 * 3_600_000  # OKX BTC-USDT-SWAP: 00:00, 08:00, 16:00 UTC


@dataclass
class _Position:
    side: int
    qty: float
    entry_price: float
    entry_ts: int
    entry_bar: int
    stop: float
    target: float | None
    risk_amount: float
    entry_reason: str
    capped: bool
    fees: float = 0.0
    funding: float = 0.0
    slippage_cost: float = 0.0
    worst: float = 0.0  # adverse excursion (price units, >= 0)
    best: float = 0.0   # favourable excursion
    ambiguous: bool = False


@dataclass
class BacktestResult:
    config: BacktestConfig
    trades: list[Trade]
    rejections: list[Rejection]
    equity: pd.DataFrame  # ts, equity, drawdown_pct, in_position
    bars: int
    signals_seen: int = 0
    notes: list[str] = field(default_factory=list)


def _funding_times_between(prev_ts: int, ts: int) -> int:
    """Number of funding timestamps f with prev_ts < f <= ts."""
    return int(ts // FUNDING_INTERVAL_MS - prev_ts // FUNDING_INTERVAL_MS)


def run_backtest(
    bars: pd.DataFrame,
    signals: pd.DataFrame,
    config: BacktestConfig,
    instrument: InstrumentSpec,
    entries_allowed: np.ndarray | None = None,
) -> BacktestResult:
    """entries_allowed[i] = False blocks NEW entries at bar i (strategy switched OFF) while open
    positions keep being managed by their stop/target/exit rules."""
    validate_signals(signals, bars)
    if has_limit_entries(signals):
        raise NotImplementedError("limit entries are not supported by the Phase-1 engine (planned for Phase 3)")
    n = len(bars)
    ts = bars["ts"].to_numpy(dtype="int64")
    o, h, l, c = (bars[k].to_numpy(dtype="float64") for k in ("open", "high", "low", "close"))
    s_entry = signals["entry"].to_numpy()
    s_stop = signals["stop"].to_numpy(dtype="float64")
    s_target = signals["target"].to_numpy(dtype="float64")
    s_exit_long = signals["exit_long"].to_numpy(dtype=bool)
    s_exit_short = signals["exit_short"].to_numpy(dtype=bool)
    s_reason = signals["reason"].to_numpy()

    costs = config.costs
    slip = costs.slippage_bps / 10_000
    risk = RiskEngine(config.risk, instrument)

    cash = config.initial_capital  # realized equity
    pos: _Position | None = None
    pending_entry: int | None = None  # bar index of the signal
    pending_exit = False
    trades: list[Trade] = []
    rejections: list[Rejection] = []
    equity = np.empty(n)
    in_pos = np.zeros(n, dtype=bool)
    signals_seen = 0

    def close_position(i: int, ref_price: float, reason: str, market: bool) -> None:
        nonlocal cash, pos
        assert pos is not None
        if market:
            fill = ref_price * (1 - slip * pos.side)  # adverse: long sells lower, short buys higher
            fee_rate = costs.taker_fee_rate
        else:  # resting take-profit limit at target
            fill = ref_price
            fee_rate = costs.maker_fee_rate if costs.target_fill_as_maker else costs.taker_fee_rate
        exit_fee = abs(fill) * pos.qty * fee_rate
        pos.fees += exit_fee
        pos.slippage_cost += abs(fill - ref_price) * pos.qty
        gross = (fill - pos.entry_price) * pos.side * pos.qty
        net = gross - pos.fees - pos.funding
        cash += gross - exit_fee  # entry fee and funding were already deducted when incurred
        risk.record_realized(net)
        trades.append(
            Trade(
                trade_id=len(trades) + 1,
                side=pos.side,
                entry_ts=pos.entry_ts,
                entry_price=pos.entry_price,
                exit_ts=int(ts[i]),
                exit_price=fill,
                qty=pos.qty,
                contracts=instrument.contracts(pos.qty),
                stop=pos.stop,
                target=pos.target,
                risk_amount=pos.risk_amount,
                gross_pnl=gross,
                fees=pos.fees,
                funding=pos.funding,
                slippage_cost=pos.slippage_cost,
                net_pnl=net,
                r_multiple=net / pos.risk_amount if pos.risk_amount > 0 else 0.0,
                exit_reason=reason,
                entry_reason=pos.entry_reason,
                bars_held=i - pos.entry_bar + 1,
                ambiguous_bar=pos.ambiguous,
                mae=pos.worst,
                mfe=pos.best,
                capped_by_leverage=pos.capped,
            )
        )
        pos = None

    for i in range(n):
        risk.on_time(int(ts[i]), cash)

        # 2. funding for a position carried in from the previous bar
        if pos is not None and i > 0:
            k = _funding_times_between(int(ts[i - 1]), int(ts[i]))
            if k:
                charge = k * costs.funding_rate_per_8h * pos.qty * o[i]
                pos.funding += charge
                cash -= charge

        # 1a. scheduled signal exit at this open
        if pending_exit and pos is not None:
            close_position(i, o[i], "signal_exit", market=True)
        pending_exit = False

        # 1b. scheduled entry at this open
        if pending_entry is not None and entries_allowed is not None and not entries_allowed[i]:
            rejections.append(Rejection(ts=int(ts[i]), side=int(s_entry[pending_entry]), reason="entries_disabled"))
            pending_entry = None
        if pending_entry is not None:
            j = pending_entry
            side = int(s_entry[j])
            fill = o[i] * (1 + slip * side)
            decision = risk.authorize_entry(
                side=side,
                entry_price=fill,
                stop_price=s_stop[j],
                equity=cash,
                open_positions=0 if pos is None else 1,
            )
            if decision.approved:
                fee = fill * decision.qty * costs.taker_fee_rate
                cash -= fee
                tgt = float(s_target[j]) if np.isfinite(s_target[j]) else None
                if tgt is not None and (tgt - fill) * side <= 0:
                    tgt = None  # target already passed at fill -> no target, rely on stop/exit
                pos = _Position(
                    side=side,
                    qty=decision.qty,
                    entry_price=fill,
                    entry_ts=int(ts[i]),
                    entry_bar=i,
                    stop=float(s_stop[j]),
                    target=tgt,
                    risk_amount=decision.risk_amount,
                    entry_reason=str(s_reason[j]),
                    capped=decision.capped_by_leverage,
                    fees=fee,
                    slippage_cost=abs(fill - o[i]) * decision.qty,
                )
            else:
                rejections.append(Rejection(ts=int(ts[i]), side=side, reason=decision.reason))
            pending_entry = None

        # 3. intra-bar stop / target
        if pos is not None:
            side = pos.side
            adverse = (pos.entry_price - l[i]) if side == 1 else (h[i] - pos.entry_price)
            favour = (h[i] - pos.entry_price) if side == 1 else (pos.entry_price - l[i])
            pos.worst = max(pos.worst, adverse)
            pos.best = max(pos.best, favour)
            stop_hit = l[i] <= pos.stop if side == 1 else h[i] >= pos.stop
            target_hit = pos.target is not None and (h[i] >= pos.target if side == 1 else l[i] <= pos.target)
            if stop_hit:
                pos.ambiguous = bool(target_hit)
                gapped = (o[i] <= pos.stop) if side == 1 else (o[i] >= pos.stop)
                close_position(i, o[i] if gapped else pos.stop, "stop", market=True)
            elif target_hit:
                close_position(i, pos.target, "target", market=False)  # type: ignore[arg-type]

        # 4. signals at this bar's close -> actions at next open
        if i < n - 1:
            if pos is not None:
                if (pos.side == 1 and s_exit_long[i]) or (pos.side == -1 and s_exit_short[i]):
                    pending_exit = True
            if s_entry[i] != 0:
                signals_seen += 1
                if pos is None or (pending_exit and s_entry[i] != pos.side):
                    pending_entry = i
                else:
                    rejections.append(Rejection(ts=int(ts[i]), side=int(s_entry[i]), reason="already_in_position"))

        # 5. mark to market
        unreal = 0.0
        if pos is not None:
            unreal = (c[i] - pos.entry_price) * pos.side * pos.qty
            in_pos[i] = True
        equity[i] = cash + unreal

    notes: list[str] = []
    if pos is not None:
        close_position(n - 1, c[n - 1], "end_of_data", market=True)
        equity[n - 1] = cash
        notes.append("Open position force-closed at the last close (end_of_data).")

    peak = np.maximum.accumulate(equity) if n else equity
    dd = np.where(peak > 0, equity / peak - 1.0, 0.0) * 100 if n else equity
    eq_df = pd.DataFrame({"ts": ts, "equity": equity, "drawdown_pct": dd, "in_position": in_pos})
    return BacktestResult(
        config=config,
        trades=trades,
        rejections=rejections,
        equity=eq_df,
        bars=n,
        signals_seen=signals_seen,
        notes=notes,
    )
