"""Forecaster interface shared by every forecaster.

A forecaster answers one question per contract: "what is the probability this contract resolves
YES?" It sees only a Target, which holds information known at forecast_time. Outcomes are never
part of a Target, so a forecaster cannot peek at them by accident.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class Target:
    """One contract to forecast, with everything a forecaster is allowed to know.

    Attributes:
        event_id: the release, for example KXU3-26JUN.
        market_ticker: the contract, for example KXU3-26JUN-T4.2.
        series_key: internal series name such as "unemployment".
        forecast_time: the moment the forecast is made; all inputs are at or before it.
        release_at: scheduled release time (after forecast_time).
        features: as-of feature values (point-in-time; see fct_features_asof).
        market_probability: the market price at forecast_time, if it had one.
    """

    event_id: str
    market_ticker: str
    series_key: str
    forecast_time: datetime
    release_at: datetime
    features: dict[str, Any] = field(default_factory=dict)
    market_probability: float | None = None


class LeakageError(RuntimeError):
    """Raised when an input to a forecaster is newer than the forecast_time."""


def check_no_lookahead(target: Target) -> None:
    """Raise LeakageError if any timestamp in the target's features is after forecast_time.

    The dbt layer already guarantees this; checking again at the point of use means a bug or a
    hand-built target can never silently leak the future into a forecast.
    """
    for name, value in target.features.items():
        if isinstance(value, datetime | pd.Timestamp) and _naive(value) > _naive(
            target.forecast_time
        ):
            raise LeakageError(
                f"{target.market_ticker}: feature {name}={value} is after "
                f"forecast_time={target.forecast_time}"
            )


def _naive(value: datetime) -> datetime:
    """Drop the time zone (all project timestamps are UTC) so values compare safely."""
    return value.replace(tzinfo=None)


class Forecaster(ABC):
    """Base class: a name, a version, and a predict method."""

    #: short stable identifier stored in the predictions table
    name: str
    #: bump when the logic changes so old and new predictions stay distinguishable
    version: str

    @abstractmethod
    def predict(self, target: Target) -> float | None:
        """Return P(YES) in [0, 1], or None when this forecaster cannot forecast the target."""

    def metadata(self, target: Target) -> dict[str, Any]:
        """Extra facts to store with a prediction (training window, prompt hash, ...)."""
        return {}
