"""ICT v1 failure analysis on the IN-SAMPLE window (read-only diagnostics, no optimisation).

    backend/.venv/Scripts/python backend/scripts/ict_failure_analysis.py [--html]

Uses the stored IS runs (trades, rejections) and re-evaluates the SAME frozen v1 configurations to
obtain the strategy's own setup records. Everything goes through the guarded loader, so the
sealed OOS window cannot be touched. `--html` also writes docs/analysis/ict_v1_is_trades.html.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import UTC, datetime

import numpy as np

from tradingos.config import PROJECT_DIR, get_settings
from tradingos.domain.timeframes import Timeframe
from tradingos.features.common import atr
from tradingos.services.data_access import load_strategy_bars, research_split
from tradingos.storage.runs import RunRepository
from tradingos.strategy.configs import ConfigStore
from tradingos.strategy.registry import build_strategy

M5 = Timeframe.M5
SID = "ict_bearish_ote"
f = lambda ms: datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M") if ms else "-"
med = lambda xs: float(np.median(xs)) if len(xs) else float("nan")
q = lambda xs, p: float(np.percentile(xs, p)) if len(xs) else float("nan")


def latest_is_run(repo: RunRepository, version: str) -> dict:
    for r in repo.list(500):
        if r["strategy_id"] == SID and r["strategy_version"] == version and r["engine_version"] == "0.3.0":
            full = repo.get(r["run_id"])
            if full.get("segment") == "IS":
                return full
    raise SystemExit(f"no stored IS run for {version}")


def describe(name: str, rows: list[dict], keys: list[str]) -> None:
    print(f"  {name:34s} n={len(rows):4d} | " + "  ".join(
        f"{k}={med([r[k] for r in rows if r.get(k) is not None]):.3g}" for k in keys))


def main() -> None:
    s = get_settings()
    split = research_split(s)
    assert split is not None and split.oos_sealed
    start, end = split.is_start, split.is_end
    repo, configs = RunRepository(s.db_path), ConfigStore(s.db_path, s.strategy_config_dir)
    runs = {v: latest_is_run(repo, v) for v in ("v1-mss-close", "v1-fvg-retest")}
    out: dict = {"window": [f(start), f(end)], "runs": {v: r["run_id"] for v, r in runs.items()}}

    analyses = {}
    for v in runs:
        cfg = configs.get(SID, v)
        strat = build_strategy(SID, cfg.params)
        bars, ctx = load_strategy_bars(s, "BTC-USDT-SWAP", M5, start, end, strat.warmup_bars(), "failure analysis")
        assert ctx["segment"] == "IS"
        analyses[v] = (strat, bars.reset_index(drop=True), strat.analyze(bars))
    strat, bars, a = analyses["v1-mss-close"]
    p = strat.params
    ts5 = bars["ts"].to_numpy()
    h5, l5 = bars["high"].to_numpy(), bars["low"].to_numpy()
    imp = a.impulse
    its = imp.bars["ts"].to_numpy()
    atr15 = atr(imp.bars, 14)
    buf = p.stop.buffer_bps / 10_000
    in_window = lambda x: x["known_ts"] >= start

    # ------------------------------------------------------------------ per-setup features
    def features(x: dict) -> dict:
        rng = x["a"] - x["b"]
        k = int(np.searchsorted(its, x["known_ts"] - imp.tf.ms))        # 15m bar whose close made B known
        zone_lo = min((z["bottom"] for z in x["zones"]), default=None)
        d = {
            "id": x["id"], "status": x["status"], "known_ts": x["known_ts"], "a": x["a"], "b": x["b"],
            "a_ts": x["a_ts"], "b_ts": x["b_ts"], "break_kind": x["break_kind"],
            "range_pct": rng / x["b"] * 100, "range_atr": rng / atr15[min(k, len(atr15) - 1)],
            "impulse_bars_15m": (x["b_ts"] - x["a_ts"]) // imp.tf.ms,
            "n_zones": len(x["zones"]),
            "zone_width_pct": (max(z["top"] for z in x["zones"]) - zone_lo) / x["b"] * 100 if x["zones"] else None,
            "zone_bottom_ratio": (zone_lo - x["b"]) / rng if zone_lo is not None else None,
            "fvg_size_range": med([(z["fvg_top"] - z["fvg_bottom"]) / rng for z in x["zones"] if z["fvg_top"]]) if x["zones"] else None,
            "hours_known_to_touch": (x["touched_ts"] - x["known_ts"]) / 3.6e6 if x["touched_ts"] else None,
            "bias_at_known": x["bias_at_known"], "trigger_ts": x["trigger_ts"], "touched_ts": x["touched_ts"],
        }
        if zone_lo is not None:
            # RR available AT THE TOUCH with a structural stop above A (no hindsight)
            stop_a = x["a"] * (1 + buf)
            d["rr_at_touch_stop_above_A"] = (zone_lo - x["b"]) / (stop_a - zone_lo)
        if x["trigger_ts"] is not None and x["entry_ref"] is not None and x["stop"] is not None:
            e, st = x["entry_ref"], x["stop"]
            d.update({
                "entry": e, "stop": st, "retr_high": x["retr_high"],
                "entry_ratio": (e - x["b"]) / rng, "high_ratio": (x["retr_high"] - x["b"]) / rng,
                "giveback_ratio": (x["retr_high"] - e) / rng,
                "stop_dist_pct": (st - e) / e * 100, "target_dist_pct": (e - x["b"]) / e * 100,
                "rr_at_mss_close": x["rr"], "bars_touch_to_mss": (x["trigger_ts"] - x["touched_ts"]) // M5.ms,
                # same final stop, entry at the zone edge: what waiting for the MSS cost (uses the later stop)
                "rr_at_zone_edge_same_stop": (zone_lo - x["b"]) / (st - zone_lo) if st > zone_lo else None,
            })
        return d

    setups = [features(x) for x in a.setups if in_window(x)]
    by_trigger = {d["trigger_ts"]: d for d in setups if d["trigger_ts"] is not None}
    run = runs["v1-mss-close"]
    traded = {t["signal_ts"]: t for t in run["trades"]}
    eng_rej = {r["ts"] - M5.ms: r for r in run["rejections"] if r["reason"] == "net_rr_below_min"}
    for d in setups:
        d["group"] = ("traded" if d["trigger_ts"] in traded else "engine_net_rr_reject" if d["trigger_ts"] in eng_rej
                      else d["status"])

    # ================================================================ A. MSS confirmation delay
    print("\n=== A. MSS confirmation delay (v1-mss-close, IS) ===")
    trig = [d for d in setups if d.get("entry") is not None]
    print(f"setups that reached the zone: {sum(1 for d in setups if d['touched_ts'])}; with a qualifying 5m MSS: {len(trig)}")
    keys = ["entry_ratio", "high_ratio", "giveback_ratio", "bars_touch_to_mss", "stop_dist_pct", "target_dist_pct",
            "rr_at_mss_close", "rr_at_zone_edge_same_stop", "rr_at_touch_stop_above_A", "range_pct"]
    print("  medians ->", ", ".join(keys))
    groups = {}
    for g in ("traded", "engine_net_rr_reject", "rejected_rr", "rejected_target", "bias_lost"):
        groups[g] = [d for d in trig if d["group"] == g]
        describe(g, groups[g], keys)
    describe("ALL triggered", trig, keys)
    cost_rt = 2 * 0.05 + 2 * 0.02     # round trip: 2 x taker fee + 2 x slippage, in % of price
    tiny = [d for d in trig if d["stop_dist_pct"] < cost_rt]
    print(f"  stop distance below round-trip cost ({cost_rt:.2f}% of price): {len(tiny)}/{len(trig)}; "
          f"below 2x cost: {sum(1 for d in trig if d['stop_dist_pct'] < 2 * cost_rt)}/{len(trig)}")
    gb = [d["giveback_ratio"] for d in trig]
    print(f"  give-back from retracement high to MSS close (share of impulse range): "
          f"p25 {q(gb, 25):.2f}  median {med(gb):.2f}  p75 {q(gb, 75):.2f}")
    below_zone = sum(1 for d in trig if d["entry_ratio"] < 0.62)
    print(f"  MSS close already BELOW the OTE band (ratio < 0.62): {below_zone}/{len(trig)}; "
          f"below equilibrium (< 0.5): {sum(1 for d in trig if d['entry_ratio'] < 0.5)}/{len(trig)}")
    net = [float(r["detail"].split()[2]) for r in eng_rej.values()]
    gross = [by_trigger[k]["rr_at_mss_close"] for k in eng_rej if k in by_trigger]
    print(f"  engine net-RR rejections: gross RR median {med(gross):.2f} -> net RR median {med(net):.2f}")
    out["A"] = {g: {k: med([d[k] for d in rows if d.get(k) is not None]) for k in keys} | {"n": len(rows)}
                for g, rows in groups.items()}

    # ======================================================================= B. 4H bias
    print("\n=== B. 4H bias (IS) ===")
    b = a.bias_on_base[ts5 >= start]
    share = {k: float((b == k).mean() * 100) for k in (-1, 0, 1)}
    flips = int((np.diff(b) != 0).sum())
    runs_len = np.diff(np.concatenate([[0], np.nonzero(np.diff(b) != 0)[0] + 1, [len(b)]])) * 5 / 60
    print(f"  time share: bearish {share[-1]:.1f}%  neutral {share[0]:.1f}%  bullish {share[1]:.1f}%; "
          f"state changes {flips}, median state duration {med(runs_len):.0f} h")
    cands = [c for c in a.inspection.candidates if c["ts"] >= start]
    distinct = [c for c in cands if c["stages"]["distinct_impulse"]["status"] == "PASS"]
    by_bias = Counter(c["stages"]["htf_bias"]["value"]["bias"] for c in distinct)
    zone_ok = Counter(c["stages"]["htf_bias"]["value"]["bias"] for c in distinct
                      if c["stages"]["ote_intersection"]["status"] == "PASS")
    print(f"  distinct 15m impulses: {len(distinct)} -> bearish {by_bias[-1]}, neutral {by_bias[0]}, bullish {by_bias[1]}")
    print(f"  ... with a valid OTE x FVG zone: bearish {zone_ok[-1]}, neutral {zone_ok[0]}, bullish {zone_ok[1]} "
          f"(the 4H filter removes {zone_ok[0] + zone_ok[1]} of {sum(zone_ok.values())} zone-valid setups)")
    sw = a.bias.swings
    lag_h = ((sw["known_ts"] - sw["ts"]) / 3.6e6)
    print(f"  4H swing recognition lag: {lag_h.min():.0f} h (constant: k_right=3 bars + bar close); "
          f"a bias change needs 2 highs + 2 lows -> uses swings up to days old")
    # how old is the information behind the bias at each impulse?
    ages = []
    sk = sw.sort_values("known_ts")
    kts = sk["known_ts"].to_numpy()
    for c in distinct:
        t = c["info"].get("impulse_known_ts")
        if t is None:
            continue
        i = np.searchsorted(kts, t, side="right")
        if i >= 4:
            ages.append((t - sk["ts"].to_numpy()[i - 4:i].min()) / 3.6e6)
    print(f"  age of the OLDEST of the last 4 known 4H swings at impulse time: median {med(ages):.0f} h, p90 {q(ages, 90):.0f} h")
    out["B"] = {"time_share": share, "impulses_by_bias": dict(by_bias), "zone_valid_by_bias": dict(zone_ok)}

    # ================================================================ C. impulse / FVG quality
    print("\n=== C. Impulse / FVG quality ===")
    ckeys = ["range_pct", "range_atr", "impulse_bars_15m", "n_zones", "zone_width_pct", "zone_bottom_ratio",
             "fvg_size_range", "hours_known_to_touch"]
    print("  medians ->", ", ".join(ckeys))
    zone_valid = [d for d in setups if d["n_zones"] > 0 and d["bias_at_known"] == -1]
    describe("bias ok + zone (entered funnel)", zone_valid, ckeys)
    describe("reached the zone", [d for d in zone_valid if d["touched_ts"]], ckeys)
    describe("never reached (impulse_extended)", [d for d in zone_valid if d["status"] == "impulse_extended"], ckeys)
    describe("triggered", trig, ckeys)
    describe("traded", groups["traded"], ckeys)
    small = sum(1 for d in zone_valid if d["range_pct"] < 0.5)
    print(f"  impulses smaller than 0.5% of price: {small}/{len(zone_valid)}; smaller than 1 x ATR(15m): "
          f"{sum(1 for d in zone_valid if d['range_atr'] < 1)}/{len(zone_valid)}; "
          f"smaller than 3 x ATR: {sum(1 for d in zone_valid if d['range_atr'] < 3)}/{len(zone_valid)}")
    kinds = Counter(d["break_kind"] for d in zone_valid)
    print(f"  impulse started by: {dict(kinds)}; B confirmed on the break bar itself or the next bar: "
          f"{sum(1 for x in a.setups if in_window(x) and x['b_idx'] - x['break_idx'] <= 1)}/{sum(1 for x in a.setups if in_window(x))}")

    # trades: what happened after entry / after the stop
    print("\n  --- trades (v1-mss-close) ---")
    rows = []
    for t in run["trades"]:
        d = by_trigger[t["signal_ts"]]
        i_exit = int(np.searchsorted(ts5, t["exit_ts"]))
        hh, ll = h5[i_exit + 1 : i_exit + 1 + 288], l5[i_exit + 1 : i_exit + 1 + 288]
        hit_t = np.nonzero(ll <= t["target"])[0]
        hit_a = np.nonzero(hh > d["a"])[0]
        first_t = int(hit_t[0]) if len(hit_t) else 10**9
        first_a = int(hit_a[0]) if len(hit_a) else 10**9
        after = "target_first" if first_t < first_a else "A_exceeded_first" if first_a < first_t else "neither_24h"
        stop_d = t["stop"] - t["entry_price"]
        rows.append({**d, "r": t["r_multiple"], "exit": t["exit_reason"], "bars_held": t["bars_held"],
                     "mfe_r": t["mfe"] / stop_d, "after_stop_24h": after if t["exit_reason"] == "stop" else "-",
                     "entry_ts": t["entry_ts"], "exit_ts": t["exit_ts"], "net_rr": t["planned_net_rr"]})
    losers = [r for r in rows if r["exit"] == "stop"]
    print(f"  losers {len(losers)}: MFE before the stop (in units of stop distance): median {med([r['mfe_r'] for r in losers]):.2f}, "
          f"reached >= 1.0: {sum(1 for r in losers if r['mfe_r'] >= 1)}, reached >= 0.5: {sum(1 for r in losers if r['mfe_r'] >= 0.5)}")
    print(f"  bars held before the stop: median {med([r['bars_held'] for r in losers]):.0f} (5m bars); "
          f"stopped within 12 bars (1 h): {sum(1 for r in losers if r['bars_held'] <= 12)}")
    print(f"  24 h after the stop-out: {dict(Counter(r['after_stop_24h'] for r in losers))}")
    print(f"  entry position in the impulse (0 = B, 1 = A): median {med([r['entry_ratio'] for r in rows]):.2f}; "
          f"stop distance %: median {med([r['stop_dist_pct'] for r in rows]):.2f}")
    for r in rows:
        print(f"   {f(r['entry_ts'])} {r['break_kind']} rng {r['range_pct']:.2f}% ({r['range_atr']:.1f} ATR) entry@{r['entry_ratio']:.2f} "
              f"high@{r['high_ratio']:.2f} stop {r['stop_dist_pct']:.2f}% netRR {r['net_rr']:.2f} held {r['bars_held']:3d} "
              f"MFE {r['mfe_r']:.2f}R -> {r['exit']} {r['r']:+.2f}R  after: {r['after_stop_24h']}")
    out["C_trades"] = [{k: (v if not isinstance(v, (np.floating, np.integer)) else float(v)) for k, v in r.items()} for r in rows]

    # ============================================================ D. candidates across variants
    print("\n=== D. Same candidates across the two variants ===")
    key = lambda x: (x["a_ts"], x["b_ts"])
    per_variant = {}
    for v, (st, _, an) in analyses.items():
        tr = {t["signal_ts"]: t for t in runs[v]["trades"]}
        per_variant[v] = {key(x): tr[x["trigger_ts"]] for x in an.setups if in_window(x) and x["trigger_ts"] in tr}
    k_m, k_r = set(per_variant["v1-mss-close"]), set(per_variant["v1-fvg-retest"])
    both = sorted(k_m & k_r)
    print(f"  trades: mss-close {len(k_m)}, fvg-retest {len(k_r)}; same impulse traded by both: {len(both)}, "
          f"only mss-close: {len(k_m - k_r)}, only fvg-retest: {len(k_r - k_m)}")
    for k in both:
        m, r = per_variant["v1-mss-close"][k], per_variant["v1-fvg-retest"][k]
        print(f"   impulse B {f(k[1])}: mss {m['entry_price']:.1f} -> {m['exit_reason']} {m['r_multiple']:+.2f}R | "
              f"retest {r['entry_price']:.1f} ({(r['entry_ts'] - r['signal_ts']) // M5.ms} bars after signal) -> {r['exit_reason']} {r['r_multiple']:+.2f}R")
    out["D"] = {"both": len(both), "only_mss": len(k_m - k_r), "only_retest": len(k_r - k_m)}

    # ==================================================== E. code vs. ICT definition diagnostics
    print("\n=== E. Definition / implementation diagnostics ===")
    st_ev = a.base.structure
    bos5 = st_ev[(st_ev["kind"] == "BOS") & (st_ev["direction"] == -1)]
    bos_ts, bos_ref = bos5["ts"].to_numpy(), bos5["ref_ts"].to_numpy()
    no_trig = [x for x in a.setups if in_window(x) and x["touched_ts"] and x["trigger_ts"] is None
               and x["status"] in ("invalidated", "expired_no_trigger", "superseded")]
    had_bos = 0
    for x in no_trig:
        m = (bos_ts >= x["touched_ts"]) & (bos_ts < x["end_ts"]) & (bos_ref >= x["b5_ts"])
        had_bos += bool(m.any())
    print(f"  touched but never triggered: {len(no_trig)}; of these, a 5m bearish close-break of a post-B swing DID occur "
          f"but was classified BOS (5m trend was not bullish) so it did not count as MSS: {had_bos}")
    skipped = Counter()
    for x in a.setups:
        if in_window(x) and x["touched_ts"]:
            skipped["mss_breaking_pre_B_swing"] += x["mss_skipped_old_ref"]
            skipped["mss_without_displacement"] += x["mss_skipped_no_displacement"]
    print(f"  5m MSS events seen after a touch but not accepted: {dict(skipped)}")
    ext = [x for x in a.setups if in_window(x) and x["status"] == "impulse_extended"]
    waited = [(x["end_ts"] - x["known_ts"]) / 3.6e6 for x in ext]
    print(f"  impulse_extended (new low below B before the zone): {len(ext)}; median time from 'B known' to the new low: {med(waited):.1f} h "
          f"-> B (first confirmed swing low after the break) is often NOT the end of the move")
    disp = [c["stages"]["displacement"]["status"] for c in cands]
    print(f"  displacement requirement passes {disp.count('PASS')}/{disp.count('PASS') + disp.count('FAIL')} "
          f"of bearish breaks -> it barely filters")
    print(json.dumps({"summary_for_report": out}, default=float)[:0])

    if "--html" in sys.argv:
        write_html(analyses, runs, per_variant, start)


# ------------------------------------------------------------------------------ HTML charts
def write_html(analyses, runs, per_variant, start) -> None:
    W, H, PADL, PADR, PADT, PADB = 1180, 420, 64, 150, 18, 26
    key = lambda x: (x["a_ts"], x["b_ts"])
    other = {"v1-mss-close": "v1-fvg-retest", "v1-fvg-retest": "v1-mss-close"}
    parts = ["""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>ICT v1 IS 거래 검증</title>
