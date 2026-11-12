"""Does the Chart Workspace overlay show the signals the backtest actually traded?

    backend/.venv/Scripts/python backend/scripts/chart_overlay_check.py [http://127.0.0.1:8000]

For every trade of the stored ICT runs (in-sample and already-observed; never OOS) it requests the
same chart window the UI requests for that trade, on each display timeframe, and checks that
  * the overlay contains an entry-signal marker on the trade's signal bar,
  * the overlay's SL / TP for that signal equal the trade's stop / target,
  * the window contains the entry and exit candles,
  * no candle from the sealed OOS window is returned.
It does NOT prove that the browser draws the overlay correctly.
"""

from __future__ import annotations

import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
TF_MS = {"5m": 300_000, "15m": 900_000, "4H": 14_400_000}
c = httpx.Client(base_url=BASE, timeout=300)

segs = c.get("/api/chart/segments").json()
split = segs["split"]
oos = (split["oos"]["start_ts"], split["oos"]["end_ts"]) if split and split["oos_sealed"] else None
print("allowed segments:", [(s["name"], time.strftime("%Y-%m-%d", time.gmtime(s["start_ts"] / 1000)),
                             time.strftime("%Y-%m-%d", time.gmtime(s["end_ts"] / 1000))) for s in segs["segments"]])

runs = [r for r in c.get("/api/backtests", params={"limit": 300}).json()
        if r["strategy_id"] == "ict_bearish_ote" and r["engine_version"] == "0.3.0" and r["summary"]["total_trades"]]
seen, picked = set(), []
for r in runs:                       # newest run per (version, period)
    key = (r["strategy_version"], r["start_ts"], r["end_ts"])
    if key not in seen:
        seen.add(key)
        picked.append(r)

total = ok = 0
slowest = 0.0
problems: list[str] = []
for r in picked:
    rt = c.get(f"/api/backtests/{r['run_id']}/trades").json()
    n_ok = 0
    for t in rt["trades"]:
        for tf in ("5m", "15m", "4H"):
            ms = TF_MS[tf]
            start = t["entry_ts"] - 260 * ms
            end = max(t["exit_ts"] + 160 * ms, start + 420 * ms)
            t0 = time.time()
            resp = c.post("/api/chart/window", json={"timeframe": tf, "start_ts": start, "end_ts": end,
                                                     "strategy_id": rt["strategy_id"],
                                                     "strategy_version": rt["strategy_version"]})
            slowest = max(slowest, time.time() - t0)
            total += 1
            if resp.status_code != 200:
                problems.append(f"{r['run_id']} #{t['trade_id']} {tf}: HTTP {resp.status_code} {resp.text[:120]}")
                continue
            d = resp.json()
            ts = d["candles"]["ts"]
            errs = []
            if oos and any(oos[0] <= x < oos[1] for x in ts):
                errs.append("OOS candle returned")
            if not (ts[0] <= t["entry_ts"] and ts[-1] + ms > t["entry_ts"]):
                errs.append("entry candle not in window")
            items = d["view"]["items"]
            sig = [it for it in items if it["layer"] == "signals" and it["type"] == "marker" and it["ts"] == t["signal_ts"]]
            if not sig:
                errs.append("no signal marker on the trade's signal bar")
            sl = [it["price"] for it in items if it["layer"] == "sltp" and it.get("label") == "SL" and it["t1"] == t["signal_ts"]]
            tp = [it["price"] for it in items if it["layer"] == "sltp" and it.get("label") == "TP" and it["t1"] == t["signal_ts"]]
            if not (sl and abs(sl[0] - t["stop"]) < 1e-6):
                errs.append("overlay SL != trade stop")
            if t["target"] is not None and not (tp and abs(tp[0] - t["target"]) < 1e-6):
                errs.append("overlay TP != trade target")
            if errs:
                problems.append(f"{r['run_id']} {rt['strategy_version']} #{t['trade_id']} {tf}: " + "; ".join(errs))
            else:
                ok += 1
                n_ok += 1
    print(f"{rt['strategy_version']:14s} run {r['run_id']} segment {rt['segment']}: {len(rt['trades'])} trades, "
          f"{n_ok}/{len(rt['trades']) * 3} windows consistent")

print(f"\n{ok}/{total} chart windows consistent with the backtest; slowest request {slowest:.2f}s")
for p in problems[:15]:
    print("  PROBLEM:", p)
sys.exit(1 if problems else 0)
