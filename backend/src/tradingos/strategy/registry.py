"""Strategy registry: plugin discovery + versioned configurations.

Plugins (Definitions) are discovered by importing every module inside the LOCAL package
`tradingos.strategy.builtin` — reviewed code that ships with the repository. Nothing is ever
loaded from a path, URL or string supplied at runtime, and configuration files are parsed with
yaml.safe_load and validated by the plugin's pydantic schema (data only, never code).

Configurations live in config/strategies/<strategy_id>/<version>.yaml (reviewed, in git) and in
the ConfigStore (user-created through the UI/API; see strategy/configs.py).
    strategy_id: ema_cross
    version: v1
    status: DRAFT
    description: ...
    params: {...}           # may omit values -> plugin defaults apply

Two hashes (see docs/STRATEGY_REGISTRY.md):
  config_hash  SHA-256 of the params AS DECLARED — identity of a configuration version
  params_hash  SHA-256 of the RESOLVED params (declared + defaults of the current definition)
"""

from __future__ import annotations

import hashlib
import importlib
import json
import pkgutil
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel

from tradingos.strategy.base import Strategy

PLUGIN_PACKAGE = "tradingos.strategy.builtin"


class RegistryError(RuntimeError):
    pass


def discover_plugins(package: str = PLUGIN_PACKAGE) -> dict[str, type[Strategy]]:
    pkg = importlib.import_module(package)
    found: dict[str, type[Strategy]] = {}
    for info in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
        module = importlib.import_module(info.name)
        for obj in vars(module).values():
            if (isinstance(obj, type) and issubclass(obj, Strategy) and obj.__module__ == module.__name__
                    and "definition" in obj.__dict__):
                sid = obj.definition.strategy_id
                if sid in found and found[sid] is not obj:
                    raise RegistryError(f"duplicate strategy_id {sid!r}: {found[sid].__module__} and {obj.__module__}")
                found[sid] = obj
    return dict(sorted(found.items()))


STRATEGY_CLASSES: dict[str, type[Strategy]] = discover_plugins()


class LifecycleStage(StrEnum):
    DRAFT = "DRAFT"
    BACKTESTED = "BACKTESTED"
    OUT_OF_SAMPLE_VALIDATED = "OUT_OF_SAMPLE_VALIDATED"
    DEMO_FORWARD_TESTED = "DEMO_FORWARD_TESTED"
    LIVE_ELIGIBLE = "LIVE_ELIGIBLE"


LIFECYCLE_ORDER = list(LifecycleStage)


class StrategyConfig(BaseModel):
    """A Strategy Configuration: one immutable, versioned parameter set for one Definition."""

    strategy_id: str
    version: str
    status: LifecycleStage = LifecycleStage.DRAFT
    description: str = ""
    declared_params: dict[str, Any]
    params: dict[str, Any]              # resolved with current definition defaults
    config_hash: str
    params_hash: str
    definition_version: str
    source: Literal["repo", "user"] = "repo"
    source_file: str | None = None
    parent_version: str | None = None
    created_at: str | None = None


StrategyVersion = StrategyConfig  # backwards-compatible name


def canonical_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


params_hash = canonical_hash


def resolve_config(strategy_id: str, version: str, declared: dict[str, Any], **meta: Any) -> StrategyConfig:
    """Validate declared params against the plugin (schema + components + custom rules)."""
    if strategy_id not in STRATEGY_CLASSES:
        raise KeyError(f"unknown strategy_id {strategy_id!r}")
    cls = STRATEGY_CLASSES[strategy_id]
    resolved = cls(declared).params.model_dump()   # raises ConfigValidationError
    return StrategyConfig(strategy_id=strategy_id, version=version, declared_params=declared, params=resolved,
                          config_hash=canonical_hash(declared), params_hash=canonical_hash(resolved),
                          definition_version=cls.definition.definition_version, **meta)


def load_repo_entries(config_dir: Path) -> list[tuple[StrategyConfig | None, dict[str, Any]]]:
    """Every repository YAML as (config, info). A file that fails to parse or validate yields
    (None, {strategy_id, version, source_file, error}) instead of breaking the other versions."""
    out: list[tuple[StrategyConfig | None, dict[str, Any]]] = []
    for f in sorted(Path(config_dir).glob("*/*.yaml")):
        info: dict[str, Any] = {"strategy_id": f.parent.name, "version": f.stem,
                                "source_file": f"{f.parent.name}/{f.name}"}
        try:
            raw = yaml.safe_load(f.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("file is not a mapping")
            info.update(strategy_id=str(raw["strategy_id"]), version=str(raw["version"]))
            cfg = resolve_config(
                info["strategy_id"], info["version"], raw.get("params") or {},
                status=raw.get("status", "DRAFT"), description=raw.get("description", ""), source="repo",
                source_file=info["source_file"], parent_version=raw.get("parent_version"),
            )
            out.append((cfg, info))
        except Exception as exc:  # noqa: BLE001 — any bad file is reported, never fatal
            out.append((None, {**info, "error": f"{type(exc).__name__}: {exc}"}))
    return out


def load_versions(config_dir: Path) -> list[StrategyConfig]:
    """Valid repository configurations (strict: raises on the first invalid file)."""
    out = []
    for cfg, info in load_repo_entries(config_dir):
        if cfg is None:
            raise RegistryError(f"{info['source_file']}: {info['error']}")
        out.append(cfg)
    return out


def get_version(config_dir: Path, strategy_id: str, version: str) -> StrategyConfig:
    for v in load_versions(config_dir):
        if v.strategy_id == strategy_id and v.version == version:
            return v
    raise KeyError(f"strategy {strategy_id} version {version} not found")


def build_strategy(strategy_id: str, params: dict[str, Any]) -> Strategy:
    if strategy_id not in STRATEGY_CLASSES:
        raise KeyError(f"unknown strategy_id {strategy_id!r}")
    return STRATEGY_CLASSES[strategy_id](params)


def describe_strategy(strategy_id: str) -> dict[str, Any]:
    """Everything a client needs to render, validate and document a strategy generically."""
    from tradingos.strategy.schema import describe_params

    cls = STRATEGY_CLASSES[strategy_id]
    d = cls.definition
    toggle_paths = {c.toggle_path for c in d.components if c.toggle_path}
    defaults = cls().params.model_dump()
    return {
        "strategy_id": d.strategy_id,
        "name": d.name,
        "definition_version": d.definition_version,
        "category": d.category,
        "maturity": d.maturity,
        "description": d.description,
        "docs": d.docs,
        "data": d.data.model_dump(),
        "required_timeframe": cls.required_timeframe,
        "components": [c.model_dump() for c in d.components],
        "component_rules": [r.model_dump() for r in d.component_rules],
        "param_specs": [p.model_dump() for p in describe_params(cls.Params)],
        "component_toggle_paths": sorted(toggle_paths),
        "default_params": defaults,
        "default_warmup_bars": cls().warmup_bars(),
        "has_research_view": True,
    }
