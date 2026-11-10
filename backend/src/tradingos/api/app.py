"""HTTP API. Never returns credential values — only whether they are configured."""

from __future__ import annotations

import csv
import io
import time
from collections.abc import Callable

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from tradingos import __version__
from tradingos.backtest.models import BacktestConfig, CostModel
from tradingos.config import ExecutionEnvironment, Settings, get_settings
from tradingos.domain.instruments import KNOWN_INSTRUMENTS
from tradingos.domain.timeframes import Timeframe
from tradingos.marketdata.collector import sync_history
from tradingos.marketdata.okx_public import OkxApiError, OkxPublicClient
from tradingos.marketdata.store import CandleStore
from tradingos.marketdata.validation import validate_candles
from tradingos.risk.engine import RiskConfig
from tradingos.services.backtest_service import BacktestInputError, run_and_store
from tradingos.services.research_service import analyze_for_chart
from tradingos.storage.runs import RunRepository
from tradingos.strategy.registry import STRATEGY_CLASSES, load_versions

DAY_MS = 86_400_000
MAX_COLLECT_DAYS = 730
MAX_CANDLES_RESPONSE = 50_000


class ResearchRequest(BaseModel):
    strategy_id: str = "ict_bearish_ote"
    strategy_version: str = "v1-mss-close"
    inst_id: str = "BTC-USDT-SWAP"
    start_ts: int
    end_ts: int


class CollectRequest(BaseModel):
    inst_id: str = "BTC-USDT-SWAP"
    timeframe: Timeframe = Timeframe.M15
    days: int = Field(90, ge=1, le=MAX_COLLECT_DAYS)


def create_app(
    settings: Settings | None = None,
    client_factory: Callable[[Settings], OkxPublicClient] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    store = CandleStore(settings.market_data_dir)
    repo = RunRepository(settings.db_path)
    make_client = client_factory or (
        lambda s: OkxPublicClient(s.okx_rest_base_url, timeout_s=s.http_timeout_s, use_system_ca=s.use_system_ca)
    )

    app = FastAPI(title="Trading OS", version=__version__)
    app.add_middleware(GZipMiddleware, minimum_size=2048)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:4173", "http://127.0.0.1:4173"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/api/system")
    def system() -> dict:
        return {
            "version": __version__,
            "environments": {
                ExecutionEnvironment.BACKTEST: {"enabled": True, "status": "available"},
                ExecutionEnvironment.DEMO: {
                    "enabled": False,
                    "status": "not implemented (Phase 4)",
                    "credentials_configured": settings.demo_credentials_configured(),
                },
                ExecutionEnvironment.LIVE: {"enabled": False, "status": "hard-disabled in this build"},
            },
            "defaults": {"costs": CostModel().model_dump(), "risk": RiskConfig().model_dump()},
        }

    # ------------------------------------------------------------ market data
    @app.get("/api/market/datasets")
    def datasets() -> list[dict]:
        out = []
        for inst_id, tf in store.datasets():
            df = store.load(inst_id, tf)
            out.append({"inst_id": inst_id, "timeframe": tf.value, **validate_candles(df, tf).to_dict()})
        return out

    @app.get("/api/market/candles")
    def candles(
        inst_id: str = "BTC-USDT-SWAP",
        timeframe: Timeframe = Timeframe.M15,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> dict:
        df = store.load(inst_id, timeframe, start_ts, end_ts)
        truncated = len(df) > MAX_CANDLES_RESPONSE
        if truncated:
            df = df.iloc[-MAX_CANDLES_RESPONSE:]
        return {"truncated": truncated, **df[["ts", "open", "high", "low", "close", "volume"]].to_dict("list")}

    @app.post("/api/market/collect")
    def collect(req: CollectRequest) -> dict:
        if req.inst_id not in KNOWN_INSTRUMENTS:
            raise HTTPException(400, f"unsupported instrument {req.inst_id}")
        start = int(time.time() * 1000) - req.days * DAY_MS
        try:
            with make_client(settings) as client:
                res = sync_history(client, store, req.inst_id, req.timeframe, start)
        except OkxApiError as exc:
            raise HTTPException(502, str(exc)) from exc
        return {
            "inst_id": res.inst_id,
            "timeframe": res.timeframe,
            "fetched": res.fetched,
            "dropped_unconfirmed": res.dropped_unconfirmed,
            "requests": res.requests,
            "stored_rows": res.stored_rows,
            "validation": res.validation.to_dict(),
        }

    # ------------------------------------------------------------ strategies
    @app.get("/api/strategies")
    def strategies() -> list[dict]:
        versions = load_versions(settings.strategy_config_dir)
        return [
            {
                "strategy_id": sid,
                "description": cls.description,
                "param_schema": cls.Params.model_json_schema(),
                "versions": [v.model_dump() for v in versions if v.strategy_id == sid],
                "required_timeframe": cls.required_timeframe,
                "has_research_view": hasattr(cls, "analyze"),
            }
            for sid, cls in STRATEGY_CLASSES.items()
        ]

    # -------------------------------------------------------------- research
    @app.post("/api/research/analyze")
    def research_analyze(req: ResearchRequest) -> dict:
        """Features + setups for Strategy Lab. Research view only — no orders."""
        try:
            return analyze_for_chart(settings, req.strategy_id, req.strategy_version, req.inst_id,
                                     req.start_ts, req.end_ts)
        except (BacktestInputError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc

    # ------------------------------------------------------------- backtests
    @app.post("/api/backtests")
    def create_backtest(config: BacktestConfig) -> dict:
        if config.end_ts <= config.start_ts:
            raise HTTPException(400, "end_ts must be after start_ts")
        try:
            return run_and_store(config, settings, repo)
        except (BacktestInputError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/backtests")
    def list_backtests(limit: int = Query(100, ge=1, le=1000)) -> list[dict]:
        return repo.list(limit)

    @app.get("/api/backtests/{run_id}")
    def get_backtest(run_id: str) -> dict:
        run = repo.get(run_id)
        if run is None:
            raise HTTPException(404, "run not found")
        return run

    @app.get("/api/backtests/{run_id}/trades.csv", response_class=PlainTextResponse)
    def export_trades(run_id: str) -> PlainTextResponse:
        run = repo.get(run_id)
        if run is None:
            raise HTTPException(404, "run not found")
        buf = io.StringIO()
        trades = run["trades"]
        if trades:
            w = csv.DictWriter(buf, fieldnames=list(trades[0].keys()))
            w.writeheader()
            w.writerows(trades)
        return PlainTextResponse(
            buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{run_id}-trades.csv"'},
        )

    return app
