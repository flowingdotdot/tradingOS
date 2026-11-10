"""Timeframe definitions.

Convention: a candle's `ts` is its OPEN time in UTC epoch milliseconds.
Its close time is `ts + timeframe.ms`. Information from a candle is only
available at its close time.
"""

from __future__ import annotations

from enum import StrEnum

MINUTE_MS = 60_000


class Timeframe(StrEnum):
    # Values are OKX `bar` parameter strings.
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1H"
    H4 = "4H"

    @property
    def ms(self) -> int:
        return _TF_MS[self]

    @property
    def minutes(self) -> int:
        return self.ms // MINUTE_MS


_TF_MS: dict[Timeframe, int] = {
    Timeframe.M1: 1 * MINUTE_MS,
    Timeframe.M5: 5 * MINUTE_MS,
    Timeframe.M15: 15 * MINUTE_MS,
    Timeframe.H1: 60 * MINUTE_MS,
    Timeframe.H4: 240 * MINUTE_MS,
}
