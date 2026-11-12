"""Bar-by-bar backtest simulator (engine 0.3.0). Full rules: docs/BACKTEST_EXECUTION_MODEL.md.

Timeline for bar i (open time ts_i):
  1. Funding for a position carried in from the previous bar.
  2. At ts_i open: actions decided at the close of bar i-1 — signal exit, then MARKET entry
     (open +/- adverse slippage) after the pre-trade checks.
  3. Resting LIMIT order (placed at an earlier close): cancel / fill using this bar's range.
  4. Intra-bar stop / target. If both are touched inside one candle the STOP is assumed first
     (path unknown -> conservative). A gap through the stop fills at the worse open.
  5. At bar i close: read signals[i] -> schedule a market entry for bar i+1 or place a limit order.
  6. Mark-to-market equity at close.

Limit orders (conservative, no look-ahead inside a bar):
  * active from the bar after the signal for `limit_expiry_bars` bars;
  * FILL only if price trades THROUGH the limit by `limit_fill_penetration_bps` — a mere touch
    of the high/low is not a fill; the fill price is the limit (never better);
  * cancelled when: expired, a data gap appears, entries are switched off, or the target is
    reached while unfilled (the idea is void). Once filled an order cannot be cancelled: the
    position is managed by its stop/target;
  * in the FILL bar the order of events is unknown: a stop that is also inside the bar's range
    is assumed hit (loss), a target inside the range is NOT credited. Both are flagged.

Signals on the last bar are never executed (there is no next bar).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from tradingos.backtest.models import BacktestConfig, Rejection, Trade
from tradingos.domain.instruments import InstrumentSpec
from tradingos.domain.timeframes import Timeframe
from tradingos.risk.engine import ExecutionCosts, RiskEngine, net_reward_risk
from tradingos.strategy.base import validate_signals

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
    risk_budget: float
    entry_reason: str
    capped: bool
    entry_type: str = "market"
    signal_ts: int | None = None
    planned_net_rr: float | None = None
    fees: float = 0.0
    funding: float = 0.0
    slippage_cost: float = 0.0
    worst: float = 0.0  # adverse excursion (price units, >= 0)
    best: float = 0.0   # favourable excursion
    ambiguous: bool = False
    fill_bar_ambiguous: bool = False


@dataclass
class _LimitOrder:
    signal_bar: int
    side: int
    price: float
    stop: float
    target: float | None
    expiry_bars: int
    reason: str
    net_rr: float | None


@dataclass
class BacktestResult:
    config: BacktestConfig
    trades: list[Trade]
    rejections: list[Rejection]
    equity: pd.DataFrame  # ts, equity, drawdown_pct, in_position
    bars: int
    signals_seen: int = 0
    notes: list[str] = field(default_factory=list)
    limit_orders_placed: int = 0
    limit_orders_filled: int = 0


def _funding_times_between(prev_ts: int, ts: int) -> int:
    """Number of funding timestamps f with prev_ts < f <= ts."""
    return int(ts // FUNDING_INTERVAL_MS - prev_ts // FUNDING_INTERVAL_MS)


def _col(signals: pd.DataFrame, name: str, n: int) -> np.ndarray:
    return signals[name].to_numpy(dtype="float64") if name in signals.columns else np.full(n, np.nan)


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
    n = len(bars)
    ts = bars["ts"].to_numpy(dtype="int64")
    o, h, l, c = (bars[k].to_numpy(dtype="float64") for k in ("open", "high", "low", "close"))
    s_entry = signals["entry"].to_numpy()
    s_stop = signals["stop"].to_numpy(dtype="float64")
    s_target = signals["target"].to_numpy(dtype="float64")
    s_exit_long = signals["exit_long"].to_numpy(dtype=bool)
    s_exit_short = signals["exit_short"].to_numpy(dtype=bool)
    s_reason = signals["reason"].to_numpy()
    s_limit = _col(signals, "limit_price", n)
    s_expiry = _col(signals, "limit_expiry_bars", n)
    s_min_rr = _col(signals, "min_rr", n)

    costs, ex = config.costs, config.execution
    bar_ms = Timeframe(config.timeframe).ms
    slip = costs.slippage_bps / 10_000
    pen = ex.limit_fill_penetration_bps / 10_000
    target_fee = costs.maker_fee_rate if costs.target_fill_as_maker else costs.taker_fee_rate
    limit_fee = costs.maker_fee_rate if ex.limit_entry_fee == "maker" else costs.taker_fee_rate
    risk = RiskEngine(config.risk, instrument)

    def expected_costs(entry_fee: float) -> ExecutionCosts:
        return ExecutionCosts(entry_fee_rate=entry_fee, exit_fee_rate=costs.taker_fee_rate,
                              exit_slippage=slip, target_fee_rate=target_fee)

    cash = config.initial_capital  # realized equity
    pos: _Position | None = None
    pending_entry: int | None = None  # bar index of the signal (market entry at the next open)
    pending_exit = False
    order: _LimitOrder | None = None
    trades: list[Trade] = []
    rejections: list[Rejection] = []
    equity = np.empty(n)
    in_pos = np.zeros(n, dtype=bool)
    signals_seen = placed = filled = 0

    def reject(i: int, side: int, reason: str, detail: str = "") -> None:
        rejections.append(Rejection(ts=int(ts[i]), side=side, reason=reason, detail=detail))

    def close_position(i: int, ref_price: float, reason: str, market: bool) -> None:
        nonlocal cash, pos
        assert pos is not None
        if market:
            fill = ref_price * (1 - slip * pos.side)  # adverse: long sells lower, short buys higher
            fee_rate = costs.taker_fee_rate
        else:  # resting take-profit limit at target
            fill = ref_price
            fee_rate = target_fee
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
                r_multiple=net / pos.risk_budget if pos.risk_budget > 0 else 0.0,
                exit_reason=reason,
                entry_reason=pos.entry_reason,
                bars_held=i - pos.entry_bar + 1,
                ambiguous_bar=pos.ambiguous,
                mae=pos.worst,
                mfe=pos.best,
                capped_by_leverage=pos.capped,
                entry_type=pos.entry_type,
                signal_ts=pos.signal_ts,
                risk_budget=pos.risk_budget,
                planned_net_rr=pos.planned_net_rr,
                fill_bar_ambiguous=pos.fill_bar_ambiguous,
            )
        )
        pos = None

    def pre_trade(side: int, ref: float, fill: float, stop: float, tgt: float | None, min_rr: float,
                  entry_fee: float) -> tuple[str | None, str, float | None]:
        """Checks that need the expected fill price. Returns (reject reason, detail, net RR)."""
        if not (ref - stop) * side > 0:
            # the reference price is already at/through the stop (also a stop inside the slippage band)
            return "stop_on_wrong_side", "", None
        if tgt is not None and (tgt - fill) * side <= 0:
            return "target_passed_at_fill", "", None
        rr = net_reward_risk(side, fill, stop, tgt, expected_costs(entry_fee)) if tgt is not None else None
        if ex.enforce_net_rr_at_fill and np.isfinite(min_rr) and tgt is not None and (rr is None or rr < min_rr):
            return "net_rr_below_min", f"net RR {rr:.2f} < {min_rr:g} at expected fill {fill:.1f}", rr
        return None, "", rr

    def open_position(i: int, j: int, side: int, ref: float, fill: float, stop: float, tgt: float | None,
                      rr: float | None, entry_fee: float, entry_type: str) -> bool:
        nonlocal cash, pos
        decision = risk.authorize_entry(
            side=side, entry_price=fill, stop_price=stop, equity=cash, open_positions=0 if pos is None else 1,
            costs=expected_costs(entry_fee) if ex.cost_inclusive_sizing else None,
        )
        if not decision.approved:
            reject(i, side, decision.reason)
            return False
        fee = fill * decision.qty * entry_fee
        cash -= fee
        pos = _Position(
            side=side, qty=decision.qty, entry_price=fill, entry_ts=int(ts[i]), entry_bar=i, stop=stop, target=tgt,
            risk_amount=decision.risk_amount, risk_budget=decision.planned_loss, entry_reason=str(s_reason[j]),
            capped=decision.capped_by_leverage, entry_type=entry_type, signal_ts=int(ts[j]), planned_net_rr=rr,
            fees=fee, slippage_cost=abs(fill - ref) * decision.qty,
        )
        return True

    for i in range(n):
        risk.on_time(int(ts[i]), cash)
        gap = i > 0 and ts[i] - ts[i - 1] != bar_ms
        limit_filled_now = False

        # 1. funding for a position carried in from the previous bar
        if pos is not None and i > 0:
            k = _funding_times_between(int(ts[i - 1]), int(ts[i]))
            if k:
                rate = costs.funding_rate_per_8h
                per_event = abs(rate) if costs.funding_mode == "cost" else rate * pos.side
                charge = k * per_event * pos.qty * o[i]     # signed mode: negative charge = received
                pos.funding += charge
                cash -= charge

        # 2a. scheduled signal exit at this open (protective: executed even after a data gap)
        if pending_exit and pos is not None:
            close_position(i, o[i], "signal_exit", market=True)
        pending_exit = False

        # 2b. scheduled MARKET entry at this open
        if pending_entry is not None:
            j = pending_entry
            pending_entry = None
            side = int(s_entry[j])
            fill = o[i] * (1 + slip * side)
            tgt = float(s_target[j]) if np.isfinite(s_target[j]) else None
            if entries_allowed is not None and not entries_allowed[i]:
                reject(i, side, "entries_disabled")
            elif gap:
                # bars are missing between the signal bar and this bar: never execute a stale signal
                reject(i, side, "stale_signal_data_gap")
            else:
                why, detail, rr = pre_trade(side, o[i], fill, float(s_stop[j]), tgt, s_min_rr[j], costs.taker_fee_rate)
                if why:
                    reject(i, side, why, detail)
                else:
                    open_position(i, j, side, o[i], fill, float(s_stop[j]), tgt, rr, costs.taker_fee_rate, "market")

        # 3. resting LIMIT order
        if order is not None and pos is None:
            od = order
            if entries_allowed is not None and not entries_allowed[i]:
                reject(i, od.side, "limit_cancelled_entries_disabled")
                order = None
            elif gap:
                reject(i, od.side, "limit_cancelled_data_gap")
                order = None
            elif i - od.signal_bar > od.expiry_bars:
                reject(i, od.side, "limit_expired", f"unfilled after {od.expiry_bars} bars")
                order = None
            else:
                # sell limit above the market fills when price trades UP through it; buy limit: down
                through = h[i] >= od.price * (1 + pen) if od.side == -1 else l[i] <= od.price * (1 - pen)
                tgt_hit = od.target is not None and (l[i] <= od.target if od.side == -1 else h[i] >= od.target)
                if through:
                    order = None
                    if open_position(i, od.signal_bar, od.side, od.price, od.price, od.stop, od.target, od.net_rr,
                                     limit_fee, "limit"):
                        filled += 1
                        limit_filled_now = True
                        assert pos is not None
                        # target inside the fill bar: order of events unknown -> not credited, flagged
                        pos.fill_bar_ambiguous = bool(tgt_hit)
                elif tgt_hit:
                    reject(i, od.side, "limit_cancelled_target_reached", "target reached before the limit filled")
                    order = None
        elif order is not None:
            order = None  # defensive: an order never coexists with a position

        # 4. intra-bar stop / target
        if pos is not None:
            side = pos.side
            adverse = (pos.entry_price - l[i]) if side == 1 else (h[i] - pos.entry_price)
            pos.worst = max(pos.worst, adverse)
            stop_hit = l[i] <= pos.stop if side == 1 else h[i] >= pos.stop
            if limit_filled_now:
                # fill bar of a limit order: only the adverse outcome is taken from this bar
                if stop_hit:
                    pos.fill_bar_ambiguous = True
                    gapped = (o[i] <= pos.stop) if side == 1 else (o[i] >= pos.stop)
                    close_position(i, o[i] if gapped else pos.stop, "stop", market=True)
            else:
                favour = (h[i] - pos.entry_price) if side == 1 else (pos.entry_price - l[i])
                pos.best = max(pos.best, favour)
                target_hit = pos.target is not None and (h[i] >= pos.target if side == 1 else l[i] <= pos.target)
                if stop_hit:
                    pos.ambiguous = bool(target_hit)
                    gapped = (o[i] <= pos.stop) if side == 1 else (o[i] >= pos.stop)
                    close_position(i, o[i] if gapped else pos.stop, "stop", market=True)
                elif target_hit:
                    close_position(i, pos.target, "target", market=False)  # type: ignore[arg-type]

        # 5. signals at this bar's close -> actions from the next bar
        if i < n - 1:
            if pos is not None and ((pos.side == 1 and s_exit_long[i]) or (pos.side == -1 and s_exit_short[i])):
                pending_exit = True
            if s_entry[i] != 0:
                signals_seen += 1
                side = int(s_entry[i])
                if not (pos is None or (pending_exit and side != pos.side)):
                    reject(i, side, "already_in_position")
                elif order is not None:
                    reject(i, side, "order_pending")
                elif np.isfinite(s_limit[i]):
                    lp, stop = float(s_limit[i]), float(s_stop[i])
                    tgt = float(s_target[i]) if np.isfinite(s_target[i]) else None
                    why, detail, rr = pre_trade(side, lp, lp, stop, tgt, s_min_rr[i], limit_fee)
                    if why:
                        reject(i, side, why, detail)
                    else:
                        expiry = int(s_expiry[i]) if np.isfinite(s_expiry[i]) else 1
                        order = _LimitOrder(i, side, lp, stop, tgt, max(1, expiry), str(s_reason[i]), rr)
                        placed += 1
                else:
                    pending_entry = i

        # 6. mark to market
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
    if order is not None:
        notes.append("A limit order was still resting when the data ended (never filled).")

    peak = np.maximum.accumulate(equity) if n else equity
    dd = np.where(peak > 0, equity / peak - 1.0, 0.0) * 100 if n else equity
    eq_df = pd.DataFrame({"ts": ts, "equity": equity, "drawdown_pct": dd, "in_position": in_pos})
    return BacktestResult(
        config=config, trades=trades, rejections=rejections, equity=eq_df, bars=n, signals_seen=signals_seen,
        notes=notes, limit_orders_placed=placed, limit_orders_filled=filled,
    )
