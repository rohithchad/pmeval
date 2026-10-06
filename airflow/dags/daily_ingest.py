"""daily_ingest: pull fresh data, load bronze, rebuild dbt models.

Raw ingestion tasks run in parallel (they only write Parquet). Bronze, dbt and the run log use the
single-slot `duckdb` pool.
"""

from __future__ import annotations

from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator
from pmeval_common import DEFAULT_ARGS, WAREHOUSE_POOL, dbt_command, record_success

with DAG(
    dag_id="daily_ingest",
    description="Ingest Kalshi, FRED, release calendar and Fed statements; rebuild bronze and dbt",
    schedule="0 6 * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["pmeval", "ingest"],
) as dag:
    kalshi_live = BashOperator(
        task_id="kalshi_live_open_markets",
        bash_command="python -m pmeval.ingest.kalshi_live",
    )
    kalshi_backfill = BashOperator(
        task_id="kalshi_backfill_settled",
        bash_command="python -m pmeval.ingest.kalshi_historical",
        execution_timeout=None,
    )
    fred = BashOperator(task_id="fred_first_release", bash_command="python -m pmeval.ingest.fred")
    calendar = BashOperator(
        task_id="release_calendar", bash_command="python -m pmeval.ingest.release_calendar"
    )
    fed = BashOperator(
        task_id="fed_statements", bash_command="python -m pmeval.ingest.fed_statements"
    )
    bronze = BashOperator(
        task_id="load_bronze",
        bash_command="python -m pmeval.warehouse.bronze",
        pool=WAREHOUSE_POOL,
    )
    dbt_build = BashOperator(
        task_id="dbt_build",
        bash_command=dbt_command("build"),
        pool=WAREHOUSE_POOL,
    )
    done = PythonOperator(
        task_id="record_success",
        python_callable=record_success,
        op_args=["daily_ingest"],
        pool=WAREHOUSE_POOL,
    )

    kalshi_live >> kalshi_backfill  # the backfill reuses the Kalshi rate budget, so run it after
    [kalshi_backfill, fred, calendar, fed] >> bronze >> dbt_build >> done
