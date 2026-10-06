"""DuckDB connection helper."""

from __future__ import annotations

from pathlib import Path

import duckdb

from pmeval.config import get_settings


def connect(path: Path | str | None = None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open the warehouse file, creating its parent directory when needed.

    Defaults to the path in settings. Pass ":memory:" for throwaway databases in tests.
    """
    target = str(path) if path is not None else str(get_settings().pmeval_warehouse_path)
    if target != ":memory:" and not read_only:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(target, read_only=read_only)
    # Never let a SQL table name silently resolve to a Python variable or DataFrame of the same
    # name. Data is passed explicitly with register(); a missing table should be a clear error.
    connection.execute("SET python_enable_replacements = false")
    return connection
