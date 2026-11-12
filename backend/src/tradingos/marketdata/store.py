"""Parquet market-data store: one file per (exchange, instrument, timeframe)."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pandas as pd

from tradingos.domain.timeframes import Timeframe
from tradingos.marketdata.okx_public import CANDLE_COLUMNS


class CandleStore:
    def __init__(self, root: Path, exchange: str = "okx"):
        self.root = Path(root) / exchange

    def path(self, inst_id: str, tf: Timeframe) -> Path:
        return self.root / inst_id / f"{tf.value}.parquet"

    def load(self, inst_id: str, tf: Timeframe, start_ts: int | None = None, end_ts: int | None = None) -> pd.DataFrame:
        """Load candles with start_ts <= ts < end_ts (open times, ms)."""
        p = self.path(inst_id, tf)
        if not p.exists():
            return pd.DataFrame(columns=CANDLE_COLUMNS).astype({"ts": "int64"})
        df = pd.read_parquet(p)
        if start_ts is not None:
            df = df[df["ts"] >= start_ts]
        if end_ts is not None:
            df = df[df["ts"] < end_ts]
        return df.reset_index(drop=True)

    def merge_save(self, inst_id: str, tf: Timeframe, new: pd.DataFrame) -> pd.DataFrame:
        """Merge new candles into the stored file (new rows win on equal ts). Atomic write."""
        existing = self.load(inst_id, tf)
        frames = [f for f in (existing, new[CANDLE_COLUMNS]) if not f.empty]
        merged = pd.concat(frames, ignore_index=True) if frames else new[CANDLE_COLUMNS]
        merged = merged.drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)
        p = self.path(inst_id, tf)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".parquet.tmp")
        merged.to_parquet(tmp, index=False)
        os.replace(tmp, p)
        return merged

    def datasets(self) -> list[tuple[str, Timeframe]]:
        out: list[tuple[str, Timeframe]] = []
        if not self.root.exists():
            return out
        for inst_dir in sorted(self.root.iterdir()):
            for f in sorted(inst_dir.glob("*.parquet")):
                try:
                    out.append((inst_dir.name, Timeframe(f.stem)))
                except ValueError:
                    continue
        return out


def data_fingerprint(df: pd.DataFrame) -> str:
    """SHA-256 over the exact candle values used in a run (for reproducibility records)."""
    h = hashlib.sha256()
    h.update(df[CANDLE_COLUMNS[:5]].to_numpy(dtype="float64").tobytes())
    return h.hexdigest()


def write_provenance(store: CandleStore, inst_id: str, tf: Timeframe, *, source: str, collector_revision: str,
                     requested_start_ts: int | None = None, requested_end_ts: int | None = None) -> dict:
    """Sidecar `<bar>.meta.json` next to the Parquet file: where the data came from, what it
    covers, its integrity and fingerprint. Rewritten after every sync; `history` keeps one line
    per sync so a change of fingerprint can be traced."""
    import json
    from datetime import UTC, datetime

    from tradingos.marketdata.validation import validate_candles

    df = store.load(inst_id, tf)
    v = validate_candles(df, tf)
    path = store.path(inst_id, tf).with_suffix(".meta.json")
    history = []
    if path.exists():
        try:
            history = json.loads(path.read_text(encoding="utf-8")).get("history", [])
        except (ValueError, OSError):
            history = []
    fp = data_fingerprint(df) if len(df) else None
    now = datetime.now(UTC).isoformat(timespec="seconds")
    history.append({"synced_at": now, "rows": v.rows, "first_ts": v.first_ts, "last_ts": v.last_ts,
                    "fingerprint": fp, "requested_start_ts": requested_start_ts,
                    "requested_end_ts": requested_end_ts, "collector_revision": collector_revision})
    meta = {
        "exchange": store.root.name, "inst_id": inst_id, "timeframe": tf.value, "source": source,
        "timestamp_convention": "ts = candle OPEN time, UTC epoch milliseconds; closed candles only",
        "rows": v.rows, "first_ts": v.first_ts, "last_ts": v.last_ts,
        "first_utc": datetime.fromtimestamp(v.first_ts / 1000, UTC).isoformat() if v.first_ts else None,
        "last_utc": datetime.fromtimestamp(v.last_ts / 1000, UTC).isoformat() if v.last_ts else None,
        "fingerprint_sha256_ohlc": fp,
        "integrity": {"ok": v.ok, "duplicates": v.duplicates, "misaligned": v.misaligned,
                      "invalid_ohlc": v.invalid_ohlc, "missing_bars": v.missing_bars,
                      "gaps": [{"start_ts": g.start_ts, "end_ts": g.end_ts, "missing_bars": g.missing_bars}
                               for g in v.gaps]},
        "updated_at": now, "history": history[-50:],
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return meta


def read_provenance(store: CandleStore, inst_id: str, tf: Timeframe) -> dict | None:
    import json

    path = store.path(inst_id, tf).with_suffix(".meta.json")
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
