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


def find_earliest_candle(client: OkxPublicClient, inst_id: str, tf: Timeframe,
                         lo_ts: int = 1_500_000_000_000, hi_ts: int | None = None) -> dict:
    """Probe how far back the exchange actually serves candles for (inst_id, tf).

    history-candles?after=T returns candles with ts < T, so the response is non-empty iff
    T > earliest_ts. Binary search on T (about 40 requests). Returns the earliest candle found;
    makes no assumption that years of history exist."""
    hi = hi_ts or int(time.time() * 1000)
    hi -= hi % tf.ms   # start of the forming candle: everything strictly older is confirmed
    page, _ = client.get_history_candles(inst_id, tf, after=hi, limit=3)   # >1: tolerate a forming candle
    requests = 1
    if page.empty:
        return {"inst_id": inst_id, "timeframe": tf.value, "earliest_ts": None, "requests": requests}
    lo = lo_ts - lo_ts % tf.ms
    while hi - lo > tf.ms:
        mid = (lo + hi) // 2
        mid -= mid % tf.ms
        if mid <= lo:
            break
        page, _ = client.get_history_candles(inst_id, tf, after=mid, limit=1)
        requests += 1
        if page.empty:
            lo = mid
        else:
            hi = mid
    # invariant: nothing older than `lo`, something older than `hi`, hi - lo <= one bar
    page, _ = client.get_history_candles(inst_id, tf, after=hi, limit=1)
    earliest = int(page["ts"].iloc[0]) if not page.empty else None
    # sanity: a full page right after the earliest candle should be contiguous
    first_page, _ = client.get_history_candles(inst_id, tf, after=(earliest or hi) + 100 * tf.ms, limit=100)
    requests += 2
    contiguous = bool(len(first_page) > 1 and (first_page["ts"].diff().dropna() == tf.ms).all())
    return {"inst_id": inst_id, "timeframe": tf.value, "earliest_ts": earliest, "requests": requests,
            "first_page_rows": len(first_page), "first_page_contiguous": contiguous}


def check_mtf_consistency(store: CandleStore, inst_id: str, ltf: Timeframe, htf: Timeframe) -> dict:
    """Compare HTF candles built from stored LTF candles with the natively collected HTF candles."""
    from tradingos.features.mtf import resample

    low, native = store.load(inst_id, ltf), store.load(inst_id, htf)
    if low.empty or native.empty:
        return {"ltf": ltf.value, "htf": htf.value, "compared": 0, "note": "missing dataset"}
    built = resample(low, ltf, htf)
    m = built.merge(native, on="ts", suffixes=("_built", "_native"))
    diffs = {k: float((m[f"{k}_built"] - m[f"{k}_native"]).abs().max()) if len(m) else 0.0
             for k in ("open", "high", "low", "close")}
    vol = float((m["volume_built"] - m["volume_native"]).abs().max()) if len(m) else 0.0
    return {"ltf": ltf.value, "htf": htf.value, "built": len(built), "native": len(native), "compared": len(m),
            "max_abs_price_diff": max(diffs.values()) if diffs else 0.0, "max_abs_volume_diff": vol,
            "consistent": bool(len(m) > 0 and max(diffs.values()) == 0.0)}
