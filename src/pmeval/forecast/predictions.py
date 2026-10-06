"""The predictions table: schema, validation and insert-only storage.

Every forecast any forecaster makes ends up in forecast.predictions, one row per
(event, contract, forecaster, version). Rows are never overwritten: a rerun keeps the first
prediction, so a live forecast made before a release cannot be replaced after the outcome is known.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from typing import Any

import duckdb
import pandas as pd

from pmeval.forecast.base import Forecaster, Target, check_no_lookahead

MODES = ("live", "backtest")

COLUMNS = [
    "event_id",
    "market_ticker",
    "forecaster",
    "version",
    "probability",
    "forecast_time",
    "release_at",
    "made_at",
    "mode",
    "metadata_json",
]

CREATE_TABLE_SQL = """
CREATE SCHEMA IF NOT EXISTS forecast;
CREATE TABLE IF NOT EXISTS forecast.predictions (
    event_id        VARCHAR   NOT NULL,
    market_ticker   VARCHAR   NOT NULL,
    forecaster      VARCHAR   NOT NULL,
    version         VARCHAR   NOT NULL,
    probability     DOUBLE    NOT NULL CHECK (probability BETWEEN 0 AND 1),
    forecast_time   TIMESTAMP NOT NULL,
    release_at      TIMESTAMP NOT NULL,
    made_at         TIMESTAMP NOT NULL,
    mode            VARCHAR   NOT NULL CHECK (mode IN ('live', 'backtest')),
    metadata_json   VARCHAR,
    PRIMARY KEY (event_id, market_ticker, forecaster, version)
);
"""


class PredictionValidationError(ValueError):
    """Raised when a predictions DataFrame breaks the schema rules."""


def validate_predictions(frame: pd.DataFrame) -> pd.DataFrame:
    """Check a predictions DataFrame and return it unchanged, or raise with every problem found.

    Rules: all columns present and non-null (except metadata_json); probability in [0, 1];
    mode is live or backtest; the key is unique; live predictions are made before the release.
    """
    problems: list[str] = []
    missing = [column for column in COLUMNS if column not in frame.columns]
    if missing:
        raise PredictionValidationError(f"missing columns: {missing}")

    required = [column for column in COLUMNS if column != "metadata_json"]
    for column in required:
        if frame[column].isna().any():
            problems.append(f"{column} has null values")
    if not frame["probability"].between(0, 1).all():
        problems.append("probability outside [0, 1]")
    if not frame["mode"].isin(MODES).all():
        problems.append(f"mode must be one of {MODES}")
    key = ["event_id", "market_ticker", "forecaster", "version"]
    if frame.duplicated(subset=key).any():
        problems.append("duplicate (event_id, market_ticker, forecaster, version)")
    live = frame[frame["mode"] == "live"]
    if (live["made_at"] >= live["release_at"]).any():
        problems.append("live prediction made at or after the release time")
    if problems:
        raise PredictionValidationError("; ".join(problems))
    return frame


def build_predictions(
    forecaster: Forecaster,
    targets: Iterable[Target],
    made_at: datetime,
    mode: str,
) -> pd.DataFrame:
    """Run a forecaster over targets and return a validated predictions DataFrame.

    Targets the forecaster declines (returns None) are skipped.
    """
    rows: list[dict[str, Any]] = []
    for target in targets:
        check_no_lookahead(target)
        probability = forecaster.predict(target)
        if probability is None:
            continue
        rows.append(
            {
                "event_id": target.event_id,
                "market_ticker": target.market_ticker,
                "forecaster": forecaster.name,
                "version": forecaster.version,
                "probability": float(probability),
                "forecast_time": target.forecast_time,
                "release_at": target.release_at,
                "made_at": made_at,
                "mode": mode,
                "metadata_json": json.dumps(
                    forecaster.metadata(target), sort_keys=True, default=str
                ),
            }
        )
    frame = pd.DataFrame(rows, columns=COLUMNS)
    if frame.empty:
        return frame
    return validate_predictions(frame)


def ensure_table(con: duckdb.DuckDBPyConnection) -> None:
    """Create the predictions table if it does not exist."""
    con.execute(CREATE_TABLE_SQL)


def write_predictions(con: duckdb.DuckDBPyConnection, frame: pd.DataFrame) -> int:
    """Insert predictions, keeping existing rows on key conflicts. Returns rows actually added."""
    ensure_table(con)
    if frame.empty:
        return 0
    validate_predictions(frame)
    before = con.execute("SELECT count(*) FROM forecast.predictions").fetchone()[0]
    con.register("new_predictions", frame[COLUMNS])
    con.execute(
        f"INSERT INTO forecast.predictions ({', '.join(COLUMNS)}) "
        f"SELECT {', '.join(COLUMNS)} FROM new_predictions ON CONFLICT DO NOTHING"
    )
    con.unregister("new_predictions")
    return con.execute("SELECT count(*) FROM forecast.predictions").fetchone()[0] - before
