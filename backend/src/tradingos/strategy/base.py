"""Strategy interface shared by backtesting and (Phase 4) live execution.

CAUSALITY CONTRACT: row i of `generate_signals(bars)` may depend only on rows 0..i
of `bars`. The backtest computes all rows at once; the live loop will call it on a
rolling window and use only the last row. tests/test_strategy_causality.py checks
the contract for every registered strategy.

Strategies express *intent* (side, stop, target). Quantity and authorization are
the Risk Engine's job. Plugin contract: docs/STRATEGY_PLUGIN_SPEC.md.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

import numpy as np
import pandas as pd
from pydantic import BaseModel, ValidationError

from tradingos.domain.timeframes import Timeframe
from tradingos.strategy.inspect import Inspection
from tradingos.strategy.schema import (
    ConfigValidationError,
    StrategyDefinition,
    component_states,
    describe_params,
    unknown_param_paths,
    validate_components,
    validate_dependencies,
)
from tradingos.strategy.view import ResearchView, add_signal_layers

SIGNAL_COLUMNS = ["entry", "stop", "target", "exit_long", "exit_short", "reason"]
# Optional order details. NaN limit_price = market order at the next open (Phase-1 engine).
# A finite limit_price requests a limit entry, which the engine supports from Phase 3.
#   min_rr: the engine re-checks reward/risk at the EXPECTED FILL, net of expected costs, and
#   rejects the entry if it is below this value (NaN = no check).
OPTIONAL_SIGNAL_COLUMNS = ["limit_price", "limit_expiry_bars", "min_rr"]


def empty_signals(index: pd.Index) -> pd.DataFrame:
    n = len(index)
    return pd.DataFrame(
        {
            "entry": np.zeros(n, dtype="int8"),
            "stop": np.full(n, np.nan),
            "target": np.full(n, np.nan),
            "exit_long": np.zeros(n, dtype=bool),
            "exit_short": np.zeros(n, dtype=bool),
            "reason": np.full(n, "", dtype=object),
            "limit_price": np.full(n, np.nan),
            "limit_expiry_bars": np.full(n, np.nan),
            "min_rr": np.full(n, np.nan),
        },
        index=index,
    )


@dataclass
class StrategyState:
    """Incremental (live) state. The reference implementation keeps the full closed-bar history
    and recomputes, which makes `update` exactly equal to the batch result (see causality.py).
    Plugins with bounded memory may override create_state/update for speed."""

    bars: pd.DataFrame
    last_signal: pd.Series | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class Strategy(ABC):
    """Base class for every strategy plugin (the 'Strategy Definition' in code).

    A plugin declares `definition` (identity, data requirements, components) and `Params`
    (typed parameter schema). It returns trading INTENT only; it never sizes positions, places
    orders or talks to an exchange — the Risk and Execution engines decide what is allowed.
    """

    definition: ClassVar[StrategyDefinition]
    Params: ClassVar[type[BaseModel]]
    # Derived from `definition` for convenience / backwards compatibility:
    strategy_id: ClassVar[str]
    description: ClassVar[str] = ""
    required_timeframe: ClassVar[str | None] = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        d = cls.__dict__.get("definition")
        if d is not None:
            cls.strategy_id = d.strategy_id
            cls.description = d.description
            tfs = d.data.base_timeframes
            cls.required_timeframe = tfs[0] if len(tfs) == 1 else None

    def __init__(self, params: dict[str, Any] | None = None):
        if "definition" not in vars(type(self)) and not hasattr(type(self), "definition"):
            raise TypeError(f"{type(self).__name__} must declare `definition = StrategyDefinition(...)`")
        specs = describe_params(self.Params)
        unknown = unknown_param_paths(specs, params or {})
        if unknown:
            raise ConfigValidationError([{"path": u, "message": "unknown parameter (not defined by this strategy)"}
                                         for u in unknown])
        try:
            self.params = self.Params(**(params or {}))
        except ValidationError as exc:
            raise ConfigValidationError(
                [{"path": ".".join(str(x) for x in e["loc"]) or "params", "message": e["msg"]} for e in exc.errors()]
            ) from exc
        resolved = self.params.model_dump()
        errors = (validate_components(self.definition.components, self.definition.component_rules, resolved)
                  + validate_dependencies(specs, resolved) + self.validate_config())
        if errors:
            raise ConfigValidationError(errors)

    # ------------------------------------------------------------- plugin hooks
    def validate_config(self) -> list[dict[str, str]]:
        """Strategy-specific cross-field rules beyond the schema. Return [{path, message}]."""
        return []

    @abstractmethod
    def warmup_bars(self) -> int:
        """Bars needed before signals are meaningful; no entries are produced before this."""

    @abstractmethod
    def generate_signals(self, bars: pd.DataFrame) -> pd.DataFrame:
        """bars: columns ts, open, high, low, close, volume (ascending, closed candles only)."""

    def evaluate(self, bars: pd.DataFrame) -> tuple[pd.DataFrame, Inspection | None]:
        """Signals plus an optional Signal Inspector record produced by the same evaluation.
        Plugins that record their decision funnel override this (see strategy/inspect.py)."""
        return self.generate_signals(bars), None

    def indicator_lines(self, bars: pd.DataFrame) -> list[tuple[str, str, np.ndarray, str]]:
        """Optional (id, label, values aligned to bars, color) series for the default research view."""
        return []

    def research_view(self, bars: pd.DataFrame, tf: Timeframe, start: int, end: int) -> ResearchView:
        """Default, strategy-agnostic research view: candles, indicator lines, signals, SL/TP.
        Plugins may override to add structure overlays and tables (e.g. ICT)."""
        bars = bars.reset_index(drop=True)
        view = ResearchView(tf.value)
        view.candles(tf.value, bars, start, end, role="execution")
        ts = bars["ts"].to_numpy(dtype="int64")
        lines = self.indicator_lines(bars)
        if lines:
            view.layer("indicators", "Indicators")
        for line_id, label, values, color in lines:
            view.line("indicators", line_id, label, tf.value, ts, np.asarray(values, dtype=float), tf.ms, color,
                      start, end)
        add_signal_layers(view, self.generate_signals(bars), bars, tf.ms, start, end)
        return view

    def component_states(self) -> dict[str, bool]:
        return component_states(self.definition.components, self.params.model_dump())

    # ---------------------------------------------------------- incremental API
    def create_state(self, history: pd.DataFrame) -> StrategyState:
        return StrategyState(bars=history.reset_index(drop=True).copy())

    def update(self, state: StrategyState, bar: dict[str, Any]) -> pd.Series:
        """Feed ONE newly closed bar; returns the signal row for that bar."""
        state.bars = pd.concat([state.bars, pd.DataFrame([bar])], ignore_index=True)
        state.last_signal = self.generate_signals(state.bars).iloc[-1]
        return state.last_signal


def has_limit_entries(signals: pd.DataFrame) -> bool:
    return "limit_price" in signals.columns and bool(
        np.isfinite(signals.loc[signals["entry"] != 0, "limit_price"].to_numpy(dtype=float)).any()
    )


def validate_signals(signals: pd.DataFrame, bars: pd.DataFrame) -> None:
    """Structural checks applied by the engine to every strategy's output."""
    missing = set(SIGNAL_COLUMNS) - set(signals.columns)
    if missing:
        raise ValueError(f"signals missing columns: {sorted(missing)}")
    if len(signals) != len(bars):
        raise ValueError("signals must have one row per bar")
    entries = signals["entry"] != 0
    if not signals.loc[entries, "entry"].isin([1, -1]).all():
        raise ValueError("entry must be -1, 0 or +1")
    if signals.loc[entries, "stop"].isna().any():
        raise ValueError("every entry signal requires a stop price")
