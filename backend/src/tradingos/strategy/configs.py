"""ConfigStore — immutable, versioned Strategy Configurations.

* Repository configs (YAML in git) are registered the first time they are seen: their
  config_hash is recorded. If the file later changes under the SAME version name, the version is
  flagged and refused ("never overwrite a version — create a new one").
* User configs (created via API/UI: edit, clone, save as new version) are stored in SQLite and
  can never be updated or deleted through the application.
SQL is kept portable (PostgreSQL migration = new implementation of this class).
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tradingos.strategy.registry import (
    STRATEGY_CLASSES,
    StrategyConfig,
    canonical_hash,
    load_repo_entries,
    resolve_config,
)
from tradingos.strategy.schema import ConfigValidationError

VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,47}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_configs (
    strategy_id     TEXT NOT NULL,
    version         TEXT NOT NULL,
    source          TEXT NOT NULL,          -- repo | user
    config_hash     TEXT NOT NULL,
    declared_json   TEXT NOT NULL,
    status          TEXT NOT NULL,
    description     TEXT NOT NULL,
    parent_version  TEXT,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (strategy_id, version)
);
"""


class ConfigIntegrityError(RuntimeError):
    pass


class ConfigExistsError(ValueError):
    pass


class ConfigStore:
    def __init__(self, db_path: Path, repo_dir: Path):
        self.db_path = Path(db_path)
        self.repo_dir = Path(repo_dir)
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

    # ------------------------------------------------------------------ queries
    def list(self, strategy_id: str | None = None) -> list[dict[str, Any]]:
        """All configurations with an `integrity` field: ok | modified | invalid."""
        out: list[dict[str, Any]] = []
        with self._conn() as con:
            known = {(r["strategy_id"], r["version"]): r for r in con.execute("SELECT * FROM strategy_configs")}
            for cfg, info in self._repo_entries():
                if cfg is None:
                    out.append({"strategy_id": info["strategy_id"], "version": info["version"], "source": "repo",
                                "source_file": info["source_file"], "integrity": "invalid", "error": info["error"]})
                    continue
                key = (cfg.strategy_id, cfg.version)
                row = known.get(key)
                if row is None:
                    con.execute(
                        "INSERT INTO strategy_configs VALUES (?,?,?,?,?,?,?,?,?)",
                        (cfg.strategy_id, cfg.version, "repo", cfg.config_hash, json.dumps(cfg.declared_params),
                         cfg.status.value, cfg.description, cfg.parent_version, _now()),
                    )
                    integrity = "ok"
                else:
                    integrity = "ok" if row["config_hash"] == cfg.config_hash else "modified"
                out.append({**cfg.model_dump(), "integrity": integrity})
            for row in known.values():
                if row["source"] != "user":
                    continue
                try:
                    cfg = resolve_config(row["strategy_id"], row["version"], json.loads(row["declared_json"]),
                                         status=row["status"], description=row["description"], source="user",
                                         parent_version=row["parent_version"], created_at=row["created_at"])
                    out.append({**cfg.model_dump(), "integrity": "ok"})
                except (ConfigValidationError, KeyError) as exc:
                    out.append({"strategy_id": row["strategy_id"], "version": row["version"], "source": "user",
                                "integrity": "invalid", "error": str(exc)})
        if strategy_id:
            out = [c for c in out if c["strategy_id"] == strategy_id]
        return sorted(out, key=lambda c: (c["strategy_id"], c.get("created_at") or "", c["version"]))

    def get(self, strategy_id: str, version: str) -> StrategyConfig:
        for c in self.list(strategy_id):
            if c["version"] != version:
                continue
            if c["integrity"] == "modified":
                raise ConfigIntegrityError(
                    f"{strategy_id}:{version} was modified after it was first registered. Versions are immutable — "
                    "restore the file and save your change as a NEW version."
                )
            if c["integrity"] != "ok":
                raise ConfigIntegrityError(f"{strategy_id}:{version} is no longer valid: {c.get('error')}")
            return StrategyConfig(**{k: v for k, v in c.items() if k in StrategyConfig.model_fields})
        raise KeyError(f"strategy {strategy_id} version {version} not found")

    # ------------------------------------------------------------------ commands
    def create(self, strategy_id: str, version: str, declared: dict[str, Any], description: str = "",
               parent_version: str | None = None) -> StrategyConfig:
        """Save a NEW configuration version (edit / clone). Existing versions are never changed."""
        if strategy_id not in STRATEGY_CLASSES:
            raise KeyError(f"unknown strategy_id {strategy_id!r}")
        if not VERSION_RE.match(version):
            raise ConfigValidationError([{"path": "version", "message": "use letters, digits, . _ - (max 48)"}])
        if any(c["version"] == version for c in self.list(strategy_id)):
            raise ConfigExistsError(f"{strategy_id}:{version} already exists — versions are immutable")
        if parent_version is not None:
            self.get(strategy_id, parent_version)
        cfg = resolve_config(strategy_id, version, declared, description=description, source="user",
                             parent_version=parent_version, created_at=_now())
        with self._conn() as con:
            con.execute(
                "INSERT INTO strategy_configs VALUES (?,?,?,?,?,?,?,?,?)",
                (strategy_id, version, "user", canonical_hash(declared), json.dumps(declared), "DRAFT",
                 description, parent_version, cfg.created_at),
            )
        return cfg

    def _repo_entries(self):
        return load_repo_entries(self.repo_dir) if self.repo_dir.exists() else []


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
