"""Strategy registry: Python classes + versioned YAML parameter files.

Layout: config/strategies/<strategy_id>/<version>.yaml
    strategy_id: ema_cross
    version: v1
    status: DRAFT            # lifecycle stage (docs/PRODUCT_SPEC.md §6)
    description: ...
    params: {...}

params_hash = SHA-256 of canonical JSON params; recorded with every run so that a
silently edited YAML is detectable.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from tradingos.strategy.base import Strategy
from tradingos.strategy.builtin.ema_cross import EmaCrossStrategy
from tradingos.strategy.builtin.ict_ote import IctOteStrategy

STRATEGY_CLASSES: dict[str, type[Strategy]] = {
    EmaCrossStrategy.strategy_id: EmaCrossStrategy,
    IctOteStrategy.strategy_id: IctOteStrategy,
}


class LifecycleStage(StrEnum):
    DRAFT = "DRAFT"
    BACKTESTED = "BACKTESTED"
    OUT_OF_SAMPLE_VALIDATED = "OUT_OF_SAMPLE_VALIDATED"
    DEMO_FORWARD_TESTED = "DEMO_FORWARD_TESTED"
    LIVE_ELIGIBLE = "LIVE_ELIGIBLE"


class StrategyVersion(BaseModel):
    strategy_id: str
    version: str
    status: LifecycleStage = LifecycleStage.DRAFT
    description: str = ""
    params: dict[str, Any]
    params_hash: str
    source_file: str


def params_hash(params: dict[str, Any]) -> str:
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def load_versions(config_dir: Path) -> list[StrategyVersion]:
    out: list[StrategyVersion] = []
    for f in sorted(Path(config_dir).glob("*/*.yaml")):
        raw = yaml.safe_load(f.read_text(encoding="utf-8"))
        sid = raw["strategy_id"]
        if sid not in STRATEGY_CLASSES:
            raise ValueError(f"{f}: unknown strategy_id {sid!r}")
        # Validate + normalise through the strategy's Params model (fills defaults).
        params = STRATEGY_CLASSES[sid].Params(**(raw.get("params") or {})).model_dump()
        out.append(
            StrategyVersion(
                strategy_id=sid,
                version=str(raw["version"]),
                status=raw.get("status", "DRAFT"),
                description=raw.get("description", ""),
                params=params,
                params_hash=params_hash(params),
                source_file=f.name,
            )
        )
    return out


def get_version(config_dir: Path, strategy_id: str, version: str) -> StrategyVersion:
    for v in load_versions(config_dir):
        if v.strategy_id == strategy_id and v.version == version:
            return v
    raise KeyError(f"strategy {strategy_id} version {version} not found")


def build_strategy(strategy_id: str, params: dict[str, Any]) -> Strategy:
    return STRATEGY_CLASSES[strategy_id](params)
