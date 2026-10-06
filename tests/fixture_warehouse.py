"""Builds a small warehouse from synthetic fixtures so dbt can run without network access.

IMPORTANT: the numbers here are made up for testing (marked synthetic). Field names and shapes
copy the real recorded API responses in tests/fixtures. Used by pytest and by CI for
`dbt build`.

Usage:  python tests/fixture_warehouse.py <raw_dir> <warehouse.duckdb>
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

from pmeval.ingest.raw import write_raw
from pmeval.warehouse.bronze import load_bronze
from pmeval.warehouse.db import connect

INGESTED = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def kalshi_market(ticker: str, event: str, strike: float, result: str, close: str) -> dict:
    """A settled market in the historical-tier shape (field names as in the real API)."""
    return {
        "ticker": ticker,
        "event_ticker": event,
        "title": f"Synthetic market {ticker}",
        "subtitle": f"{strike}%",
        "status": "finalized",
        "result": result,
        "rules_primary": f"Synthetic rule: resolves Yes if the value is above {strike}.",
        "strike_type": "greater",
        "floor_strike": strike,
        "expiration_value": "synthetic",
        "open_time": "2026-04-01T00:30:00Z",
        "close_time": close,
        "expected_expiration_time": close,
        "occurrence_datetime": close,
        "settlement_ts": close,
        "last_price_dollars": "0.0100",
        "volume_fp": "100.00",
    }


def historical_candle(end_ts: int, close: str | None, previous: str) -> dict:
    """A historical-tier candlestick (no _dollars suffix). close=None means no trades."""
    return {
        "end_period_ts": end_ts,
        "price": {"close": close, "previous": previous, "open": close, "mean": close},
        "yes_bid": {"close": "0.3000"},
        "yes_ask": {"close": "0.3500"},
        "volume": "5.00",
        "open_interest": "50.00",
    }


def live_candle(end_ts: int, close: str | None) -> dict:
    """A live-tier candlestick (_dollars suffix). An empty price object means no trades."""
    price = {"close_dollars": close, "previous_dollars": "0.2000"} if close else {}
    return {
        "end_period_ts": end_ts,
        "price": price,
        "yes_bid": {"close_dollars": "0.3000"},
        "yes_ask": {"close_dollars": "0.3500"},
        "volume_fp": "5.00",
        "open_interest_fp": "50.00",
    }


def write_kalshi(raw: Path) -> None:
    """Two settled unemployment events, one in each tier, with candles around forecast time."""
    may = [kalshi_market("KXU3-26MAY-T4.0", "KXU3-26MAY", 4.0, "yes", "2026-06-05T12:29:00Z")]
    jun = [kalshi_market("KXU3-26JUN-T4.2", "KXU3-26JUN", 4.2, "no", "2026-07-02T12:29:00Z")]
    write_raw(raw, "kalshi", "historical_markets", may, ingested_at=INGESTED)
    # July: no matching row in the release calendar, so dim_event must fall back to close time.
    jul = [kalshi_market("KXU3-26JUL-T4.5", "KXU3-26JUL", 4.5, "no", "2026-08-07T12:29:00Z")]
    # Fed decision on 2026-06-17 (hold). Kalshi closes it at 2:00 p.m. ET; the statement
    # is published at 18:00 UTC.
    fed = [
        kalshi_market(
            "KXFEDDECISION-26JUN-H0", "KXFEDDECISION-26JUN", 0.0, "yes", "2026-06-17T17:59:00Z"
        )
    ]
    # An older event named without the KX prefix, as Kalshi's early Fed events are.
    legacy = [
        kalshi_market(
            "FEDDECISION-26APR-H0", "FEDDECISION-26APR", 0.0, "yes", "2026-04-29T17:59:00Z"
        )
    ]
    # Kalshi's oldest payroll events are named PROLLS; they must still map to the payrolls series.
    prolls = [kalshi_market("PROLLS-23MAR-T0", "PROLLS-23MAR", 0.0, "yes", "2023-04-07T12:25:00Z")]
    write_raw(
        raw, "kalshi", "live_markets", jun + jul + fed + legacy + prolls, ingested_at=INGESTED
    )
    # May event: release 2026-06-05 12:30Z, forecast_time 2026-06-04 12:30Z (epoch 1780576200).
    forecast = 1780576200
    candles = [
        {
            "ticker": "KXU3-26MAY-T4.0",
            "period_minutes": 60,
            **historical_candle(forecast - 7200, "0.6000", "0.5500"),
        },
        {
            "ticker": "KXU3-26MAY-T4.0",
            "period_minutes": 60,
            **historical_candle(forecast - 3600, None, "0.6000"),
        },
        # This candle ends AFTER forecast_time and must never reach a forecaster.
        {
            "ticker": "KXU3-26MAY-T4.0",
            "period_minutes": 60,
            **historical_candle(forecast + 3600, "0.9500", "0.6000"),
        },
    ]
    write_raw(raw, "kalshi", "historical_candlesticks", candles, ingested_at=INGESTED)
    # June event: release 2026-07-02 12:30Z, forecast_time 2026-07-01 12:30Z (epoch 1782909000).
    forecast = 1782909000
    candles = [
        {
            "ticker": "KXU3-26JUN-T4.2",
            "period_minutes": 60,
            **live_candle(forecast - 3600, "0.2500"),
        },
        {
            "ticker": "KXU3-26JUN-T4.2",
            "period_minutes": 60,
            **live_candle(forecast + 1800, "0.0500"),
        },
    ]
    write_raw(raw, "kalshi", "live_candlesticks", candles, ingested_at=INGESTED)
    trades = [
        {
            "trade_id": "t-1",
            "ticker": "KXU3-26MAY-T4.0",
            "created_time": "2026-06-04T10:00:00.123456Z",
            "yes_price_dollars": "0.6000",
            "no_price_dollars": "0.4000",
            "count_fp": "10.00",
            "taker_side": "yes",
            "is_block_trade": False,
        }
    ]
    write_raw(raw, "kalshi", "historical_trades", trades, ingested_at=INGESTED)
    # Same trade ingested again by a rerun: staging must keep one row.
    write_raw(
        raw, "kalshi", "historical_trades", trades, ingested_at=datetime(2026, 10, 2, tzinfo=UTC)
    )


def write_fred_and_calendar(raw: Path) -> None:
    """Synthetic UNRATE first releases, the jobs release calendar, FOMC meetings and statements."""
    observations = [
        ("2026-03-01", "3.9", "2026-04-03"),
        ("2026-04-01", "4.0", "2026-05-08"),
        ("2026-05-01", "4.1", "2026-06-05"),
        ("2026-06-01", "4.3", "2026-07-02"),
    ]
    rows = [
        {
            "series_id": "UNRATE",
            "vintage_policy": "first_release",
            "date": day,
            "value": value,
            "realtime_start": released,
            "realtime_end": "9999-12-31",
        }
        for day, value, released in observations
    ]
    # Fed target (never revised): the rate in force on each day, known from the end of that day.
    rows += [
        {
            "series_id": "DFEDTARU",
            "vintage_policy": "unrevised",
            "date": day,
            "value": value,
            "realtime_start": "2026-10-01",
            "realtime_end": "2026-10-01",
        }
        for day, value in [("2026-06-15", "4.25"), ("2026-06-16", "4.25"), ("2026-06-17", "4.00")]
    ]
    write_raw(raw, "fred", "observations", rows, ingested_at=INGESTED)
    calendar = [
        {
            "series_key": "unemployment",
            "release_id": 50,
            "release_name": "Employment Situation",
            "release_date": released,
            "release_time_local": "08:30",
            "timezone": "America/New_York",
        }
        for _, _, released in observations
    ]
    write_raw(raw, "fred", "release_calendar", calendar, ingested_at=INGESTED)
    meetings = [
        {"year": 2026, "month_text": "April", "date_text": "28-29", "statement_path": None},
        {
            "year": 2026,
            "month_text": "June",
            "date_text": "16-17*",
            "statement_path": "/p/m0617.htm",
        },
        {"year": 2026, "month_text": "Apr/May", "date_text": "30-1", "statement_path": None},
    ]
    write_raw(raw, "fed", "fomc_meetings", meetings, ingested_at=INGESTED)
    statement = {
        "path": "/p/m0617.htm",
        "date_text": "June 17, 2026",
        "release_line": "For release at 2:00 p.m. EDT",
        "body_text": "Synthetic statement text.",
    }
    # A page without a "For release at" line: staging must assume 14:00 and flag it.
    no_time = {
        **statement,
        "path": "/p/m0429.htm",
        "date_text": "April 29, 2026",
        "release_line": None,
    }
    write_raw(raw, "fed", "fomc_statements", [statement, no_time], ingested_at=INGESTED)


def build(raw_dir: Path, warehouse_path: Path) -> None:
    """Write synthetic raw files and register them as bronze views."""
    write_kalshi(raw_dir)
    write_fred_and_calendar(raw_dir)
    con = connect(warehouse_path)
    try:
        load_bronze(con, raw_dir)
    finally:
        con.close()


if __name__ == "__main__":
    build(Path(sys.argv[1]), Path(sys.argv[2]))
