"""Declarative strategy metadata: parameter schema, components, data requirements.

A plugin DESCRIBES itself with these types; the registry, validation, API, generated docs and
the auto-generated UI are all driven from this description — never from strategy names.

Parameters are ordinary pydantic models (the single source of truth for validation). UI metadata
is attached with `param()` / `group()`, and `describe_params()` flattens a model into a list of
typed ParamSpec records the frontend renders generically.
"""

from __future__ import annotations

import types
import typing
from typing import Annotated, Any, Literal, Union, get_args, get_origin

import annotated_types as at
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from tradingos.domain.timeframes import Timeframe

ParamKind = Literal[
    "int", "float", "bool", "enum", "multi_enum", "timeframe", "percent", "price", "bps", "session", "string", "group",
]

# "HH:MM-HH:MM" in UTC; may wrap midnight (e.g. "22:00-02:00").
Session = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")]
TimeframeValue = Literal["1m", "5m", "15m", "1H", "4H"]


def param(
    default: Any,
    *,
    label: str,
    description: str = "",
    unit: str | None = None,
    step: float | None = None,
    kind: ParamKind | None = None,
    depends_on: dict[str, Any] | None = None,
    advanced: bool = False,
    ge: float | None = None,
    le: float | None = None,
    gt: float | None = None,
    lt: float | None = None,
) -> Any:
    """A parameter with UI/documentation metadata. Constraints are enforced by pydantic.

    depends_on: {"path": "entry.mode", "in": ["fvg_retest"]} or {"path": "...", "equals": True}
                -> the UI disables the field when the condition is false (it is then irrelevant).
    """
    extra = {"label": label, "unit": unit, "step": step, "kind": kind, "depends_on": depends_on,
             "advanced": advanced or None}
    return Field(default, description=description, ge=ge, le=le, gt=gt, lt=lt,
                 json_schema_extra={k: v for k, v in extra.items() if v is not None})


def group(model: type[BaseModel], *, label: str, description: str = "") -> Any:
    return Field(default_factory=model, description=description, json_schema_extra={"label": label})


# ------------------------------------------------------------------------ param specs
class ParamSpec(BaseModel):
    path: str
    label: str
    description: str = ""
    kind: ParamKind
    default: Any = None
    nullable: bool = False
    minimum: float | None = None
    maximum: float | None = None
    exclusive_minimum: float | None = None
    exclusive_maximum: float | None = None
    step: float | None = None
    unit: str | None = None
    options: list[Any] | None = None
    pattern: str | None = None
    depends_on: dict[str, Any] | None = None
    advanced: bool = False
    group: str | None = None   # parent group path ("" = top level)


def _unwrap_optional(tp: Any) -> tuple[Any, bool]:
    origin = get_origin(tp)
    if origin in (Union, types.UnionType):
        args = [a for a in get_args(tp) if a is not type(None)]
        if len(args) == 1 and len(get_args(tp)) == 2:
            return args[0], True
    return tp, False


def _strip_annotated(tp: Any) -> tuple[Any, list[Any]]:
    meta: list[Any] = []
    while get_origin(tp) is Annotated:
        tp, *m = get_args(tp)
        meta += m
    return tp, meta


