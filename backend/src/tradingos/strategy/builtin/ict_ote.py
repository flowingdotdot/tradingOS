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
from pydantic import BaseModel, Field, model_validator

from tradingos.domain.timeframes import Timeframe
from tradingos.features.displacement import displacement
from tradingos.features.fvg import detect_fvgs
from tradingos.features.mtf import map_series_to_ltf, resample
from tradingos.features.ote import DealingRange, intersect
from tradingos.features.structure import structure_events, swing_sequence_bias
from tradingos.features.swings import detect_swings
from tradingos.strategy.base import Strategy, empty_signals

# ---------------------------------------------------------------------------- params


class SwingParams(BaseModel):
    k_left: int = Field(3, ge=1, le=20)
    k_right: int = Field(3, ge=1, le=20)
    internal_k: int = Field(3, ge=1, le=20, description="k (left=right) for 5m internal structure / MSS")
    require_contiguous: bool = True


class DisplacementParams(BaseModel):
    enabled: bool = True
    method: Literal["median_body", "atr"] = "median_body"
    lookback: int = Field(20, ge=3)
    mult: float = Field(1.5, gt=0)
    atr_period: int = Field(14, ge=2)
    atr_mult: float = Field(1.2, gt=0)
    min_body_ratio: float | None = Field(None, gt=0, le=1)


class BiasParams(BaseModel):
    enabled: bool = True
    timeframe: Literal["1H", "4H"] = "4H"
    mode: Literal["swing_sequence", "last_break"] = "swing_sequence"
    recheck_at_trigger: bool = True


class ImpulseParams(BaseModel):
    timeframe: Literal["5m", "15m", "1H"] = "15m"
    break_kinds: list[Literal["BOS", "MSS"]] = ["BOS", "MSS"]
    require_displacement: bool = True


class FvgParams(BaseModel):
    enabled: bool = True
    min_size_mode: Literal["atr", "none"] = "atr"
    min_size_atr: float = Field(0.1, ge=0)
    atr_period: int = Field(14, ge=2)
    require_displacement_middle: bool = False
    invalidation: Literal["close", "wick"] = "close"


class OteParams(BaseModel):
    enabled: bool = True
    low: float = Field(0.62, gt=0, lt=1)
    high: float = Field(0.79, gt=0, lt=1)
    ref: float = Field(0.705, gt=0, lt=1)

    @model_validator(mode="after")
    def _order(self) -> OteParams:
        if not self.low < self.high:
            raise ValueError("ote.low must be < ote.high")
        return self


class EntryParams(BaseModel):
    trigger: Literal["mss", "touch"] = "mss"
    mode: Literal["mss_close", "fvg_retest"] = "mss_close"
    require_displacement_in_mss: bool = True
    setup_expiry_bars: int = Field(96, ge=1, description="impulse-TF bars from impulse known to zone touch")
    trigger_expiry_bars: int = Field(36, ge=1, description="5m bars from zone touch to trigger")
    retest_expiry_bars: int = Field(12, ge=1, description="5m bars a retest limit order stays open")


class StopParams(BaseModel):
    mode: Literal["retracement_high", "impulse_high"] = "retracement_high"
    buffer_bps: float = Field(5.0, ge=0)


class TargetParams(BaseModel):
    mode: Literal["impulse_low"] = "impulse_low"
    min_rr: float = Field(1.5, gt=0)


