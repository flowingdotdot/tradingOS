from __future__ import annotations

import httpx
import pandas as pd
import pytest
from conftest import T0, make_bars
from fake_okx import FakeOkx

from tradingos.domain.timeframes import Timeframe
from tradingos.marketdata.collector import fetch_range, sync_history
from tradingos.marketdata.okx_public import OkxApiError, OkxPublicClient, parse_candles
from tradingos.marketdata.store import CandleStore, data_fingerprint
from tradingos.marketdata.validation import validate_candles

TF = Timeframe.M15


def client_for(fake: FakeOkx) -> OkxPublicClient:
    return OkxPublicClient(transport=fake.transport(), sleep=lambda s: None)


def test_parse_drops_unconfirmed_and_sorts_ascending():
    rows = [
        ["3000", "1", "2", "0.5", "1.5", "1", "1", "1", "0"],
        ["2000", "1", "2", "0.5", "1.5", "1", "1", "1", "1"],
        ["1000", "1", "2", "0.5", "1.5", "1", "1", "1", "1"],
    ]
    df, dropped = parse_candles(rows)
    assert dropped == 1
    assert df["ts"].tolist() == [1000, 2000]
    assert df["ts"].dtype == "int64" and df["close"].dtype == "float64"


def test_fetch_range_paginates_and_excludes_forming_candle():
    last = T0 + 349 * TF.ms
    fake = FakeOkx(TF, T0, last)
    with client_for(fake) as c:
        df, dropped, requests = fetch_range(c, "BTC-USDT-SWAP", TF, T0, last + 2 * TF.ms)
    assert len(df) == 350 and df["ts"].is_monotonic_increasing
    assert int(df["ts"].iloc[-1]) == last  # forming candle never included
    assert dropped == 1
    assert requests >= 4  # 100 per page


def test_retry_on_rate_limit_then_success():
    fake = FakeOkx(TF, T0, T0 + 10 * TF.ms, fail_first=2)
    with client_for(fake) as c:
        df, _ = c.get_history_candles("BTC-USDT-SWAP", TF)
    assert len(df) == 11
    assert len(fake.requests) == 3


def test_api_error_code_raises_without_retry():
    fake = FakeOkx(TF, T0, T0 + 10 * TF.ms, error_code="51001")
    with client_for(fake) as c, pytest.raises(OkxApiError) as exc:
        c.get_history_candles("BAD-ID", TF)
    assert exc.value.code == "51001" and len(fake.requests) == 1


def test_retries_exhausted_raises():
    fake = FakeOkx(TF, T0, T0 + TF.ms, fail_first=99)
    with OkxPublicClient(transport=fake.transport(), sleep=lambda s: None, max_retries=2) as c:
        with pytest.raises(OkxApiError):
            c.get_history_candles("BTC-USDT-SWAP", TF)


def test_validation_reports_gaps_duplicates_misalignment_and_bad_ohlc():
    bars = make_bars([(100, 101, 99, 100)] * 10)
    bars = bars.drop(index=[3, 4]).reset_index(drop=True)            # gap of 2 bars
    bars = pd.concat([bars, bars.iloc[[0]]], ignore_index=True)      # duplicate (and unsorted)
    bars.loc[len(bars)] = [T0 + 10 * TF.ms + 7, 100, 101, 99, 100, 1, 1, 1]  # misaligned
    bars.loc[len(bars)] = [T0 + 12 * TF.ms, 100, 99, 98, 100, 1, 1, 1]       # high < open
    r = validate_candles(bars, TF)
    assert r.duplicates == 1 and r.unsorted and r.misaligned == 1 and r.invalid_ohlc == 1
    assert any(g.missing_bars == 2 and g.start_ts == T0 + 3 * TF.ms for g in r.gaps)
    assert not r.ok


def test_store_merge_dedups_and_new_rows_win(tmp_path):
    store = CandleStore(tmp_path)
    a = make_bars([(100, 101, 99, 100)] * 5)
    b = make_bars([(200, 201, 199, 200)] * 5, t0=T0 + 3 * TF.ms)
    store.merge_save("X", TF, a)
    merged = store.merge_save("X", TF, b)
    assert len(merged) == 8 and merged["ts"].is_monotonic_increasing
    assert merged.loc[merged["ts"] == T0 + 3 * TF.ms, "close"].item() == 200
    assert store.datasets() == [("X", TF)]
    loaded = store.load("X", TF, T0 + TF.ms, T0 + 4 * TF.ms)
    assert len(loaded) == 3
    assert data_fingerprint(loaded) == data_fingerprint(loaded.copy())
    assert data_fingerprint(loaded) != data_fingerprint(store.load("X", TF))


def test_sync_history_is_incremental_and_refetches_gaps(tmp_path):
    store = CandleStore(tmp_path)
    last = T0 + 299 * TF.ms
    fake = FakeOkx(TF, T0, last)
    with client_for(fake) as c:
        r1 = sync_history(c, store, "BTC-USDT-SWAP", TF, T0, last + TF.ms)
    assert r1.stored_rows == 300 and r1.validation.ok and not r1.validation.gaps

    # A later sync with 50 new candles only fetches the new part.
    fake2 = FakeOkx(TF, T0, last + 50 * TF.ms)
    with client_for(fake2) as c:
        r2 = sync_history(c, store, "BTC-USDT-SWAP", TF, T0, last + 51 * TF.ms)
    assert r2.stored_rows == 350 and r2.fetched == 50
    assert all(int(q["after"]) > last for q in fake2.requests)


def test_sync_reports_real_exchange_gaps_without_filling(tmp_path):
    store = CandleStore(tmp_path)
    missing = {T0 + 10 * TF.ms, T0 + 11 * TF.ms}
    fake = FakeOkx(TF, T0, T0 + 49 * TF.ms, missing=missing)
    with client_for(fake) as c:
        r = sync_history(c, store, "BTC-USDT-SWAP", TF, T0, T0 + 50 * TF.ms)
    assert r.stored_rows == 48
    assert r.validation.missing_bars == 2


def test_transport_error_is_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"code": "0", "data": []})

    with OkxPublicClient(transport=httpx.MockTransport(handler), sleep=lambda s: None) as c:
        df, _ = c.get_history_candles("BTC-USDT-SWAP", TF)
    assert df.empty and calls["n"] == 2


def test_provenance_sidecar_records_source_coverage_and_fingerprint(tmp_path):
    from tradingos.marketdata.store import read_provenance, write_provenance

    store = CandleStore(tmp_path)
    store.merge_save("X", TF, make_bars([(100, 101, 99, 100)] * 10).drop(index=[4]))
    m = write_provenance(store, "X", TF, source="https://example/api", collector_revision="abc123")
    assert m["rows"] == 9 and m["integrity"]["missing_bars"] == 1 and m["integrity"]["gaps"][0]["start_ts"] == T0 + 4 * TF.ms
    assert m["fingerprint_sha256_ohlc"] == data_fingerprint(store.load("X", TF)) and m["first_ts"] == T0
    store.merge_save("X", TF, make_bars([(100, 101, 99, 100)] * 10))           # the gap gets filled later
    m2 = write_provenance(store, "X", TF, source="https://example/api", collector_revision="abc124")
    assert m2["integrity"]["missing_bars"] == 0 and len(m2["history"]) == 2
    assert m2["history"][0]["fingerprint"] != m2["history"][1]["fingerprint"]   # the change is traceable
    assert read_provenance(store, "X", TF)["source"] == "https://example/api"
    assert store.datasets() == [("X", TF)]                                      # sidecar is not a dataset
