import math
import shutil
from datetime import datetime

import duckdb
import numpy as np
import pandas as pd
import pytest

from pmeval.eval.scoring import (
    add_scores,
    brier_score,
    calibration_bins,
    is_possibly_contaminated,
    log_loss,
    write_scores,
)
from pmeval.forecast.run import run


def test_brier_known_values():
    assert brier_score([0.5, 0.9, 0.0], [1, 1, 1]).tolist() == pytest.approx([0.25, 0.01, 1.0])
    assert brier_score([0.5], [0])[0] == 0.25  # always saying 0.5 scores 0.25


def test_log_loss_known_values():
    assert log_loss([0.5], [1])[0] == pytest.approx(math.log(2))
    assert log_loss([0.8], [1])[0] == pytest.approx(-math.log(0.8))
    assert log_loss([0.8], [0])[0] == pytest.approx(-math.log(0.2))


def test_log_loss_is_finite_for_certain_wrong_answers():
    loss = log_loss([0.0, 1.0], [1, 0])
    assert np.isfinite(loss).all() and (loss > 10).all()


def test_perfect_forecast_scores_near_zero():
    assert brier_score([1.0, 0.0], [1, 0]).sum() == 0
    assert log_loss([1.0, 0.0], [1, 0]).max() < 1e-5


def test_calibration_bins_hand_checked():
    p = [0.05, 0.08, 0.55, 0.57, 0.95, 1.0]
    y = [0, 0, 1, 0, 1, 1]
    bins = calibration_bins(p, y, n_bins=10)
    assert bins["count"].tolist() == [2, 2, 2]
    assert bins["bin_lower"].tolist() == pytest.approx([0.0, 0.5, 0.9])
    assert bins["observed_frequency"].tolist() == [0.0, 0.5, 1.0]
    assert bins["mean_predicted"].tolist() == pytest.approx([0.065, 0.56, 0.975])


def test_calibration_probability_one_goes_in_top_bin():
    bins = calibration_bins([1.0], [1], n_bins=5)
    assert bins["bin_upper"].tolist() == [1.0]


def test_contamination_label_only_for_llm_backtests():
    assert is_possibly_contaminated("llm", "backtest")
    assert not is_possibly_contaminated("llm", "live")
    assert not is_possibly_contaminated("market", "backtest")
    assert not is_possibly_contaminated("llm_dry_run", "backtest")


def test_add_scores_columns():
    frame = pd.DataFrame(
        {"probability": [0.8], "outcome": [1], "forecaster": ["llm"], "mode": ["backtest"]}
    )
    scored = add_scores(frame)
    assert scored.loc[0, "brier"] == pytest.approx(0.04)
    assert bool(scored.loc[0, "possibly_contaminated"])


@pytest.fixture
def con(dbt_warehouse, tmp_path):
    copy = tmp_path / "warehouse.duckdb"
    shutil.copy(dbt_warehouse, copy)
    connection = duckdb.connect(str(copy))
    yield connection
    connection.close()


def test_scores_written_to_warehouse(con):
    run(con, ["base_rate", "market"], "backtest", datetime(2026, 10, 1))
    count = write_scores(con, now=datetime(2026, 10, 2))
    assert count > 0
    row = con.execute(
        "select probability, outcome, brier, log_loss from eval.scores "
        "where market_ticker = 'KXU3-26MAY-T4.0' and forecaster = 'market'"
    ).fetchone()
    # market said 0.6 and the contract resolved YES
    assert row[0] == 0.6 and row[1] == 1
    assert row[2] == pytest.approx(0.16) and row[3] == pytest.approx(-math.log(0.6))
    summary = con.execute(
        "select n_contracts, mean_brier from eval.score_summary "
        "where forecaster = 'market' and series_key = 'all'"
    ).fetchone()
    assert summary[0] >= 1
    assert con.execute("select count(*) from eval.calibration_bins").fetchone()[0] > 0


def test_rescoring_is_idempotent(con):
    run(con, ["market"], "backtest", datetime(2026, 10, 1))
    first = write_scores(con, now=datetime(2026, 10, 2))
    second = write_scores(con, now=datetime(2026, 10, 3))
    assert first == second
    assert con.execute("select count(*) from eval.scores").fetchone()[0] == first


def test_unresolved_or_unpredicted_contracts_are_not_scored(con):
    run(con, ["market"], "backtest", datetime(2026, 10, 1))
    write_scores(con)
    tickers = {r[0] for r in con.execute("select market_ticker from eval.scores").fetchall()}
    assert "KXU3-26JUL-T4.5" not in tickers  # it had no market price, so no prediction
