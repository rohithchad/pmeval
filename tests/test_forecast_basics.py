import shutil
from datetime import datetime, timedelta

import duckdb
import pandas as pd
import pytest

from pmeval.forecast.base import LeakageError, Target, check_no_lookahead
from pmeval.forecast.baseline import BaseRateForecaster
from pmeval.forecast.data import frame_to_targets, load_target_frame
from pmeval.forecast.market import MarketForecaster
from pmeval.forecast.predictions import (
    COLUMNS,
    PredictionValidationError,
    build_predictions,
    validate_predictions,
    write_predictions,
)
from pmeval.forecast.run import run, select_targets

T0 = datetime(2026, 6, 4, 12, 30)
RELEASE = datetime(2026, 6, 5, 12, 30)


def make_target(**overrides) -> Target:
    values = {
        "event_id": "E1",
        "market_ticker": "E1-T1",
        "series_key": "unemployment",
        "forecast_time": T0,
        "release_at": RELEASE,
        "features": {},
        "market_probability": 0.4,
    }
    values.update(overrides)
    return Target(**values)


def make_row(**overrides) -> dict:
    row = {
        "event_id": "E1",
        "market_ticker": "E1-T1",
        "forecaster": "f",
        "version": "1",
        "probability": 0.5,
        "forecast_time": T0,
        "release_at": RELEASE,
        "made_at": T0,
        "mode": "live",
        "metadata_json": "{}",
    }
    row.update(overrides)
    return row


# ---- predictions schema and validation -------------------------------------------------------


def test_valid_frame_passes():
    frame = pd.DataFrame([make_row()], columns=COLUMNS)
    assert validate_predictions(frame) is frame


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"probability": 1.2}, "probability"),
        ({"probability": None}, "null"),
        ({"mode": "paper"}, "mode"),
        ({"made_at": RELEASE}, "after the release"),
        ({"forecaster": None}, "null"),
    ],
)
def test_invalid_rows_rejected(overrides, message):
    frame = pd.DataFrame([make_row(**overrides)], columns=COLUMNS)
    with pytest.raises(PredictionValidationError, match=message):
        validate_predictions(frame)


def test_duplicate_key_rejected():
    frame = pd.DataFrame([make_row(), make_row()], columns=COLUMNS)
    with pytest.raises(PredictionValidationError, match="duplicate"):
        validate_predictions(frame)


def test_missing_column_rejected():
    with pytest.raises(PredictionValidationError, match="missing"):
        validate_predictions(pd.DataFrame([{"event_id": "E1"}]))


def test_backtest_may_be_made_after_release():
    frame = pd.DataFrame([make_row(mode="backtest", made_at=RELEASE + timedelta(days=9))])
    validate_predictions(frame)


def test_writes_are_insert_only():
    con = duckdb.connect(":memory:")
    first = pd.DataFrame([make_row(probability=0.3)], columns=COLUMNS)
    assert write_predictions(con, first) == 1
    # A rerun with a different probability must not replace the earlier prediction.
    rerun = pd.DataFrame([make_row(probability=0.9)], columns=COLUMNS)
    assert write_predictions(con, rerun) == 0
    assert con.execute("select probability from forecast.predictions").fetchall() == [(0.3,)]


def test_table_check_constraint_blocks_bad_probability():
    con = duckdb.connect(":memory:")
    write_predictions(con, pd.DataFrame([make_row()], columns=COLUMNS))
    with pytest.raises(duckdb.ConstraintException):
        con.execute(
            "insert into forecast.predictions values "
            "('E', 'M', 'f', '1', 1.5, now()::timestamp, now()::timestamp, now()::timestamp, "
            "'live', null)"
        )


# ---- forecasters -----------------------------------------------------------------------------


def test_market_forecaster_returns_price_or_none():
    forecaster = MarketForecaster()
    assert forecaster.predict(make_target(market_probability=0.42)) == 0.42
    assert forecaster.predict(make_target(market_probability=None)) is None


