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
    write_raw(raw, "kalshi", "live_markets", jun, ingested_at=INGESTED)
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


def build(raw_dir: Path, warehouse_path: Path) -> None:
    """Write synthetic raw files and register them as bronze views."""
    write_kalshi(raw_dir)
    con = connect(warehouse_path)
    try:
        load_bronze(con, raw_dir)
    finally:
        con.close()


if __name__ == "__main__":
    build(Path(sys.argv[1]), Path(sys.argv[2]))
