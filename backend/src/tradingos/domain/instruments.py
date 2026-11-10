"""Exchange instrument metadata needed for sizing and price rounding."""

from __future__ import annotations

import math
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class InstrumentSpec(BaseModel):
    """Contract specification for a derivatives instrument (OKX naming).

    For BTC-USDT-SWAP: 1 contract = ct_val (0.01) BTC, orders in multiples of
    lot_sz (0.01) contracts, minimum min_sz (0.01) contracts, price step tick_sz (0.1).
    """

    model_config = ConfigDict(frozen=True)

    inst_id: str
    inst_type: str = "SWAP"
    ct_val: float          # base-currency amount per contract
    lot_sz: float          # contract quantity step
    min_sz: float          # minimum order size in contracts
    tick_sz: float         # price step
    settle_ccy: str = "USDT"
    max_leverage: float | None = None

    @property
    def base_qty_step(self) -> float:
        """Smallest tradable increment in base currency (e.g. BTC)."""
        return float(Decimal(str(self.lot_sz)) * Decimal(str(self.ct_val)))

    @property
    def min_base_qty(self) -> float:
        return float(Decimal(str(self.min_sz)) * Decimal(str(self.ct_val)))

    def floor_base_qty(self, qty: float) -> float:
        """Round a base-currency quantity DOWN to a tradable multiple (never up: risk must not grow)."""
        step = Decimal(str(self.base_qty_step))
        # small epsilon guards against float representation (e.g. 0.29999999 -> 0.3)
        n = math.floor(Decimal(str(qty)) / step + Decimal("1e-9"))
        return float(n * step)

    def contracts(self, base_qty: float) -> float:
        return float(Decimal(str(base_qty)) / Decimal(str(self.ct_val)))


# Fallback spec used when offline. Verified against OKX /api/v5/public/instruments
# on 2026-11-10. Live code paths must refresh from the exchange.
BTC_USDT_SWAP = InstrumentSpec(
    inst_id="BTC-USDT-SWAP", ct_val=0.01, lot_sz=0.01, min_sz=0.01, tick_sz=0.1, max_leverage=100
)

KNOWN_INSTRUMENTS: dict[str, InstrumentSpec] = {BTC_USDT_SWAP.inst_id: BTC_USDT_SWAP}
