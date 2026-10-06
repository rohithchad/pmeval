"""Market forecaster: the Kalshi YES price at forecast_time, taken as the probability.

The price is read from fct_market_forecast, which only uses trades and candles at or before
forecast_time, so this forecaster has no look-ahead by construction.
"""

from __future__ import annotations

from typing import Any

from pmeval.forecast.base import Forecaster, Target


class MarketForecaster(Forecaster):
    """Returns the market-implied probability, or None if the contract had no price yet."""

    name = "market"
    version = "1"

    def predict(self, target: Target) -> float | None:
        return target.market_probability

    def metadata(self, target: Target) -> dict[str, Any]:
        return {"source": "fct_market_forecast"}
