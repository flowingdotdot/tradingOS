"""Strategy Instances — a configuration deployed to an instrument/timeframe/environment.

Definition (code) -> Configuration (immutable params version) -> Instance (what runs where).
An instance's configuration can never be changed: to change parameters, save a new
configuration version, validate it, and create a new instance.

Mutable operational state is limited to `entries_enabled`. Turning it OFF stops NEW entries
only; open positions keep their protective orders and risk management (RISK_POLICY §5).
LIVE instances cannot be created (assert_environment_allowed).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from tradingos.config import ExecutionEnvironment, Settings, assert_environment_allowed
from tradingos.domain.timeframes import Timeframe
from tradingos.marketdata.store import CandleStore
from tradingos.risk.engine import RiskConfig
from tradingos.strategy.configs import ConfigIntegrityError, ConfigStore
from tradingos.strategy.registry import LIFECYCLE_ORDER, STRATEGY_CLASSES, LifecycleStage

SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_instances (
    instance_id     TEXT PRIMARY KEY,
    body_json       TEXT NOT NULL,
    entries_enabled INTEGER NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS strategy_instance_events (
    instance_id TEXT NOT NULL,
    ts          TEXT NOT NULL,
    kind        TEXT NOT NULL,
    detail_json TEXT NOT NULL
);
"""

DEMO_MIN_STAGE = LifecycleStage.OUT_OF_SAMPLE_VALIDATED


class InstanceSpec(BaseModel):
    strategy_id: str
    config_version: str
    inst_id: str = "BTC-USDT-SWAP"
    timeframe: str
    environment: Literal["BACKTEST", "DEMO", "LIVE"] = "DEMO"
    capital_allocation: float = Field(1_000.0, gt=0, description="USDT budget this instance may risk against")
    risk: RiskConfig = RiskConfig()
    note: str = ""


class StrategyInstance(InstanceSpec):
    instance_id: str
    config_hash: str
    params_hash: str
    definition_version: str
    entries_enabled: bool = False
    created_at: str


def check_data_requirements(strategy_id: str, inst_id: str, timeframe: str) -> list[str]:
    """Static compatibility (no data access). Returns error messages."""
    cls = STRATEGY_CLASSES[strategy_id]
    req = cls.definition.data
    errors = []
    if timeframe not in req.base_timeframes:
        errors.append(f"{strategy_id} runs on {', '.join(req.base_timeframes)} bars, not {timeframe}")
    if not any(fnmatch(inst_id, pat) for pat in req.instruments):
        errors.append(f"{strategy_id} does not support instrument {inst_id} (supported: {req.instruments})")
    return errors


