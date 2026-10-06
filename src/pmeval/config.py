"""Central configuration: environment settings and the economic series registry."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings read from environment variables or a local .env file.

    Secrets use SecretStr so they never show up in repr() or log output.
    """

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    fred_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None

    pmeval_raw_dir: Path = Path("data/raw")
    pmeval_warehouse_path: Path = Path("data/warehouse.duckdb")
    pmeval_forecast_hours_before: int = 24
    pmeval_log_level: str = "INFO"

    kalshi_base_url: str = "https://external-api.kalshi.com/trade-api/v2"


@lru_cache
def get_settings() -> Settings:
    """Return one shared Settings instance (cached so .env is read once)."""
    return Settings()


@dataclass(frozen=True)
class SeriesSpec:
    """Links one economic indicator to its FRED series and Kalshi series.

    Attributes:
        key: short internal name used in table keys.
        fred_series_id: FRED/ALFRED series id for the underlying data.
        kalshi_series_ticker: Kalshi series that trades the release.
        release_name: human-readable name of the scheduled release.
    """

    key: str
    fred_series_id: str
    kalshi_series_ticker: str
    release_name: str


# Kalshi tickers are recorded in docs/DECISIONS.md as assumptions to re-verify live.
SERIES_REGISTRY: tuple[SeriesSpec, ...] = (
    SeriesSpec("cpi", "CPIAUCSL", "KXCPIYOY", "Consumer Price Index"),
    SeriesSpec("payrolls", "PAYEMS", "KXPAYROLLS", "Employment Situation"),
    SeriesSpec("unemployment", "UNRATE", "KXU3", "Employment Situation"),
    SeriesSpec("gdp", "GDPC1", "KXGDP", "Gross Domestic Product"),
    SeriesSpec("fed", "DFEDTARU", "KXFEDDECISION", "FOMC Meeting"),
)