class IctOteParams(BaseModel):
    base_timeframe: Literal["5m"] = "5m"
    swing: SwingParams = SwingParams()
    displacement: DisplacementParams = DisplacementParams()
    bias: BiasParams = BiasParams()
    impulse: ImpulseParams = ImpulseParams()
    fvg: FvgParams = FvgParams()
    ote: OteParams = OteParams()
    entry: EntryParams = EntryParams()
    stop: StopParams = StopParams()
    target: TargetParams = TargetParams()

    @model_validator(mode="after")
    def _consistency(self) -> IctOteParams:
        if not (self.fvg.enabled or self.ote.enabled):
            raise ValueError("at least one of fvg.enabled / ote.enabled is required to define an entry zone")
        if self.entry.mode == "fvg_retest" and self.entry.trigger != "mss":
            raise ValueError("entry.mode=fvg_retest requires entry.trigger=mss")
        return self


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
    strategy_id = "ict_bearish_ote"
    description = "Bearish OTE Continuation (ICT-inspired research hypothesis; unvalidated)."
    Params = IctOteParams
    params: IctOteParams
    required_timeframe = "5m"

    def warmup_bars(self) -> int:
        p = self.params
        base = Timeframe(p.base_timeframe).ms
        need = Timeframe(p.impulse.timeframe).ms * 100
        if p.bias.enabled:
            need = max(need, Timeframe(p.bias.timeframe).ms * 60)
        return need // base

    def generate_signals(self, bars: pd.DataFrame) -> pd.DataFrame:
        return self.analyze(bars).signals  # type: ignore[return-value]

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

        a = Analysis(base=base, impulse=imp, bias=bias, bias_on_base=bias_on_base)
        impulses = self._impulses(imp)
        a.setups, a.signals = self._run_setups(bars, base_tf, base, imp, impulses, bias_on_base)
        return a

    # ----------------------------------------------------------------- impulses
    def _impulses(self, imp: TfFeatures) -> list[dict[str, Any]]:
        """Bearish impulses A->B on the impulse timeframe (docs §2.10)."""
        p = self.params
        ev = imp.structure
        if ev.empty:
            return []
        ev = ev[(ev["kind"].isin(p.impulse.break_kinds)) & (ev["direction"] == -1)]
        if p.impulse.require_displacement and p.displacement.enabled:
            ev = ev[ev["has_displacement"]]
        lows = imp.swings[imp.swings["kind"] == "low"].sort_values("idx")
        h = imp.bars["high"].to_numpy()
        ts = imp.bars["ts"].to_numpy(dtype="int64")
        low_idx = lows["idx"].to_numpy()
        by_b: dict[int, dict[str, Any]] = {}
        for e in ev.itertuples():
            k = np.searchsorted(low_idx, e.idx, side="left")
            if k >= len(lows):
                continue  # impulse end not formed / not confirmed within data
            b = lows.iloc[k]
            a_idx, a_px = int(e.leg_extreme_idx), float(e.leg_extreme_price)
            if np.max(h[a_idx + 1 : int(b.known_idx) + 1], initial=-np.inf) > a_px:
                continue  # price exceeded the impulse origin before the impulse completed
            cand = {
                "break_idx": int(e.idx), "break_ts": int(e.ts), "break_kind": e.kind,
                "a_idx": a_idx, "a_ts": int(ts[a_idx]), "a": a_px,
                "b_idx": int(b.idx), "b_ts": int(b.ts), "b": float(b.price),
                "b_known_idx": int(b.known_idx), "known_ts": int(b.known_ts),
            }
            prev = by_b.get(cand["b_idx"])
            # Consecutive breaks resolving to the same impulse end: keep the highest origin A.
            if prev is None or cand["a"] > prev["a"]:
                by_b[cand["b_idx"]] = cand
        return sorted(by_b.values(), key=lambda x: (x["known_ts"], x["b_idx"]))

    def _zones(self, imp: TfFeatures, im: dict[str, Any]) -> tuple[list[dict[str, Any]], tuple[float, float]]:
        p = self.params
        dr = DealingRange(im["a"], im["b"])
        ote = dr.ote(p.ote.low, p.ote.high)
        zones: list[dict[str, Any]] = []
        if p.fvg.enabled:
            f = imp.fvgs
            if not f.empty:
                f = f[(f["direction"] == -1) & (f["first_idx"] >= im["a_idx"]) & (f["known_idx"] <= im["b_idx"])]
                ts = imp.bars["ts"].to_numpy(dtype="int64")
                for r in f.itertuples():
                    inval_known = int(ts[r.invalidated_idx] + imp.tf.ms) if r.invalidated_idx >= 0 else None
                    if inval_known is not None and inval_known <= im["known_ts"]:
                        continue  # already invalidated when the impulse became known
                    zone = intersect((r.bottom, r.top), ote) if p.ote.enabled else (r.bottom, r.top)
                    if zone is not None:
                        zones.append({"bottom": zone[0], "top": zone[1], "fvg_ts": int(r.ts),
                                      "fvg_bottom": float(r.bottom), "fvg_top": float(r.top),
                                      "fvg_known_ts": int(r.known_ts), "inval_known_ts": inval_known})
        else:
            zones.append({"bottom": ote[0], "top": ote[1], "fvg_ts": None, "fvg_bottom": None, "fvg_top": None,
                          "fvg_known_ts": None, "inval_known_ts": None})
        return zones, ote

    # -------------------------------------------------------------- state machine
    def _run_setups(self, bars: pd.DataFrame, base_tf: Timeframe, base: TfFeatures, imp: TfFeatures,
                    impulses: list[dict[str, Any]], bias5: np.ndarray) -> tuple[list[dict], pd.DataFrame]:
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

        def close_setup(s: dict[str, Any], status: str, j: int) -> None:
            s["status"] = status
            s["end_ts"] = int(ts[j] + base_tf.ms)

        for j in range(n):
            t_close = int(ts[j] + base_tf.ms)
            s = active
            if s is not None and ts[j] >= s["known_ts"]:
                done = self._step(s, j, ts, o, h, l, c, base_tf, mss_at, fvg5, bias5, buf, sig, warm, n)
                if done:
                    active = None
            # Activate impulses that became known at this bar's close (they watch from j+1).
            while next_imp < len(impulses) and impulses[next_imp]["known_ts"] <= t_close:
                im = impulses[next_imp]
                next_imp += 1
                rec = self._new_setup(im, imp, bars, ts, l, base_tf, len(setups))
                setups.append(rec)
                if active is not None:
                    close_setup(active, "superseded", j)
                    active["reason"] = f"newer impulse #{rec['id']} became known"
                    active = None
                if bias5[j] != -1:
                    close_setup(rec, "bias_not_bearish", j)
                elif not rec["zones"]:
                    close_setup(rec, "no_zone", j)
                else:
                    active = rec
        if active is not None:
            active["status"] = "open_at_end"
        return setups, sig

    def _new_setup(self, im: dict, imp: TfFeatures, bars: pd.DataFrame, ts: np.ndarray, l: np.ndarray,
                   base_tf: Timeframe, sid: int) -> dict[str, Any]:
        p = self.params
        zones, ote = self._zones(imp, im)
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
            if e is None or e.ref_ts < s["b5_ts"]:
                return False  # MSS must break an internal swing formed after the impulse low
            if p.entry.require_displacement_in_mss and p.displacement.enabled and not e.has_displacement:
                return False
            s["mss_ref_ts"], s["mss_ref_price"] = int(e.ref_ts), float(e.ref_price)
        s["trigger_ts"], s["trigger_known_ts"] = int(ts[j]), t_close

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
        sig.loc[j, "reason"] = (
            f"setup#{s['id']} {s['break_kind']} impulse A={s['a']:.1f} B={s['b']:.1f}; "
            f"{'MSS' if p.entry.trigger == 'mss' else 'touch'} trigger; RR {rr:.2f}"
        )
        if p.entry.mode == "fvg_retest":
            sig.loc[j, "limit_price"] = entry_ref
            sig.loc[j, "limit_expiry_bars"] = p.entry.retest_expiry_bars
            s["retest_outcome"] = self._hypothetical_retest(j, h, l, entry_ref, stop, target, ts)
        return finish("signal")

    def _hypothetical_retest(self, j, h, l, limit, stop, target, ts) -> dict[str, Any]:
        """RESEARCH VIEW ONLY (uses bars after j): would the retest limit have filled?"""
        end = min(len(h), j + 1 + self.params.entry.retest_expiry_bars)
        for k in range(j + 1, end):
            if l[k] <= target:
                return {"outcome": "cancelled_target_first", "ts": int(ts[k])}
            if h[k] >= limit:
                return {"outcome": "would_fill", "ts": int(ts[k]), "same_bar_stop": bool(h[k] >= stop)}
        return {"outcome": "expired_unfilled", "ts": int(ts[end - 1]) if end > j + 1 else None}
