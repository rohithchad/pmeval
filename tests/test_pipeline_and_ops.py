import json
import shutil
from datetime import datetime

import duckdb
import pytest

from pmeval.config import Settings
from pmeval.eval.scoring import write_scores
from pmeval.forecast.run import run
from pmeval.ops import latest_runs, log_run, record_dbt_results, summarise_dbt_results
from pmeval.pipeline import (
    events_awaiting_scoring,
    events_due_for_prediction,
    live_forecaster_names,
)


@pytest.fixture
def con(dbt_warehouse, tmp_path):
    copy = tmp_path / "warehouse.duckdb"
    shutil.copy(dbt_warehouse, copy)
    connection = duckdb.connect(str(copy))
    yield connection
    connection.close()


def test_due_events_are_between_forecast_time_and_release(con):
    # June jobs event: forecast_time 2026-07-01 12:30, release 2026-07-02 12:30
    assert events_due_for_prediction(con, datetime(2026, 7, 1, 13, 0)) == ["KXU3-26JUN"]
    assert events_due_for_prediction(con, datetime(2026, 7, 1, 12, 0)) == []  # too early
    assert events_due_for_prediction(con, datetime(2026, 7, 2, 13, 0)) == []  # already released


def test_events_with_live_predictions_are_not_due_again(con):
    now = datetime(2026, 7, 1, 13, 0)
    run(con, ["market"], "live", now)
    assert events_due_for_prediction(con, now) == []
    # rerunning is a no-op because predictions are insert-only
    assert sum(run(con, ["market"], "live", now).values()) == 0


def test_awaiting_scoring_until_scores_exist(con):
    run(con, ["market"], "live", datetime(2026, 7, 1, 13, 0))
    after_release = datetime(2026, 7, 3)
    assert events_awaiting_scoring(con, after_release) == ["KXU3-26JUN"]
    write_scores(con)
    assert events_awaiting_scoring(con, after_release) == []


def test_awaiting_scoring_gives_up_after_two_weeks(con):
    run(con, ["market"], "live", datetime(2026, 7, 1, 13, 0))
    assert events_awaiting_scoring(con, datetime(2026, 7, 20)) == []


def test_empty_warehouse_returns_nothing():
    empty = duckdb.connect(":memory:")
    assert events_due_for_prediction(empty, datetime(2026, 1, 1)) == []
    assert events_awaiting_scoring(empty, datetime(2026, 1, 1)) == []


def test_llm_only_added_when_a_key_exists():
    assert live_forecaster_names(Settings(_env_file=None, anthropic_api_key=None)) == (
        "base_rate,market,logreg"
    )
    with_key = Settings(_env_file=None, anthropic_api_key="sk-test")
    assert live_forecaster_names(with_key).endswith(",llm")


# ---- ops log ---------------------------------------------------------------------------------


def test_run_log_tracks_last_status_and_last_success():
    con = duckdb.connect(":memory:")
    log_run(con, "daily_ingest", "dbt_build", "ok", now=datetime(2026, 1, 1))
    log_run(con, "daily_ingest", "dbt_build", "failed", "boom", now=datetime(2026, 1, 2))
    row = latest_runs(con).iloc[0]
    assert row["last_status"] == "failed"
    assert row["last_success_at"] == datetime(2026, 1, 1)
    assert row["last_detail"] == "boom"


def test_latest_runs_on_empty_warehouse_is_empty():
    assert latest_runs(duckdb.connect(":memory:")).empty


def test_run_log_rejects_unknown_status():
    con = duckdb.connect(":memory:")
    with pytest.raises(duckdb.ConstraintException):
        log_run(con, "p", "s", "great")


def write_results(path, statuses):
    path.write_text(json.dumps({"results": [{"status": s} for s in statuses]}))
    return path


def test_dbt_results_summary(tmp_path):
    assert summarise_dbt_results(write_results(tmp_path / "a.json", ["pass", "pass"])) == (
        "ok",
        {"pass": 2},
    )
    assert summarise_dbt_results(write_results(tmp_path / "b.json", ["pass", "warn"]))[0] == "warn"
    assert summarise_dbt_results(write_results(tmp_path / "c.json", ["pass", "fail"]))[0] == (
        "failed"
    )


def test_record_dbt_results_writes_run_log(tmp_path):
    con = duckdb.connect(":memory:")
    path = write_results(tmp_path / "r.json", ["pass", "pass", "fail"])
    assert record_dbt_results(con, path, "weekly_quality", "dbt_test") == "failed"
    assert json.loads(latest_runs(con).iloc[0]["last_detail"]) == {"fail": 1, "pass": 2}
