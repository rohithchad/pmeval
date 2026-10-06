"""post_release: after a release, pull the outcome and score the live predictions.

Runs hourly and skips itself unless some recent event has live predictions that are not yet scored.
Kalshi settles contracts some time after the release, so several runs may pass before the outcome
appears; the 14-day limit in `events_awaiting_scoring` stops retries if it never does.
"""

from __future__ import annotations

from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator, ShortCircuitOperator
from pmeval_common import DEFAULT_ARGS, WAREHOUSE_POOL, dbt_command, record_success


def outcomes_pending() -> bool:
    """True when live predictions exist for released events that are not scored yet."""
    from pmeval.ops import utc_now
    from pmeval.pipeline import events_awaiting_scoring
    from pmeval.warehouse.db import connect

    con = connect()
    try:
        return bool(events_awaiting_scoring(con, utc_now()))
    finally:
        con.close()


with DAG(
    dag_id="post_release",
    description="Pull settled outcomes and score live predictions",
    schedule="@hourly",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["pmeval", "evaluate"],
) as dag:
    check_pending = ShortCircuitOperator(
        task_id="check_outcomes_pending", python_callable=outcomes_pending, pool=WAREHOUSE_POOL
    )
    backfill = BashOperator(
        task_id="kalshi_pull_settled", bash_command="python -m pmeval.ingest.kalshi_historical"
    )
    fred = BashOperator(task_id="fred_first_release", bash_command="python -m pmeval.ingest.fred")
    bronze = BashOperator(
        task_id="load_bronze", bash_command="python -m pmeval.warehouse.bronze", pool=WAREHOUSE_POOL
    )
    dbt_build = BashOperator(
        task_id="dbt_build",
        bash_command=dbt_command("build"),
        pool=WAREHOUSE_POOL,
    )
    score = BashOperator(
        task_id="score_predictions",
        bash_command="python -m pmeval.eval.scoring && python -m pmeval.eval.compare",
        pool=WAREHOUSE_POOL,
    )
    done = PythonOperator(
        task_id="record_success",
        python_callable=record_success,
        op_args=["post_release"],
        pool=WAREHOUSE_POOL,
    )

    check_pending >> [backfill, fred] >> bronze >> dbt_build >> score >> done
