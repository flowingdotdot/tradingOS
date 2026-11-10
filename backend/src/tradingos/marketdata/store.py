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
