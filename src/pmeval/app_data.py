"""Read-only data access for the dashboard.

Every function takes a read-only DuckDB connection and returns a DataFrame. Missing tables (the
pipeline has not produced them yet) give empty frames, so the dashboard shows "no data yet" instead
of failing. Nothing here writes to the warehouse.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from pmeval.ops import latest_runs
from pmeval.warehouse.db import connect

# Fixed colour slot per forecaster (order from the validated categorical palette), so a forecaster
# keeps its colour whatever else is filtered in or out.
FORECASTER_ORDER = ["base_rate", "market", "logreg", "llm"]
FORECASTER_COLORS = {
    "base_rate": "#2a78d6",
    "market": "#eb6834",
    "logreg": "#1baf7a",
    "llm": "#e87ba4",
}
FORECASTER_LABELS = {
    "base_rate": "Base rate",
    "market": "Market price",
    "logreg": "Logistic model",
    "llm": "LLM",
}
CONTAMINATION_NOTE = "possibly contaminated by training data"


def open_read_only() -> duckdb.DuckDBPyConnection:
    """Open the warehouse file read-only; the dashboard can never modify it."""
    return connect(read_only=True)


def display_name(forecaster: str, mode: str) -> str:
    """Human label; LLM backtests always carry the contamination warning."""
    label = FORECASTER_LABELS.get(forecaster, forecaster)
    if forecaster == "llm" and mode == "backtest":
        return f"{label} ({CONTAMINATION_NOTE})"
    return label


def _query(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> pd.DataFrame:
    try:
        return con.execute(sql, params or []).df()
    except duckdb.CatalogException:
        return pd.DataFrame()


def leaderboard(con: duckdb.DuckDBPyConnection, mode: str, metric: str) -> pd.DataFrame:
    """Leaderboard rows (mean score with bootstrap interval) for one mode and metric."""
    frame = _query(
        con,
        "SELECT * FROM eval.leaderboard WHERE mode = ? AND metric = ? ORDER BY mean_score",
        [mode, metric],
    )
    if not frame.empty:
        frame["label"] = [display_name(f, mode) for f in frame["forecaster"]]
    return frame


def comparisons(con: duckdb.DuckDBPyConnection, mode: str, metric: str) -> pd.DataFrame:
    """Pairwise comparison rows for one mode and metric."""
    return _query(
        con,
        "SELECT * FROM eval.comparisons WHERE mode = ? AND metric = ? ORDER BY forecaster_a",
        [mode, metric],
    )


def calibration(con: duckdb.DuckDBPyConnection, mode: str) -> pd.DataFrame:
    """Calibration bins for one mode, with display labels."""
    frame = _query(con, "SELECT * FROM eval.calibration_bins WHERE mode = ?", [mode])
    if not frame.empty:
        frame["label"] = [display_name(f, mode) for f in frame["forecaster"]]
    return frame


def events(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """All events, newest release first."""
    return _query(
        con,
        "SELECT event_id, series_key, release_at, forecast_time, release_time_source, is_resolved "
        "FROM marts.dim_event ORDER BY release_at DESC",
    )


def event_contracts(con: duckdb.DuckDBPyConnection, event_id: str) -> pd.DataFrame:
    """Per contract: strike, outcome, and every forecaster's probability for one event."""
    base = _query(
        con,
        """
        SELECT features.market_ticker, features.strike, features.contract_code,
               features.last_value, features.prev_value, outcomes.outcome
        FROM marts.fct_features_asof AS features
        LEFT JOIN marts.fct_outcome AS outcomes USING (event_id, market_ticker)
        WHERE features.event_id = ?
        ORDER BY features.strike, features.market_ticker
        """,
        [event_id],
    )
    predictions = _query(
        con,
        "SELECT market_ticker, forecaster, probability, mode FROM forecast.predictions "
        "WHERE event_id = ? AND forecaster <> 'llm_dry_run'",
        [event_id],
    )
    if base.empty or predictions.empty:
        return base
    wide = predictions.pivot_table(
        index="market_ticker", columns=["forecaster", "mode"], values="probability"
    )
    wide.columns = [f"{forecaster} ({mode})" for forecaster, mode in wide.columns]
    return base.merge(wide.reset_index(), on="market_ticker", how="left")


def prediction_log(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Every stored prediction, newest first, with made_at and the contamination label."""
    frame = _query(
        con,
        """
        SELECT made_at, event_id, market_ticker, forecaster, version, mode, probability,
               forecast_time, release_at
        FROM forecast.predictions
        WHERE forecaster <> 'llm_dry_run'
        ORDER BY made_at DESC, event_id, market_ticker
        """,
    )
    if not frame.empty:
        frame["note"] = [
            CONTAMINATION_NOTE if f == "llm" and m == "backtest" else ""
            for f, m in zip(frame["forecaster"], frame["mode"], strict=True)
        ]
    return frame


def data_health(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Last status and last success per pipeline step, from ops.run_log."""
    return latest_runs(con)


def counts(con: duckdb.DuckDBPyConnection) -> dict[str, int | None]:
    """Row counts for the headline numbers; None when a table does not exist yet."""
    queries = {
        "events": "SELECT count(*) FROM marts.dim_event",
        "resolved_events": "SELECT count(*) FROM marts.dim_event WHERE is_resolved",
        "live_predictions": "SELECT count(*) FROM forecast.predictions WHERE mode = 'live'",
        "backtest_predictions": "SELECT count(*) FROM forecast.predictions WHERE mode = 'backtest'",
        "scored_predictions": "SELECT count(*) FROM eval.scores",
    }
    result: dict[str, int | None] = {}
    for name, sql in queries.items():
        try:
            result[name] = con.execute(sql).fetchone()[0]
        except duckdb.CatalogException:
            result[name] = None
    return result