<style>body{background:#0d1117;color:#d6dde6;font:13px system-ui,'Malgun Gothic',sans-serif;margin:16px}
h1{font-size:18px}h2{font-size:14px;margin:26px 0 4px}.m{color:#7d8a99;font:12px ui-monospace,Consolas,monospace}
svg{background:#131a23;border:1px solid #243040;display:block}.note{border:1px solid #3b82f6;padding:8px 12px;border-radius:5px;margin:8px 0}
table{border-collapse:collapse;font-size:12px}td,th{border-bottom:1px solid #243040;padding:3px 8px;text-align:left}</style></head><body>
<h1>ICT Bearish OTE v1 — IS(표본 내) 거래별 시각적 검증</h1>
<div class="note">가상의 과거 시뮬레이션이며 미래 성과를 보장하지 않습니다. 구간: IS 2020-01-01 ~ 2024-09-30 (UTC). OOS는 봉인 상태로 포함하지 않습니다.<br>
캔들 5m · 회색 점선 = 임펄스 A(위)/B(아래) · 노란 띠 = OTE 0.62~0.79 · 보라 띠 = 진입 구역(OTE∩FVG) · 주황 선 = MSS가 돌파한 5m 스윙 저점 ·
파란 세로선 = 신호 봉(5m MSS 종가) · 파란 가로선 = 체결가 · 빨강 = 손절 · 초록 = 목표 · ● = 실제 청산.
스윙·구역은 발생 위치에 그려지지만 전략은 인식 시각 이후에만 사용합니다 (각 거래의 감사 결과 참조).</div>"""]
    for v, (strat, bars, an) in analyses.items():
        ts = bars["ts"].to_numpy()
        o, h, l, c = (bars[k].to_numpy() for k in ("open", "high", "low", "close"))
        tr = {t["signal_ts"]: t for t in runs[v]["trades"]}
        parts.append(f"<h1>{v} — run {runs[v]['run_id']} · 거래 {len(tr)}건</h1>")
        n = 0
        for x in an.setups:
            if x["known_ts"] < start or x["trigger_ts"] not in tr:
                continue
            t = tr[x["trigger_ts"]]
            n += 1
            i0 = max(0, int(np.searchsorted(ts, x["a_ts"])) - 12)
            i1 = min(len(ts) - 1, int(np.searchsorted(ts, t["exit_ts"])) + 24)
            if i1 - i0 > 700:
                i0 = i1 - 700
            lo = min(l[i0:i1 + 1].min(), t["target"], x["b"]) ; hi = max(h[i0:i1 + 1].max(), t["stop"], x["a"])
            pad = (hi - lo) * 0.04
            lo, hi = lo - pad, hi + pad
            nb = i1 - i0 + 1
            bw = (W - PADL - PADR) / nb
            X = lambda i: PADL + (i - i0 + 0.5) * bw
            Xt = lambda tms: X(min(max(int(np.searchsorted(ts, tms)), i0), i1))
            Y = lambda px: PADT + (hi - px) / (hi - lo) * (H - PADT - PADB)
            g = [f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}">']
            xr = W - PADR
            # OTE band and zones from "impulse known" onwards
            xk = Xt(x["known_ts"])
            g.append(f'<rect x="{xk:.1f}" y="{Y(x["ote_high"]):.1f}" width="{xr - xk:.1f}" height="{Y(x["ote_low"]) - Y(x["ote_high"]):.1f}" fill="rgba(240,180,41,0.13)"/>')
            for z in x["zones"]:
                g.append(f'<rect x="{xk:.1f}" y="{Y(z["top"]):.1f}" width="{xr - xk:.1f}" height="{max(1, Y(z["bottom"]) - Y(z["top"])):.1f}" fill="rgba(167,139,250,0.30)"/>')
            for px, lab in ((x["a"], "A"), (x["b"], "B")):
                g.append(f'<line x1="{PADL}" x2="{xr}" y1="{Y(px):.1f}" y2="{Y(px):.1f}" stroke="#9aa5b1" stroke-dasharray="5 4"/>'
                         f'<text x="{xr + 4}" y="{Y(px) + 4:.1f}" fill="#9aa5b1" font-size="11">{lab} {px:.1f}</text>')
            for i in range(i0, i1 + 1):
                up = c[i] >= o[i]
                col = "#26a69a" if up else "#ef5350"
                g.append(f'<line x1="{X(i):.1f}" x2="{X(i):.1f}" y1="{Y(h[i]):.1f}" y2="{Y(l[i]):.1f}" stroke="{col}"/>'
                         f'<rect x="{X(i) - bw * 0.35:.1f}" y="{Y(max(o[i], c[i])):.1f}" width="{max(1, bw * 0.7):.1f}" height="{max(1, abs(Y(o[i]) - Y(c[i]))):.1f}" fill="{col}"/>')
            if x["mss_ref_ts"]:
                g.append(f'<line x1="{Xt(x["mss_ref_ts"]):.1f}" x2="{Xt(x["trigger_ts"]):.1f}" y1="{Y(x["mss_ref_price"]):.1f}" y2="{Y(x["mss_ref_price"]):.1f}" stroke="#f0b429" stroke-width="2"/>')
            xs, xe, xx = Xt(x["trigger_ts"]), Xt(t["entry_ts"]), Xt(t["exit_ts"])
            g.append(f'<line x1="{xs:.1f}" x2="{xs:.1f}" y1="{PADT}" y2="{H - PADB}" stroke="#4ea1ff" stroke-dasharray="2 3"/>')
            if x["touched_ts"]:
                g.append(f'<text x="{Xt(x["touched_ts"]):.1f}" y="{PADT + 10}" fill="#a78bfa" font-size="10" text-anchor="middle">touch</text>')
            for px, col, lab in ((t["entry_price"], "#4ea1ff", "entry"), (t["stop"], "#ef5350", "stop"), (t["target"], "#26a69a", "target")):
                g.append(f'<line x1="{xe:.1f}" x2="{xx:.1f}" y1="{Y(px):.1f}" y2="{Y(px):.1f}" stroke="{col}" stroke-width="2"/>'
                         f'<text x="{xr + 4}" y="{Y(px) + 4:.1f}" fill="{col}" font-size="11">{lab} {px:.1f}</text>')
            g.append(f'<circle cx="{xx:.1f}" cy="{Y(t["exit_price"]):.1f}" r="5" fill="{"#26a69a" if t["net_pnl"] > 0 else "#f0b429"}" stroke="#fff"/>')
            for frac in (0, 0.25, 0.5, 0.75, 1):
                i = int(i0 + frac * (nb - 1))
                g.append(f'<text x="{X(i):.1f}" y="{H - 8}" fill="#7d8a99" font-size="10" text-anchor="middle">{f(int(ts[i]))[5:]}</text>')
            g.append("</svg>")
            k = key(x)
            same = per_variant[other[v]].get(k)
            cross = (f"같은 임펄스를 {other[v]}도 거래함 → {same['exit_reason']} {same['r_multiple']:+.2f}R" if same
                     else f"{other[v]}는 이 임펄스를 거래하지 않음")
            parts.append(
                f'<h2>#{t["trade_id"]} · 신호 {f(x["trigger_ts"])} UTC → 체결 {f(t["entry_ts"])} ({t.get("entry_type", "market")}) '
                f'→ 청산 {f(t["exit_ts"])} · {t["exit_reason"]} · {t["r_multiple"]:+.2f}R</h2>'
                f'<div class="m">{t["entry_reason"]} · 임펄스 {x["break_kind"]} A {f(x["a_ts"])} / B {f(x["b_ts"])} (인식 {f(x["known_ts"])}) · '
                f'터치 {f(x["touched_ts"])} · 체결가 {t["entry_price"]:.1f} 손절 {t["stop"]:.1f} 목표 {t["target"]:.1f} 청산가 {t["exit_price"]:.1f} · '
                f'순손익비 {t["planned_net_rr"]:.2f} · 수량 {t["qty"]} BTC · 수수료 {t["fees"]:.2f} · {cross}</div>' + "".join(g))
        assert n == len(tr), (v, n, len(tr))
    parts.append("</body></html>")
    path = PROJECT_DIR / "docs" / "analysis" / "ict_v1_is_trades.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts), encoding="utf-8")
    print(f"\nwrote {path} ({path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
