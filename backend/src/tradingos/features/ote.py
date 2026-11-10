"""Dealing range, premium/discount and OTE for a bearish impulse (docs/STRATEGY_SPEC.md §2.7-2.8).

Impulse from A (high, start) down to B (low, end). R = A - B.
  retracement ratio r(P) = (P - B) / R    (0 at B, 1 at A)
  equilibrium EQ = B + 0.5 R; premium r > 0.5; discount r < 0.5
  OTE band = [B + low*R, B + high*R] (0.62 .. 0.79), reference level B + ref*R (0.705)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DealingRange:
    a_high: float
    b_low: float

    @property
    def size(self) -> float:
        return self.a_high - self.b_low

    def level(self, ratio: float) -> float:
        return self.b_low + ratio * self.size

    def ratio(self, price: float) -> float:
        return (price - self.b_low) / self.size

    @property
    def equilibrium(self) -> float:
        return self.level(0.5)

    def ote(self, low: float = 0.62, high: float = 0.79) -> tuple[float, float]:
        return self.level(low), self.level(high)


def intersect(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float] | None:
    lo, hi = max(a[0], b[0]), min(a[1], b[1])
    return (lo, hi) if lo <= hi else None
