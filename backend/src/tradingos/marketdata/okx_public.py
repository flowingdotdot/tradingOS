"""OKX API v5 public (unauthenticated) REST client.

Only market-data endpoints live here. Authenticated trading endpoints will be a
separate module (Phase 4) so that this client can never place orders.

Retries: market-data GETs are idempotent, so retrying on 429/5xx/network errors
is safe. (Order submission is NOT idempotent and follows different rules.)
"""

from __future__ import annotations

import logging
import ssl
import time
from collections.abc import Callable
from typing import Any

import httpx
import pandas as pd

from tradingos.domain.instruments import InstrumentSpec
from tradingos.domain.timeframes import Timeframe

log = logging.getLogger(__name__)

CANDLE_COLUMNS = ["ts", "open", "high", "low", "close", "volume", "volume_ccy", "volume_quote"]
HISTORY_LIMIT = 100  # max rows per /market/history-candles request
MIN_REQUEST_INTERVAL_S = 0.12  # history-candles: 20 req / 2 s


class OkxApiError(RuntimeError):
    def __init__(self, code: str, msg: str, path: str):
        super().__init__(f"OKX error {code} on {path}: {msg}")
        self.code = code


def build_ssl_context(use_system_ca: bool) -> ssl.SSLContext | bool:
    if not use_system_ca:
        return True  # httpx default (certifi)
    import truststore

    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def parse_candles(rows: list[list[str]]) -> tuple[pd.DataFrame, int]:
    """Parse OKX candle rows. Returns (confirmed candles sorted ascending, #dropped unconfirmed).

    Row format: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm].
    confirm == "0" means the candle is still forming; it is never stored or used.
    """
    confirmed = [r for r in rows if len(r) >= 9 and r[8] == "1"]
    dropped = len(rows) - len(confirmed)
    if not confirmed:
        return pd.DataFrame({c: pd.Series(dtype="int64" if c == "ts" else "float64") for c in CANDLE_COLUMNS}), dropped
    df = pd.DataFrame([r[:8] for r in confirmed], columns=CANDLE_COLUMNS)
    df["ts"] = df["ts"].astype("int64")
    for col in CANDLE_COLUMNS[1:]:
        df[col] = df[col].astype("float64")
    df = df.sort_values("ts").drop_duplicates("ts", keep="last").reset_index(drop=True)
    return df, dropped


class OkxPublicClient:
    def __init__(
        self,
        base_url: str = "https://www.okx.com",
        *,
        timeout_s: float = 15.0,
        use_system_ca: bool = True,
        max_retries: int = 4,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout_s,
            verify=build_ssl_context(use_system_ca) if transport is None else True,
            transport=transport,
            headers={"User-Agent": "TradingOS/0.1"},
        )
        self._max_retries = max_retries
        self._sleep = sleep
        self._last_request = 0.0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OkxPublicClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ core
    def _get(self, path: str, params: dict[str, Any]) -> list[Any]:
        attempt = 0
        while True:
            wait = MIN_REQUEST_INTERVAL_S - (time.monotonic() - self._last_request)
            if wait > 0:
                self._sleep(wait)
            self._last_request = time.monotonic()
            try:
                resp = self._client.get(path, params=params)
            except httpx.TransportError as exc:
                retry_reason = f"transport error: {exc!r}"
            else:
                if resp.status_code == 429 or resp.status_code >= 500:
                    retry_reason = f"HTTP {resp.status_code}"
                else:
                    resp.raise_for_status()
                    body = resp.json()
                    code = str(body.get("code"))
                    if code == "0":
                        return body.get("data", [])
                    if code == "50011":  # rate limit
                        retry_reason = "OKX rate limit (50011)"
                    else:
                        raise OkxApiError(code, body.get("msg", ""), path)
            attempt += 1
            if attempt > self._max_retries:
                raise OkxApiError("retry_exhausted", retry_reason, path)
            backoff = min(0.5 * 2 ** (attempt - 1), 8.0)
            log.warning("okx request retry", extra={"path": path, "attempt": attempt, "reason": retry_reason})
            self._sleep(backoff)

    # ------------------------------------------------------------ endpoints
    def get_history_candles(
        self, inst_id: str, bar: Timeframe, *, after: int | None = None, limit: int = HISTORY_LIMIT
    ) -> tuple[pd.DataFrame, int]:
        """One page of candles strictly OLDER than `after` (ms), newest first from OKX."""
        params: dict[str, Any] = {"instId": inst_id, "bar": bar.value, "limit": str(limit)}
        if after is not None:
            params["after"] = str(after)
        return parse_candles(self._get("/api/v5/market/history-candles", params))

    def get_instrument(self, inst_id: str, inst_type: str = "SWAP") -> InstrumentSpec:
        data = self._get("/api/v5/public/instruments", {"instType": inst_type, "instId": inst_id})
        if not data:
            raise OkxApiError("not_found", f"instrument {inst_id} not found", "/api/v5/public/instruments")
        d = data[0]
        return InstrumentSpec(
            inst_id=d["instId"],
            inst_type=d["instType"],
            ct_val=float(d["ctVal"]),
            lot_sz=float(d["lotSz"]),
            min_sz=float(d["minSz"]),
            tick_sz=float(d["tickSz"]),
            settle_ccy=d.get("settleCcy") or "USDT",
            max_leverage=float(d["lever"]) if d.get("lever") else None,
        )
