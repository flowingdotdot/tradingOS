"""Chart Workspace data: one WINDOW of candles + strategy overlay + trades.

The browser never receives the whole history: it asks for the window it is looking at. Every
request goes through `load_strategy_bars`, i.e. the common data-access policy (OOS seal, no
sealed bar even as warm-up, zero-volume rule). A window that reaches into the sealed OOS window
is CLIPPED to the allowed segment that contains its centre; a window entirely inside it is refused.

Candles of every timeframe are built from the 5m base bars with the same `resample` the
strategies use, so the chart shows exactly the higher-timeframe bars the strategy saw.
The overlay is the plugin's own `research_view()` — nothing is recomputed for display.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from tradingos.config import Settings
from tradingos.domain.instruments import KNOWN_INSTRUMENTS
from tradingos.domain.timeframes import Timeframe
from tradingos.features.mtf import resample
from tradingos.marketdata.store import CandleStore
from tradingos.services.backtest_service import BacktestInputError
from tradingos.services.data_access import PeriodNotAllowedError, load_strategy_bars, research_split
from tradingos.strategy.configs import ConfigIntegrityError, ConfigStore
from tradingos.strategy.instances import check_data_requirements
from tradingos.strategy.registry import STRATEGY_CLASSES

BASE_TF = Timeframe.M5
MAX_WINDOW_BARS = 2_500          # of the DISPLAY timeframe
DAY_MS = 86_400_000


def allowed_segments(settings: Settings, inst_id: str) -> list[dict[str, Any]]:
    """Time segments a chart may show: stored data minus the sealed OOS window."""
    store = CandleStore(settings.market_data_dir)
    df = store.load(inst_id, BASE_TF)
    if df.empty:
        return []
    first, last = int(df["ts"].iloc[0]), int(df["ts"].iloc[-1]) + BASE_TF.ms
    split = research_split(settings)
    if split is None or not split.oos_sealed:
        return [{"name": "ALL", "start_ts": first, "end_ts": last}]
    segs = []
    if first < split.oos_start:
        segs.append({"name": "IS", "start_ts": first, "end_ts": min(last, split.oos_start)})
    if last > split.oos_end:
        segs.append({"name": "OBSERVED", "start_ts": max(first, split.oos_end), "end_ts": last})
    return segs


def _clip(segs: list[dict[str, Any]], start_ts: int, end_ts: int) -> tuple[int, int, str, bool]:
    centre = (start_ts + end_ts) // 2
    for s in segs:
        if s["start_ts"] <= centre < s["end_ts"]:
            a, b = max(start_ts, s["start_ts"]), min(end_ts, s["end_ts"])
            return a, b, s["name"], (a, b) != (start_ts, end_ts)
    # centre outside every allowed segment: is any part of the window allowed?
    for s in segs:
        a, b = max(start_ts, s["start_ts"]), min(end_ts, s["end_ts"])
        if b > a:
            return a, b, s["name"], True
    raise BacktestInputError(
        "the requested chart window lies in the SEALED out-of-sample period or outside the stored data; "
        "allowed: " + ", ".join(f"{s['name']} {pd.Timestamp(s['start_ts'], unit='ms'):%Y-%m-%d}"
                                f"..{pd.Timestamp(s['end_ts'], unit='ms'):%Y-%m-%d}" for s in segs)
    )


def chart_window(settings: Settings, configs: ConfigStore, inst_id: str, timeframe: str, start_ts: int,
                 end_ts: int, strategy_id: str | None = None, strategy_version: str | None = None) -> dict[str, Any]:
    tf = Timeframe(timeframe)
    if inst_id not in KNOWN_INSTRUMENTS:
        raise BacktestInputError(f"unknown instrument {inst_id}")
    if tf.ms % BASE_TF.ms:
        raise BacktestInputError(f"chart timeframe {tf.value} cannot be built from {BASE_TF.value} bars")
    if end_ts <= start_ts:
        raise BacktestInputError("end_ts must be after start_ts")
    start_ts -= start_ts % tf.ms
    end_ts = min(end_ts, start_ts + MAX_WINDOW_BARS * tf.ms)
    segs = allowed_segments(settings, inst_id)
    if not segs:
        raise BacktestInputError(f"no stored {BASE_TF.value} candles for {inst_id} — collect data first")
    start_ts, end_ts, segment, clipped = _clip(segs, start_ts, end_ts)

    strategy = cfg = None
    warmup = 0
    if strategy_id:
        if strategy_id not in STRATEGY_CLASSES:
            raise BacktestInputError(f"unknown strategy {strategy_id!r}")
        errors = check_data_requirements(strategy_id, inst_id, BASE_TF.value)
        if errors:
            raise BacktestInputError("chart overlay needs a strategy that runs on 5m bars: " + "; ".join(errors))
        try:
            cfg = configs.get(strategy_id, strategy_version or "")
        except ConfigIntegrityError as exc:
            raise BacktestInputError(str(exc)) from exc
        strategy = STRATEGY_CLASSES[strategy_id](cfg.params)
        warmup = strategy.warmup_bars()
    try:
        bars, ctx = load_strategy_bars(settings, inst_id, BASE_TF, start_ts, end_ts, warmup, "chart")
    except PeriodNotAllowedError as exc:      # cannot happen after clipping; kept as a second line of defence
        raise BacktestInputError(str(exc)) from exc
    if bars.empty:
        raise BacktestInputError("no candles in the requested window")

    shown = bars if tf == BASE_TF else resample(bars, BASE_TF, tf)
    shown = shown[(shown["ts"] >= start_ts) & (shown["ts"] < end_ts)]
    out: dict[str, Any] = {
        "inst_id": inst_id, "timeframe": tf.value, "start_ts": start_ts, "end_ts": end_ts,
        "segment": segment, "clipped_to_allowed_segment": clipped, "allowed_segments": segs,
        "max_window_bars": MAX_WINDOW_BARS,
        "candles": shown[["ts", "open", "high", "low", "close", "volume"]].to_dict("list"),
        "data_quality_rules": ctx["quality"],
        "signals_valid_from_ts": ctx["trade_start_ts"],
        "strategy": None, "view": None,
    }
    if strategy is not None and cfg is not None:
        view = strategy.research_view(bars, BASE_TF, start_ts, end_ts).to_dict()
        # send only what the displayed timeframe needs (items without a timeframe apply to all)
        view["items"] = [it for it in view["items"] if it["tf"] in (None, tf.value)
                         and (not it.get("max_tf") or tf.ms <= Timeframe(it["max_tf"]).ms)]
        view["lines"] = [ln for ln in view["lines"] if ln["tf"] == tf.value]
        view["bar_colors"] = [b for b in view["bar_colors"] if b["tf"] == tf.value]
        view["timeframes"] = {}                      # candles are sent once, in `candles`
        view["default_tf"] = tf.value
        out["view"] = view
        out["strategy"] = {
            "strategy_id": strategy_id, "name": strategy.definition.name, "strategy_version": cfg.version,
            "definition_version": strategy.definition.definition_version, "params_hash": cfg.params_hash,
            "config_hash": cfg.config_hash, "warmup_bars": warmup,
            "overlay_timeframes": sorted({it["tf"] for it in view["items"] if it["tf"]}),
        }
    return out


def run_trades(run: dict[str, Any]) -> dict[str, Any]:
    """Trades of a stored run for chart navigation (no equity curve / inspection payload)."""
    keys = ("trade_id", "side", "entry_ts", "entry_price", "exit_ts", "exit_price", "qty", "stop", "target",
            "net_pnl", "r_multiple", "exit_reason", "entry_reason", "bars_held", "fees", "funding", "slippage_cost",
            "entry_type", "signal_ts", "planned_net_rr", "risk_budget", "fill_bar_ambiguous", "ambiguous_bar")
    return {
        "run_id": run["run_id"], "strategy_id": run["strategy_id"], "strategy_version": run["strategy_version"],
        "timeframe": run["timeframe"], "segment": run.get("segment"), "engine_version": run["engine_version"],
        "start_ts": run["start_ts"], "end_ts": run["end_ts"], "params_hash": run["params_hash"],
        "trades": [{k: t.get(k) for k in keys} for t in run["trades"]],
    }
