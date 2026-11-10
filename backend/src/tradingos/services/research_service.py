"""Strategy Lab research use case: compute features + setups for charting.

Every exported item carries both `ts` (occurrence, where it is drawn) and `known_ts` (when the
algorithm could first know it), so the UI can show "what was knowable at time T".
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from tradingos.config import Settings
from tradingos.domain.timeframes import Timeframe
from tradingos.marketdata.store import CandleStore, data_fingerprint
from tradingos.services.backtest_service import BacktestInputError, code_revision
from tradingos.strategy.builtin.ict_ote import Analysis, TfFeatures
from tradingos.strategy.registry import build_strategy, get_version

MAX_RESEARCH_DAYS = 60
DAY_MS = 86_400_000


def _candles(bars: pd.DataFrame) -> dict[str, list]:
    return bars[["ts", "open", "high", "low", "close"]].to_dict("list")


def _lifecycle_ts(ts: np.ndarray, idx: int, tf: Timeframe) -> tuple[int | None, int | None]:
    """(occurrence ts, known ts) for a bar-index lifecycle event; (None, None) if it did not happen."""
    if idx is None or idx < 0:
        return None, None
    return int(ts[idx]), int(ts[idx] + tf.ms)


def _tf_payload(f: TfFeatures, start: int, end: int) -> dict[str, Any]:
    ts = f.bars["ts"].to_numpy(dtype="int64")
    in_range = lambda df: df[(df["ts"] >= start) & (df["ts"] < end)] if not df.empty else df

    swings = [
        {"kind": r.kind, "ts": int(r.ts), "price": float(r.price), "known_ts": int(r.known_ts),
         "lag_bars": int(r.known_idx - r.idx)}
        for r in in_range(f.swings).itertuples()
    ]
    structure = [
        {"kind": r.kind, "direction": int(r.direction), "ts": int(r.ts), "known_ts": int(r.known_ts),
         "prior_trend": int(r.prior_trend), "ref_ts": int(r.ref_ts), "ref_price": float(r.ref_price),
         "ref_known_ts": int(r.ref_known_ts), "has_displacement": bool(r.has_displacement),
         "leg_extreme_ts": int(ts[r.leg_extreme_idx]) if r.leg_extreme_idx >= 0 else None,
         "leg_extreme_price": None if np.isnan(r.leg_extreme_price) else float(r.leg_extreme_price)}
        for r in in_range(f.structure).itertuples()
    ]
    fvgs = []
    for r in in_range(f.fvgs).itertuples():
        item = {"direction": int(r.direction), "ts": int(r.ts), "first_ts": int(ts[r.first_idx]),
                "known_ts": int(r.known_ts), "bottom": float(r.bottom), "top": float(r.top)}
        for name in ("touched", "filled", "invalidated"):
            occ, known = _lifecycle_ts(ts, int(getattr(r, f"{name}_idx")), f.tf)
            item[f"{name}_ts"], item[f"{name}_known_ts"] = occ, known
        fvgs.append(item)
    disp = f.disp
    mask = (disp["bear"] | disp["bull"]).to_numpy() & (ts >= start) & (ts < end)
    displacement = [{"ts": int(ts[i]), "direction": -1 if disp["bear"].iat[i] else 1,
                     "known_ts": int(ts[i] + f.tf.ms)} for i in np.nonzero(mask)[0]]
    bars = f.bars[(f.bars["ts"] >= start) & (f.bars["ts"] < end)]
    return {"timeframe": f.tf.value, "candles": _candles(bars), "swings": swings, "structure": structure,
            "fvgs": fvgs, "displacement": displacement}


def _bias_changes(a: Analysis, start: int, end: int) -> list[dict[str, int]]:
    ts = a.base.bars["ts"].to_numpy(dtype="int64")
    b = a.bias_on_base
    out, prev = [], None
    for i in range(len(ts)):
        if ts[i] >= end:
            break
        if b[i] != prev and ts[i] + a.base.tf.ms >= start:
            out.append({"known_ts": int(ts[i] + a.base.tf.ms), "bias": int(b[i])})
        prev = b[i]
    return out


def _clean(v: Any) -> Any:
    if isinstance(v, float) and not np.isfinite(v):
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean(x) for x in v]
    return v


def analyze_for_chart(settings: Settings, strategy_id: str, version: str, inst_id: str,
                      start_ts: int, end_ts: int) -> dict[str, Any]:
    if end_ts <= start_ts:
        raise BacktestInputError("end_ts must be after start_ts")
    if end_ts - start_ts > MAX_RESEARCH_DAYS * DAY_MS:
        raise BacktestInputError(f"research view is limited to {MAX_RESEARCH_DAYS} days per request")
    v = get_version(settings.strategy_config_dir, strategy_id, version)
    strategy = build_strategy(v.strategy_id, v.params)
    if not hasattr(strategy, "analyze"):
        raise BacktestInputError(f"{strategy_id} has no research analysis view")
    tf = Timeframe(strategy.required_timeframe or "15m")
    warm = strategy.warmup_bars() * tf.ms
    bars = CandleStore(settings.market_data_dir).load(inst_id, tf, start_ts - warm, end_ts)
    if bars.empty:
        raise BacktestInputError(f"no stored {tf.value} candles — collect data first")
    a: Analysis = strategy.analyze(bars)  # type: ignore[attr-defined]

    setups = [s for s in a.setups if start_ts <= s["known_ts"] < end_ts]
    sig = a.signals
    base_ts = a.base.bars["ts"].to_numpy(dtype="int64")
    signals = [
        {"ts": int(base_ts[i]), "known_ts": int(base_ts[i] + tf.ms), "side": int(sig.at[i, "entry"]),
         "stop": float(sig.at[i, "stop"]), "target": float(sig.at[i, "target"]),
         "limit_price": _clean(float(sig.at[i, "limit_price"])), "reason": str(sig.at[i, "reason"])}
        for i in sig.index[sig["entry"] != 0] if start_ts <= base_ts[i] < end_ts
    ]
    return _clean({
        "strategy_id": v.strategy_id, "strategy_version": v.version, "params": v.params,
        "params_hash": v.params_hash, "code_revision": code_revision(), "data_fingerprint": data_fingerprint(bars),
        "inst_id": inst_id, "start_ts": start_ts, "end_ts": end_ts, "base_timeframe": tf.value,
        "warmup_from_ts": int(bars["ts"].iloc[0]),
        "timeframes": {
            a.base.tf.value: _tf_payload(a.base, start_ts, end_ts),
            a.impulse.tf.value: _tf_payload(a.impulse, start_ts, end_ts),
            a.bias.tf.value: _tf_payload(a.bias, start_ts, end_ts),
        },
        "roles": {"base": a.base.tf.value, "impulse": a.impulse.tf.value, "bias": a.bias.tf.value},
        "bias": _bias_changes(a, start_ts, end_ts),
        "setups": setups,
        "signals": signals,
        "setup_status_counts": pd.Series([s["status"] for s in setups]).value_counts().to_dict() if setups else {},
    })
