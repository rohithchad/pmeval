"""Run forecasters and store their predictions.

Modes:
  backtest  events already released and resolved. Predictions are labeled "backtest".
  live      events whose forecast_time has passed but whose release has not happened yet.
            These are the primary results: they are made before the outcome exists.

Run:  python -m pmeval.forecast.run --mode live
      python -m pmeval.forecast.run --mode backtest --forecasters base_rate,market
"""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime

import duckdb
import pandas as pd

from pmeval.config import get_settings
from pmeval.forecast.base import Forecaster
from pmeval.forecast.baseline import BaseRateForecaster
from pmeval.forecast.data import frame_to_targets, load_history, load_target_frame
from pmeval.forecast.market import MarketForecaster
from pmeval.forecast.predictions import build_predictions, write_predictions
from pmeval.logging_setup import setup_logging
from pmeval.warehouse.db import connect

logger = logging.getLogger(__name__)


def select_targets(frame: pd.DataFrame, mode: str, now: datetime) -> pd.DataFrame:
    """Pick the contracts to forecast for a mode, as of `now` (naive UTC)."""
    now_ts = pd.Timestamp(now)
    if mode == "live":
        return frame[(frame["forecast_time"] <= now_ts) & (frame["release_at"] > now_ts)]
    return frame[frame["is_resolved"] & (frame["release_at"] <= now_ts)]


def build_forecasters(con: duckdb.DuckDBPyConnection, names: list[str]) -> list[Forecaster]:
    """Create the requested forecasters. Later commits register more names here."""
    available = {
        "base_rate": lambda: BaseRateForecaster(load_history(con)),
        "market": lambda: MarketForecaster(),
    }
    unknown = [name for name in names if name not in available]
    if unknown:
        raise ValueError(f"unknown forecasters: {unknown}; available: {sorted(available)}")
    return [available[name]() for name in names]


def run(
    con: duckdb.DuckDBPyConnection, names: list[str], mode: str, now: datetime
) -> dict[str, int]:
    """Forecast and store predictions. Returns {forecaster name: rows added}."""
    targets = frame_to_targets(select_targets(load_target_frame(con), mode, now))
    added: dict[str, int] = {}
    for forecaster in build_forecasters(con, names):
        frame = build_predictions(forecaster, targets, made_at=now, mode=mode)
        added[forecaster.name] = write_predictions(con, frame)
        logger.info("%s: %d new predictions (%s)", forecaster.name, added[forecaster.name], mode)
    return added


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["live", "backtest"], required=True)
    parser.add_argument("--forecasters", default="base_rate,market")
    args = parser.parse_args()
    setup_logging(get_settings().pmeval_log_level)
    con = connect()
    try:
        now = datetime.now(UTC).replace(tzinfo=None)
        run(con, args.forecasters.split(","), args.mode, now)
    finally:
        con.close()


if __name__ == "__main__":
    main()
