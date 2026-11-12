"""Bearish OTE Continuation — RESEARCH HYPOTHESIS, not a validated strategy.

Definitions: docs/STRATEGY_SPEC.md §2-§3. Pipeline (all causal, keyed by known_ts):

  5m base bars --resample--> 15m (impulse TF) and 4H (bias TF), complete buckets only
  4H : swings(k) -> bias (-1 bearish) known at each 4H close -> mapped to 5m decisions
  15m: swings(k), displacement, structure -> bearish breaks -> impulses A->B, FVGs, OTE, zones
  5m : swings(internal_k), displacement, structure -> MSS triggers; FVGs for the retest variant
  5m state machine: impulse known -> watch -> zone touch -> trigger -> signal / rejection

Variants (entry.mode):
  mss_close  : short at the next 5m open after the MSS bar closes (market).
  fvg_retest : after the MSS, a sell LIMIT at the bottom of the newest bearish 5m FVG of the MSS
               leg; cancelled after retest_expiry_bars or if price reaches the target first.
               The Phase-1 backtest engine has no limit orders -> this variant is research/
               visualisation only until Phase 3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel

from tradingos.domain.timeframes import Timeframe
from tradingos.features.displacement import displacement
from tradingos.features.fvg import detect_fvgs
from tradingos.features.mtf import map_series_to_ltf, resample
from tradingos.features.ote import DealingRange, intersect
from tradingos.features.structure import structure_events, swing_sequence_bias
from tradingos.features.swings import detect_swings
from tradingos.strategy.base import Strategy, empty_signals
from tradingos.strategy.inspect import FAIL, NA, NOT_EVALUATED, PASS, PENDING, Inspection, StageDef
from tradingos.strategy.schema import (
    ComponentRule,
    ComponentSpec,
    DataRequirements,
    StrategyDefinition,
    group,
    param,
)

# ---------------------------------------------------------------------------- params
# Every value is a FIRST RESEARCH HYPOTHESIS (docs/strategies/ict_ote.md), not an optimum.


class SwingParams(BaseModel):
    k_left: int = param(3, label="Swing left bars", unit="bars", ge=1, le=20, step=1,
                        description="A swing high must be strictly higher than this many bars on the left")
    k_right: int = param(3, label="Swing right bars (confirmation delay)", unit="bars", ge=1, le=20, step=1,
                         description="Bars that must CLOSE on the right before a swing is known")
    internal_k: int = param(3, label="5m internal swing bars", unit="bars", ge=1, le=20, step=1,
                            description="Left=right bars for the 5m internal structure used by the MSS trigger")
    require_contiguous: bool = param(True, label="Reject swings across data gaps", advanced=True)


class DisplacementParams(BaseModel):
    enabled: bool = param(True, label="Use displacement",
                          description="OFF removes every displacement requirement (impulse, MSS, FVG middle candle)")
    method: Literal["median_body", "atr"] = param(
        "median_body", label="Method", description="median_body: body vs median of prior bodies; atr: body vs ATR")
    lookback: int = param(20, label="Median lookback", unit="bars", ge=3, le=500, step=1,
                          depends_on={"path": "displacement.method", "in": ["median_body"]})
    mult: float = param(1.5, label="Body >= median x", unit="x", gt=0, le=20, step=0.1,
                        depends_on={"path": "displacement.method", "in": ["median_body"]})
    atr_period: int = param(14, label="ATR period", unit="bars", ge=2, le=200, step=1,
                            depends_on={"path": "displacement.method", "in": ["atr"]})
    atr_mult: float = param(1.2, label="Body >= ATR x", unit="x", gt=0, le=20, step=0.1,
                            depends_on={"path": "displacement.method", "in": ["atr"]})
    min_body_ratio: float | None = param(None, label="Min body / range", gt=0, le=1, step=0.05, advanced=True)


class BiasParams(BaseModel):
    enabled: bool = param(True, label="Use higher-timeframe bias filter")
    timeframe: Literal["1H", "4H"] = param("4H", label="Bias timeframe")
    mode: Literal["swing_sequence", "last_break"] = param(
        "swing_sequence", label="Bias definition",
        description="swing_sequence: last two confirmed highs lower AND lows lower; last_break: last structure break")
    recheck_at_trigger: bool = param(True, label="Re-check bias at trigger")


class ImpulseParams(BaseModel):
    timeframe: Literal["5m", "15m", "1H"] = param("15m", label="Setup (impulse) timeframe")
    break_kinds: list[Literal["BOS", "MSS"]] = param(["BOS", "MSS"], label="Breaks that start an impulse")
    require_displacement: bool = param(True, label="Impulse needs a displacement candle",
                                       depends_on={"path": "displacement.enabled", "equals": True})


class FvgParams(BaseModel):
    enabled: bool = param(True, label="Use FVG in the entry zone")
    min_size_mode: Literal["atr", "none"] = param("atr", label="Minimum size rule")
    min_size_atr: float = param(0.1, label="Min size", unit="x ATR", ge=0, le=5, step=0.05,
                                depends_on={"path": "fvg.min_size_mode", "in": ["atr"]})
    atr_period: int = param(14, label="ATR period", unit="bars", ge=2, le=200, step=1, advanced=True)
    require_displacement_middle: bool = param(False, label="Middle candle must be displacement")
    invalidation: Literal["close", "wick"] = param("close", label="Invalidation rule")


class OteParams(BaseModel):
    enabled: bool = param(True, label="Use OTE retracement band")
    low: float = param(0.62, label="OTE low", unit="ratio", gt=0, lt=1, step=0.005)
    high: float = param(0.79, label="OTE high", unit="ratio", gt=0, lt=1, step=0.005)
    ref: float = param(0.705, label="Reference level", unit="ratio", gt=0, lt=1, step=0.005)


class EntryParams(BaseModel):
    trigger: Literal["mss", "touch"] = param(
        "mss", label="Trigger", description="mss: 5m bearish MSS after the zone touch; touch: the touch itself")
    mode: Literal["mss_close", "fvg_retest"] = param(
        "mss_close", label="Entry method",
        description="mss_close: market at next 5m open; fvg_retest: limit at the MSS-leg 5m FVG (Phase-3 engine)")
    require_displacement_in_mss: bool = param(True, label="MSS needs displacement",
                                              depends_on={"path": "entry.trigger", "in": ["mss"]})
    setup_expiry_bars: int = param(96, label="Setup expiry (to touch)", unit="impulse-TF bars", ge=1, le=10_000)
    trigger_expiry_bars: int = param(36, label="Trigger expiry (after touch)", unit="5m bars", ge=1, le=10_000)
    retest_expiry_bars: int = param(12, label="Retest limit expiry", unit="5m bars", ge=1, le=10_000,
                                    depends_on={"path": "entry.mode", "in": ["fvg_retest"]})


class StopParams(BaseModel):
    mode: Literal["retracement_high", "impulse_high"] = param("retracement_high", label="Stop anchor")
    buffer_bps: float = param(5.0, label="Stop buffer", unit="bps", kind="bps", ge=0, le=500, step=0.5)


class TargetParams(BaseModel):
    mode: Literal["impulse_low"] = param("impulse_low", label="Target", description="Previous confirmed low (B)")
    min_rr: float = param(1.5, label="Minimum planned reward/risk", unit="R", gt=0, le=20, step=0.1)


class IctOteParams(BaseModel):
    base_timeframe: Literal["5m"] = param("5m", label="Execution timeframe")
    swing: SwingParams = group(SwingParams, label="Swings")
    displacement: DisplacementParams = group(DisplacementParams, label="Displacement")
    bias: BiasParams = group(BiasParams, label="Higher-timeframe bias")
    impulse: ImpulseParams = group(ImpulseParams, label="Impulse")
    fvg: FvgParams = group(FvgParams, label="Fair value gap")
    ote: OteParams = group(OteParams, label="OTE")
    entry: EntryParams = group(EntryParams, label="Entry")
    stop: StopParams = group(StopParams, label="Stop loss")
    target: TargetParams = group(TargetParams, label="Target")


# -------------------------------------------------------------------------- analysis


@dataclass
class TfFeatures:
    tf: Timeframe
    bars: pd.DataFrame
    swings: pd.DataFrame
    disp: pd.DataFrame
    structure: pd.DataFrame
    trend: np.ndarray
    fvgs: pd.DataFrame


@dataclass
class Analysis:
    base: TfFeatures
    impulse: TfFeatures
    bias: TfFeatures
    bias_on_base: np.ndarray
    setups: list[dict[str, Any]] = field(default_factory=list)
    signals: pd.DataFrame | None = None
    inspection: Inspection | None = None


def _end(c: dict[str, Any], outcome: str, decision_ts: int | None = None) -> None:
    """Close an inspection candidate: record the outcome and the final entry decision."""
    c["outcome"], c["decision_ts"] = outcome, decision_ts
    if outcome == "signal":
        Inspection.mark(c, "final_decision", PASS, reason="entry signal emitted")
    elif outcome.startswith("pending"):
        Inspection.mark(c, "final_decision", PENDING, reason="evaluation still open when the data ended")
    else:
        Inspection.mark(c, "final_decision", FAIL, reason=f"no entry: {outcome}")


def _tf_features(bars: pd.DataFrame, tf: Timeframe, k_left: int, k_right: int, p: IctOteParams,
                 with_fvg: bool) -> TfFeatures:
    d = p.displacement
    disp = displacement(bars, d.method, d.lookback, d.mult, d.atr_period, d.atr_mult, d.min_body_ratio)
    if not d.enabled:
        disp["bear"] = False
        disp["bull"] = False
    swings = detect_swings(bars, tf, k_left, k_right, p.swing.require_contiguous)
    structure, trend = structure_events(bars, tf, swings, disp["bear"].to_numpy(), disp["bull"].to_numpy())
    fvgs = (
        detect_fvgs(bars, tf, p.fvg.min_size_mode, p.fvg.min_size_atr, p.fvg.atr_period,
                    p.fvg.require_displacement_middle and d.enabled,
                    disp["bear"].to_numpy(), disp["bull"].to_numpy(), p.fvg.invalidation)
        if with_fvg else detect_fvgs(bars.iloc[:0], tf)
    )
    return TfFeatures(tf, bars, swings, disp, structure, trend, fvgs)


class IctOteStrategy(Strategy):
    definition = StrategyDefinition(
        strategy_id="ict_bearish_ote",
        name="ICT Bearish OTE Continuation",
        definition_version="1.1.0",   # 1.1.0: signals carry min_rr for the net-RR-at-fill check (which bars signal is unchanged)
        category="ict",
        maturity="research",
        description="4H bearish bias, 15m impulse with OTE/FVG entry zone, 5m MSS trigger, short toward the "
                    "previous confirmed low. ICT-inspired research hypothesis - unvalidated.",
        data=DataRequirements(base_timeframes=["5m"], derived_timeframes=["15m", "4H"],
                              instruments=["BTC-USDT-SWAP"],
                              notes="15m/4H are built from 5m bars (complete buckets only)."),
        components=[
            ComponentSpec(id="structure", label="Swing / BOS structure", role="required",
                          description="Confirmed swings and close-based breaks define the impulse"),
            ComponentSpec(id="impulse", label="Bearish impulse A->B", role="required"),
            ComponentSpec(id="bias_filter", label="HTF bias filter", role="optional", toggle_path="bias.enabled"),
            ComponentSpec(id="fvg_filter", label="FVG zone", role="optional", toggle_path="fvg.enabled"),
            ComponentSpec(id="ote_filter", label="OTE zone", role="optional", toggle_path="ote.enabled"),
            ComponentSpec(id="displacement", label="Displacement requirement", role="optional",
                          toggle_path="displacement.enabled"),
            ComponentSpec(id="mss_confirmation", label="5m MSS confirmation", role="optional",
                          toggle_path="entry.trigger", on_value="mss", off_value="touch"),
            ComponentSpec(id="fvg_retest_entry", label="FVG retest limit entry", role="optional",
                          toggle_path="entry.mode", on_value="fvg_retest", off_value="mss_close",
                          requires=["mss_confirmation"]),
        ],
        component_rules=[ComponentRule(kind="at_least_one", components=["fvg_filter", "ote_filter"],
                                       message="an entry zone needs the FVG zone, the OTE zone, or both")],
        docs="docs/strategies/ict_ote.md",
    )
    Params = IctOteParams
    params: IctOteParams

    def validate_config(self) -> list[dict[str, str]]:
        o = self.params.ote
        if not o.low < o.ref < o.high:
            return [{"path": "ote", "message": "OTE requires low < ref < high"}]
        return []

    def warmup_bars(self) -> int:
        p = self.params
        base = Timeframe(p.base_timeframe).ms
        need = Timeframe(p.impulse.timeframe).ms * 100
        if p.bias.enabled:
            need = max(need, Timeframe(p.bias.timeframe).ms * 60)
        return need // base

    def generate_signals(self, bars: pd.DataFrame) -> pd.DataFrame:
        return self.analyze(bars).signals  # type: ignore[return-value]

    def evaluate(self, bars: pd.DataFrame):
        a = self.analyze(bars)
        return a.signals, a.inspection

    def _stages(self) -> list[StageDef]:
        p = self.params
        itf, btf = p.impulse.timeframe, p.bias.timeframe
        stages = [
            StageDef("structure_break", f"{itf} market structure: bearish BOS/MSS",
                     "Close below the most recently confirmed swing low (the candidate itself)"),
            StageDef("displacement", f"{itf} displacement in the break leg",
                     "At least one displacement candle between the leg high and the break bar"),
            StageDef("impulse_confirmed", f"{itf} swing: impulse end B confirmed",
                     "First swing low at/after the break confirmed (k right bars) and origin A not exceeded"),
            StageDef("distinct_impulse", "Deduplication (one setup per impulse end)",
                     "Consecutive breaks that resolve to the same swing low B are one impulse"),
            StageDef("htf_bias", f"{btf} market bias bearish", "Bias as known when the impulse became known",
                     independent=True),
            StageDef("fvg_detection", f"{itf} FVG in the impulse leg",
                     "At least one bearish FVG formed inside A..B that is not invalidated", independent=True),
            StageDef("ote_intersection", "OTE x FVG entry zone", "At least one FVG overlaps the OTE band",
                     independent=True),
            StageDef("zone_touch", "Retracement reaches the entry zone",
                     "5m high reaches a valid zone before invalidation, a new low below B, expiry or supersession"),
            StageDef("entry_confirmation", "5m entry confirmation (bearish MSS)",
                     "5m bearish MSS breaking an internal swing formed after B, with displacement if required"),
            StageDef("htf_bias_at_trigger", f"{btf} bias still bearish at trigger"),
        ]
        if p.entry.mode == "fvg_retest":
            stages.append(StageDef("retest_fvg", "5m FVG in the MSS leg (limit level)"))
        stages += [
            StageDef("reward_risk", f"Minimum planned reward/risk >= {p.target.min_rr}",
                     "Target below and stop above the entry reference, RR at the signal bar close"),
            StageDef("session_filter", "Session filter", "Not part of this strategy definition", independent=True),
            StageDef("final_decision", "Final entry decision"),
        ]
        return stages

    def research_view(self, bars: pd.DataFrame, tf: Timeframe, start: int, end: int):
        """Custom view: structure overlays + setup tables (see ict_ote_view.py)."""
        from tradingos.strategy.builtin.ict_ote_view import build_ict_view

        return build_ict_view(self.analyze(bars), start, end)

    # ------------------------------------------------------------------ analyze
    def analyze(self, bars: pd.DataFrame) -> Analysis:
        p = self.params
        bars = bars.reset_index(drop=True)
        base_tf = Timeframe(p.base_timeframe)
        imp_tf, bias_tf = Timeframe(p.impulse.timeframe), Timeframe(p.bias.timeframe)

        base = _tf_features(bars, base_tf, p.swing.internal_k, p.swing.internal_k, p, with_fvg=p.entry.mode == "fvg_retest")
        imp_bars = bars if imp_tf == base_tf else resample(bars, base_tf, imp_tf)
        imp = _tf_features(imp_bars, imp_tf, p.swing.k_left, p.swing.k_right, p, with_fvg=p.fvg.enabled)
        bias_bars = resample(bars, base_tf, bias_tf)
        bias = _tf_features(bias_bars, bias_tf, p.swing.k_left, p.swing.k_right, p, with_fvg=False)

        if p.bias.mode == "swing_sequence":
            bias_series = swing_sequence_bias(bias.swings, len(bias_bars))
        else:
            bias_series = bias.trend
        bias_on_base = (
            map_series_to_ltf(bias_bars, bias_tf, bias_series, bars, base_tf, fill=0)
            if len(bias_bars) else np.zeros(len(bars), dtype=np.int64)
        )
        if not p.bias.enabled:
            bias_on_base = np.full(len(bars), -1, dtype=np.int64)

        a = Analysis(base=base, impulse=imp, bias=bias, bias_on_base=bias_on_base,
                     inspection=Inspection(self._stages()))
        impulses = self._impulses(imp, a.inspection)
        a.setups, a.signals = self._run_setups(bars, base_tf, base, imp, impulses, bias_on_base, a)
        return a

    # ----------------------------------------------------------------- impulses
    def _impulses(self, imp: TfFeatures, insp: Inspection) -> list[dict[str, Any]]:
        """Bearish impulses A->B on the impulse timeframe (docs 3.2). Every bearish structure
        break is an inspection candidate; the reasons a break does not become an impulse are
        recorded where they are decided."""
        p = self.params
        ev = imp.structure
        if ev.empty:
            return []
        ev = ev[(ev["kind"].isin(["BOS", "MSS"])) & (ev["direction"] == -1)]
        need_disp = p.impulse.require_displacement and p.displacement.enabled
        lows = imp.swings[imp.swings["kind"] == "low"].sort_values("idx")
        h = imp.bars["high"].to_numpy()
        c = imp.bars["close"].to_numpy()
        ts = imp.bars["ts"].to_numpy(dtype="int64")
        low_idx = lows["idx"].to_numpy()
        by_b: dict[int, dict[str, Any]] = {}
        mark = insp.mark
        for e in ev.itertuples():
            cd = insp.candidate(int(e.ts), int(e.known_ts), f"{e.kind} down {imp.tf.value}", break_kind=e.kind,
                                ref_price=float(e.ref_price), break_close=float(c[e.idx]))
            mark(cd, "session_filter", NA, reason="not part of ict_bearish_ote")
            if e.kind not in p.impulse.break_kinds:
                mark(cd, "structure_break", FAIL, value={"kind": e.kind},
                     reason=f"break_kind_not_allowed: {e.kind} not in impulse.break_kinds")
                _end(cd, "break_kind_not_allowed", int(e.known_ts))
                continue
            mark(cd, "structure_break", PASS,
                 value={"kind": e.kind, "broken_swing_low": float(e.ref_price), "close": float(c[e.idx]),
                        "prior_trend": int(e.prior_trend)})
            if not need_disp:
                mark(cd, "displacement", NA, reason="displacement not required")
            elif e.has_displacement:
                mark(cd, "displacement", PASS)
            else:
                mark(cd, "displacement", FAIL, reason="no_displacement: no displacement candle in the break leg")
                _end(cd, "no_displacement", int(e.known_ts))
                continue
            k = np.searchsorted(low_idx, e.idx, side="left")
            a_idx, a_px = int(e.leg_extreme_idx), float(e.leg_extreme_price)
            if k >= len(lows):
                # impulse end not formed / not confirmed within data
                mark(cd, "impulse_confirmed", PENDING, value={"a": a_px},
                     reason="no confirmed swing low after the break within the data")
                _end(cd, "pending_impulse")
                continue
            b = lows.iloc[k]
            if np.max(h[a_idx + 1 : int(b.known_idx) + 1], initial=-np.inf) > a_px:
                # price exceeded the impulse origin before the impulse completed
                mark(cd, "impulse_confirmed", FAIL, value={"a": a_px, "b": float(b.price)},
                     reason="origin_exceeded: price traded above A before B was confirmed")
                _end(cd, "origin_exceeded", int(b.known_ts))
                continue
            mark(cd, "impulse_confirmed", PASS,
                 value={"a": a_px, "b": float(b.price), "b_ts": int(b.ts), "b_known_ts": int(b.known_ts)})
            cand = {
                "break_idx": int(e.idx), "break_ts": int(e.ts), "break_kind": e.kind,
                "a_idx": a_idx, "a_ts": int(ts[a_idx]), "a": a_px,
                "b_idx": int(b.idx), "b_ts": int(b.ts), "b": float(b.price),
                "b_known_idx": int(b.known_idx), "known_ts": int(b.known_ts),
                "cid": cd["id"], "ref_ts": int(e.ref_ts), "ref_known_ts": int(e.ref_known_ts),
                "ref_price": float(e.ref_price),
            }
            prev = by_b.get(cand["b_idx"])
            # Consecutive breaks resolving to the same impulse end: keep the highest origin A.
            if prev is None or cand["a"] > prev["a"]:
                by_b[cand["b_idx"]] = cand
                loser, winner = prev, cand
            else:
                loser, winner = cand, prev
            if loser is not None:
                lc = insp.candidates[loser["cid"]]
                mark(lc, "distinct_impulse", FAIL,
                     reason=f"duplicate: same impulse end B as candidate #{winner['cid']} (kept: higher origin A)")
                _end(lc, "duplicate", loser["known_ts"])
        for cand in by_b.values():
            mark(insp.candidates[cand["cid"]], "distinct_impulse", PASS)
        return sorted(by_b.values(), key=lambda x: (x["known_ts"], x["b_idx"]))

    def _zones(self, imp: TfFeatures, im: dict[str, Any]
               ) -> tuple[list[dict[str, Any]], tuple[float, float], dict[str, int]]:
        p = self.params
        dr = DealingRange(im["a"], im["b"])
        ote = dr.ote(p.ote.low, p.ote.high)
        zones: list[dict[str, Any]] = []
        stats = {"fvg_in_leg": 0, "fvg_valid": 0}
        if p.fvg.enabled:
            f = imp.fvgs
            if not f.empty:
                f = f[(f["direction"] == -1) & (f["first_idx"] >= im["a_idx"]) & (f["known_idx"] <= im["b_idx"])]
                ts = imp.bars["ts"].to_numpy(dtype="int64")
                for r in f.itertuples():
                    stats["fvg_in_leg"] += 1
                    inval_known = int(ts[r.invalidated_idx] + imp.tf.ms) if r.invalidated_idx >= 0 else None
                    if inval_known is not None and inval_known <= im["known_ts"]:
                        continue  # already invalidated when the impulse became known
                    stats["fvg_valid"] += 1
                    zone = intersect((r.bottom, r.top), ote) if p.ote.enabled else (r.bottom, r.top)
                    if zone is not None:
                        zones.append({"bottom": zone[0], "top": zone[1], "fvg_ts": int(r.ts),
                                      "fvg_bottom": float(r.bottom), "fvg_top": float(r.top),
                                      "fvg_known_ts": int(r.known_ts), "inval_known_ts": inval_known})
        else:
            zones.append({"bottom": ote[0], "top": ote[1], "fvg_ts": None, "fvg_bottom": None, "fvg_top": None,
                          "fvg_known_ts": None, "inval_known_ts": None})
        return zones, ote, stats

    # -------------------------------------------------------------- state machine
    def _run_setups(self, bars: pd.DataFrame, base_tf: Timeframe, base: TfFeatures, imp: TfFeatures,
                    impulses: list[dict[str, Any]], bias5: np.ndarray, a: Analysis
                    ) -> tuple[list[dict], pd.DataFrame]:
        p = self.params
        n = len(bars)
        sig = empty_signals(bars.index)
        ts = bars["ts"].to_numpy(dtype="int64")
        o, h, l, c = (bars[k].to_numpy(dtype="float64") for k in ("open", "high", "low", "close"))
        warm = self.warmup_bars()
        buf = p.stop.buffer_bps / 10_000

        mss_at: dict[int, Any] = {}
        if not base.structure.empty:
            st = base.structure
            m = st[(st["kind"] == "MSS") & (st["direction"] == -1)]
            for e in m.itertuples():
                mss_at[int(e.idx)] = e
        fvg5 = base.fvgs[base.fvgs["direction"] == -1] if not base.fvgs.empty else base.fvgs

        setups: list[dict[str, Any]] = []
        active: dict[str, Any] | None = None
        next_imp = 0

        def close_setup(s: dict[str, Any], status: str, j: int, reason: str = "") -> None:
            s["status"] = status
            s["end_ts"] = int(ts[j] + base_tf.ms)
            if reason:
                s["reason"] = reason
            self._record(a, s)

        for j in range(n):
            t_close = int(ts[j] + base_tf.ms)
            s = active
            if s is not None and ts[j] >= s["known_ts"]:
                done = self._step(s, j, ts, o, h, l, c, base_tf, mss_at, fvg5, bias5, buf, sig, warm, n)
                if done:
                    self._record(a, s)
                    active = None
            # Activate impulses that became known at this bar's close (they watch from j+1).
            while next_imp < len(impulses) and impulses[next_imp]["known_ts"] <= t_close:
                im = impulses[next_imp]
                next_imp += 1
                rec = self._new_setup(im, imp, bars, ts, l, base_tf, len(setups))
                rec["bias_at_known"] = int(bias5[j])
                setups.append(rec)
                if active is not None:
                    close_setup(active, "superseded", j, f"newer impulse #{rec['id']} became known")
                    active = None
                if bias5[j] != -1:
                    close_setup(rec, "bias_not_bearish", j)
                elif not rec["zones"]:
                    close_setup(rec, "no_zone", j)
                else:
                    active = rec
        if active is not None:
            active["status"] = "open_at_end"
            self._record(a, active)
        return setups, sig

    def _new_setup(self, im: dict, imp: TfFeatures, bars: pd.DataFrame, ts: np.ndarray, l: np.ndarray,
                   base_tf: Timeframe, sid: int) -> dict[str, Any]:
        p = self.params
        zones, ote, zstats = self._zones(imp, im)
        dr = DealingRange(im["a"], im["b"])
        # Base-TF bar that printed the impulse low (inside the impulse-TF B bar).
        b_start = int(np.searchsorted(ts, im["b_ts"], side="left"))
        b_end = int(np.searchsorted(ts, im["b_ts"] + imp.tf.ms, side="left"))
        b5 = b_start + int(np.argmin(l[b_start:b_end])) if b_end > b_start else b_start
        return {
            "id": sid, "status": "active", "reason": "", **im,
            "impulse_tf": imp.tf.value, "equilibrium": dr.equilibrium,
            "ote_low": ote[0], "ote_high": ote[1], "ote_ref": dr.level(p.ote.ref),
            "zones": zones, "b5_idx": b5, "b5_ts": int(ts[min(b5, len(ts) - 1)]) if len(ts) else 0,
            "retr_high": None, "touched_ts": None, "touch_idx": None,
            "trigger_ts": None, "trigger_known_ts": None, "mss_ref_ts": None, "mss_ref_price": None,
            "mode": p.entry.mode, "entry_ref": None, "stop": None, "target": None, "rr": None,
            "limit_price": None, "retest_fvg_ts": None, "retest_outcome": None, "end_ts": None,
            # inspector bookkeeping (never read by the decision logic)
            "n_fvg_leg": zstats["fvg_in_leg"], "n_fvg_valid": zstats["fvg_valid"], "bias_at_known": None,
            "bias_at_trigger": None, "mss_ref_known_ts": None, "retest_fvg_known_ts": None,
            "mss_skipped_old_ref": 0, "mss_skipped_no_displacement": 0,
        }

    def _step(self, s: dict, j: int, ts, o, h, l, c, base_tf: Timeframe, mss_at: dict, fvg5: pd.DataFrame,
              bias5: np.ndarray, buf: float, sig: pd.DataFrame, warm: int, n: int) -> bool:
        """Advance setup `s` with base bar j. Returns True when the setup is finished."""
        p = self.params
        t_close = int(ts[j] + base_tf.ms)

        def finish(status: str, reason: str = "") -> bool:
            s["status"], s["reason"], s["end_ts"] = status, reason, t_close
            return True

        if s["retr_high"] is None:  # initialise with base bars from the impulse low up to activation
            s["retr_high"] = float(np.max(h[s["b5_idx"] : j], initial=s["b"]))
        s["retr_high"] = max(s["retr_high"], float(h[j]))

        if h[j] > s["a"]:
            return finish("invalidated", "price traded above impulse origin A")

        # Zones still valid at the OPEN of this bar.
        valid = [z for z in s["zones"] if z["inval_known_ts"] is None or z["inval_known_ts"] > ts[j]]
        if s["touch_idx"] is None:
            if not valid:
                return finish("zones_invalidated", "all FVG zones invalidated before touch")
            if l[j] < s["b"]:
                return finish("impulse_extended", "new low below B before reaching the zone")
            imp_ms = Timeframe(s["impulse_tf"]).ms
            if t_close > s["known_ts"] + p.entry.setup_expiry_bars * imp_ms:
                return finish("expired_no_touch")
            if h[j] >= min(z["bottom"] for z in valid):
                s["touch_idx"], s["touched_ts"] = j, int(ts[j])
            else:
                return False

        if j - s["touch_idx"] > p.entry.trigger_expiry_bars:
            return finish("expired_no_trigger")

        if p.entry.trigger == "mss":
            e = mss_at.get(j)
            if e is None:
                return False
            if e.ref_ts < s["b5_ts"]:
                s["mss_skipped_old_ref"] += 1
                return False  # MSS must break an internal swing formed after the impulse low
            if p.entry.require_displacement_in_mss and p.displacement.enabled and not e.has_displacement:
                s["mss_skipped_no_displacement"] += 1
                return False
            s["mss_ref_ts"], s["mss_ref_price"] = int(e.ref_ts), float(e.ref_price)
            s["mss_ref_known_ts"] = int(e.ref_known_ts)
        s["trigger_ts"], s["trigger_known_ts"] = int(ts[j]), t_close
        s["bias_at_trigger"] = int(bias5[j])

        if p.bias.enabled and p.bias.recheck_at_trigger and bias5[j] != -1:
            return finish("bias_lost", "4H bias no longer bearish at trigger")

        stop_base = s["retr_high"] if p.stop.mode == "retracement_high" else s["a"]
        stop = stop_base * (1 + buf)
        target = s["b"]
        entry_ref = float(c[j])
        if p.entry.mode == "fvg_retest":
            leg_start = int(mss_at[j].leg_extreme_idx)
            cand = fvg5[(fvg5["first_idx"] >= leg_start) & (fvg5["known_idx"] <= j)] if not fvg5.empty else fvg5
            if cand.empty:
                return finish("no_retest_fvg", "no bearish 5m FVG in the MSS leg")
            r = cand.sort_values(["known_idx", "bottom"], ascending=[False, True]).iloc[0]
            entry_ref = float(r["bottom"])
            s["limit_price"], s["retest_fvg_ts"] = entry_ref, int(r["ts"])
            s["retest_fvg_known_ts"] = int(r["known_ts"])
        s["entry_ref"], s["stop"], s["target"] = entry_ref, stop, target

        if not target < entry_ref:
            return finish("rejected_target", "target (impulse low) is not below the entry price")
        if not stop > entry_ref:
            return finish("rejected_stop", "stop is not above the entry price")
        rr = (entry_ref - target) / (stop - entry_ref)
        s["rr"] = rr
        if rr < p.target.min_rr:
            return finish("rejected_rr", f"planned R:R {rr:.2f} < {p.target.min_rr}")
        # NOTE: no "is this the last bar?" check here — that would depend on whether future bars
        # exist (look-ahead) and would silence every live signal. The engine handles the last bar.
        if j < warm:
            return finish("warmup", "signal inside warm-up period suppressed")

        sig.loc[j, "entry"] = -1
        sig.loc[j, "stop"] = stop
        sig.loc[j, "target"] = target
        sig.loc[j, "min_rr"] = p.target.min_rr   # engine re-checks it at the expected fill, net of costs
        sig.loc[j, "reason"] = (
            f"setup#{s['id']} {s['break_kind']} impulse A={s['a']:.1f} B={s['b']:.1f}; "
            f"{'MSS' if p.entry.trigger == 'mss' else 'touch'} trigger; RR {rr:.2f}"
        )
        if p.entry.mode == "fvg_retest":
            sig.loc[j, "limit_price"] = entry_ref
            sig.loc[j, "limit_expiry_bars"] = p.entry.retest_expiry_bars
            s["retest_outcome"] = self._hypothetical_retest(j, h, l, entry_ref, stop, target, ts)
        return finish("signal")

    def _record(self, a: Analysis, s: dict[str, Any]) -> None:
        """Write the finished setup's evaluation into the inspection (called exactly where the
        state machine closes a setup). Uses only values the decision logic stored on the setup."""
        p = self.params
        insp = a.inspection
        assert insp is not None
        c = insp.candidates[s["cid"]]
        mark = insp.mark
        st = s["status"]
        c["info"].update(setup_id=s["id"], a=s["a"], b=s["b"], impulse_known_ts=s["known_ts"])
        pending = st == "open_at_end"

        # --- independent conditions, all known when the impulse became known
        if not p.bias.enabled:
            mark(c, "htf_bias", NA, reason="bias filter disabled")
        else:
            ok = s["bias_at_known"] == -1
            mark(c, "htf_bias", PASS if ok else FAIL,
                 value={"bias": s["bias_at_known"], "timeframe": p.bias.timeframe, "mode": p.bias.mode},
                 reason="" if ok else ("bias_bullish: " if s["bias_at_known"] == 1 else "bias_neutral: ")
                 + f"{p.bias.timeframe} bias = {s['bias_at_known']} (needs -1)")
        fvg_ok = True
        if not p.fvg.enabled:
            mark(c, "fvg_detection", NA, reason="FVG zone disabled")
        else:
            fvg_ok = s["n_fvg_valid"] > 0
            reason = "" if fvg_ok else (
                "no_fvg: no bearish FVG formed inside the impulse leg" if s["n_fvg_leg"] == 0
                else f"fvg_invalidated: all {s['n_fvg_leg']} leg FVG(s) invalidated before the impulse was known")
            mark(c, "fvg_detection", PASS if fvg_ok else FAIL,
                 value={"fvg_in_leg": s["n_fvg_leg"], "fvg_valid": s["n_fvg_valid"]}, reason=reason)
        ote_val = {"ote_low": s["ote_low"], "ote_high": s["ote_high"], "zones": len(s["zones"])}
        if not p.ote.enabled:
            mark(c, "ote_intersection", NA, reason="OTE zone disabled")
        elif not fvg_ok:
            mark(c, "ote_intersection", NOT_EVALUATED, value=ote_val, reason="no valid FVG to intersect with")
        elif s["zones"]:
            mark(c, "ote_intersection", PASS, value=ote_val)
        else:
            mark(c, "ote_intersection", FAIL, value=ote_val,
                 reason=f"no_intersection: none of {s['n_fvg_valid']} valid FVG(s) overlaps the OTE band")
        if st in ("bias_not_bearish", "no_zone"):
            return _end(c, st, s["known_ts"])

        # --- sequential path
        if s["touch_idx"] is None:
            if pending:
                mark(c, "zone_touch", PENDING, reason="waiting for the retracement when the data ended")
                return _end(c, "pending_touch")
            mark(c, "zone_touch", FAIL, value={"retracement_high": s["retr_high"]},
                 reason=f"{st}: {s['reason'] or 'zone not reached in time'}")
            return _end(c, st, s["end_ts"])
        mark(c, "zone_touch", PASS, value={"touched_ts": s["touched_ts"]})

        if s["trigger_ts"] is None:
            skipped = {"mss_breaking_swing_older_than_B": s["mss_skipped_old_ref"],
                       "mss_without_displacement": s["mss_skipped_no_displacement"]}
            if pending:
                mark(c, "entry_confirmation", PENDING, value=skipped, reason="waiting for the 5m MSS")
                return _end(c, "pending_confirmation")
            mark(c, "entry_confirmation", FAIL, value=skipped,
                 reason=f"{st}: {s['reason'] or 'no qualifying 5m bearish MSS after the touch'}")
            return _end(c, st, s["end_ts"])
        decision = s["trigger_known_ts"]
        if p.entry.trigger == "mss":
            mark(c, "entry_confirmation", PASS,
                 value={"mss_ts": s["trigger_ts"], "broken_swing_low": s["mss_ref_price"], "swing_ts": s["mss_ref_ts"]})
        else:
            mark(c, "entry_confirmation", NA, reason="trigger = touch (no MSS confirmation)")

        if not (p.bias.enabled and p.bias.recheck_at_trigger):
            mark(c, "htf_bias_at_trigger", NA, reason="bias re-check disabled")
        elif st == "bias_lost":
            mark(c, "htf_bias_at_trigger", FAIL, value={"bias": s["bias_at_trigger"]},
                 reason=f"bias_lost: {p.bias.timeframe} bias = {s['bias_at_trigger']} at trigger")
            return _end(c, st, decision)
        else:
            mark(c, "htf_bias_at_trigger", PASS, value={"bias": s["bias_at_trigger"]})

        if p.entry.mode == "fvg_retest":
            if st == "no_retest_fvg":
                mark(c, "retest_fvg", FAIL, reason=f"no_retest_fvg: {s['reason']}")
                return _end(c, st, decision)
            mark(c, "retest_fvg", PASS, value={"limit_price": s["limit_price"], "fvg_ts": s["retest_fvg_ts"]})

        rr_val = {"entry_ref": s["entry_ref"], "stop": s["stop"], "target": s["target"], "rr": s["rr"],
                  "min_rr": p.target.min_rr}
        if st in ("rejected_target", "rejected_stop", "rejected_rr"):
            mark(c, "reward_risk", FAIL, value=rr_val, reason=f"{st}: {s['reason']}")
            return _end(c, st, decision)
        mark(c, "reward_risk", PASS, value=rr_val)
        if st == "signal":
            self._evidence(a, s, c)
        return _end(c, st, decision)

    def _evidence(self, a: Analysis, s: dict[str, Any], c: dict[str, Any]) -> None:
        """Inputs of a signal with occurrence vs. recognition time (for the look-ahead audit)."""
        p = self.params
        ev = Inspection.evidence
        itf, btf, ltf = a.impulse.tf, a.bias.tf, a.base.tf
        decision = s["trigger_known_ts"]
        ev(c, f"{itf.value} swing low broken by the {s['break_kind']}", itf.value, s["ref_ts"], s["ref_known_ts"],
           price=s["ref_price"], kind="swing")
        ev(c, f"{itf.value} break bar ({s['break_kind']})", itf.value, s["break_ts"], s["break_ts"] + itf.ms, kind="bar")
        ev(c, "impulse origin A (leg high bar)", itf.value, s["a_ts"], s["a_ts"] + itf.ms, price=s["a"], kind="bar")
        ev(c, "impulse end B (swing low)", itf.value, s["b_ts"], s["known_ts"], price=s["b"], kind="swing")
        for z in s["zones"]:
            if z["fvg_ts"] is not None:
                ev(c, f"{itf.value} bearish FVG (entry zone)", itf.value, z["fvg_ts"], z["fvg_known_ts"],
                   bottom=z["fvg_bottom"], top=z["fvg_top"], kind="fvg")
        if p.bias.enabled:
            last_bar = decision // btf.ms * btf.ms - btf.ms
            ev(c, f"last closed {btf.value} bar used for the bias", btf.value, last_bar, last_bar + btf.ms, kind="bar")
            sw = a.bias.swings
            if p.bias.mode == "swing_sequence" and not sw.empty:
                known = sw[sw["known_ts"] <= decision]
                for kind in ("high", "low"):
                    for r in known[known["kind"] == kind].sort_values("known_idx").tail(2).itertuples():
                        ev(c, f"{btf.value} swing {kind} (bias)", btf.value, int(r.ts), int(r.known_ts),
                           price=float(r.price), kind="swing")
        ev(c, "zone touch bar", ltf.value, s["touched_ts"], s["touched_ts"] + ltf.ms, kind="bar")
        if s["mss_ref_ts"] is not None:
            ev(c, "5m internal swing low broken by the MSS", ltf.value, s["mss_ref_ts"], s["mss_ref_known_ts"],
               price=s["mss_ref_price"], kind="swing")
        if s["retest_fvg_ts"] is not None:
            ev(c, "5m FVG of the MSS leg (limit level)", ltf.value, s["retest_fvg_ts"], s["retest_fvg_known_ts"],
               kind="fvg")
        ev(c, "signal bar (5m MSS close)" if p.entry.trigger == "mss" else "signal bar (touch)", ltf.value,
           s["trigger_ts"], decision, kind="bar")

    def _hypothetical_retest(self, j, h, l, limit, stop, target, ts) -> dict[str, Any]:
        """RESEARCH VIEW ONLY (uses bars after j): would the retest limit have filled?"""
        end = min(len(h), j + 1 + self.params.entry.retest_expiry_bars)
        for k in range(j + 1, end):
            if l[k] <= target:
                return {"outcome": "cancelled_target_first", "ts": int(ts[k])}
            if h[k] >= limit:
                return {"outcome": "would_fill", "ts": int(ts[k]), "same_bar_stop": bool(h[k] >= stop)}
        return {"outcome": "expired_unfilled", "ts": int(ts[end - 1]) if end > j + 1 else None}
