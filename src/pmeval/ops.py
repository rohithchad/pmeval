"""Operational run log: what ran, when, and whether it worked.

The Airflow DAGs and the dashboard's data-health panel share this table (ops.run_log), so
the dashboard can show the last successful run and the latest test status while reading only from
the warehouse.

Run:  python -m pmeval.ops log --pipeline daily_ingest --step dbt_build --status ok
      python -m pmeval.ops dbt-results --path target/run_results.json --step dbt_test
"""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pandas as pd

from pmeval.warehouse.db import connect

CREATE_SQL = """
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS ops.run_log (
    run_id       VARCHAR PRIMARY KEY,
    pipeline     VARCHAR   NOT NULL,
    step         VARCHAR   NOT NULL,
    status       VARCHAR   NOT NULL CHECK (status IN ('ok', 'failed', 'warn')),
    recorded_at  TIMESTAMP NOT NULL,
    detail       VARCHAR
);
"""


def utc_now() -> datetime:
    """Current time as naive UTC, the convention used across the warehouse."""
    return datetime.now(UTC).replace(tzinfo=None)


def log_run(
    con: duckdb.DuckDBPyConnection,
    pipeline: str,
    step: str,
    status: str,
    detail: str = "",
    now: datetime | None = None,
) -> None:
    """Append one entry to ops.run_log."""
    con.execute(CREATE_SQL)
    con.execute(
        "INSERT INTO ops.run_log VALUES (?, ?, ?, ?, ?, ?)",
        [str(uuid.uuid4()), pipeline, step, status, now or utc_now(), detail],
    )


def summarise_dbt_results(path: Path) -> tuple[str, dict[str, int]]:
    """Read dbt's run_results.json (or sources.json) and return (status, counts by dbt status)."""
    results = json.loads(Path(path).read_text())["results"]
    counts: dict[str, int] = {}
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    if counts.get("fail", 0) or counts.get("error", 0) or counts.get("runtime error", 0):
        return "failed", counts
    if counts.get("warn", 0):
        return "warn", counts
    return "ok", counts


def record_dbt_results(con: duckdb.DuckDBPyConnection, path: Path, pipeline: str, step: str) -> str:
    """Store the outcome of a dbt test/build/freshness run; returns the overall status."""
    status, counts = summarise_dbt_results(path)
    log_run(con, pipeline, step, status, json.dumps(counts, sort_keys=True))
    return status


def latest_runs(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """The most recent entry for every (pipeline, step), plus the last successful time."""
    try:
        return con.execute(
            """
            SELECT
                pipeline,
                step,
                arg_max(status, recorded_at) AS last_status,
                max(recorded_at) AS last_recorded_at,
                max(recorded_at) FILTER (WHERE status IN ('ok', 'warn')) AS last_success_at,
                arg_max(detail, recorded_at) AS last_detail
            FROM ops.run_log
            GROUP BY pipeline, step
            ORDER BY pipeline, step
            """
        ).df()
    except duckdb.CatalogException:
        return pd.DataFrame(
            columns=[
                "pipeline",
                "step",
                "last_status",
                "last_recorded_at",
                "last_success_at",
                "last_detail",
            ]
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    log = sub.add_parser("log")
    log.add_argument("--pipeline", required=True)
    log.add_argument("--step", required=True)
    log.add_argument("--status", required=True, choices=["ok", "failed", "warn"])
    log.add_argument("--detail", default="")
    dbt = sub.add_parser("dbt-results")
    dbt.add_argument("--path", required=True)
    dbt.add_argument("--pipeline", default="weekly_quality")
    dbt.add_argument("--step", required=True)
    args = parser.parse_args()
    con = connect()
    try:
        if args.command == "log":
            log_run(con, args.pipeline, args.step, args.status, args.detail)
        else:
            record_dbt_results(con, Path(args.path), args.pipeline, args.step)
    finally:
        con.close()


if __name__ == "__main__":
    main()
