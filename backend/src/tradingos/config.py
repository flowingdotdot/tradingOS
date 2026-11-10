"""Application settings and execution-environment guards.

Settings come from environment variables (optionally a local `.env` file).
Secrets are typed as SecretStr so they never appear in repr/logs.

LIVE trading is hard-blocked in code. No configuration value can enable it;
see docs/RISK_POLICY.md §7 for the activation procedure.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent


class ExecutionEnvironment(StrEnum):
    BACKTEST = "BACKTEST"
    DEMO = "DEMO"
    LIVE = "LIVE"


class LiveTradingDisabledError(RuntimeError):
    """Raised whenever any code path attempts to use the LIVE environment."""


# Environments that the current build is allowed to *use*. DEMO is listed so the
# Phase-4 adapter can be developed against it; LIVE is deliberately absent and
# must not be added by configuration — only by a reviewed code change.
_ALLOWED_ENVIRONMENTS: frozenset[ExecutionEnvironment] = frozenset(
    {ExecutionEnvironment.BACKTEST, ExecutionEnvironment.DEMO}
)


def assert_environment_allowed(env: ExecutionEnvironment | str) -> ExecutionEnvironment:
    env = ExecutionEnvironment(env)
    if env is ExecutionEnvironment.LIVE or env not in _ALLOWED_ENVIRONMENTS:
        raise LiveTradingDisabledError(
            "LIVE trading is disabled in this build. See docs/RISK_POLICY.md §7."
        )
    return env


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TRADINGOS_",
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Field(default=PROJECT_DIR / "data")
    strategy_config_dir: Path = Field(default=BACKEND_DIR / "config" / "strategies")
    log_level: str = "INFO"
    log_json: bool = True

    okx_rest_base_url: str = "https://www.okx.com"
    # Use the operating-system certificate store for TLS verification (works behind
    # corporate proxies / antivirus HTTPS inspection without disabling verification).
    use_system_ca: bool = True
    http_timeout_s: float = 15.0

    # DEMO credentials (Phase 4). Read with the OKX_DEMO_ prefix, never TRADINGOS_.
    # LIVE credentials are intentionally not modelled anywhere.
    okx_demo_api_key: SecretStr | None = Field(default=None, alias="OKX_DEMO_API_KEY")
    okx_demo_api_secret: SecretStr | None = Field(default=None, alias="OKX_DEMO_API_SECRET")
    okx_demo_passphrase: SecretStr | None = Field(default=None, alias="OKX_DEMO_PASSPHRASE")

    @property
    def market_data_dir(self) -> Path:
        return self.data_dir / "market"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "tradingos.sqlite"

    def demo_credentials_configured(self) -> bool:
        return all(
            v is not None and v.get_secret_value()
            for v in (self.okx_demo_api_key, self.okx_demo_api_secret, self.okx_demo_passphrase)
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
