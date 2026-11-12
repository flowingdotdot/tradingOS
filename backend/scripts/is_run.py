"""In-sample validation run for a strategy configuration (Phase 3, step 6).

    backend/.venv/Scripts/python backend/scripts/is_run.py ict_bearish_ote v1-mss-close [--audit]

Runs the stored configuration on the IN-SAMPLE window of config/research_split.yaml through the
normal guarded service (the run is stored with its inspection and execution-model record), then
prints frequency, funnel, a bootstrap interval for expectancy and the funding sensitivity.
It cannot touch the sealed OOS window: the period comes from the split file and the loader guards it.
"""

from __future__ import annotations

import sys
from collections import Counter
from datetime import UTC, datetime

import numpy as np

from tradingos.backtest.models import BacktestConfig
from tradingos.config import get_settings
from tradingos.services.audit_service import audit_run
from tradingos.services.backtest_service import run_and_store
from tradingos.services.data_access import research_split
from tradingos.services.sensitivity import funding_sensitivity
from tradingos.storage.runs import RunRepository

MIN_TRADES_FOR_CONCLUSIONS = 30   # fixed before looking at results (docs/PHASE3_VALIDATION_PLAN.md §4)


def main() -> None:
    sid, version = sys.argv[1], sys.argv[2]
    do_audit = "--audit" in sys.argv
    s = get_settings()
    split = research_split(s)
    assert split is not None and split.oos_sealed, "research split must exist and the OOS window must be sealed"
    cfg = BacktestConfig(strategy_id=sid, strategy_version=version, timeframe="5m",
                         start_ts=split.is_start, end_ts=split.is_end)
    repo = RunRepository(s.db_path)
    # --reuse <run_id>: print the report for an already stored IS run instead of running again
    run = repo.get(sys.argv[sys.argv.index("--reuse") + 1]) if "--reuse" in sys.argv else run_and_store(cfg, s, repo)
    assert run["segment"] == "IS" and run["strategy_version"] == version
    m, trades = run["metrics"], run["trades"]
    year = lambda ms: datetime.fromtimestamp(ms / 1000, UTC).year
    print(f"run {run['run_id']}  {sid}:{version}  segment={run['segment']}  engine {run['engine_version']}  "
          f"definition {run['strategy']['definition_version']}  code {run['code_revision']}")
    print(f"config #{run['strategy']['config_hash'][:12]}  params #{run['params_hash'][:12]}  "
          f"split #{run['research_split']['fingerprint']}  data #{run['conditions']['data_fingerprint'][:12]}")
    dq = run["data_quality_rules"]
    print(f"data: {run['data_quality']['rows']} bars, invalid zero-volume runs {len(dq['invalid_runs'])} "
          f"({dq['bars_removed']} bars removed), flagged short-run bars {dq['flagged_short_bars']}")
    insp = run["inspection"]
    if insp:
        print(f"\ncandidates {insp['n_candidates']} -> strategy signals {insp['n_signals']}")
        for f in insp["funnel"]:
            print(f"  {f['label'][:46]:46s} {f['entered']:5d} -> {f['continue']:5d}  {dict(list(f['fail_reasons'].items())[:3])}")
        print("  independent pass rates:", {c["stage"]: round(c["pass_rate_pct"], 1) for c in insp["conditions"]
                                             if c["independent"] and c["pass_rate_pct"] is not None})
    em = run["execution_model"]
    print(f"\nengine: signals seen {m['signals_seen']}, rejections {m['rejection_reasons']}, "
          f"limit placed/filled {em['limit_orders_placed']}/{em['limit_orders_filled']}")
    print(f"TRADES: {len(trades)}  by year {dict(sorted(Counter(year(t['entry_ts']) for t in trades).items()))}")
    print(f"exit reasons {m['exit_reasons']}  ambiguous SL/TP bars {m['ambiguous_exits']}  "
          f"ambiguous limit-fill bars {sum(1 for t in trades if t.get('fill_bar_ambiguous'))}")
    if len(trades) < MIN_TRADES_FOR_CONCLUSIONS:
        print(f"\n** {len(trades)} trades < {MIN_TRADES_FOR_CONCLUSIONS}: performance metrics are reported for "
              "completeness only; NO conclusion about profitability (criterion fixed in advance). **")
    r = np.array([t["r_multiple"] for t in trades])
    if len(r):
        rng = np.random.default_rng(12345)
        boots = rng.choice(r, size=(10_000, len(r)), replace=True).mean(axis=1)
        lo, hi = np.percentile(boots, [2.5, 97.5])
        cost = m["total_fees"] + m["total_slippage"] + m["total_funding"]
        print(f"wins {m['wins']} / losses {m['losses']}  win rate {m['win_rate_pct']:.1f}%  "
              f"expectancy {r.mean():+.3f} R  95% bootstrap CI [{lo:+.3f}, {hi:+.3f}] R")
        print(f"net {m['net_profit']:+.2f} USDT ({m['return_pct']:+.2f}%)  max DD {m['max_drawdown_pct']:.2f}%  "
              f"profit factor {m['profit_factor']}  max consecutive losses {m['max_consecutive_losses']}")
        print(f"costs: fees {m['total_fees']:.2f}  slippage {m['total_slippage']:.2f}  "
              f"funding (ASSUMED) {m['total_funding']:.2f}  = {cost:.2f} USDT")
        print(f"R distribution: min {r.min():+.2f}  median {np.median(r):+.2f}  max {r.max():+.2f}; "
              f"planned net RR median {np.median([t['planned_net_rr'] for t in trades if t['planned_net_rr']]):.2f}")
        print("\nfunding sensitivity (assumption, not data):")
        for row in funding_sensitivity(s, cfg, run["strategy"]["resolved_params"]):
            print(f"  {row['scenario'][:56]:56s} net {row['net_profit']:+9.2f}  funding {row['total_funding']:+8.2f}  "
                  f"exp {row['expectancy_r'] if row['expectancy_r'] is None else round(row['expectancy_r'], 3)} R")
    if do_audit and trades:
        a = audit_run(s, repo.get(run["run_id"]))
        bad = [t["trade_id"] for t in a["trades"] if not (t["signal_reproduced"] and t["prefix_replay_ok"]
                                                          and t["evidence_ok"] in (True, None))]
        print(f"\nlook-ahead audit: {a['n_trades']} trades, all checks passed = {a['all_checks_passed']}, "
              f"same data = {a['same_market_data_as_run']}, failing trades = {bad}")


if __name__ == "__main__":
    main()