def describe_params(model: type[BaseModel], prefix: str = "") -> list[ParamSpec]:
    """Flatten a (nested) pydantic params model into typed specs, in declaration order."""
    out: list[ParamSpec] = []
    hints = typing.get_type_hints(model, include_extras=True)
    for name, f in model.model_fields.items():
        path = f"{prefix}{name}"
        extra = dict(f.json_schema_extra or {}) if isinstance(f.json_schema_extra, dict) else {}
        tp, nullable = _unwrap_optional(hints.get(name, f.annotation))
        tp, ann_meta = _strip_annotated(tp)
        label = extra.get("label") or name.replace("_", " ").capitalize()
        default = f.get_default(call_default_factory=True)

        if isinstance(tp, type) and issubclass(tp, BaseModel):
            out.append(ParamSpec(path=path, label=label, description=f.description or "", kind="group",
                                 group=prefix.rstrip(".")))
            out += describe_params(tp, prefix=f"{path}.")
            continue

        spec: dict[str, Any] = {
            "path": path, "label": label, "description": f.description or "", "nullable": nullable,
            "default": default.model_dump() if isinstance(default, BaseModel) else default,
            "unit": extra.get("unit"), "step": extra.get("step"), "depends_on": extra.get("depends_on"),
            "advanced": bool(extra.get("advanced")), "group": prefix.rstrip("."),
        }
        kind = extra.get("kind")
        origin = get_origin(tp)
        if origin is Literal:
            spec["options"] = list(get_args(tp))
            kind = kind or ("timeframe" if set(spec["options"]) <= {t.value for t in Timeframe} else "enum")
        elif origin is list and get_origin(get_args(tp)[0]) is Literal:
            spec["options"] = list(get_args(get_args(tp)[0]))
            kind = kind or "multi_enum"
        elif tp is bool:
            kind = kind or "bool"
        elif tp is int:
            kind = kind or "int"
        elif tp is float:
            kind = kind or "float"
        else:
            kind = kind or "string"
        for m in list(f.metadata) + ann_meta:
            if isinstance(m, at.Ge):
                spec["minimum"] = m.ge
            elif isinstance(m, at.Le):
                spec["maximum"] = m.le
            elif isinstance(m, at.Gt):
                spec["exclusive_minimum"] = m.gt
            elif isinstance(m, at.Lt):
                spec["exclusive_maximum"] = m.lt
            elif isinstance(m, StringConstraints) and m.pattern:
                spec["pattern"] = m.pattern
                if "HH" not in (spec["unit"] or "") and "([01]" in m.pattern:
                    kind = "session"
                    spec["unit"] = spec["unit"] or "UTC HH:MM-HH:MM"
        spec["kind"] = kind
        out.append(ParamSpec(**spec))
    return out


def get_path(d: dict[str, Any], path: str) -> Any:
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(path)
        cur = cur[part]
    return cur


def set_path(d: dict[str, Any], path: str, value: Any) -> None:
    *parents, last = path.split(".")
    cur = d
    for p in parents:
        cur = cur.setdefault(p, {})
    cur[last] = value


# ---------------------------------------------------------------- dependency validation
# Enforced on the BACKEND for every path that builds a strategy (API validate/save, repo YAML,
# research drafts, backtests, instances) — the UI greying a field out is only a convenience.
def dependency_met(dep: dict[str, Any], params: dict[str, Any]) -> bool:
    try:
        v = get_path(params, dep["path"])
    except KeyError:
        return False
    return v in dep["in"] if "in" in dep else v == dep.get("equals")


def effective_dependencies(spec: ParamSpec, by_path: dict[str, ParamSpec]) -> list[dict[str, Any]]:
    """Explicit `depends_on` plus the group convention: fields of a group that has an `enabled`
    flag are only meaningful while that flag is true."""
    deps = [spec.depends_on] if spec.depends_on else []
    if spec.group:
        flag = f"{spec.group}.enabled"
        implicit = {"path": flag, "equals": True}
        if flag in by_path and spec.path != flag and implicit not in deps:
            deps.append(implicit)
    return deps


def validate_dependencies(specs: list[ParamSpec], params: dict[str, Any]) -> list[dict[str, str]]:
    """A parameter whose dependency is NOT met has no effect on the strategy. Setting it to a
    non-default value is rejected: such a configuration misrepresents what will actually run
    (and would give two identical behaviours different hashes)."""
    by_path = {s.path: s for s in specs}
    errors: list[dict[str, str]] = []
    for spec in specs:
        if spec.kind == "group":
            continue
        for dep in effective_dependencies(spec, by_path):
            if dep["path"] not in by_path:
                errors.append({"path": spec.path, "message": f"depends on unknown parameter {dep['path']!r}"})
                continue
            if dependency_met(dep, params):
                continue
            value = get_path(params, spec.path)
            if value != spec.default:
                cond = f"in {dep['in']}" if "in" in dep else f"= {dep.get('equals')!r}"
                errors.append({
                    "path": spec.path,
                    "message": f"has no effect unless {dep['path']} {cond} (currently "
                               f"{get_path(params, dep['path'])!r}); leave it at its default {spec.default!r} "
                               f"or change {dep['path']}",
                })
    return errors