class InstanceStore:
    def __init__(self, db_path: Path, configs: ConfigStore):
        self.db_path = Path(db_path)
        self.configs = configs
        with self._conn() as con:
            con.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def create(self, spec: InstanceSpec) -> StrategyInstance:
        assert_environment_allowed(spec.environment)          # LIVE -> LiveTradingDisabledError
        cfg = self.configs.get(spec.strategy_id, spec.config_version)   # integrity-checked
        errors = check_data_requirements(spec.strategy_id, spec.inst_id, spec.timeframe)
        if errors:
            raise ValueError("; ".join(errors))
        inst = StrategyInstance(**spec.model_dump(), instance_id=f"{spec.strategy_id}-{uuid.uuid4().hex[:8]}",
                                config_hash=cfg.config_hash, params_hash=cfg.params_hash,
                                definition_version=cfg.definition_version, entries_enabled=False, created_at=_now())
        with self._conn() as con:
            con.execute("INSERT INTO strategy_instances VALUES (?,?,?,?)",
                        (inst.instance_id, inst.model_dump_json(), 0, inst.created_at))
        self._event(inst.instance_id, "created", {"config_version": spec.config_version, "env": spec.environment})
        return inst

    def list(self) -> list[StrategyInstance]:
        with self._conn() as con:
            rows = con.execute("SELECT * FROM strategy_instances ORDER BY created_at DESC").fetchall()
        return [self._row(r) for r in rows]

    def get(self, instance_id: str) -> StrategyInstance:
        with self._conn() as con:
            r = con.execute("SELECT * FROM strategy_instances WHERE instance_id = ?", (instance_id,)).fetchone()
        if r is None:
            raise KeyError(f"instance {instance_id} not found")
        return self._row(r)

    def set_entries_enabled(self, instance_id: str, enabled: bool, reason: str = "") -> StrategyInstance:
        inst = self.get(instance_id)
        if enabled and inst.environment == ExecutionEnvironment.DEMO:
            blocking = [c for c in self.readiness(inst, None) if not c["ok"]]
            if blocking:
                raise PermissionError("not ready for DEMO entries: " + "; ".join(c["check"] for c in blocking))
        with self._conn() as con:
            con.execute("UPDATE strategy_instances SET entries_enabled = ? WHERE instance_id = ?",
                        (int(enabled), instance_id))
        self._event(instance_id, "entries_enabled" if enabled else "entries_disabled",
                    {"reason": reason, "note": "OFF stops new entries only; open positions stay protected"})
        return self.get(instance_id)

    def events(self, instance_id: str) -> list[dict[str, Any]]:
        with self._conn() as con:
            rows = con.execute("SELECT * FROM strategy_instance_events WHERE instance_id = ? ORDER BY ts",
                               (instance_id,)).fetchall()
        return [{"ts": r["ts"], "kind": r["kind"], "detail": json.loads(r["detail_json"])} for r in rows]

    def readiness(self, inst: StrategyInstance, settings: Settings | None) -> list[dict[str, Any]]:
        """DEMO deployment checklist. Every item must pass before entries can be enabled."""
        checks: list[dict[str, Any]] = []

        def add(name: str, ok: bool, detail: str) -> None:
            checks.append({"check": name, "ok": bool(ok), "detail": detail})

        try:
            cfg = self.configs.get(inst.strategy_id, inst.config_version)
            add("configuration integrity", cfg.config_hash == inst.config_hash, f"config_hash {inst.config_hash[:10]}")
            stage_ok = LIFECYCLE_ORDER.index(cfg.status) >= LIFECYCLE_ORDER.index(DEMO_MIN_STAGE)
            add("lifecycle stage", stage_ok, f"{cfg.status} (DEMO requires ≥ {DEMO_MIN_STAGE})")
        except (ConfigIntegrityError, KeyError) as exc:
            add("configuration integrity", False, str(exc))
        errors = check_data_requirements(inst.strategy_id, inst.inst_id, inst.timeframe)
        add("data requirements", not errors, "; ".join(errors) or "instrument and timeframe supported")
        if settings is not None:
            n = len(CandleStore(settings.market_data_dir).load(inst.inst_id, Timeframe(inst.timeframe)))
            add("market data available", n > 0, f"{n} stored {inst.timeframe} candles")
            add("DEMO credentials", settings.demo_credentials_configured(), "OKX_DEMO_* environment variables")
        add("execution engine", False, "OKX DEMO execution engine is not implemented yet (Phase 4)")
        return checks

    def _row(self, r: sqlite3.Row) -> StrategyInstance:
        inst = StrategyInstance.model_validate_json(r["body_json"])
        return inst.model_copy(update={"entries_enabled": bool(r["entries_enabled"])})

    def _event(self, instance_id: str, kind: str, detail: dict[str, Any]) -> None:
        with self._conn() as con:
            con.execute("INSERT INTO strategy_instance_events VALUES (?,?,?,?)",
                        (instance_id, _now(timespec="milliseconds"), kind, json.dumps(detail)))


def _now(timespec: str = "seconds") -> str:
    return datetime.now(UTC).isoformat(timespec=timespec)
