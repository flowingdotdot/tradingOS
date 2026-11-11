"""Strategy-agnostic research view.

Plugins describe what to draw with generic primitives; the frontend renders them without
knowing anything about the strategy. Every item carries `known_ts` (when it became knowable)
so "knowledge replay" works for every strategy.

Item types (times are UTC epoch ms):
  marker  {ts, known_ts, position: above|below, shape: circle|square|arrowUp|arrowDown, color, text, size}
  rect    {t1, t2|None, end_known_ts|None, known_ts, p1, p2, fill, stroke, dash, label}
  segment {t1, t2|None, end_known_ts|None, known_ts, price, color, width, dash, label}
  band    {t1, t2|None, end_known_ts|None, known_ts, fill}            (full-height time band)
  t2=None means "still open" -> drawn to the replay cutoff / end of data.
  An item may carry `tf` to restrict it to one chart timeframe.
Colors may be semantic tokens (bear, bull, neutral, accent, warn, zone, ote, muted, sweep) or CSS.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _clean(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return float(v) if np.isfinite(v) else None
    if isinstance(v, np.bool_):
        return bool(v)
    return v


class ResearchView:
    def __init__(self, default_tf: str):
        self.default_tf = default_tf
        self.timeframes: dict[str, dict[str, list]] = {}
        self.layers: list[dict[str, Any]] = []
        self.items: list[dict[str, Any]] = []
        self.lines: list[dict[str, Any]] = []
        self.bar_colors: list[dict[str, Any]] = []
        self.tables: list[dict[str, Any]] = []
        self.summary: list[dict[str, Any]] = []
        self.tf_roles: dict[str, str] = {}

    # --------------------------------------------------------------------- building
    def candles(self, tf: str, bars: pd.DataFrame, start: int, end: int, role: str = "") -> None:
        b = bars[(bars["ts"] >= start) & (bars["ts"] < end)]
        self.timeframes[tf] = b[["ts", "open", "high", "low", "close"]].to_dict("list")
        if role:
            self.tf_roles[tf] = role

    def layer(self, layer_id: str, label: str, default_on: bool = True) -> None:
        if not any(x["id"] == layer_id for x in self.layers):
            self.layers.append({"id": layer_id, "label": label, "default_on": default_on})

    def marker(self, layer: str, ts: int, known_ts: int, *, position: str = "above", shape: str = "circle",
               color: str = "neutral", text: str = "", size: float = 1.0, tf: str | None = None) -> None:
        self.items.append({"type": "marker", "layer": layer, "tf": tf, "ts": ts, "known_ts": known_ts,
                           "position": position, "shape": shape, "color": color, "text": text, "size": size})

    def rect(self, layer: str, t1: int, t2: int | None, p1: float, p2: float, known_ts: int, *,
             end_known_ts: int | None = None, fill: str | None = None, stroke: str | None = None,
             dash: bool = False, label: str = "", tf: str | None = None) -> None:
        self.items.append({"type": "rect", "layer": layer, "tf": tf, "t1": t1, "t2": t2, "known_ts": known_ts,
                           "end_known_ts": end_known_ts, "p1": p1, "p2": p2, "fill": fill, "stroke": stroke,
                           "dash": dash, "label": label})

    def segment(self, layer: str, t1: int, t2: int | None, price: float, known_ts: int, *,
                end_known_ts: int | None = None, color: str = "neutral", width: float = 1, dash: bool = False,
                label: str = "", tf: str | None = None) -> None:
        self.items.append({"type": "segment", "layer": layer, "tf": tf, "t1": t1, "t2": t2, "known_ts": known_ts,
                           "end_known_ts": end_known_ts, "price": price, "color": color, "width": width,
                           "dash": dash, "label": label})

    def band(self, layer: str, t1: int, t2: int | None, known_ts: int, fill: str, *,
             end_known_ts: int | None = None) -> None:
        self.items.append({"type": "band", "layer": layer, "tf": None, "t1": t1, "t2": t2, "known_ts": known_ts,
                           "end_known_ts": end_known_ts, "fill": fill})

    def line(self, layer: str, line_id: str, label: str, tf: str, ts: np.ndarray, values: np.ndarray,
             bar_ms: int, color: str = "accent", start: int | None = None, end: int | None = None) -> None:
        """Indicator series; point i is known at the close of its bar (ts + bar_ms)."""
        m = np.isfinite(values)
        if start is not None:
            m &= ts >= start
        if end is not None:
            m &= ts < end
        self.lines.append({"layer": layer, "id": line_id, "label": label, "tf": tf, "color": color,
                           "bar_ms": bar_ms, "ts": ts[m].tolist(), "value": values[m].tolist()})

    def highlight_bars(self, layer: str, tf: str, ts: list[int], color: str, bar_ms: int) -> None:
        self.bar_colors.append({"layer": layer, "tf": tf, "ts": ts, "color": color, "bar_ms": bar_ms})

    def table(self, table_id: str, label: str, columns: list[tuple[str, str, str]], rows: list[dict[str, Any]], *,
              known_key: str = "known_ts", focus_from: str | None = None, focus_to: str | None = None) -> None:
        """columns: (key, label, kind) with kind in ts|num|price|text|status."""
        self.tables.append({"id": table_id, "label": label,
                            "columns": [{"key": k, "label": lbl, "kind": kd} for k, lbl, kd in columns],
                            "rows": rows, "known_key": known_key, "focus_from": focus_from, "focus_to": focus_to})

    def stat(self, label: str, value: Any) -> None:
        self.summary.append({"label": label, "value": value})

    def to_dict(self) -> dict[str, Any]:
        return _clean({"default_tf": self.default_tf, "timeframes": self.timeframes, "tf_roles": self.tf_roles,
                       "layers": self.layers, "items": self.items, "lines": self.lines,
                       "bar_colors": self.bar_colors, "tables": self.tables, "summary": self.summary})


def add_signal_layers(view: ResearchView, signals: pd.DataFrame, bars: pd.DataFrame, bar_ms: int,
                      start: int, end: int, horizon_bars: int = 60) -> None:
    """Generic entry markers + stop/target segments + signal table (used by every strategy)."""
    view.layer("signals", "Entry signals")
    view.layer("sltp", "Stop / target")
    ts = bars["ts"].to_numpy(dtype="int64")
    rows = []
    for i in signals.index[signals["entry"] != 0]:
        t = int(ts[i])
        if not start <= t < end:
            continue
        side = int(signals.at[i, "entry"])
        known = t + bar_ms
        stop, target = float(signals.at[i, "stop"]), float(signals.at[i, "target"])
        limit = float(signals.at[i, "limit_price"]) if "limit_price" in signals else float("nan")
        txt = f"{'LONG' if side > 0 else 'SHORT'} {'limit ' + format(limit, '.1f') if np.isfinite(limit) else '(next open)'}"
        view.marker("signals", t, known, position="below" if side > 0 else "above",
                    shape="arrowUp" if side > 0 else "arrowDown", color="accent", text=txt)
        t2 = t + horizon_bars * bar_ms
        view.segment("sltp", t, t2, stop, known, end_known_ts=known, color="bear", width=2,
                     label="SL")
        if np.isfinite(target):
            view.segment("sltp", t, t2, target, known, end_known_ts=known, color="bull", width=2, label="TP")
        if np.isfinite(limit):
            view.segment("sltp", t, t2, limit, known, end_known_ts=known, color="accent", dash=True, label="limit")
        rows.append({"ts": t, "known_ts": known, "side": "LONG" if side > 0 else "SHORT", "stop": stop,
                     "target": target, "limit_price": limit, "reason": str(signals.at[i, "reason"])})
    view.table("signals", f"Signals ({len(rows)})",
               [("ts", "Signal bar (UTC)", "ts"), ("known_ts", "Known", "ts"), ("side", "Side", "text"),
                ("stop", "Stop", "price"), ("target", "Target", "price"), ("limit_price", "Limit", "price"),
                ("reason", "Reason", "text")],
               rows, focus_from="ts", focus_to="ts")
    view.stat("Signals", len(rows))
