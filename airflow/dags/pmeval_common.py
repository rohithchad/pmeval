"""Shared settings and helpers for the pmeval DAGs.

Design rules followed by every DAG:
- Idempotent tasks: ingestion appends new raw files (dbt de-duplicates), bronze uses CREATE OR
  REPLACE, dbt rebuilds models, predictions are insert-only and scores are rebuilt from scratch.
  Re-running any task or any DAG run is safe.
- Retries with exponential backoff on every task.
- One writer to the DuckDB file at a time: every task that touches the warehouse uses the
  `duckdb` pool (1 slot, created by airflow-init). Raw ingestion only writes Parquet and runs
  freely.
- Failures call `notify_failure`: it logs, records the failure in ops.run_log, and posts to an
  optional webhook (PMEVAL_ALERT_WEBHOOK_URL).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import timedelta

import requests

logger = logging.getLogger(__name__)

DBT_BIN = "/home/airflow/dbt-venv/bin/dbt"
DBT_DIR = "/opt/airflow/dbt_project"
DBT_TARGET = "/tmp/dbt_target"  # writable inside the container; run_results.json lands here
DBT_LOGS = "/tmp/dbt_logs"
WAREHOUSE_POOL = "duckdb"

DEFAULT_ARGS = {
    "owner": "pmeval",
    "retries": 3,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=60),
    "on_failure_callback": None,  # filled in below, after notify_failure is defined
}


def dbt_command(*args: str) -> str:
    """Shell command that runs dbt inside the dbt virtualenv against the shared warehouse."""
    joined = " ".join(args)
    return (
        f"cd {DBT_DIR} && DBT_TARGET_PATH={DBT_TARGET} DBT_LOG_PATH={DBT_LOGS} "
        f"{DBT_BIN} {joined} --profiles-dir ."
    )


def notify_failure(context: dict) -> None:
    """Airflow failure callback: log, record in ops.run_log, and post to the alert webhook.

    Nothing secret is included in the message. Alerting must never break the pipeline, so every
    error here is logged and swallowed.
    """
    task_instance = context.get("task_instance")
    dag_id = getattr(task_instance, "dag_id", "unknown")
    task_id = getattr(task_instance, "task_id", "unknown")
    message = f"pmeval task failed: {dag_id}.{task_id} (run {context.get('run_id')})"
    logger.error(message)
    try:
        from pmeval.ops import log_run
        from pmeval.warehouse.db import connect

        con = connect()
        try:
            log_run(con, dag_id, task_id, "failed", str(context.get("exception"))[:500])
        finally:
            con.close()
    except Exception:  # the warehouse may be locked or missing; the log line above remains
        logger.exception("could not record failure in ops.run_log")
    webhook = os.environ.get("PMEVAL_ALERT_WEBHOOK_URL")
    if webhook:
        try:
            requests.post(webhook, data=json.dumps({"text": message}), timeout=10)
        except requests.RequestException:
            logger.exception("alert webhook failed")


DEFAULT_ARGS["on_failure_callback"] = notify_failure


def record_success(pipeline: str, step: str = "dag_run") -> None:
    """Final task of a DAG: note in ops.run_log that the whole run succeeded."""
    from pmeval.ops import log_run
    from pmeval.warehouse.db import connect

    con = connect()
    try:
        log_run(con, pipeline, step, "ok")
    finally:
        con.close()
