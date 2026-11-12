"""HTTP API. Never returns credential values — only whether they are configured."""

from __future__ import annotations

import csv
import io
import time
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from tradingos import __version__
from tradingos.backtest.models import BacktestConfig, CostModel
from tradingos.config import ExecutionEnvironment, LiveTradingDisabledError, Settings, get_settings
from tradingos.domain.instruments import KNOWN_INSTRUMENTS
from tradingos.domain.timeframes import Timeframe
from tradingos.marketdata.collector import sync_history
from tradingos.marketdata.okx_public import OkxApiError, OkxPublicClient
from tradingos.marketdata.store import CandleStore
from tradingos.marketdata.validation import validate_candles
from tradingos.risk.engine import RiskConfig
from tradingos.services.audit_service import audit_run, inspect_signals
from tradingos.services.backtest_service import BacktestInputError, run_and_store
from tradingos.services.research_service import analyze_for_chart
from tradingos.storage.runs import RunRepository
from tradingos.strategy.configs import ConfigExistsError, ConfigIntegrityError, ConfigStore
from tradingos.strategy.instances import InstanceSpec, InstanceStore
from tradingos.strategy.registry import STRATEGY_CLASSES, describe_strategy, resolve_config
from tradingos.strategy.schema import ConfigValidationError

DAY_MS = 86_400_000
MAX_COLLECT_DAYS = 730
MAX_CANDLES_RESPONSE = 50_000


class ResearchRequest(BaseModel):
    strategy_id: str
    strategy_version: str | None = None
    draft_params: dict[str, Any] | None = None   # unsaved edits: exploration only
    inst_id: str = "BTC-USDT-SWAP"
    timeframe: str | None = None
    start_ts: int
    end_ts: int


class ValidateRequest(BaseModel):
    params: dict[str, Any]


class NewConfigRequest(BaseModel):
    version: str
    params: dict[str, Any]
    description: str = ""
    parent_version: str | None = None


class EntriesRequest(BaseModel):
    enabled: bool
    reason: str = ""


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
    configs = ConfigStore(settings.db_path, settings.strategy_config_dir)
    instances = InstanceStore(settings.db_path, configs)
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
    # Everything below is driven by plugin metadata — no strategy names in the API layer.
    def _strategy(sid: str) -> dict:
        if sid not in STRATEGY_CLASSES:
            raise HTTPException(404, f"unknown strategy {sid}")
        return {**describe_strategy(sid), "configs": configs.list(sid)}

    @app.get("/api/strategies")
    def strategies() -> list[dict]:
        out = []
        for sid in STRATEGY_CLASSES:
            d = _strategy(sid)
            d["versions"] = [c for c in d["configs"] if c.get("integrity") == "ok"]  # backwards compatible
            out.append(d)
        return out

    @app.get("/api/strategies/{strategy_id}")
    def strategy(strategy_id: str) -> dict:
        return _strategy(strategy_id)

    @app.post("/api/strategies/{strategy_id}/validate")
    def validate_params(strategy_id: str, req: ValidateRequest) -> dict:
        if strategy_id not in STRATEGY_CLASSES:
            raise HTTPException(404, f"unknown strategy {strategy_id}")
        try:
            cfg = resolve_config(strategy_id, "(draft)", req.params)
        except ConfigValidationError as exc:
            return {"ok": False, "errors": exc.errors}
        strat = STRATEGY_CLASSES[strategy_id](cfg.params)
        return {"ok": True, "errors": [], "resolved_params": cfg.params, "params_hash": cfg.params_hash,
                "config_hash": cfg.config_hash, "warmup_bars": strat.warmup_bars(),
                "components": strat.component_states()}

    @app.post("/api/strategies/{strategy_id}/configs")
    def create_config(strategy_id: str, req: NewConfigRequest) -> dict:
        """Save a NEW immutable configuration version (edit / clone). Never overwrites."""
        try:
            cfg = configs.create(strategy_id, req.version, req.params, req.description, req.parent_version)
        except ConfigValidationError as exc:
            raise HTTPException(422, {"message": "invalid configuration", "errors": exc.errors}) from exc
        except ConfigExistsError as exc:
            raise HTTPException(409, str(exc)) from exc
        except (KeyError, ConfigIntegrityError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return cfg.model_dump()

    # -------------------------------------------------------------- research
    @app.post("/api/research/analyze")
    def research_analyze(req: ResearchRequest) -> dict:
        """Generic research view for any strategy. Research only — no orders."""
        if req.strategy_version is None and req.draft_params is None:
            raise HTTPException(400, "give strategy_version or draft_params")
        try:
            return analyze_for_chart(settings, configs, req.strategy_id, req.strategy_version, req.inst_id,
                                     req.start_ts, req.end_ts, req.timeframe, req.draft_params)
        except ConfigValidationError as exc:
            raise HTTPException(422, {"message": "invalid draft parameters", "errors": exc.errors}) from exc
        except (BacktestInputError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/research/inspect")
    def research_inspect(req: ResearchRequest) -> dict:
        """Signal Inspector: how every candidate signal was evaluated (funnel + per-condition)."""
        if req.strategy_version is None and req.draft_params is None:
            raise HTTPException(400, "give strategy_version or draft_params")
        try:
            return inspect_signals(settings, configs, req.strategy_id, req.strategy_version, req.inst_id,
                                   req.start_ts, req.end_ts, req.timeframe, req.draft_params)
        except ConfigValidationError as exc:
            raise HTTPException(422, {"message": "invalid draft parameters", "errors": exc.errors}) from exc
        except (BacktestInputError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc

    # ------------------------------------------------------------- instances
    @app.get("/api/instances")
    def list_instances() -> list[dict]:
        return [i.model_dump() for i in instances.list()]

    @app.post("/api/instances")
    def create_instance(spec: InstanceSpec) -> dict:
        try:
            inst = instances.create(spec)
        except LiveTradingDisabledError as exc:
            raise HTTPException(403, str(exc)) from exc
        except (ValueError, KeyError, ConfigIntegrityError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return inst.model_dump()

    @app.get("/api/instances/{instance_id}")
    def get_instance(instance_id: str) -> dict:
        try:
            inst = instances.get(instance_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        checks = instances.readiness(inst, settings)
        return {**inst.model_dump(), "readiness": checks, "ready": all(c["ok"] for c in checks),
                "runtime_status": "not running — execution engine arrives in Phase 4",
                "events": instances.events(instance_id)}

    @app.post("/api/instances/{instance_id}/entries")
    def set_entries(instance_id: str, req: EntriesRequest) -> dict:
        """Strategy ON/OFF. OFF stops NEW entries only; open positions remain protected."""
        try:
            return instances.set_entries_enabled(instance_id, req.enabled, req.reason).model_dump()
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(409, str(exc)) from exc

    # ------------------------------------------------------------- backtests
    @app.post("/api/backtests")
    def create_backtest(config: BacktestConfig) -> dict:
        if config.end_ts <= config.start_ts:
            raise HTTPException(400, "end_ts must be after start_ts")
        try:
            return run_and_store(config, settings, repo, configs)
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

    @app.get("/api/backtests/{run_id}/audit")
    def audit_backtest(run_id: str) -> dict:
        """Per-trade look-ahead audit (evidence known-times + prefix replay). Read-only."""
        run = repo.get(run_id)
        if run is None:
            raise HTTPException(404, "run not found")
        try:
            return audit_run(settings, run)
        except (BacktestInputError, ConfigValidationError) as exc:
            raise HTTPException(400, str(exc)) from exc

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