def unknown_param_paths(specs: list[ParamSpec], declared: dict[str, Any], prefix: str = "") -> list[str]:
    """Keys present in a declared configuration that the schema does not define (typos would
    otherwise be ignored silently and the default used instead)."""
    known = {s.path: s for s in specs}
    out: list[str] = []
    for key, value in declared.items():
        path = f"{prefix}{key}"
        spec = known.get(path)
        if spec is None:
            out.append(path)
        elif spec.kind == "group":
            if isinstance(value, dict):
                out += unknown_param_paths(specs, value, prefix=f"{path}.")
            else:
                out.append(path)
    return out


# ------------------------------------------------------------------------- components
class ComponentSpec(BaseModel):
    """A building block of a strategy's logic (e.g. "FVG filter", "Trend filter").

    required components are part of the strategy's identity and cannot be switched off;
    optional components map to a parameter (`toggle_path`) whose value `on_value` means ON.
    """

    model_config = ConfigDict(frozen=True)
    id: str
    label: str
    description: str = ""
    role: Literal["required", "optional"]
    toggle_path: str | None = None
    on_value: Any = True
    off_value: Any = False
    requires: list[str] = []          # components that must be ON while this one is ON


class ComponentRule(BaseModel):
    kind: Literal["at_least_one"]
    components: list[str]
    message: str


class ConfigValidationError(ValueError):
    def __init__(self, errors: list[dict[str, str]]):
        super().__init__("; ".join(f"{e['path']}: {e['message']}" for e in errors))
        self.errors = errors


def component_states(components: list[ComponentSpec], params: dict[str, Any]) -> dict[str, bool]:
    states: dict[str, bool] = {}
    for c in components:
        if c.toggle_path is None:
            states[c.id] = True
        else:
            states[c.id] = get_path(params, c.toggle_path) == c.on_value
    return states


def validate_components(components: list[ComponentSpec], rules: list[ComponentRule],
                        params: dict[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    states = component_states(components, params)
    by_id = {c.id: c for c in components}
    for c in components:
        if c.role == "required" and not states[c.id]:
            errors.append({"path": c.toggle_path or c.id,
                           "message": f"'{c.label}' is a required component and cannot be disabled; "
                                      "create a separate strategy variant instead"})
        if states[c.id]:
            for dep in c.requires:
                if not states.get(dep, False):
                    errors.append({"path": c.toggle_path or c.id,
                                   "message": f"'{c.label}' requires '{by_id[dep].label}' to be enabled"})
    for r in rules:
        if r.kind == "at_least_one" and not any(states.get(cid, False) for cid in r.components):
            errors.append({"path": ",".join(r.components), "message": r.message})
    return errors


# ------------------------------------------------------------------- data requirements
class DataRequirements(BaseModel):
    base_timeframes: list[TimeframeValue]           # timeframes the strategy can be RUN on
    derived_timeframes: list[TimeframeValue] = []   # built internally from the base bars
    fields: list[str] = ["open", "high", "low", "close"]
    instruments: list[str]                          # instrument ids or fnmatch patterns
    notes: str = ""


class StrategyDefinition(BaseModel):
    """Identity + capabilities of a strategy plugin (the 'Definition' level)."""

    model_config = ConfigDict(frozen=True)
    strategy_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,40}$")
    name: str
    definition_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")  # bump when signal logic changes
    category: Literal["ict", "trend_following", "momentum", "mean_reversion", "breakout", "stat_arb", "example"]
    description: str
    data: DataRequirements
    components: list[ComponentSpec] = []
    component_rules: list[ComponentRule] = []
    maturity: Literal["educational", "research", "validated"] = "research"
    docs: str | None = None                         # repo-relative path of the human-written doc
