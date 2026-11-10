"""In-memory fake of the OKX public candle endpoints (mock exchange for tests)."""

from __future__ import annotations

import httpx

from tradingos.domain.timeframes import Timeframe


class FakeOkx:
    def __init__(self, tf: Timeframe, first_ts: int, last_closed_ts: int, missing: set[int] = frozenset(),
                 fail_first: int = 0, error_code: str | None = None):
        self.tf = tf
        self.missing = set(missing)
        self.candles = {
            ts: [str(ts), "100", "101", "99", "100.5", "10", "0.1", "1000", "1"]
            for ts in range(first_ts, last_closed_ts + tf.ms, tf.ms)
            if ts not in self.missing
        }
        # The currently forming candle (confirm=0) right after the last closed one.
        forming = last_closed_ts + tf.ms
        self.candles[forming] = [str(forming), "100", "101", "99", "100", "1", "0.01", "100", "0"]
        self.requests: list[dict] = []
        self.fail_first = fail_first
        self.error_code = error_code

    def handler(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.requests.append({"path": request.url.path, **params})
        if self.fail_first > 0:
            self.fail_first -= 1
            return httpx.Response(429, json={"code": "50011", "msg": "Too Many Requests"})
        if self.error_code:
            return httpx.Response(200, json={"code": self.error_code, "msg": "boom", "data": []})
        if request.url.path == "/api/v5/market/history-candles":
            after = int(params.get("after", 10**15))
            limit = int(params.get("limit", 100))
            rows = sorted((ts for ts in self.candles if ts < after), reverse=True)[:limit]
            return httpx.Response(200, json={"code": "0", "msg": "", "data": [self.candles[t] for t in rows]})
        return httpx.Response(404, json={"code": "404", "msg": "unknown"})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)
