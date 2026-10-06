"""Load forecast targets and training history from the gold tables."""

from __future__ import annotations

import duckdb
import pandas as pd

from pmeval.forecast.base import Target

# Columns copied into Target.features (everything the models and the LLM are allowed to see).
FEATURE_COLUMNS = [
    "strike",
    "contract_code",
    "contract_bps",
    "last_value",
    "last_observation_date",
    "prev_value",
    "change_1",
    "mean_prior_12",
    "strike_minus_last_value",
    "latest_statement_path",
    "last_value_available_at",
    "prev_value_available_at",
    "latest_statement_published_at",
    "latest_feature_available_at",
]

TARGETS_SQL = f"""
SELECT
    features.event_id,
    features.market_ticker,
    features.series_key,
    events.forecast_time,
    events.release_at,
    events.is_resolved,
    market.market_probability,
    {", ".join("features." + column for column in FEATURE_COLUMNS)}
FROM marts.fct_features_asof AS features
INNER JOIN marts.dim_event AS events USING (event_id)
LEFT JOIN marts.fct_market_forecast AS market USING (event_id, market_ticker)
ORDER BY events.release_at, features.market_ticker
"""

HISTORY_SQL = """
SELECT
    outcomes.event_id,
    outcomes.market_ticker,
    events.series_key,
    events.forecast_time,
    events.release_at,
    outcomes.outcome,
    outcomes.settled_at
FROM marts.fct_outcome AS outcomes
INNER JOIN marts.dim_event AS events USING (event_id)
WHERE outcomes.outcome IS NOT NULL
ORDER BY events.release_at, outcomes.market_ticker
"""


def load_target_frame(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """All contracts with their as-of features, market price and event timing."""
    return con.execute(TARGETS_SQL).df()


def load_history(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Resolved contracts with outcomes. Forecasters must filter it by forecast_time themselves."""
    return con.execute(HISTORY_SQL).df()


def frame_to_targets(frame: pd.DataFrame) -> list[Target]:
    """Convert target rows to Target objects; missing values become None."""
    targets: list[Target] = []
    for row in frame.to_dict("records"):
        features = {column: _none_if_missing(row[column]) for column in FEATURE_COLUMNS}
        targets.append(
            Target(
                event_id=row["event_id"],
                market_ticker=row["market_ticker"],
                series_key=row["series_key"],
                forecast_time=pd.Timestamp(row["forecast_time"]).to_pydatetime(),
                release_at=pd.Timestamp(row["release_at"]).to_pydatetime(),
                features=features,
                market_probability=_none_if_missing(row["market_probability"]),
            )
        )
    return targets


def _none_if_missing(value):
    """pandas uses NaN/NaT for missing values; forecasters expect None."""
    return None if pd.isna(value) else value
