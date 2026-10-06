"""Decisions the orchestration needs, kept in plain Python so they can be unit tested.

The Airflow DAG files stay thin: they call these functions and run the module commands.
"""

from __future__ import annotations

from datetime import datetime

import duckdb

from pmeval.config import Settings

BASE_LIVE_FORECASTERS = ["base_rate", "market", "logreg"]


DUE_SQL = """
SELECT events.event_id
FROM marts.dim_event AS events
WHERE events.forecast_time <= ? AND events.release_at > ?
  {no_prediction_clause}
ORDER BY events.release_at
"""

NO_LIVE_PREDICTION = """
  AND NOT EXISTS (
      SELECT 1 FROM forecast.predictions AS p
      WHERE p.event_id = events.event_id AND p.mode = 'live'
  )
"""


def events_due_for_prediction(con: duckdb.DuckDBPyConnection, now: datetime) -> list[str]:
    """Events whose forecast_time has passed, whose release has not, and with no live prediction.

    Events with no market price get no market prediction and are retried on each run until the
    release passes, which is harmless because predictions are insert-only.
    Returns [] when the warehouse has not been built yet.
    """
    try:
        sql = DUE_SQL.format(no_prediction_clause=NO_LIVE_PREDICTION)
        return [row[0] for row in con.execute(sql, [now, now]).fetchall()]
    except duckdb.CatalogException:
        pass
    try:  # forecast.predictions does not exist yet, so nothing has been predicted
        sql = DUE_SQL.format(no_prediction_clause="")
        return [row[0] for row in con.execute(sql, [now, now]).fetchall()]
    except duckdb.CatalogException:
        return []


AWAITING_SQL = """
SELECT DISTINCT p.event_id
FROM forecast.predictions AS p
INNER JOIN marts.dim_event AS events USING (event_id)
WHERE p.mode = 'live'
  AND events.release_at <= ?
  AND events.release_at > ? - INTERVAL 14 DAY
  {not_scored_clause}
"""

NOT_YET_SCORED = """
  AND NOT EXISTS (
      SELECT 1 FROM eval.scores AS s
      WHERE s.event_id = p.event_id AND s.mode = 'live'
  )
"""


def events_awaiting_scoring(con: duckdb.DuckDBPyConnection, now: datetime) -> list[str]:
    """Events released in the last 14 days with live predictions that are not scored yet.

    The 14-day limit stops the pipeline retrying forever if an outcome never arrives.
    """
    for clause in (NOT_YET_SCORED, ""):  # second form: eval.scores does not exist yet
        try:
            sql = AWAITING_SQL.format(not_scored_clause=clause)
            return [row[0] for row in con.execute(sql, [now, now]).fetchall()]
        except duckdb.CatalogException:
            continue
    return []


def live_forecaster_names(settings: Settings) -> str:
    """Comma-separated forecasters for live runs; `llm` only when an API key is configured."""
    names = list(BASE_LIVE_FORECASTERS)
    key = settings.anthropic_api_key
    if key is not None and key.get_secret_value():
        names.append("llm")
    return ",".join(names)
