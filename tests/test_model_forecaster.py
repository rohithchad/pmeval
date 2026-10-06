from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from pmeval.forecast.base import Target
from pmeval.forecast.data import FEATURE_COLUMNS
from pmeval.forecast.model import LogisticModelForecaster, add_derived_features

START = datetime(2024, 1, 1, 12, 30)


def training_frame(n_events: int = 20, contracts: int = 4, seed: int = 0) -> pd.DataFrame:
    """Synthetic unemployment history: YES exactly when the strike is below the true value."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n_events):
        release_at = START + timedelta(days=30 * i)
        last_value = 4.0 + rng.normal(0, 0.1)
        truth = last_value + rng.normal(0, 0.2)
        for strike in np.linspace(last_value - 0.4, last_value + 0.4, contracts):
            rows.append(
                {
                    "series_key": "unemployment",
                    "event_id": f"E{i}",
                    "market_ticker": f"E{i}-{strike:.2f}",
                    "forecast_time": release_at - timedelta(days=1),
                    "release_at": release_at,
                    "settled_at": release_at + timedelta(hours=1),
                    "outcome": int(truth > strike),
                    "strike_minus_last_value": strike - last_value,
                    "change_1": rng.normal(0, 0.1),
                    "contract_bps": np.nan,
                }
            )
    return pd.DataFrame(rows)


def make_target(frame: pd.DataFrame, event: int, strike_gap: float) -> Target:
    release_at = START + timedelta(days=30 * event)
    features = {column: None for column in FEATURE_COLUMNS}
    features.update({"strike_minus_last_value": strike_gap, "change_1": 0.0})
    return Target(
        event_id=f"E{event}",
        market_ticker=f"E{event}-x",
        series_key="unemployment",
        forecast_time=release_at - timedelta(days=1),
        release_at=release_at,
        features=features,
    )


def test_declines_when_not_enough_history():
    frame = training_frame()
    forecaster = LogisticModelForecaster(frame, min_train=30)
    # the first event has no earlier settled contracts
    assert forecaster.predict(make_target(frame, 0, 0.1)) is None
    # the third event has only 8 earlier contracts, below the minimum of 30
    assert forecaster.predict(make_target(frame, 2, 0.1)) is None


def test_learns_that_higher_strikes_are_less_likely():
    frame = training_frame()
    forecaster = LogisticModelForecaster(frame, min_train=30)
    low = forecaster.predict(make_target(frame, 15, -0.3))
    high = forecaster.predict(make_target(frame, 15, 0.3))
    assert low is not None and high is not None
    assert 0 <= high < low <= 1
    assert low > 0.7 and high < 0.3


def test_training_uses_only_contracts_settled_before_forecast_time():
    frame = training_frame()
    target = make_target(frame, 10, 0.0)
    forecaster = LogisticModelForecaster(frame, min_train=10)
    info = forecaster.metadata(target)
    # Event 10 forecasts one day before its release, 29 days after event 9 settled, so events
    # 0..9 (4 contracts each) are known and nothing later is.
    assert info["n_train"] == 40
    assert pd.Timestamp(info["train_window_end"]) <= pd.Timestamp(target.forecast_time)


def test_future_outcomes_cannot_change_a_prediction():
    frame = training_frame()
    target = make_target(frame, 10, 0.0)
    baseline = LogisticModelForecaster(frame, min_train=10).predict(target)
    # Add contradictory outcomes that settle AFTER the target's forecast time.
    poison = frame[frame["event_id"].isin(["E10", "E11", "E12"])].copy()
    poison["outcome"] = 1 - poison["outcome"]
    poison["settled_at"] = pd.Timestamp(target.forecast_time) + timedelta(hours=1)
    poisoned = pd.concat([frame, poison], ignore_index=True)
    assert LogisticModelForecaster(poisoned, min_train=10).predict(target) == baseline


def test_missing_inputs_decline():
    frame = training_frame()
    target = make_target(frame, 15, 0.1)
    target.features["change_1"] = None
    assert LogisticModelForecaster(frame, min_train=10).predict(target) is None


def test_metadata_records_training_window_and_settings():
    frame = training_frame()
    target = make_target(frame, 15, 0.1)
    info = LogisticModelForecaster(frame, min_train=10).metadata(target)
    assert info["features"] == ["strike_minus_last_value", "change_1"]
    assert info["n_train"] == 60 and info["regularisation"] == 1.0
    assert "train_window_start" in info and "train_window_end" in info


def test_model_is_fitted_once_per_series_and_forecast_time():
    frame = training_frame()
    forecaster = LogisticModelForecaster(frame, min_train=10)
    forecaster.predict(make_target(frame, 15, 0.1))
    forecaster.predict(make_target(frame, 15, -0.1))
    assert len(forecaster._cache) == 1


def test_fed_indicators_derived_from_contract_bps():
    frame = pd.DataFrame({"contract_bps": [0.0, 25.0, -25.0, np.nan]})
    derived = add_derived_features(frame)
    assert derived["is_hold"].tolist()[:3] == [1.0, 0.0, 0.0]
    assert derived["is_hike"].tolist()[:3] == [0.0, 1.0, 0.0]
    assert derived["is_cut"].tolist()[:3] == [0.0, 0.0, 1.0]
    assert derived["is_hold"].isna().tolist()[3]


def test_deterministic():
    frame = training_frame()
    target = make_target(frame, 15, 0.1)
    first = LogisticModelForecaster(frame, min_train=10).predict(target)
    second = LogisticModelForecaster(frame, min_train=10).predict(target)
    assert first == pytest.approx(second, abs=0)
