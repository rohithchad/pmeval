"""Raw layer writer: untouched API responses saved as Parquet.

Each row is one API record kept as its original JSON text, plus the request
parameters and an ingested_at timestamp. Keeping JSON text means the raw layer
never loses or reinterprets fields; typing happens later in dbt staging.

Layout (Hive style, so DuckDB can read it with hive_partitioning):

    <raw_dir>/source=<source>/dataset=<dataset>/ingest_date=<YYYY-MM-DD>/<file>.parquet
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd


def write_raw(
    raw_dir: Path,
    source: str,
    dataset: str,
    records: Iterable[dict[str, Any]],
    request_params: dict[str, Any] | None = None,
    ingested_at: datetime | None = None,
) -> Path | None:
    """Write records to a new Parquet file and return its path.

    Returns None when there are no records, so empty pages leave no empty files.
    Every call writes a new file (named by its microsecond timestamp); duplicates
    across runs are removed later in dbt staging, not here.
    """
    rows = list(records)
    if not rows:
        return None
    stamp = ingested_at or datetime.now(UTC)
    params_json = json.dumps(request_params or {}, sort_keys=True)
    frame = pd.DataFrame(
        {
            "payload": [json.dumps(row, sort_keys=True) for row in rows],
            "request_params": params_json,
            "ingested_at": pd.Timestamp(stamp),
        }
    )
    directory = (
        Path(raw_dir)
        / f"source={source}"
        / f"dataset={dataset}"
        / f"ingest_date={stamp.date().isoformat()}"
    )
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stamp.strftime('%Y%m%dT%H%M%S%f')}.parquet"
    frame.to_parquet(path, index=False)
    return path
