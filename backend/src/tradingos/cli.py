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
            res = sync_history(client, CandleStore(settings.market_data_dir), args.inst, Timeframe(args.tf), start)
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
