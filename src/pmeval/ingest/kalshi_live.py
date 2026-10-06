"""Ingest live-tier Kalshi data for configured series into raw Parquet.

By default only markets with status "open" are fetched: those are the ones that still change, and
settled markets are covered by the resumable backfill (pmeval.ingest.kalshi_historical). Pass
--status all to fetch markets of every status.

Run:  python -m pmeval.ingest.kalshi_live            (all series in the registry)
      python -m pmeval.ingest.kalshi_live KXCPIYOY   (one series)
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from pmeval.config import SERIES_REGISTRY, get_settings
from pmeval.ingest.kalshi import KalshiClient, make_http_client
from pmeval.ingest.raw import write_raw
from pmeval.ingest.timeutil import parse_iso_to_unix
from pmeval.logging_setup import setup_logging

logger = logging.getLogger(__name__)

SOURCE = "kalshi"


def ingest_series(
    client: KalshiClient,
    raw_dir: Path,
    series_ticker: str,
    period: int = 60,
    status: str | None = "open",
) -> None:
    """Ingest the series record, its events, and the candlesticks of its markets with `status`.

    status=None fetches markets of every status.
    """
    write_raw(raw_dir, SOURCE, "series", [client.get_series(series_ticker)])
    params = {"series_ticker": series_ticker}
    write_raw(raw_dir, SOURCE, "events", client.iter_events(series_ticker), params)
    markets = list(client.iter_markets(series_ticker=series_ticker, status=status))
    write_raw(raw_dir, SOURCE, "markets", markets, params)
    for market in markets:
        ingest_market_candlesticks(client, raw_dir, series_ticker, market, period)


def ingest_market_candlesticks(
    client: KalshiClient, raw_dir: Path, series_ticker: str, market: dict, period: int
) -> None:
    """Fetch candlesticks from the market's open_time to its close_time (or now, if earlier)."""
    ticker = market["ticker"]
    start = parse_iso_to_unix(market.get("open_time"))
    close = parse_iso_to_unix(market.get("close_time"))
    end = None if close is None else min(close, int(time.time()))
    if start is None or end is None:
        logger.warning("skipping candlesticks for %s: missing open/close time", ticker)
        return
    candles = client.get_candlesticks(series_ticker, ticker, start, end, period)
    # Candles carry no ticker of their own, so attach it to keep rows self-describing.
    rows = [{"ticker": ticker, "period_minutes": period, **candle} for candle in candles]
    write_raw(raw_dir, SOURCE, "candlesticks", rows, {"ticker": ticker, "period": period})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("series", nargs="*", help="Kalshi series tickers (default: registry)")
    parser.add_argument(
        "--status", default="open", help="market status to fetch, or 'all' (default: open)"
    )
    args = parser.parse_args()
    settings = get_settings()
    setup_logging(settings.pmeval_log_level)
    client = KalshiClient(make_http_client())
    tickers = args.series or [spec.kalshi_series_ticker for spec in SERIES_REGISTRY]
    for ticker in tickers:
        logger.info("ingesting live data for %s", ticker)
        ingest_series(
            client,
            settings.pmeval_raw_dir,
            ticker,
            status=None if args.status == "all" else args.status,
        )


if __name__ == "__main__":
    main()
