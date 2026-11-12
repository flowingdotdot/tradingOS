"""Signal inspection and trade audit — strategy-agnostic use cases.

inspect_signals : run the strategy's own evaluation and return its Signal Inspector record.
audit_run       : for a stored backtest run, re-evaluate with the run's parameter snapshot and
                  check every trade for look-ahead:
                    1. evidence audit  — each input the strategy recorded for the signal was
                       knowable (known_ts) at or before the decision time;
                    2. prefix replay   — the signal is reproduced exactly when the strategy is
                       given ONLY the bars up to the signal bar (no later data exists for it).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from tradingos.config import Settings
from tradingos.domain.timeframes import Timeframe
from tradingos.marketdata.store import data_fingerprint
from tradingos.services.data_access import PeriodNotAllowedError, load_strategy_bars
from tradingos.services.backtest_service import BacktestInputError
from tradingos.strategy.configs import ConfigIntegrityError, ConfigStore
from tradingos.strategy.inspect import audit_evidence
from tradingos.strategy.instances import check_data_requirements
from tradingos.strategy.registry import STRATEGY_CLASSES, resolve_config

MAX_INSPECT_DAYS = 1500
DAY_MS = 86_400_000


def inspect_signals(settings: Settings, configs: ConfigStore, strategy_id: str, version: str | None,
                    inst_id: str, start_ts: int, end_ts: int, timeframe: str | None = None,
                    draft_params: dict[str, Any] | None = None, include_candidates: bool = True) -> dict[str, Any]:
    if end_ts <= start_ts or end_ts - start_ts > MAX_INSPECT_DAYS * DAY_MS:
        raise BacktestInputError(f"inspection period must be positive and at most {MAX_INSPECT_DAYS} days")
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
    if errors:
        raise BacktestInputError("; ".join(errors))
    strategy = cls(cfg.params)
    try:
        bars, data_ctx = load_strategy_bars(settings, inst_id, tf, start_ts, end_ts, strategy.warmup_bars(),
                                            "signal inspection")
    except PeriodNotAllowedError as exc:
        raise BacktestInputError(str(exc)) from exc
    if bars.empty:
        raise BacktestInputError(f"no stored {tf.value} candles for {inst_id} — collect data first")
    _, insp = strategy.evaluate(bars)
    base = {
        "strategy_id": strategy_id, "strategy_version": cfg.version, "draft": draft_params is not None,
        "definition_version": cls.definition.definition_version, "params_hash": cfg.params_hash,
        "config_hash": cfg.config_hash, "timeframe": tf.value, "inst_id": inst_id,
        "start_ts": start_ts, "end_ts": end_ts, "input_fingerprint": data_fingerprint(bars),
        "data_first_ts": int(bars["ts"].iloc[0]), "data_last_ts": int(bars["ts"].iloc[-1]),
        "warmup_bars": strategy.warmup_bars(),
        "warmup_available_bars": int((bars["ts"] < start_ts).sum()),
        "segment": data_ctx["segment"], "data_quality_rules": data_ctx["quality"],
    }
    if insp is None:
        return {**base, "supported": False, "inspection": None}
    return {**base, "supported": True,
            "inspection": insp.summarize(start_ts, end_ts, include_candidates=include_candidates)}


def _planned_rr(side: int, entry: float, stop: float, target: float | None) -> float | None:
    if target is None or not np.isfinite(target):
        return None
    risk = (entry - stop) * side
    return float((target - entry) * side / risk) if risk > 0 else None


def audit_run(settings: Settings, run: dict[str, Any]) -> dict[str, Any]:
    """Re-evaluate a stored run's strategy and audit each trade. Read-only."""
    sid = run["strategy_id"]
    if sid not in STRATEGY_CLASSES:
        raise BacktestInputError(f"strategy {sid!r} is no longer installed")
    cls = STRATEGY_CLASSES[sid]
    params = (run.get("strategy") or {}).get("resolved_params") or run["params"]
    strategy = cls(params)
    tf = Timeframe(run["timeframe"])
    cfg = run["config"]
    try:
        bars, _ = load_strategy_bars(settings, run["inst_id"], tf, cfg["start_ts"], cfg["end_ts"],
                                     strategy.warmup_bars(), "trade audit")
    except PeriodNotAllowedError as exc:
        raise BacktestInputError(str(exc)) from exc
    bars = bars.reset_index(drop=True)
    if bars.empty:
        raise BacktestInputError("the run's market data is no longer stored")
    recorded_fp = (run.get("conditions") or {}).get("input_fingerprint") or run.get("data_fingerprint")
    same_data = data_fingerprint(bars) == recorded_fp
    rec_def = (run.get("strategy") or {}).get("definition_version")
    signals, insp = strategy.evaluate(bars)
    ts = bars["ts"].to_numpy(dtype="int64")
    by_decision = {c["decision_ts"]: c for c in (insp.candidates if insp else []) if c["outcome"] == "signal"}

    trades = []
    for t in run["trades"]:
        # the signal bar is the last stored bar that closed at or before the fill bar's open
        i = int(np.searchsorted(ts, t["entry_ts"], side="left")) - 1
        row = signals.iloc[i] if i >= 0 else None
        signal_ok = row is not None and int(row["entry"]) == t["side"] and bool(np.isclose(row["stop"], t["stop"]))
        prefix_ok = None
        if signal_ok:
            prefix = strategy.generate_signals(bars.iloc[: i + 1].copy()).iloc[-1]
            prefix_ok = bool(prefix.equals(row))
        cand = by_decision.get(int(ts[i] + tf.ms)) if signal_ok else None
        evidence = audit_evidence(cand) if cand else []
        sig_close = float(bars["close"].iloc[i]) if i >= 0 else float("nan")
        target = t.get("target")
        trades.append({
            **{k: t[k] for k in ("trade_id", "side", "entry_ts", "entry_price", "exit_ts", "exit_price", "qty",
                                 "contracts", "stop", "target", "risk_amount", "gross_pnl", "fees", "funding",
                                 "slippage_cost", "net_pnl", "r_multiple", "exit_reason", "entry_reason",
                                 "bars_held", "ambiguous_bar", "mae", "mfe")},
            "signal_bar_ts": int(ts[i]) if i >= 0 else None,
            "decision_ts": int(ts[i] + tf.ms) if i >= 0 else None,
            "signal_bar_close": sig_close,
            "entry_gap_vs_signal_close": t["entry_price"] - sig_close,
            "planned_rr_at_signal": _planned_rr(t["side"], sig_close, t["stop"], target),
            "planned_rr_at_fill": _planned_rr(t["side"], t["entry_price"], t["stop"], target),
            "signal_reproduced": bool(signal_ok),
            "prefix_replay_ok": prefix_ok,
            "evidence": evidence,
            "evidence_ok": all(e["ok"] for e in evidence) if evidence else None,
            "candidate": {k: cand[k] for k in ("id", "label", "ts", "known_ts", "info", "stages")} if cand else None,
        })
    checks = [x for t in trades for x in (t["signal_reproduced"], t["prefix_replay_ok"], t["evidence_ok"])
              if x is not None]
    return {
        "run_id": run["run_id"], "strategy_id": sid, "strategy_version": run["strategy_version"],
        "config_hash": (run.get("strategy") or {}).get("config_hash"), "params_hash": run["params_hash"],
        "code_revision_of_run": run["code_revision"], "engine_version_of_run": run["engine_version"],
        "definition_version_of_run": rec_def, "definition_version_now": cls.definition.definition_version,
        "same_market_data_as_run": bool(same_data),
        "timeframe": tf.value, "n_trades": len(trades),
        "all_checks_passed": bool(checks) and all(checks) and same_data,
        "trades": trades,
        "notes": ([] if same_data else ["Stored market data differs from the data the run used; "
                                         "the audit reflects the current data."])
        + ([] if rec_def in (None, cls.definition.definition_version) else
           ["The strategy definition version changed since the run."]),
    }
