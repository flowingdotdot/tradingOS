"""Signal Inspector — a strategy-agnostic record of how each candidate signal was evaluated.

A plugin fills an `Inspection` FROM INSIDE its real decision logic (never by recomputing):
  * stages: the ordered conditions a candidate must pass
  * candidates: one record per candidate with a status per stage + the values that were computed
  * evidence: for candidates that became signals, every input used with its occurrence time `ts`
    and the time it became knowable `known_ts` (audited against the decision time)

Status values
  PASS            condition evaluated and satisfied
  FAIL            condition evaluated and not satisfied (reason recorded)
  NOT_APPLICABLE  condition does not apply (component switched off / not part of the strategy)
  NOT_EVALUATED   never evaluated because an earlier stage already failed
  PENDING         evaluation was still in progress when the data ended

Two different aggregations (do not confuse them):
  funnel      sequential: of the candidates that reached stage k (all earlier stages PASS/N.A.),
              how many passed it. Answers "where do candidates die?"
  conditions  independent: of the candidates for which the condition was actually evaluated
              (regardless of other stages), how many passed. Answers "how restrictive is this
              condition by itself?" — only meaningful for stages the strategy evaluates
              independently of earlier failures (flagged `independent`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PASS, FAIL, NA, NOT_EVALUATED, PENDING = "PASS", "FAIL", "NOT_APPLICABLE", "NOT_EVALUATED", "PENDING"
_THROUGH = (PASS, NA)


@dataclass(frozen=True)
class StageDef:
    id: str
    label: str
    description: str = ""
    independent: bool = False   # evaluated for every candidate that exists at that point, even if another stage failed


@dataclass
class Inspection:
    stages: list[StageDef]
    candidates: list[dict[str, Any]] = field(default_factory=list)

    def candidate(self, ts: int, known_ts: int, label: str, **info: Any) -> dict[str, Any]:
        c = {"id": len(self.candidates), "ts": int(ts), "known_ts": int(known_ts), "label": label, "info": info,
             "stages": {s.id: {"status": NOT_EVALUATED, "value": None, "reason": ""} for s in self.stages},
             "outcome": "", "decision_ts": None, "evidence": []}
        self.candidates.append(c)
        return c

    @staticmethod
    def mark(c: dict[str, Any], stage: str, status: str, value: Any = None, reason: str = "") -> None:
        if stage not in c["stages"]:
            raise KeyError(f"unknown inspection stage {stage!r}")
        c["stages"][stage] = {"status": status, "value": value, "reason": reason}

    @staticmethod
    def evidence(c: dict[str, Any], what: str, tf: str, ts: int, known_ts: int, **detail: Any) -> None:
        c["evidence"].append({"what": what, "tf": tf, "ts": int(ts), "known_ts": int(known_ts), **detail})

    # ------------------------------------------------------------------ aggregation
    def summarize(self, start: int | None = None, end: int | None = None,
                  include_candidates: bool = True, compact: bool = False) -> dict[str, Any]:
        """compact=True drops NOT_EVALUATED stage entries without a value from each candidate
        (a missing stage means NOT_EVALUATED) to keep stored run records small."""
        cands = [c for c in self.candidates
                 if (start is None or c["ts"] >= start) and (end is None or c["ts"] < end)]
        funnel, conditions = [], []
        alive = list(cands)
        for s in self.stages:
            st = [c["stages"][s.id]["status"] for c in alive]
            passed = [c for c in alive if c["stages"][s.id]["status"] in _THROUGH]
            reasons: dict[str, int] = {}
            for c in alive:
                x = c["stages"][s.id]
                if x["status"] == FAIL:
                    key = x["reason"].split(":")[0] or "failed"
                    reasons[key] = reasons.get(key, 0) + 1
            funnel.append({
                "stage": s.id, "label": s.label, "entered": len(alive), "passed": st.count(PASS),
                "not_applicable": st.count(NA), "failed": st.count(FAIL), "pending": st.count(PENDING),
                "not_evaluated": st.count(NOT_EVALUATED), "continue": len(passed),
                "fail_reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
            })
            alive = passed

            every = [c["stages"][s.id]["status"] for c in cands]
            ev = every.count(PASS) + every.count(FAIL)
            conditions.append({
                "stage": s.id, "label": s.label, "independent": s.independent,
                "evaluated": ev, "passed": every.count(PASS), "failed": every.count(FAIL),
                "pass_rate_pct": (every.count(PASS) / ev * 100) if ev else None,
                "not_applicable": every.count(NA), "not_evaluated": every.count(NOT_EVALUATED),
                "pending": every.count(PENDING),
            })
        outcomes: dict[str, int] = {}
        for c in cands:
            outcomes[c["outcome"] or "unknown"] = outcomes.get(c["outcome"] or "unknown", 0) + 1
        out = {
            "stages": [{"id": s.id, "label": s.label, "description": s.description, "independent": s.independent}
                       for s in self.stages],
            "n_candidates": len(cands), "n_signals": sum(1 for c in cands if c["outcome"] == "signal"),
            "funnel": funnel, "conditions": conditions,
            "outcomes": dict(sorted(outcomes.items(), key=lambda kv: -kv[1])),
        }
        if include_candidates:
            out["candidates"] = cands if not compact else [
                {**c, "stages": {k: v for k, v in c["stages"].items()
                                 if v["status"] != NOT_EVALUATED or v["value"] is not None}}
                for c in cands
            ]
        return out


def audit_evidence(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Check that every recorded input of a signal was knowable at its decision time."""
    decision = candidate["decision_ts"]
    rows = []
    for e in candidate["evidence"]:
        rows.append({**e, "decision_ts": decision,
                     "lag_ms": e["known_ts"] - e["ts"],
                     "ok": decision is not None and e["known_ts"] <= decision and e["ts"] <= e["known_ts"]})
    return rows
