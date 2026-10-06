"""Register raw Parquet datasets as bronze views in DuckDB.

For every `source=<s>/dataset=<d>` directory under the raw root, this creates the view
`bronze.<s>_<d>` over all its Parquet files. Views (not copies) keep bronze always in sync
with raw files and make the loader idempotent: running it twice gives the same result, and
new files show up without a reload. Hive partition columns (`source`, `dataset`,
`ingest_date`) are exposed as columns.

Run:  python -m pmeval.warehouse.bronze
"""

from __future__ import annotations

import logging
from pathlib import Path

import duckdb

from pmeval.config import get_settings
from pmeval.logging_setup import setup_logging
from pmeval.warehouse.db import connect

logger = logging.getLogger(__name__)

BRONZE_SCHEMA = "bronze"

# Every dataset the dbt staging layer reads. If no raw files exist yet for one of them, an
# empty view with the standard raw columns is created so downstream models still compile.
EXPECTED_DATASETS: tuple[tuple[str, str], ...] = (
    ("kalshi", "series"),
    ("kalshi", "events"),
    ("kalshi", "markets"),
    ("kalshi", "candlesticks"),
    ("kalshi", "live_markets"),
    ("kalshi", "live_candlesticks"),
    ("kalshi", "live_trades"),
    ("kalshi", "historical_cutoff"),
    ("kalshi", "historical_markets"),
    ("kalshi", "historical_candlesticks"),
    ("kalshi", "historical_trades"),
    ("fred", "observations"),
    ("fred", "release_calendar"),
    ("fed", "fomc_statements"),
    ("fed", "fomc_meetings"),
)


def discover_datasets(raw_dir: Path) -> list[tuple[str, str]]:
    """Return (source, dataset) pairs that have at least one Parquet file."""
    pairs: list[tuple[str, str]] = []
    for source_dir in sorted(Path(raw_dir).glob("source=*")):
        for dataset_dir in sorted(source_dir.glob("dataset=*")):
            if any(dataset_dir.rglob("*.parquet")):
                pairs.append((source_dir.name.split("=", 1)[1], dataset_dir.name.split("=", 1)[1]))
    return pairs


def load_bronze(con: duckdb.DuckDBPyConnection, raw_dir: Path) -> list[str]:
    """Create or replace one bronze view per raw dataset; returns the view names."""
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {BRONZE_SCHEMA}")
    names: list[str] = []
    for source, dataset in discover_datasets(raw_dir):
        pattern = (
            Path(raw_dir).resolve() / f"source={source}" / f"dataset={dataset}" / "**" / "*.parquet"
        )
        name = f"{source}_{dataset}"
        # union_by_name tolerates files written at different times with extra columns.
        con.execute(
            f"CREATE OR REPLACE VIEW {BRONZE_SCHEMA}.{name} AS "
            f"SELECT * FROM read_parquet('{pattern}', hive_partitioning = true, "
            "union_by_name = true)"
        )
        names.append(name)
    for source, dataset in EXPECTED_DATASETS:
        name = f"{source}_{dataset}"
        if name not in names:
            create_empty_view(con, source, dataset)
    logger.info("registered %d bronze views with data", len(names))
    return names


def create_empty_view(con: duckdb.DuckDBPyConnection, source: str, dataset: str) -> None:
    """Create a zero-row view with the standard raw columns for a dataset with no files yet."""
    con.execute(
        f"CREATE OR REPLACE VIEW {BRONZE_SCHEMA}.{source}_{dataset} AS "
        "SELECT CAST(NULL AS VARCHAR) AS payload, CAST(NULL AS VARCHAR) AS request_params, "
        "CAST(NULL AS TIMESTAMPTZ) AS ingested_at, "
        f"'{source}' AS source, '{dataset}' AS dataset, CAST(NULL AS DATE) AS ingest_date "
        "WHERE false"
    )


def main() -> None:
    settings = get_settings()
    setup_logging(settings.pmeval_log_level)
    con = connect()
    try:
        load_bronze(con, settings.pmeval_raw_dir)
    finally:
        con.close()


if __name__ == "__main__":
    main()
