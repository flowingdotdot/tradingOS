"""Research split (IS / OOS / already-observed) and the OOS seal.

The split is fixed in backend/config/research_split.yaml (in git, so every change is recorded).
While `oos_sealed` is true, NO strategy evaluation may touch the out-of-sample window: every
service entry point (backtest, signal inspection, research chart, trade audit — and therefore
the API, the CLI and the UI) calls `guard_period` and is refused if its period overlaps it.
Collecting and validating raw market data is not a strategy evaluation and is not blocked.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

DAY_MS = 86_400_000


def _ms(value: Any) -> int:
    if isinstance(value, int):
        return value
    dt = datetime.fromisoformat(str(value))
    return int((dt if dt.tzinfo else dt.replace(tzinfo=UTC)).timestamp() * 1000)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


class OosSealedError(PermissionError):
    pass


class ResearchSplit(BaseModel):
    """All boundaries are UTC; every window is [start, end) (end exclusive)."""

    name: str
    is_start: int
    is_end: int
    oos_start: int
    oos_end: int
    oos_sealed: bool = True
    zero_volume_invalid_minutes: int = Field(30, ge=0)   # 0 disables the rule
    notes: str = ""
    fingerprint: str = ""

    @model_validator(mode="after")
    def _ordered(self) -> ResearchSplit:
        if not self.is_start < self.is_end <= self.oos_start < self.oos_end:
            raise ValueError("split must satisfy is_start < is_end <= oos_start < oos_end")
        return self

    # ------------------------------------------------------------------ queries
    def segment(self, start_ts: int, end_ts: int) -> str:
        """Label of the period [start, end): WARMUP_ONLY | IS | OOS | OBSERVED | MIXED."""
        parts = set()
        for name, a, b in (("WARMUP_ONLY", -(1 << 62), self.is_start), ("IS", self.is_start, self.is_end),
                           ("GAP", self.is_end, self.oos_start), ("OOS", self.oos_start, self.oos_end),
                           ("OBSERVED", self.oos_end, 1 << 62)):
            if start_ts < b and end_ts > a and b > a:
                parts.add(name)
        return parts.pop() if len(parts) == 1 else "MIXED"

    def overlaps_oos(self, start_ts: int, end_ts: int) -> bool:
        return start_ts < self.oos_end and end_ts > self.oos_start

    def guard(self, start_ts: int, end_ts: int, purpose: str) -> None:
        if self.oos_sealed and self.overlaps_oos(start_ts, end_ts):
            raise OosSealedError(
                f"{purpose}: the requested period {_iso(start_ts)} -> {_iso(end_ts)} overlaps the SEALED "
                f"out-of-sample window {_iso(self.oos_start)} -> {_iso(self.oos_end)} (split '{self.name}'). "
                f"Use the in-sample window {_iso(self.is_start)} -> {_iso(self.is_end)} or the already-observed "
                f"window from {_iso(self.oos_end)}. Unsealing requires explicit approval and a recorded change of "
                "research_split.yaml."
            )

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name, "fingerprint": self.fingerprint, "oos_sealed": self.oos_sealed,
            "is": {"start_ts": self.is_start, "end_ts": self.is_end, "start": _iso(self.is_start),
                   "end_exclusive": _iso(self.is_end)},
            "oos": {"start_ts": self.oos_start, "end_ts": self.oos_end, "start": _iso(self.oos_start),
                    "end_exclusive": _iso(self.oos_end)},
            "observed": {"start_ts": self.oos_end, "start": _iso(self.oos_end)},
            "zero_volume_invalid_minutes": self.zero_volume_invalid_minutes, "notes": self.notes,
        }


def load_split(path: Path | None) -> ResearchSplit | None:
    """None (no file) = no split defined: nothing is sealed. Tests use this explicitly."""
    if path is None or not Path(path).exists():
        return None
    text = Path(path).read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    return ResearchSplit(
        name=str(raw["name"]), is_start=_ms(raw["is_start"]), is_end=_ms(raw["is_end"]),
        oos_start=_ms(raw["oos_start"]), oos_end=_ms(raw["oos_end"]), oos_sealed=bool(raw.get("oos_sealed", True)),
        zero_volume_invalid_minutes=int(raw.get("zero_volume_invalid_minutes", 30)), notes=str(raw.get("notes", "")),
        fingerprint=hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
    )


def guard_period(split: ResearchSplit | None, start_ts: int, end_ts: int, purpose: str) -> str:
    """Raise if the period touches the sealed OOS window; return the period's segment label."""
    if split is None:
        return "UNSPLIT"
    split.guard(start_ts, end_ts, purpose)
    return split.segment(start_ts, end_ts)
