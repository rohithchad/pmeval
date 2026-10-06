"""Tests for the Airflow DAG files.

The failure-hook test needs only `requests`. The DAG-structure test needs Apache Airflow itself,
which is installed in the Airflow Docker image (and optionally in CI), so it is skipped locally.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import responses

DAGS = Path(__file__).parent.parent / "airflow" / "dags"
sys.path.insert(0, str(DAGS))

import pmeval_common  # noqa: E402


def failure_context():
    ti = SimpleNamespace(dag_id="daily_ingest", task_id="dbt_build")
    return {"task_instance": ti, "run_id": "manual__1", "exception": RuntimeError("boom")}


def test_notify_failure_records_run_log_and_posts_webhook(monkeypatch, tmp_path):
    monkeypatch.setenv("PMEVAL_WAREHOUSE_PATH", str(tmp_path / "w.duckdb"))
    monkeypatch.setenv("PMEVAL_ALERT_WEBHOOK_URL", "https://hooks.test/alert")
    from pmeval.config import get_settings

    get_settings.cache_clear()
    with responses.RequestsMock() as mock:
        mock.add(responses.POST, "https://hooks.test/alert", status=200)
        pmeval_common.notify_failure(failure_context())
        assert "daily_ingest.dbt_build" in mock.calls[0].request.body
    import duckdb

    row = (
        duckdb.connect(str(tmp_path / "w.duckdb"))
        .execute("select pipeline, step, status from ops.run_log")
        .fetchone()
    )
    assert row == ("daily_ingest", "dbt_build", "failed")
    get_settings.cache_clear()


def test_notify_failure_never_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("PMEVAL_WAREHOUSE_PATH", str(tmp_path / "no_such_dir" / "x" / "w.duckdb"))
    monkeypatch.setenv("PMEVAL_ALERT_WEBHOOK_URL", "https://hooks.test/alert")
    from pmeval.config import get_settings

    get_settings.cache_clear()
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        mock.add(responses.POST, "https://hooks.test/alert", status=500)
        pmeval_common.notify_failure(failure_context())  # must not raise
    get_settings.cache_clear()


def test_default_args_have_retries_backoff_and_alert_hook():
    args = pmeval_common.DEFAULT_ARGS
    assert args["retries"] >= 2 and args["retry_exponential_backoff"] is True
    assert args["on_failure_callback"] is pmeval_common.notify_failure


def test_dbt_command_uses_writable_paths():
    command = pmeval_common.dbt_command("test")
    assert "DBT_TARGET_PATH=/tmp/dbt_target" in command and "--profiles-dir ." in command


EXPECTED = {"daily_ingest", "pre_release", "post_release", "weekly_quality"}


def test_dags_import_cleanly_with_expected_structure():
    pytest.importorskip("airflow")
    from airflow.models import DagBag

    bag = DagBag(dag_folder=str(DAGS), include_examples=False)
    assert bag.import_errors == {}
    assert set(bag.dag_ids) == EXPECTED
    for dag in bag.dags.values():
        assert dag.default_args["retries"] >= 2
        assert dag.max_active_runs == 1
        for task in dag.tasks:
            assert task.on_failure_callback is not None
    # every task that touches the warehouse shares the single-writer pool
    for dag_id, task_id in [
        ("daily_ingest", "dbt_build"),
        ("pre_release", "generate_live_predictions"),
        ("post_release", "score_predictions"),
        ("weekly_quality", "dbt_test"),
    ]:
        assert bag.dags[dag_id].get_task(task_id).pool == "duckdb"
