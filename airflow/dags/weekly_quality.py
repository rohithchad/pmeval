"""weekly_quality: run all dbt tests and source freshness checks and record the results.

A failing test fails the task (so the failure alert fires), but the results are recorded in
ops.run_log either way so the dashboard's data-health panel can show them.
"""

from __future__ import annotations

from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator
from pmeval_common import DBT_TARGET, DEFAULT_ARGS, WAREHOUSE_POOL, dbt_command

RECORD = "python -m pmeval.ops dbt-results --path {path} --step {step}"

with DAG(
    dag_id="weekly_quality",
    description="Run dbt tests and source freshness checks; record results",
    schedule="0 7 * * 1",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["pmeval", "quality"],
) as dag:
    # `;` instead of `&&` so results are recorded even when tests fail, then the exit code is kept.
    dbt_test = BashOperator(
        task_id="dbt_test",
        bash_command=(
            f"{dbt_command('test')}; code=$?; "
            + RECORD.format(path=f"{DBT_TARGET}/run_results.json", step="dbt_test")
            + "; exit $code"
        ),
        pool=WAREHOUSE_POOL,
    )
    freshness = BashOperator(
        task_id="source_freshness",
        bash_command=(
            f"{dbt_command('source', 'freshness')}; code=$?; "
            + RECORD.format(path=f"{DBT_TARGET}/sources.json", step="source_freshness")
            + "; exit $code"
        ),
        pool=WAREHOUSE_POOL,
    )

    dbt_test >> freshness
