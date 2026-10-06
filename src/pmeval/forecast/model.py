"""Statistical forecaster: logistic regression on as-of features, trained walk-forward.

Walk-forward means every prediction is made by a model fitted only on contracts that had already
settled at the target's forecast_time. The model never sees an outcome that was unknown then, which
is how it would have run in real life. A model is fitted once per series and forecast_time (all
contracts of an event share a forecast_time) and cached.

One model per series, because the series differ in units and in what a contract asks:
  threshold series (unemployment, CPI, payrolls, GDP): how far the strike is from the latest known
    figure, and the latest change.
  Fed decisions: whether the contract is a hold, a hike or a cut, and the latest change.

Sample sizes are small (tens to a few hundred contracts per series, and contracts of one event are
not independent), so the model is deliberately tiny: two to four inputs and L2 regularisation.
The forecaster declines (returns None) when fewer than `min_train` contracts or only one outcome
class are available, rather than guess.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from pmeval.forecast.base import Forecaster, Target

THRESHOLD_FEATURES = ["strike_minus_last_value", "change_1"]
FED_FEATURES = ["is_hold", "is_hike", "is_cut", "change_1"]


def feature_names(series_key: str) -> list[str]:
    """The model inputs for a series."""
    return FED_FEATURES if series_key == "fed" else THRESHOLD_FEATURES


def add_derived_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Add the Fed action indicators computed from contract_bps (NaN for non-Fed rows)."""
    result = frame.copy()
    bps = result["contract_bps"].astype(float)
    result["is_hold"] = (bps == 0).astype(float).where(bps.notna())
    result["is_hike"] = (bps > 0).astype(float).where(bps.notna())
    result["is_cut"] = (bps < 0).astype(float).where(bps.notna())
    return result


class LogisticModelForecaster(Forecaster):
    """Walk-forward logistic regression.

    Args:
        training_frame: one row per resolved contract with series_key, outcome, settled_at,
            release_at, contract_bps and the feature columns of fct_features_asof.
        min_train: smallest number of earlier contracts needed to fit.
        regularisation: inverse L2 strength C (smaller is stronger).
    """

    name = "logreg"
    version = "1"

    def __init__(
        self, training_frame: pd.DataFrame, min_train: int = 30, regularisation: float = 1.0
    ) -> None:
        self.training_frame = add_derived_features(training_frame).sort_values("settled_at")
        self.min_train = min_train
        self.regularisation = regularisation
        self._cache: dict[tuple[str, pd.Timestamp], tuple[Pipeline | None, dict[str, Any]]] = {}

    def _training_rows(self, series_key: str, forecast_time: pd.Timestamp) -> pd.DataFrame:
        """Contracts of this series settled at or before forecast_time with all inputs present."""
        columns = feature_names(series_key)
        frame = self.training_frame
        rows = frame[(frame["series_key"] == series_key) & (frame["settled_at"] <= forecast_time)]
        return rows.dropna(subset=[*columns, "outcome"])

    def _fit(self, series_key: str, forecast_time: pd.Timestamp) -> tuple[Pipeline | None, dict]:
        """Fit (or fetch from cache) the model for a series as of forecast_time."""
        key = (series_key, forecast_time)
        if key in self._cache:
            return self._cache[key]
        rows = self._training_rows(series_key, forecast_time)
        info: dict[str, Any] = {
            "model": "logistic_regression_l2",
            "features": feature_names(series_key),
            "regularisation": self.regularisation,
            "n_train": len(rows),
        }
        if len(rows) < self.min_train or rows["outcome"].nunique() < 2:
            self._cache[key] = (None, info)
            return self._cache[key]
        info["train_window_start"] = str(rows["release_at"].min())
        info["train_window_end"] = str(rows["settled_at"].max())
        info["train_yes_share"] = float(rows["outcome"].mean())
        pipeline = Pipeline(
            [
                ("scale", StandardScaler()),
                ("logit", LogisticRegression(C=self.regularisation, max_iter=1000)),
            ]
        )
        pipeline.fit(rows[feature_names(series_key)].to_numpy(), rows["outcome"].astype(int))
        self._cache[key] = (pipeline, info)
        return self._cache[key]

    def _target_inputs(self, target: Target) -> np.ndarray | None:
        """The target's feature vector in model order, or None if any input is missing."""
        row = add_derived_features(pd.DataFrame([target.features]))
        values = row[feature_names(target.series_key)].iloc[0].to_numpy(dtype=float)
        return None if np.isnan(values).any() else values.reshape(1, -1)

    def predict(self, target: Target) -> float | None:
        pipeline, _ = self._fit(target.series_key, pd.Timestamp(target.forecast_time))
        inputs = self._target_inputs(target)
        if pipeline is None or inputs is None:
            return None
        return float(pipeline.predict_proba(inputs)[0, 1])

    def metadata(self, target: Target) -> dict[str, Any]:
        _, info = self._fit(target.series_key, pd.Timestamp(target.forecast_time))
        return info
