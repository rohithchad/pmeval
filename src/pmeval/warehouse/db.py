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
    return duckdb.connect(target, read_only=read_only)