def history() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "series_key": ["unemployment", "unemployment", "unemployment", "cpi"],
            "outcome": [1, 1, 0, 1],
            # the third unemployment contract settles AFTER forecast time and must be ignored
            "settled_at": [
                T0 - timedelta(days=60),
                T0 - timedelta(days=30),
                T0 + timedelta(days=1),
                T0 - timedelta(days=30),
            ],
        }
    )


def test_base_rate_counts_only_contracts_settled_before_forecast_time():
    forecaster = BaseRateForecaster(history())
    target = make_target()
    # 2 YES of 2 known unemployment contracts -> (2 + 1) / (2 + 2)
    assert forecaster.predict(target) == pytest.approx(0.75)
    assert forecaster.metadata(target) == {"yes_count": 2, "contract_count": 2}


def test_base_rate_with_no_history_is_one_half():
    assert BaseRateForecaster(history()).predict(make_target(series_key="gdp")) == 0.5


def test_base_rate_ignores_other_series():
    assert BaseRateForecaster(history()).predict(make_target(series_key="cpi")) == pytest.approx(
        2 / 3
    )


# ---- leakage guard ---------------------------------------------------------------------------


def test_leakage_guard_rejects_future_feature_timestamp():
    leaky = make_target(features={"last_value_available_at": T0 + timedelta(seconds=1)})
    with pytest.raises(LeakageError):
        check_no_lookahead(leaky)


def test_leakage_guard_accepts_equal_and_missing_timestamps():
    check_no_lookahead(make_target(features={"a_at": T0, "b_at": None, "value": 4.2}))


def test_build_predictions_refuses_leaky_targets():
    leaky = make_target(features={"x_available_at": RELEASE})
    with pytest.raises(LeakageError):
        build_predictions(MarketForecaster(), [leaky], made_at=T0, mode="live")


def test_build_predictions_skips_declined_targets():
    targets = [make_target(), make_target(market_ticker="E1-T2", market_probability=None)]
    frame = build_predictions(MarketForecaster(), targets, made_at=T0, mode="live")
    assert frame["market_ticker"].tolist() == ["E1-T1"]


# ---- against the fixture warehouse -----------------------------------------------------------


@pytest.fixture
def con(dbt_warehouse, tmp_path):
    copy = tmp_path / "wh.duckdb"
    shutil.copy(dbt_warehouse, copy)
    connection = duckdb.connect(str(copy))
    yield connection
    connection.close()


def test_every_fixture_target_passes_the_leakage_guard(con):
    for target in frame_to_targets(load_target_frame(con)):
        check_no_lookahead(target)


def test_select_targets_by_mode(con):
    frame = load_target_frame(con)
    assert select_targets(frame, "live", datetime(2026, 7, 1, 13, 0))[
        "event_id"
    ].unique().tolist() == ["KXU3-26JUN"]
    assert select_targets(frame, "live", datetime(2026, 1, 1)).empty
    backtest = select_targets(frame, "backtest", datetime(2026, 10, 1))
    assert "KXU3-26MAY" in backtest["event_id"].tolist()


def test_backtest_run_stores_baseline_and_market_predictions(con):
    now = datetime(2026, 10, 1)
    added = run(con, ["base_rate", "market"], "backtest", now)
    assert added["base_rate"] > 0 and added["market"] > 0
    rows = dict(
        con.execute(
            "select market_ticker || '|' || forecaster, probability from forecast.predictions"
        ).fetchall()
    )
    assert rows["KXU3-26MAY-T4.0|market"] == 0.6
    # nothing in the unemployment series had settled before the May forecast time
    assert rows["KXU3-26MAY-T4.0|base_rate"] == 0.5
    # by the June forecast time the May contract (YES) had settled -> (1 + 1) / (1 + 2)
    assert rows["KXU3-26JUN-T4.2|base_rate"] == pytest.approx(2 / 3)
    # a contract with no price gets no market prediction
    assert "KXU3-26JUL-T4.5|market" not in rows
    # rerunning adds nothing
    assert sum(run(con, ["base_rate", "market"], "backtest", now).values()) == 0
