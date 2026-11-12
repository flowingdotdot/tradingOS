"""Command-line entry point.

    tradingos collect --tf 15m --days 180
    tradingos backtest --strategy ema_cross --version v1 --tf 15m --days 120
    tradingos strategies        # discovered plugins + configurations
    tradingos docs              # regenerate parameter reference docs from schemas
    tradingos serve
"""

from __future__ import annotations

import argparse
import json
import time

from tradingos.config import get_settings
from tradingos.domain.timeframes import Timeframe
from tradingos.logging_setup import configure_logging

DAY_MS = 86_400_000


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="tradingos")
    sub = parser.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="collect OKX historical candles into Parquet")
    c.add_argument("--inst", default="BTC-USDT-SWAP")
    c.add_argument("--tf", default="15m", choices=[t.value for t in Timeframe])
    c.add_argument("--days", type=int, default=180)

    b = sub.add_parser("backtest", help="run a backtest on stored data")
    b.add_argument("--strategy", required=True, help="strategy_id (see: tradingos strategies)")
    b.add_argument("--version", required=True, help="configuration version")
    b.add_argument("--inst", default="BTC-USDT-SWAP")
    b.add_argument("--tf", default="15m", choices=[t.value for t in Timeframe])
    b.add_argument("--days", type=int, default=120)
    b.add_argument("--capital", type=float, default=10_000)

    d = sub.add_parser("data-range", help="probe how far back OKX actually serves candles")
    d.add_argument("--inst", default="BTC-USDT-SWAP")
    d.add_argument("--tf", nargs="+", default=["5m", "15m", "4H"], choices=[t.value for t in Timeframe])

    k = sub.add_parser("data-check", help="integrity of stored candles + cross-timeframe consistency")
    k.add_argument("--inst", default="BTC-USDT-SWAP")
    k.add_argument("--write-meta", action="store_true", help="(re)write provenance sidecar files")

    au = sub.add_parser("audit", help="per-trade look-ahead audit of a stored backtest run")
    au.add_argument("--run", required=True)

    sub.add_parser("trials", help="count stored runs per experiment / segment / configuration (trial log)")
    sub.add_parser("docs", help="regenerate docs/strategies/generated/*.md from plugin schemas")
    sub.add_parser("strategies", help="list discovered strategy plugins and their configurations")

    s = sub.add_parser("serve", help="run the API server")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)

    if args.cmd == "collect":
        from tradingos.marketdata.collector import sync_history
        from tradingos.marketdata.okx_public import OkxPublicClient
        from tradingos.marketdata.store import CandleStore

        start = int(time.time() * 1000) - args.days * DAY_MS
        with OkxPublicClient(settings.okx_rest_base_url, use_system_ca=settings.use_system_ca) as client:
            store = CandleStore(settings.market_data_dir)
            res = sync_history(client, store, args.inst, Timeframe(args.tf), start)
            from tradingos.marketdata.store import write_provenance
            from tradingos.services.backtest_service import code_revision

            write_provenance(store, args.inst, Timeframe(args.tf),
                             source=f"{settings.okx_rest_base_url}/api/v5/market/history-candles",
                             collector_revision=code_revision(), requested_start_ts=start)
        print(json.dumps({"fetched": res.fetched, "requests": res.requests, "stored_rows": res.stored_rows,
                          "dropped_unconfirmed": res.dropped_unconfirmed,
                          "validation": res.validation.to_dict()}, indent=2))
    elif args.cmd == "backtest":
        from tradingos.backtest.models import BacktestConfig
        from tradingos.services.backtest_service import run_and_store
        from tradingos.storage.runs import RunRepository

        tf = Timeframe(args.tf)
        now = int(time.time() * 1000)
        end = now - now % tf.ms
        cfg = BacktestConfig(strategy_id=args.strategy, strategy_version=args.version, inst_id=args.inst,
                             timeframe=tf.value, start_ts=end - args.days * DAY_MS, end_ts=end,
                             initial_capital=args.capital)
        run = run_and_store(cfg, settings, RunRepository(settings.db_path))
        m = run["metrics"]
        print(json.dumps({"run_id": run["run_id"], **{k: v for k, v in m.items() if k != "monthly"}},
                         indent=2, default=str))
        print("\nNOTE:", run["disclaimer"])
    elif args.cmd == "data-range":
        from datetime import UTC, datetime

        from tradingos.marketdata.collector import find_earliest_candle
        from tradingos.marketdata.okx_public import OkxPublicClient

        with OkxPublicClient(settings.okx_rest_base_url, use_system_ca=settings.use_system_ca) as client:
            for tf_name in args.tf:
                r = find_earliest_candle(client, args.inst, Timeframe(tf_name))
                e = r["earliest_ts"]
                r["earliest_utc"] = datetime.fromtimestamp(e / 1000, UTC).isoformat() if e else None
                r["days_available"] = round((time.time() * 1000 - e) / DAY_MS, 1) if e else 0
                print(json.dumps(r))
    elif args.cmd == "data-check":
        from tradingos.marketdata.collector import check_mtf_consistency
        from tradingos.marketdata.store import CandleStore, data_fingerprint
        from tradingos.marketdata.validation import validate_candles

        store = CandleStore(settings.market_data_dir)
        for inst, tf in store.datasets():
            if inst != args.inst:
                continue
            df = store.load(inst, tf)
            v = validate_candles(df, tf)
            print(json.dumps({"timeframe": tf.value, "rows": v.rows, "first_ts": v.first_ts, "last_ts": v.last_ts,
                              "gaps": len(v.gaps), "missing_bars": v.missing_bars, "ok": v.ok,
                              "fingerprint": data_fingerprint(df)[:16]}))
            if args.write_meta:
                from tradingos.marketdata.store import write_provenance
                from tradingos.services.backtest_service import code_revision

                write_provenance(store, inst, tf, source=f"{settings.okx_rest_base_url}/api/v5/market/history-candles",
                                 collector_revision=code_revision())
        for ltf, htf in ((Timeframe.M5, Timeframe.M15), (Timeframe.M5, Timeframe.H4), (Timeframe.M15, Timeframe.H4)):
            print(json.dumps(check_mtf_consistency(store, args.inst, ltf, htf)))
    elif args.cmd == "audit":
        from tradingos.services.audit_service import audit_run
        from tradingos.storage.runs import RunRepository

        run = RunRepository(settings.db_path).get(args.run)
        if run is None:
            raise SystemExit(f"run {args.run} not found")
        print(json.dumps(audit_run(settings, run), indent=2, default=str))
    elif args.cmd == "trials":
        from collections import Counter

        from tradingos.storage.runs import RunRepository

        counts: Counter = Counter()
        for r in RunRepository(settings.db_path).list(1000):
            sm = r["summary"]
            counts[(sm.get("experiment_id") or "(none)", sm.get("segment") or "(pre-split)", r["strategy_id"],
                    r["strategy_version"], r["params_hash"][:10])] += 1
        print("experiment | segment | strategy | version | params | runs")
        for key, n in sorted(counts.items()):
            print(" | ".join(key), "|", n)
    elif args.cmd == "docs":
        from tradingos.strategy.docgen import write_all

        for path in write_all():
            print("wrote", path)
    elif args.cmd == "strategies":
        from tradingos.strategy.configs import ConfigStore
        from tradingos.strategy.registry import STRATEGY_CLASSES

        store = ConfigStore(settings.db_path, settings.strategy_config_dir)
        for sid, cls in STRATEGY_CLASSES.items():
            d = cls.definition
            print(f"{sid}  [{d.category}/{d.maturity}]  def v{d.definition_version}  runs on {d.data.base_timeframes}")
            for c in store.list(sid):
                print(f"    {c['version']:24s} {c.get('status', ''):10s} {c['source']:5s} integrity={c['integrity']}")
    elif args.cmd == "serve":
        import uvicorn

        from tradingos.api.app import create_app

        uvicorn.run(create_app(settings), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
