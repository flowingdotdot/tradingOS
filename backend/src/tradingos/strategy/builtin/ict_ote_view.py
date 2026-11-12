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

# (id, label, default on, group). Groups are what the Chart Workspace offers as layer switches;
# switching a layer changes the DISPLAY only, never the strategy's decisions.
LAYERS = [
    ("swings", "시장 구조: 확정 스윙 (HH/HL/LH/LL)", True, "structure"),
    ("known", "인식 시각 표시 (✓)", False, "structure"),
    ("structure", "BOS / MSS", True, "bos_mss"),
    ("sweeps", "유동성 스윕", False, "bos_mss"),
    ("displacement", "Displacement 캔들", False, "bos_mss"),
    ("fvg_bear", "FVG (약세)", True, "fvg"),
    ("fvg_bull", "FVG (강세)", False, "fvg"),
    ("bias", "상위 TF 바이어스 (배경)", True, "htf_bias"),
    ("dealing", "Dealing Range (A–B, 균형가)", True, "ote"),
    ("dealing_filtered", "필터에서 탈락한 임펄스 (바이어스 불일치 / 구역 없음)", False, "ote"),
    ("ote", "OTE 구간 (0.62 / 0.705 / 0.79)", True, "ote"),
    ("zones", "진입 구역 (OTE∩FVG)", True, "ote"),
]


def _tf_overlays(view: ResearchView, f: TfFeatures, start: int, end: int) -> None:
    tf = f.tf.value
    ms = f.tf.ms
    ts = f.bars["ts"].to_numpy(dtype="int64")

    def in_range(df):
        return df[(df["ts"] >= start) & (df["ts"] < end)] if not df.empty else df

    # HH / LH / HL / LL: each confirmed swing against the previous confirmed swing of the same kind
    # (the comparison the bias rule itself makes). The first swing of a kind has no reference.
    prev: dict[str, float] = {}
    labels: dict[int, str] = {}
    for r in f.swings.sort_values(["known_idx", "idx"]).itertuples():
        p = prev.get(r.kind)
        if p is None:
            labels[r.Index] = "SH" if r.kind == "high" else "SL"
        elif r.kind == "high":
            labels[r.Index] = "HH" if r.price > p else "LH" if r.price < p else "EH"
        else:
            labels[r.Index] = "HL" if r.price > p else "LL" if r.price < p else "EL"
        prev[r.kind] = float(r.price)
    for r in in_range(f.swings).itertuples():
        high = r.kind == "high"
        view.marker("swings", int(r.ts), int(r.known_ts), position="above" if high else "below", shape="circle",
                    color="bear" if high else "bull", text=labels[r.Index], size=0.6, tf=tf)
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
    for layer_id, label, on, grp in LAYERS:
        view.layer(layer_id, label, on, group=grp)
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
    # Setup geometry lives on the impulse timeframe: it is hidden on coarser charts (max_tf).
    # Impulses the strategy discarded immediately (bias not bearish / no zone) go to a separate
    # layer that is off by default, so the chart shows what the strategy actually worked with.
    mtf = a.impulse.tf.value
    for s in setups:
        known, end_ts = s["known_ts"], s["end_ts"]
        if s["status"] in ("bias_not_bearish", "no_zone"):
            view.rect("dealing_filtered", s["a_ts"], end_ts, s["b"], s["a"], known, end_known_ts=end_ts,
                      stroke="muted", dash=True, label=f"#{s['id']} {s['status']}", max_tf=mtf)
            continue
        view.rect("dealing", s["a_ts"], end_ts, s["b"], s["a"], known, end_known_ts=end_ts, stroke="neutral",
                  dash=True, label=f"#{s['id']} {s['status']}", max_tf=mtf)
        view.segment("dealing", s["b_ts"], end_ts, s["equilibrium"], known, end_known_ts=end_ts, color="neutral",
                     dash=True, label="EQ", max_tf=mtf)
        view.marker("dealing", s["a_ts"], known, position="above", shape="arrowDown", color="neutral",
                    text=f"A#{s['id']}", size=0.6, max_tf=mtf)
        view.marker("dealing", s["b_ts"], known, position="below", shape="arrowUp", color="neutral",
                    text=f"B#{s['id']}", size=0.6, max_tf=mtf)
        view.rect("ote", known, end_ts, s["ote_low"], s["ote_high"], known, end_known_ts=end_ts, fill="ote_fill",
                  max_tf=mtf)
        for px, lab in ((s["ote_low"], "0.62"), (s["ote_ref"], "0.705"), (s["ote_high"], "0.79")):
            view.segment("ote", known, end_ts, px, known, end_known_ts=end_ts, color="ote", dash=True,
                         label=f"{lab}  {px:.1f}", max_tf=mtf)
        for z in s["zones"]:
            view.rect("zones", known, end_ts, z["bottom"], z["top"], known, end_known_ts=end_ts, fill="zone_fill",
                      stroke="zone", max_tf=mtf)

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
