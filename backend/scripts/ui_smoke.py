"""HTTP smoke test of the flows the UI performs (run against a live server).

    backend/.venv/Scripts/python backend/scripts/ui_smoke.py [http://127.0.0.1:8000]

It exercises the same endpoints, in the same order, as the manual checklist in
docs/UI_SMOKE_CHECKLIST.md. It does NOT prove that the browser renders correctly.
Creates one user configuration version named smoke-<timestamp> (versions are immutable).
"""

from __future__ import annotations

import json
import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
ORIGIN = "http://localhost:5173"
results: list[tuple[bool, str]] = []


# keep-alive client (like a browser). NOTE: on this machine, requests sent with
# "Connection: close" (urllib, the Vite dev proxy) get large responses reset.
client = httpx.Client(base_url=BASE, timeout=300, headers={"Origin": ORIGIN})


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict | list]:
    r = client.request(method, path, json=body)
    return r.status_code, r.json()


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((bool(ok), name))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{' | ' + detail if detail else ''}")


code, system = call("GET", "/api/system")
check("API reachable, LIVE disabled", code == 200 and system["environments"]["LIVE"]["enabled"] is False)

# 1. strategy selection
code, cat = call("GET", "/api/strategies")
by_id = {s["strategy_id"]: s for s in cat}
check("strategy catalog lists plugins", {"ict_bearish_ote", "ma_crossover"} <= set(by_id), ", ".join(by_id))
ict = by_id["ict_bearish_ote"]
check("ICT exposes typed parameter specs + components", len(ict["param_specs"]) > 30 and len(ict["components"]) >= 6)

# 2. parameter change + validation
base = next(c for c in ict["configs"] if c["version"] == "v1-mss-close")["params"]
edited = json.loads(json.dumps(base))
edited["target"]["min_rr"] = 2.0
code, v = call("POST", "/api/strategies/ict_bearish_ote/validate", {"params": edited})
check("valid parameter edit accepted", v["ok"] and v["params_hash"] != "")
bad = json.loads(json.dumps(base))
bad["entry"]["retest_expiry_bars"] = 99           # inert: only used by entry.mode = fvg_retest
code, v = call("POST", "/api/strategies/ict_bearish_ote/validate", {"params": bad})
check("inert dependent parameter rejected by backend", not v["ok"] and v["errors"][0]["path"] == "entry.retest_expiry_bars")
code, v = call("POST", "/api/strategies/ict_bearish_ote/validate", {"params": {**base, "min_rrr": 3}})
check("unknown parameter rejected", not v["ok"] and v["errors"][0]["path"] == "min_rrr")

# 3. required component lock
required = [c for c in ict["components"] if c["role"] == "required"]
check("required components have no toggle (locked in UI)", required and all(c["toggle_path"] is None for c in required))
both_off = json.loads(json.dumps(base))
both_off["fvg"]["enabled"] = False
both_off["ote"]["enabled"] = False
code, v = call("POST", "/api/strategies/ict_bearish_ote/validate", {"params": both_off})
check("component rule enforced (FVG and OTE both off)", not v["ok"])

# 4. save new configuration version
name = f"smoke-{int(time.time())}"
code, saved = call("POST", "/api/strategies/ict_bearish_ote/configs",
                   {"version": name, "params": edited, "description": "ui smoke", "parent_version": "v1-mss-close"})
check("new configuration version saved", code == 200 and saved["source"] == "user", name)
code, _ = call("POST", "/api/strategies/ict_bearish_ote/configs", {"version": name, "params": edited})
check("existing version cannot be overwritten", code == 409)
code, _ = call("POST", "/api/strategies/ict_bearish_ote/configs", {"version": name + "-bad", "params": bad})
check("invalid configuration cannot be saved", code == 422)

# 5. ICT chart overlay
code, ds = call("GET", "/api/market/datasets")
five = next((d for d in ds if d["timeframe"] == "5m"), None)
check("5m data stored", five is not None)
end = five["last_ts"] + 300_000
code, rv = call("POST", "/api/research/analyze", {"strategy_id": "ict_bearish_ote", "strategy_version": "v1-mss-close",
                                                  "start_ts": end - 14 * 86_400_000, "end_ts": end})
layers = {}
for it in rv["view"]["items"]:
    layers[it["layer"]] = layers.get(it["layer"], 0) + 1
check("ICT research view returns overlays", code == 200 and {"swings", "structure", "fvg_bear"} <= set(layers), str(layers))
check("every overlay item carries known_ts", all(it["known_ts"] is not None for it in rv["view"]["items"]))
check("three chart timeframes", set(rv["view"]["timeframes"]) == {"5m", "15m", "4H"})

# 6. backtest + inspector + audit
start = end - 150 * 86_400_000
runs = {}
for sid, ver in (("ict_bearish_ote", "v1-mss-close"), ("ma_crossover", "v1")):
    code, r = call("POST", "/api/backtests", {"strategy_id": sid, "strategy_version": ver, "timeframe": "5m",
                                              "start_ts": start, "end_ts": end})
    check(f"backtest runs: {sid}:{ver}", code == 200, f"{r.get('metrics', {}).get('total_trades')} trades" if code == 200 else str(r))
    runs[sid] = r
ict_run = runs["ict_bearish_ote"]
insp = ict_run.get("inspection")
check("signal inspection recorded with the run", insp is not None and insp["n_signals"] == ict_run["metrics"]["signals_seen"],
      f"{insp['n_candidates']} candidates -> {insp['n_signals']} signals" if insp else "")
code, audit = call("GET", f"/api/backtests/{ict_run['run_id']}/audit")
check("trade audit passes (no look-ahead)", code == 200 and audit["all_checks_passed"], f"{audit.get('n_trades')} trades")
code, insp2 = call("POST", "/api/research/inspect", {"strategy_id": "ict_bearish_ote", "strategy_version": "v1-mss-close",
                                                     "start_ts": start, "end_ts": end})
check("inspect endpoint agrees with the run record", code == 200 and insp2["inspection"]["funnel"] == insp["funnel"])

# 7. result comparison
a, b = ict_run["conditions"], runs["ma_crossover"]["conditions"]
same = all(a[k] == b[k] for k in ("inst_id", "timeframe", "start_ts", "end_ts", "initial_capital", "costs", "risk",
                                  "data_fingerprint"))
check("comparison: identical experiment conditions across strategies", same)
code, listed = call("GET", "/api/backtests")
check("runs listed for comparison", code == 200 and {ict_run["run_id"], runs["ma_crossover"]["run_id"]} <= {r["run_id"] for r in listed})

# CORS as the browser needs it
pre = client.options("/api/backtests", headers={"Access-Control-Request-Method": "POST",
                                                "Access-Control-Request-Headers": "content-type"})
check("CORS preflight for the dev UI origin", pre.status_code == 200)

failed = [n for ok, n in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)
