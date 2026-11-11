"""ICT research view: converts the ICT Analysis into generic ResearchView primitives.

This is a plugin-owned "custom view": everything ICT-specific about charting lives here, so the
platform's UI stays strategy-agnostic.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from tradingos.strategy.view import ResearchView, add_signal_layers

if TYPE_CHECKING:
    from tradingos.strategy.builtin.ict_ote import Analysis, TfFeatures

LAYERS = [
    ("swings", "Confirmed swings", True),
    ("known", "Recognition time (✓)", False),
    ("structure", "BOS / MSS", True),
    ("sweeps", "Liquidity sweeps", False),
    ("displacement", "Displacement candles", False),
    ("fvg_bear", "Bearish FVG", True),
    ("fvg_bull", "Bullish FVG", False),
    ("bias", "HTF bias background", True),
    ("dealing", "Dealing range", True),
    ("ote", "OTE zone", True),
    ("zones", "Entry zone (OTE∩FVG)", True),
]


def _tf_overlays(view: ResearchView, f: TfFeatures, start: int, end: int) -> None:
    tf = f.tf.value
    ms = f.tf.ms
    ts = f.bars["ts"].to_numpy(dtype="int64")

    def in_range(df):
        return df[(df["ts"] >= start) & (df["ts"] < end)] if not df.empty else df

    for r in in_range(f.swings).itertuples():
        high = r.kind == "high"
        view.marker("swings", int(r.ts), int(r.known_ts), position="above" if high else "below", shape="circle",
                    color="bear" if high else "bull", text="SH" if high else "SL", size=0.6, tf=tf)
        view.marker("known", int(r.known_ts - ms), int(r.known_ts), position="above" if high else "below",
                    shape="square", color="muted", text=f"✓{'SH' if high else 'SL'}+{int(r.known_idx - r.idx)}",
                    size=0.4, tf=tf)

    for r in in_range(f.structure).itertuples():
        if r.kind == "SWEEP":
            view.marker("sweeps", int(r.ts), int(r.known_ts), position="above" if r.direction == -1 else "below",
                        color="sweep", text="sweep", size=0.8, tf=tf)
            continue
        color = "warn" if r.kind == "MSS" else "muted"
        view.segment("structure", int(r.ref_ts), int(r.ts), float(r.ref_price), int(r.known_ts),
                     end_known_ts=int(r.known_ts), color=color, dash=r.kind == "BOS",
                     label=f"{r.kind}{'↓' if r.direction == -1 else '↑'}", tf=tf)
        view.marker("known", int(r.ts), int(r.known_ts), position="below" if r.direction == -1 else "above",
                    shape="square", color=color, text=f"{r.kind}{'+D' if r.has_displacement else ''}", size=0.4, tf=tf)

    for r in in_range(f.fvgs).itertuples():
        ends = [i for i in (r.invalidated_idx, r.filled_idx) if i >= 0]
        e = min(ends) if ends else None
        bear = r.direction == -1
        view.rect("fvg_bear" if bear else "fvg_bull", int(ts[r.first_idx]), int(ts[e]) if e is not None else None,
                  float(r.bottom), float(r.top), int(r.known_ts),
                  end_known_ts=int(ts[e] + ms) if e is not None else None,
                  fill="bear_fill" if bear else "bull_fill", stroke="bear_line" if bear else "bull_line", tf=tf)

    disp = f.disp
    mask = (disp["bear"] | disp["bull"]).to_numpy() & (ts >= start) & (ts < end)
    view.highlight_bars("displacement", tf, ts[mask].tolist(), "highlight", ms)


def build_ict_view(a: Analysis, start: int, end: int) -> ResearchView:
    view = ResearchView(a.impulse.tf.value)
    for layer_id, label, on in LAYERS:
        view.layer(layer_id, label, on)
    for f, role in ((a.base, "trigger"), (a.impulse, "setup"), (a.bias, "bias")):
        view.candles(f.tf.value, f.bars, start, end, role=role)
        _tf_overlays(view, f, start, end)

    # Higher-timeframe bias as background bands (known at each change)
    base_ts = a.base.bars["ts"].to_numpy(dtype="int64")
    base_ms = a.base.tf.ms
    b = a.bias_on_base
    changes = [i for i in range(len(b)) if i == 0 or b[i] != b[i - 1]]
    for n, i in enumerate(changes):
        known = int(base_ts[i] + base_ms)
        nxt = int(base_ts[changes[n + 1]] + base_ms) if n + 1 < len(changes) else None
        if b[i] == 0 or (nxt is not None and nxt < start) or known >= end:
            continue
        view.band("bias", known, nxt, known, "bear_bg" if b[i] < 0 else "bull_bg", end_known_ts=nxt)

    setups = [s for s in a.setups if start <= s["known_ts"] < end]
    for s in setups:
        known, end_ts = s["known_ts"], s["end_ts"]
        view.rect("dealing", s["a_ts"], end_ts, s["b"], s["a"], known, end_known_ts=end_ts, stroke="neutral",
                  dash=True, label=f"#{s['id']} {s['status']}")
        view.segment("dealing", s["b_ts"], end_ts, s["equilibrium"], known, end_known_ts=end_ts, color="neutral",
                     dash=True, label="EQ")
        view.marker("dealing", s["a_ts"], known, position="above", shape="arrowDown", color="neutral",
                    text=f"A#{s['id']}", size=0.6)
        view.marker("dealing", s["b_ts"], known, position="below", shape="arrowUp", color="neutral",
                    text=f"B#{s['id']}", size=0.6)
        view.rect("ote", known, end_ts, s["ote_low"], s["ote_high"], known, end_known_ts=end_ts, fill="ote_fill")
        view.segment("ote", known, end_ts, s["ote_ref"], known, end_known_ts=end_ts, color="ote", dash=True,
                     label="0.705")
        for z in s["zones"]:
            view.rect("zones", known, end_ts, z["bottom"], z["top"], known, end_known_ts=end_ts, fill="zone_fill",
                      stroke="zone")

    add_signal_layers(view, a.signals, a.base.bars.reset_index(drop=True), base_ms, start, end)

    view.table("setups", f"Setups ({len(setups)})",
               [("id", "#", "num"), ("status", "Status", "status"), ("break_kind", "Impulse", "text"),
                ("a", "A", "price"), ("b", "B", "price"), ("known_ts", "Known", "ts"),
                ("ote_low", "OTE low", "price"), ("ote_high", "OTE high", "price"), ("n_zones", "Zones", "num"),
                ("touched_ts", "Touched", "ts"), ("trigger_ts", "Trigger", "ts"), ("entry_ref", "Entry", "price"),
                ("stop", "Stop", "price"), ("target", "Target", "price"), ("rr", "R:R", "num"),
                ("reason", "Reason", "text")],
               [{**{k: v for k, v in s.items() if k != "zones"}, "n_zones": len(s["zones"]),
                 "reason": s["reason"] + (f" · retest(research): {s['retest_outcome']['outcome']}"
                                          if s.get("retest_outcome") else "")} for s in setups],
               focus_from="a_ts", focus_to="end_ts")
    _feature_tables(view, a.impulse, start, end)
    for k, v in Counter(s["status"] for s in setups).most_common():
        view.stat(f"setups: {k}", v)
    return view


def _feature_tables(view: ResearchView, f: TfFeatures, start: int, end: int) -> None:
    tf, ms = f.tf.value, f.tf.ms
    ts = f.bars["ts"].to_numpy(dtype="int64")
    st = f.structure[(f.structure["ts"] >= start) & (f.structure["ts"] < end)] if not f.structure.empty else f.structure
    view.table(f"structure_{tf}", f"BOS / MSS / sweeps {tf} ({len(st)})",
               [("kind", "Kind", "text"), ("dir", "Dir", "text"), ("ts", "Occurred", "ts"), ("known_ts", "Known", "ts"),
                ("prior_trend", "Prior trend", "num"), ("ref_price", "Broken level", "price"),
                ("ref_known_ts", "Level known", "ts"), ("disp", "Displacement", "text")],
               [{"kind": r.kind, "dir": "bear" if r.direction < 0 else "bull", "ts": int(r.ts),
                 "known_ts": int(r.known_ts), "prior_trend": int(r.prior_trend), "ref_price": float(r.ref_price),
                 "ref_ts": int(r.ref_ts), "ref_known_ts": int(r.ref_known_ts),
                 "disp": "—" if r.kind == "SWEEP" else ("yes" if r.has_displacement else "no")} for r in st.itertuples()],
               focus_from="ref_ts", focus_to="ts")
    sw = f.swings[(f.swings["ts"] >= start) & (f.swings["ts"] < end)] if not f.swings.empty else f.swings
    view.table(f"swings_{tf}", f"Swings {tf} ({len(sw)})",
               [("kind", "Kind", "text"), ("price", "Price", "price"), ("ts", "Occurred (drawn here)", "ts"),
                ("known_ts", "Known (usable from)", "ts"), ("lag", "Recognition lag (bars)", "num")],
               [{"kind": r.kind, "price": float(r.price), "ts": int(r.ts), "known_ts": int(r.known_ts),
                 "lag": int((r.known_ts - r.ts) // ms)} for r in sw.itertuples()],
               focus_from="ts", focus_to="known_ts")
    fv = f.fvgs[(f.fvgs["ts"] >= start) & (f.fvgs["ts"] < end)] if not f.fvgs.empty else f.fvgs

    def occ(i: int) -> int | None:
        return int(ts[i]) if i >= 0 else None

    view.table(f"fvgs_{tf}", f"FVGs {tf} ({len(fv)})",
               [("dir", "Dir", "text"), ("bottom", "Bottom", "price"), ("top", "Top", "price"),
                ("ts", "Occurred", "ts"), ("known_ts", "Known", "ts"), ("touched_ts", "Touched", "ts"),
                ("filled_ts", "Filled", "ts"), ("invalidated_ts", "Invalidated", "ts")],
               [{"dir": "bear" if r.direction < 0 else "bull", "bottom": float(r.bottom), "top": float(r.top),
                 "ts": int(r.ts), "known_ts": int(r.known_ts), "touched_ts": occ(r.touched_idx),
                 "filled_ts": occ(r.filled_idx), "invalidated_ts": occ(r.invalidated_idx)} for r in fv.itertuples()],
               focus_from="ts", focus_to="known_ts")
