"""Base-rate baseline: the historical share of YES outcomes for the same series.

This is the simplest honest benchmark. It ignores the strike, the news and the market, and just
says "contracts in this series have resolved YES this often so far". Any forecaster that cannot
beat it adds nothing.

No look-ahead: only contracts that had already settled at or before forecast_time are counted.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from pmeval.forecast.base import Forecaster, Target


class BaseRateForecaster(Forecaster):
    """P(YES) = (YES count + 1) / (contract count + 2) over earlier settled contracts of a series.

    The +1/+2 (Laplace smoothing) keeps the answer away from exactly 0 or 1 when history is short.
    With no history the answer is 0.5.

    Args:
        history: one row per resolved contract with columns series_key, outcome (0/1), settled_at.
    """

    name = "base_rate"
    version = "1"

    def __init__(self, history: pd.DataFrame) -> None:
        self.history = history[["series_key", "outcome", "settled_at"]].dropna()

    def _counts(self, target: Target) -> tuple[int, int]:
        """Return (yes_count, contract_count) for settled contracts known at forecast_time."""
        known = self.history[
            (self.history["series_key"] == target.series_key)
            & (self.history["settled_at"] <= target.forecast_time)
        ]
        return int(known["outcome"].sum()), len(known)

    def predict(self, target: Target) -> float | None:
        yes, total = self._counts(target)
        return (yes + 1) / (total + 2)

    def metadata(self, target: Target) -> dict[str, Any]:
        yes, total = self._counts(target)
        return {"yes_count": yes, "contract_count": total}
