"""Historical candle collection with incremental sync.

OKX history-candles paginates backwards: `after=T` returns candles with ts < T.
We fetch from `end` backwards until reaching `start`, then merge into the store.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import pandas as pd

from tradingos.domain.timeframes import Timeframe
from tradingos.marketdata.okx_public import OkxPublicClient
from tradingos.marketdata.store import CandleStore
from tradingos.marketdata.validation import ValidationReport, validate_candles

log = logging.getLogger(__name__)


@dataclass
class CollectResult:
    inst_id: str
    timeframe: str
    fetched: int
    dropped_unconfirmed: int
    requests: int
    stored_rows: int
    validation: ValidationReport


def fetch_range(
    client: OkxPublicClient, inst_id: str, tf: Timeframe, start_ts: int, end_ts: int, max_requests: int = 5000
) -> tuple[pd.DataFrame, int, int]:
    """Fetch confirmed candles with start_ts <= ts < end_ts. Returns (df, dropped_unconfirmed, requests)."""
    pages: list[pd.DataFrame] = []
    cursor = end_ts
    dropped_total = 0
    requests = 0
    while cursor > start_ts and requests < max_requests:
        page, dropped = client.get_history_candles(inst_id, tf, after=cursor)
        requests += 1
        dropped_total += dropped
        if page.empty:
            if dropped:  # only the forming candle came back; step past it
                cursor -= tf.ms
                continue
            break  # no more history
        pages.append(page)
        oldest = int(page["ts"].iloc[0])
        if oldest >= cursor:  # defensive: API not progressing
            break
        cursor = oldest
    if not pages:
        return pd.DataFrame(columns=["ts"]), dropped_total, requests
    df = pd.concat(pages, ignore_index=True).drop_duplicates("ts").sort_values("ts")
    df = df[(df["ts"] >= start_ts) & (df["ts"] < end_ts)].reset_index(drop=True)
    return df, dropped_total, requests


def sync_history(
    client: OkxPublicClient,
    store: CandleStore,
    inst_id: str,
    tf: Timeframe,
    start_ts: int,
    end_ts: int | None = None,
) -> CollectResult:
    """Ensure the store covers [start_ts, end_ts). Only fetches the parts not already stored
    (older-than-stored and newer-than-stored); interior gaps are re-requested too."""
    now_ms = int(time.time() * 1000)
    end_ts = min(end_ts or now_ms, now_ms)
    start_ts -= start_ts % tf.ms
    existing = store.load(inst_id, tf)

    ranges: list[tuple[int, int]] = []
    if existing.empty:
        ranges.append((start_ts, end_ts))
    else:
        first, last = int(existing["ts"].iloc[0]), int(existing["ts"].iloc[-1])
        if start_ts < first:
            ranges.append((start_ts, first))
        if last + tf.ms < end_ts:
            ranges.append((last + tf.ms, end_ts))
        for gap in validate_candles(existing, tf).gaps:
            if gap.end_ts >= start_ts and gap.start_ts < end_ts:
                ranges.append((gap.start_ts, gap.end_ts + tf.ms))

    fetched_frames, dropped, requests = [], 0, 0
    for a, b in ranges:
        df, d, r = fetch_range(client, inst_id, tf, a, b)
        dropped += d
        requests += r
        if not df.empty:
            fetched_frames.append(df)
        log.info("fetched range", extra={"inst_id": inst_id, "tf": tf.value, "start": a, "end": b, "rows": len(df)})

    fetched = pd.concat(fetched_frames, ignore_index=True) if fetched_frames else pd.DataFrame()
    stored = store.merge_save(inst_id, tf, fetched) if not fetched.empty else existing
    window = stored[(stored["ts"] >= start_ts) & (stored["ts"] < end_ts)]
    return CollectResult(
        inst_id=inst_id,
        timeframe=tf.value,
        fetched=len(fetched),
        dropped_unconfirmed=dropped,
        requests=requests,
        stored_rows=len(stored),
        validation=validate_candles(window, tf),
    )
