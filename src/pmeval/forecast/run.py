"""Run forecasters and store their predictions.

Modes:
  backtest  events already released and resolved. Predictions are labeled "backtest".
  live      events whose forecast_time has passed but whose release has not happened yet.
            These are the primary results: they are made before the outcome exists.

LLM forecasting: `llm` calls the Anthropic API (billed per token, key in .env). `llm_dry_run` uses a
stub and never touches the network.

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
from pmeval.forecast.base import Forecaster, Target
from pmeval.forecast.baseline import BaseRateForecaster
from pmeval.forecast.data import (
    frame_to_targets,
    load_history,
    load_model_frame,
    load_target_frame,
)
from pmeval.forecast.llm import (
    AnthropicClient,
    CallLog,
    LlmForecaster,
    StubClient,
    load_statements,
)
from pmeval.forecast.market import MarketForecaster
from pmeval.forecast.model import LogisticModelForecaster
from pmeval.forecast.predictions import build_predictions, write_predictions
from pmeval.logging_setup import setup_logging
from pmeval.pipeline import live_forecaster_names
from pmeval.warehouse.db import connect

logger = logging.getLogger(__name__)


def select_targets(frame: pd.DataFrame, mode: str, now: datetime) -> pd.DataFrame:
    """Pick the contracts to forecast for a mode, as of `now` (naive UTC)."""
    now_ts = pd.Timestamp(now)
    if mode == "live":
        return frame[(frame["forecast_time"] <= now_ts) & (frame["release_at"] > now_ts)]
    return frame[frame["is_resolved"] & (frame["release_at"] <= now_ts)]


def build_llm_forecaster(
    con: duckdb.DuckDBPyConnection, targets: list[Target], dry_run: bool, max_events: int | None
) -> LlmForecaster:
    """Create the LLM forecaster. Real mode needs ANTHROPIC_API_KEY; dry-run never calls the API.

    `max_events` keeps only the most recent N events, as a cost guard.
    """
    settings = get_settings()
    if max_events is not None:
        recent = sorted({t.event_id: t.release_at for t in targets}.items(), key=lambda i: i[1])
        keep = {event_id for event_id, _ in recent[-max_events:]}
        targets = [t for t in targets if t.event_id in keep]
    if dry_run:
        client = StubClient()
    else:
        key = settings.anthropic_api_key
        if key is None or not key.get_secret_value():
            raise RuntimeError("ANTHROPIC_API_KEY is not set; add it to .env or use llm_dry_run")
        client = AnthropicClient(
            key.get_secret_value(), settings.pmeval_llm_model, settings.pmeval_llm_effort
        )
    return LlmForecaster(
        client,
        targets,
        load_statements(con),
        CallLog(con),
        n_samples=settings.pmeval_llm_samples,
        dry_run=dry_run,
    )


def build_forecasters(
    con: duckdb.DuckDBPyConnection,
    names: list[str],
    targets: list[Target],
    llm_max_events: int | None = None,
) -> list[Forecaster]:
    """Create the requested forecasters."""
    available = {
        "base_rate": lambda: BaseRateForecaster(load_history(con)),
        "market": lambda: MarketForecaster(),
        "logreg": lambda: LogisticModelForecaster(load_model_frame(con)),
        "llm": lambda: build_llm_forecaster(con, targets, False, llm_max_events),
        "llm_dry_run": lambda: build_llm_forecaster(con, targets, True, llm_max_events),
    }
    unknown = [name for name in names if name not in available]
    if unknown:
        raise ValueError(f"unknown forecasters: {unknown}; available: {sorted(available)}")
    return [available[name]() for name in names]


def run(
    con: duckdb.DuckDBPyConnection,
    names: list[str],
    mode: str,
    now: datetime,
    llm_max_events: int | None = None,
) -> dict[str, int]:
    """Forecast and store predictions. Returns {forecaster name: rows added}."""
    targets = frame_to_targets(select_targets(load_target_frame(con), mode, now))
    added: dict[str, int] = {}
    for forecaster in build_forecasters(con, names, targets, llm_max_events):
        frame = build_predictions(forecaster, targets, made_at=now, mode=mode)
        added[forecaster.name] = write_predictions(con, frame)
        logger.info("%s: %d new predictions (%s)", forecaster.name, added[forecaster.name], mode)
    return added


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["live", "backtest"], required=True)
    parser.add_argument(
        "--forecasters",
        default="base_rate,market,logreg",
        help="comma-separated names, or 'auto' for the live set (llm only if a key is set)",
    )
    parser.add_argument(
        "--llm-max-events",
        type=int,
        default=None,
        help="cost guard: LLM forecasts at most N events",
    )
    args = parser.parse_args()
    setup_logging(get_settings().pmeval_log_level)
    con = connect()
    try:
        now = datetime.now(UTC).replace(tzinfo=None)
        names = args.forecasters
        if names == "auto":
            names = live_forecaster_names(get_settings())
        run(con, names.split(","), args.mode, now, args.llm_max_events)
    finally:
        con.close()


if __name__ == "__main__":
    main()
