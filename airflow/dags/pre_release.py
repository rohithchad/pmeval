"""pre_release: at forecast time, snapshot data, build features and generate live predictions.

Runs hourly. The first task checks whether any event is due (forecast_time passed, release not yet,
no live prediction yet) and skips everything else when none is. Features are built from timestamps,
so even a run an hour late uses exactly the information available at forecast_time.
"""

from __future__ import annotations

from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator, ShortCircuitOperator
from pmeval_common import DEFAULT_ARGS, WAREHOUSE_POOL, dbt_command, record_success


def events_are_due() -> bool:
    """True when at least one event needs live predictions now."""
    from pmeval.ops import utc_now
    from pmeval.pipeline import events_due_for_prediction
    from pmeval.warehouse.db import connect

    con = connect()
    try:
        return bool(events_due_for_prediction(con, utc_now()))
    finally:
        con.close()


with DAG(
    dag_id="pre_release",
    description="Snapshot forecast-time data, build features and generate live predictions",
    schedule="@hourly",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["pmeval", "forecast"],
) as dag:
    check_due = ShortCircuitOperator(
        task_id="check_events_due", python_callable=events_are_due, pool=WAREHOUSE_POOL
    )
    snapshot_kalshi = BashOperator(
        task_id="snapshot_kalshi_open_markets", bash_command="python -m pmeval.ingest.kalshi_live"
    )
    snapshot_fred = BashOperator(
        task_id="snapshot_fred", bash_command="python -m pmeval.ingest.fred"
    )
    snapshot_fed = BashOperator(
        task_id="snapshot_fed_statements", bash_command="python -m pmeval.ingest.fed_statements"
    )
    bronze = BashOperator(
        task_id="load_bronze", bash_command="python -m pmeval.warehouse.bronze", pool=WAREHOUSE_POOL
    )
    build_features = BashOperator(
        task_id="build_features",
        bash_command=dbt_command("build"),
        pool=WAREHOUSE_POOL,
    )
    predict = BashOperator(
        task_id="generate_live_predictions",
        # "auto" picks base_rate, market, logreg, plus llm only when an API key is configured
        bash_command="python -m pmeval.forecast.run --mode live --forecasters auto",
        pool=WAREHOUSE_POOL,
    )
    done = PythonOperator(
        task_id="record_success",
        python_callable=record_success,
        op_args=["pre_release"],
        pool=WAREHOUSE_POOL,
    )

    check_due >> [snapshot_kalshi, snapshot_fred, snapshot_fed] >> bronze
    bronze >> build_features >> predict >> done
