"""Backtest-run repository (SQLite).

SQL is kept to portable syntax and confined to this class so the backend can move
to PostgreSQL by providing another implementation of the same methods.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id            TEXT PRIMARY KEY,
    created_at        TEXT NOT NULL,
    environment       TEXT NOT NULL,
    strategy_id       TEXT NOT NULL,
    strategy_version  TEXT NOT NULL,
    params_hash       TEXT NOT NULL,
    code_revision     TEXT NOT NULL,
    engine_version    TEXT NOT NULL,
    data_fingerprint  TEXT NOT NULL,
    inst_id           TEXT NOT NULL,
    timeframe         TEXT NOT NULL,
    start_ts          INTEGER NOT NULL,
    end_ts            INTEGER NOT NULL,
    summary_json      TEXT NOT NULL,
    payload_json      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_runs_strategy ON backtest_runs (strategy_id, strategy_version);
"""

_SUMMARY_COLS = (
    "run_id", "created_at", "environment", "strategy_id", "strategy_version", "params_hash",
    "code_revision", "engine_version", "data_fingerprint", "inst_id", "timeframe", "start_ts", "end_ts",
)


class RunRepository:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as con:
            con.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def save(self, record: dict[str, Any], summary: dict[str, Any], payload: dict[str, Any]) -> None:
        cols = list(_SUMMARY_COLS) + ["summary_json", "payload_json"]
        values = [record[c] for c in _SUMMARY_COLS] + [json.dumps(summary), json.dumps(payload)]
        with self._conn() as con:
            con.execute(
                f"INSERT INTO backtest_runs ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                values,
            )

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._conn() as con:
            rows = con.execute(
                f"SELECT {', '.join(_SUMMARY_COLS)}, summary_json FROM backtest_runs "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            d = {c: r[c] for c in _SUMMARY_COLS}
            d["summary"] = json.loads(r["summary_json"])
            out.append(d)
        return out

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._conn() as con:
            r = con.execute("SELECT * FROM backtest_runs WHERE run_id = ?", (run_id,)).fetchone()
        if r is None:
            return None
        d = {c: r[c] for c in _SUMMARY_COLS}
        d["summary"] = json.loads(r["summary_json"])
        d.update(json.loads(r["payload_json"]))
        return d
