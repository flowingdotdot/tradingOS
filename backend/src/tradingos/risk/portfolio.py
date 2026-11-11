"""Portfolio-level arbitration for multiple strategy instances (preparation for Phase 4+).

Pure, deterministic functions; not yet wired to a live runner. Each instance is first
checked by its own RiskEngine (per-instance limits); this layer then applies ACCOUNT-level
rules across instances:
  * conflicting intents (opposite sides, same instrument, same decision time) -> all rejected
  * an instrument already held by one instance cannot be entered by another (ownership)
  * at most `max_positions_per_instrument` (default 1 = no netting / no stacking)
  * total open risk across instances <= max_total_risk_pct of account equity
  * duplicate intents from the same instance at the same time -> only the first counts
Order ids are deterministic so that a retried submission can be reconciled (RISK_POLICY §4).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from pydantic import BaseModel, Field


class PortfolioLimits(BaseModel):
    max_total_risk_pct: float = Field(1.0, gt=0, le=10)
    max_positions_per_instrument: int = Field(1, ge=1, le=1)


@dataclass(frozen=True)
class EntryIntent:
    instance_id: str
    inst_id: str
    side: int              # +1 / -1
    risk_amount: float     # USDT at stop, already sized by the instance's RiskEngine
    decision_ts: int
    priority: int = 0      # lower = earlier; ties by instance_id (deterministic)


@dataclass(frozen=True)
class OpenPosition:
    instance_id: str       # owner
    inst_id: str
    side: int
    risk_amount: float


@dataclass(frozen=True)
class Decision:
    intent: EntryIntent
    approved: bool
    reason: str


def arbitrate(intents: list[EntryIntent], positions: list[OpenPosition], equity: float,
              limits: PortfolioLimits | None = None) -> list[Decision]:
    limits = limits or PortfolioLimits()
    ordered = sorted(intents, key=lambda x: (x.priority, x.instance_id, x.inst_id))
    sides: dict[tuple[str, int], set[int]] = {}
    for it in ordered:
        sides.setdefault((it.inst_id, it.decision_ts), set()).add(it.side)

    open_risk = sum(p.risk_amount for p in positions)
    held: dict[str, list[OpenPosition]] = {}
    for p in positions:
        held.setdefault(p.inst_id, []).append(p)
    seen: set[tuple[str, str, int]] = set()
    budget = equity * limits.max_total_risk_pct / 100
    out: list[Decision] = []
    for it in ordered:
        key = (it.instance_id, it.inst_id, it.decision_ts)
        if key in seen:
            out.append(Decision(it, False, "duplicate_intent"))
            continue
        seen.add(key)
        if len(sides[(it.inst_id, it.decision_ts)]) > 1:
            out.append(Decision(it, False, "conflicting_signals"))
            continue
        owners = held.get(it.inst_id, [])
        if owners and any(p.instance_id != it.instance_id for p in owners):
            out.append(Decision(it, False, f"instrument_owned_by:{owners[0].instance_id}"))
            continue
        if len(owners) >= limits.max_positions_per_instrument:
            out.append(Decision(it, False, "max_positions_per_instrument"))
            continue
        if open_risk + it.risk_amount > budget + 1e-9:
            out.append(Decision(it, False, "account_risk_limit"))
            continue
        open_risk += it.risk_amount
        held.setdefault(it.inst_id, []).append(OpenPosition(it.instance_id, it.inst_id, it.side, it.risk_amount))
        out.append(Decision(it, True, "approved"))
    return out


def client_order_id(instance_id: str, decision_ts: int, purpose: str, attempt: int = 0) -> str:
    """Deterministic, exchange-safe id (OKX clOrdId: alphanumeric, <= 32 chars)."""
    digest = hashlib.sha256(f"{instance_id}|{decision_ts}|{purpose}".encode()).hexdigest()[:24]
    return f"t{digest}a{attempt:02d}"[:32]
