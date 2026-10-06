"""Scoring rules and the scores tables.

Brier score:  (probability - outcome)^2. Lower is better. 0 is perfect, 0.25 is what always
              answering 0.5 earns, 1 is certain and wrong.
Log loss:     -log(probability assigned to what happened). Lower is better, and it punishes
              confident mistakes harder than Brier does. Probabilities are clipped to
              [1e-6, 1 - 1e-6] so one extreme wrong answer gives a large but finite loss.
Calibration:  of the contracts forecast at about p, did about p of them resolve YES?

Tables written to the warehouse (rebuilt from predictions and outcomes on every run, so reruns are
safe and new outcomes are picked up automatically):
    eval.scores            one row per scored prediction
    eval.score_summary     averages per forecaster, version, mode and series
    eval.calibration_bins  reliability-curve data per forecaster, version and mode

Run:  python -m pmeval.eval.scoring
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import duckdb
import numpy as np
import pandas as pd

from pmeval.config import get_settings
from pmeval.logging_setup import setup_logging
from pmeval.warehouse.db import connect

logger = logging.getLogger(__name__)

LOG_LOSS_EPS = 1e-6
DEFAULT_BINS = 10

SCORING_SQL = """
SELECT
    predictions.event_id,
    predictions.market_ticker,
    predictions.forecaster,
    predictions.version,
    predictions.mode,
    predictions.probability,
    predictions.forecast_time,
    predictions.made_at,
    events.series_key,
    events.release_at,
    outcomes.outcome
FROM forecast.predictions AS predictions
INNER JOIN marts.fct_outcome AS outcomes USING (event_id, market_ticker)
INNER JOIN marts.dim_event AS events USING (event_id)
WHERE outcomes.outcome IS NOT NULL
ORDER BY events.release_at, predictions.market_ticker, predictions.forecaster
"""


def brier_score(probability: pd.Series | np.ndarray, outcome: pd.Series | np.ndarray):
    """Elementwise squared error between probability and the 0/1 outcome."""
    return (np.asarray(probability, dtype=float) - np.asarray(outcome, dtype=float)) ** 2


def log_loss(probability: pd.Series | np.ndarray, outcome: pd.Series | np.ndarray):
    """Elementwise negative log likelihood of the outcome, with clipped probabilities."""
    p = np.clip(np.asarray(probability, dtype=float), LOG_LOSS_EPS, 1 - LOG_LOSS_EPS)
    y = np.asarray(outcome, dtype=float)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def is_possibly_contaminated(forecaster: str, mode: str) -> bool:
    """LLM forecasts of already-resolved events may reflect training data, not forecasting skill."""
    return forecaster == "llm" and mode == "backtest"


def calibration_bins(
    probability: pd.Series | np.ndarray,
    outcome: pd.Series | np.ndarray,
    n_bins: int = DEFAULT_BINS,
) -> pd.DataFrame:
    """Group forecasts into equal-width probability bins; report the YES share in each.

    Columns: bin_lower, bin_upper, count, mean_predicted, observed_frequency. Empty bins are
    omitted. A probability of exactly 1.0 falls in the top bin.
    """
    p = np.asarray(probability, dtype=float)
    y = np.asarray(outcome, dtype=float)
    edges = np.linspace(0, 1, n_bins + 1)
    index = np.clip(np.digitize(p, edges[1:-1], right=False), 0, n_bins - 1)
    rows = []
    for bin_number in range(n_bins):
        mask = index == bin_number
        if mask.any():
            rows.append(
                {
                    "bin_lower": edges[bin_number],
                    "bin_upper": edges[bin_number + 1],
                    "count": int(mask.sum()),
                    "mean_predicted": float(p[mask].mean()),
                    "observed_frequency": float(y[mask].mean()),
                }
            )
    return pd.DataFrame(
        rows,
        columns=["bin_lower", "bin_upper", "count", "mean_predicted", "observed_frequency"],
    )


def load_scoring_frame(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Predictions joined to resolved outcomes, one row per scorable prediction."""
    return con.execute(SCORING_SQL).df()


def add_scores(frame: pd.DataFrame) -> pd.DataFrame:
    """Add brier, log_loss and the contamination label columns."""
    scored = frame.copy()
    scored["brier"] = brier_score(scored["probability"], scored["outcome"])
    scored["log_loss"] = log_loss(scored["probability"], scored["outcome"])
    scored["possibly_contaminated"] = [
        is_possibly_contaminated(f, m)
        for f, m in zip(scored["forecaster"], scored["mode"], strict=True)
    ]
    return scored


def summarise(scored: pd.DataFrame) -> pd.DataFrame:
    """Averages per forecaster, version, mode and series (plus an 'all' row per forecaster)."""
    keys = ["forecaster", "version", "mode", "possibly_contaminated"]
    parts = []
    for series_key, group in [("all", scored), *scored.groupby("series_key")]:
        grouped = group.groupby(keys).agg(
            n_contracts=("outcome", "size"),
            n_events=("event_id", "nunique"),
            mean_brier=("brier", "mean"),
            mean_log_loss=("log_loss", "mean"),
            mean_probability=("probability", "mean"),
            yes_rate=("outcome", "mean"),
        )
        grouped["series_key"] = series_key
        parts.append(grouped.reset_index())
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def calibration_table(scored: pd.DataFrame, n_bins: int = DEFAULT_BINS) -> pd.DataFrame:
    """Calibration bins per forecaster, version and mode."""
    parts = []
    for (forecaster, version, mode), group in scored.groupby(["forecaster", "version", "mode"]):
        bins = calibration_bins(group["probability"], group["outcome"], n_bins)
        bins.insert(0, "mode", mode)
        bins.insert(0, "version", version)
        bins.insert(0, "forecaster", forecaster)
        parts.append(bins)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def write_scores(con: duckdb.DuckDBPyConnection, now: datetime | None = None) -> int:
    """Rebuild eval.scores, eval.score_summary and eval.calibration_bins. Returns scored rows."""
    scored_at = (now or datetime.now(UTC)).replace(tzinfo=None)
    scored = add_scores(load_scoring_frame(con))
    scored["scored_at"] = scored_at
    summary = summarise(scored) if len(scored) else pd.DataFrame()
    calibration = calibration_table(scored) if len(scored) else pd.DataFrame()
    con.execute("CREATE SCHEMA IF NOT EXISTS eval")
    for name, frame in [
        ("scores", scored),
        ("score_summary", summary),
        ("calibration_bins", calibration),
    ]:
        con.register("frame_to_write", frame)
        con.execute(f"CREATE OR REPLACE TABLE eval.{name} AS SELECT * FROM frame_to_write")
        con.unregister("frame_to_write")
    return len(scored)


def main() -> None:
    settings = get_settings()
    setup_logging(settings.pmeval_log_level)
    con = connect()
    try:
        count = write_scores(con)
        logger.info("scored %d predictions", count)
    finally:
        con.close()


if __name__ == "__main__":
    main()
