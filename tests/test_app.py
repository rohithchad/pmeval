"""Tests for the dashboard data layer and, headlessly, the Streamlit app itself."""

import shutil
from datetime import datetime
from pathlib import Path

import duckdb
import pytest

from pmeval import app_data
from pmeval.eval.compare import write_comparisons
from pmeval.eval.scoring import write_scores
from pmeval.forecast.run import run
from pmeval.ops import log_run
from pmeval.warehouse.db import connect

APP = Path(__file__).parent.parent / "app" / "streamlit_app.py"


@pytest.fixture(scope="module")
def populated(dbt_warehouse, tmp_path_factory):
    """Fixture warehouse with predictions, scores, comparisons and run-log entries."""
    path = tmp_path_factory.mktemp("app") / "warehouse.duckdb"
    shutil.copy(dbt_warehouse, path)
    con = duckdb.connect(str(path))
    # Live first: predictions are insert-only, so a later backtest never replaces a live forecast.
    run(con, ["market"], "live", datetime(2026, 7, 1, 13, 0))
    run(con, ["base_rate", "market"], "backtest", datetime(2026, 10, 1))
    write_scores(con)
    write_comparisons(con)
    log_run(con, "weekly_quality", "dbt_test", "ok", '{"pass": 57}', now=datetime(2026, 10, 2))
    con.close()
    return path


@pytest.fixture
def con(populated):
    connection = duckdb.connect(str(populated), read_only=True)
    yield connection
    connection.close()


def test_llm_backtests_carry_the_contamination_label():
    assert "possibly contaminated" in app_data.display_name("llm", "backtest")
    assert app_data.display_name("llm", "live") == "LLM"
    assert app_data.display_name("market", "backtest") == "Market price"


def test_leaderboard_and_calibration_read_from_warehouse(con):
    board = app_data.leaderboard(con, "backtest", "brier")
    assert {"market", "base_rate"} <= set(board["forecaster"])
    assert not app_data.calibration(con, "backtest").empty


def test_prediction_log_shows_made_at_and_hides_dry_runs(con):
    log = app_data.prediction_log(con)
    assert "made_at" in log.columns and log["made_at"].notna().all()
    assert "llm_dry_run" not in set(log["forecaster"])
    assert {"live", "backtest"} <= set(log["mode"])


def test_event_contracts_pivot_predictions_per_forecaster(con):
    frame = app_data.event_contracts(con, "KXU3-26MAY")
    assert any(column.startswith("market") for column in frame.columns)
    assert frame.loc[0, "outcome"] == 1


def test_missing_tables_give_empty_frames_not_errors():
    empty = connect(":memory:")
    assert app_data.leaderboard(empty, "live", "brier").empty
    assert app_data.prediction_log(empty).empty
    assert app_data.events(empty).empty
    assert app_data.data_health(empty).empty
    assert set(app_data.counts(empty).values()) == {None}


def test_connection_is_read_only(populated, monkeypatch):
    monkeypatch.setenv("PMEVAL_WAREHOUSE_PATH", str(populated))
    from pmeval.config import get_settings

    get_settings.cache_clear()
    try:
        connection = app_data.open_read_only()
        with pytest.raises(duckdb.Error):
            connection.execute("create table forecast.hack (x integer)")
        connection.close()
    finally:
        get_settings.cache_clear()


def run_app(warehouse: Path, monkeypatch):
    pytest.importorskip("streamlit")
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    from pmeval.config import get_settings

    monkeypatch.setenv("PMEVAL_WAREHOUSE_PATH", str(warehouse))
    get_settings.cache_clear()
    st.cache_resource.clear()  # the app caches its connection; start each test fresh
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    return at


def test_app_renders_with_disclaimer_and_health_panel(populated, monkeypatch):
    at = run_app(populated, monkeypatch)
    assert not at.exception
    text = " ".join(element.value for element in at.warning)
    assert "Not financial advice" in text
    subheaders = {s.value for s in at.subheader}
    expected = {"Data health", "Leaderboard", "Calibration", "Event drill-down", "Prediction log"}
    assert expected <= subheaders
    assert any("dbt_test" in m.value for m in at.markdown)


def test_app_survives_an_empty_warehouse(tmp_path, monkeypatch):
    path = tmp_path / "empty.duckdb"
    duckdb.connect(str(path)).close()
    at = run_app(path, monkeypatch)
    assert not at.exception
    assert any("no data yet" in str(m.value) for m in at.metric)
    assert any("[N]" in i.value for i in at.info)
