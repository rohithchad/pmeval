"""Resumable backfill of settled Kalshi markets across the live and historical tiers.

Kalshi's GET /historical/cutoff returns `market_settled_ts`. Markets that settled
before it, and their candlesticks, are served only by the /historical/* endpoints.
Markets that settled at or after it are still in the live tier. A correct backfill
therefore reads both tiers, routing each market to the right endpoints.

Resumability: after a market's rows are fully written, its ticker is recorded in a
small JSON state file. A re-run skips recorded tickers, so an interrupted backfill
continues where it stopped instead of starting over.

Run:  python -m pmeval.ingest.kalshi_historical [SERIES ...] [--no-trades]
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pmeval.config import SERIES_REGISTRY, get_settings
from pmeval.ingest.kalshi import (
    CANDLE_WINDOW_SECONDS,
    MAX_PAGE_SIZE,
    KalshiClient,
    make_http_client,
)
from pmeval.ingest.raw import write_raw
from pmeval.ingest.timeutil import parse_iso_to_unix
from pmeval.logging_setup import setup_logging

logger = logging.getLogger(__name__)

SOURCE = "kalshi"


class KalshiHistoricalClient(KalshiClient):
    """Adds the historical-tier endpoints to the live client."""

    def get_cutoff(self) -> dict:
        """Return the cutoff timestamps (ISO strings) separating live from historical data."""
        return self.http.get_json("/historical/cutoff")

    def iter_historical_markets(self, series_ticker: str) -> Iterator[dict]:
        """Yield settled markets that have been archived to the historical tier."""
        params: dict[str, Any] = {"series_ticker": series_ticker, "limit": MAX_PAGE_SIZE}
        yield from self.paginate("/historical/markets", "markets", params)

    def get_historical_candlesticks(
        self, market_ticker: str, start_ts: int, end_ts: int, period_minutes: int = 60
    ) -> list[dict]:
        """Fetch historical candlesticks in windows; same period rules as the live endpoint."""
        if period_minutes not in (1, 60, 1440):
            raise ValueError("period_minutes must be 1, 60 or 1440")
        path = f"/historical/markets/{market_ticker}/candlesticks"
        candles: list[dict] = []
        window_start = start_ts
        while window_start <= end_ts:
            window_end = min(window_start + CANDLE_WINDOW_SECONDS, end_ts)
            body = self.http.get_json(
                path,
                {"start_ts": window_start, "end_ts": window_end, "period_interval": period_minutes},
            )
            candles.extend(body.get("candlesticks") or [])
            window_start = window_end + 1
        return candles

    def iter_historical_trades(self, ticker: str) -> Iterator[dict]:
        """Yield archived trades for one market."""
        yield from self.paginate(
            "/historical/trades", "trades", {"ticker": ticker, "limit": MAX_PAGE_SIZE}
        )


class BackfillState:
    """Set of market tickers already fully backfilled, persisted as JSON."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.done: set[str] = set()
        if path.exists():
            self.done = set(json.loads(path.read_text())["done"])

    def is_done(self, ticker: str) -> bool:
        return ticker in self.done

    def mark_done(self, ticker: str) -> None:
        """Record a ticker and save immediately so a crash loses at most one market."""
        self.done.add(ticker)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps({"done": sorted(self.done)}))
        temp.replace(self.path)  # atomic rename: never leaves a half-written state file


def is_historical(market: dict, cutoff_unix: int) -> bool:
    """True when the market settled before the cutoff (so only /historical/* serves it)."""
    settled = parse_iso_to_unix(market.get("settlement_ts"))
    return settled is not None and settled < cutoff_unix


def backfill_series(
    client: KalshiHistoricalClient,
    raw_dir: Path,
    series_ticker: str,
    state: BackfillState,
    period: int = 60,
    include_trades: bool = True,
) -> None:
    """Backfill every settled market of one series from both tiers."""
    cutoff = client.get_cutoff()
    write_raw(raw_dir, SOURCE, "historical_cutoff", [cutoff])
    cutoff_unix = parse_iso_to_unix(cutoff["market_settled_ts"])
    if cutoff_unix is None:
        raise ValueError("cutoff response has no market_settled_ts")

    historical = client.iter_historical_markets(series_ticker)
    live_settled = client.iter_markets(series_ticker=series_ticker, status="settled")
    for market in historical:
        backfill_market(client, raw_dir, series_ticker, market, True, state, period, include_trades)
    for market in live_settled:
        # A live-tier market that settled before the cutoff would also be served historically;
        # skip it here so the historical tier stays the source of truth for those.
        if is_historical(market, cutoff_unix):
            continue
        backfill_market(
            client, raw_dir, series_ticker, market, False, state, period, include_trades
        )


def backfill_market(
    client: KalshiHistoricalClient,
    raw_dir: Path,
    series_ticker: str,
    market: dict,
    historical: bool,
    state: BackfillState,
    period: int,
    include_trades: bool,
) -> None:
    """Write one market's record, candlesticks and trades, then mark it done."""
    ticker = market["ticker"]
    if state.is_done(ticker):
        return
    tier = "historical" if historical else "live"
    start = parse_iso_to_unix(market.get("open_time"))
    end = parse_iso_to_unix(market.get("close_time"))
    write_raw(raw_dir, SOURCE, f"{tier}_markets", [market], {"series_ticker": series_ticker})
    if start is not None and end is not None:
        if historical:
            candles = client.get_historical_candlesticks(ticker, start, end, period)
        else:
            candles = client.get_candlesticks(series_ticker, ticker, start, end, period)
        rows = [{"ticker": ticker, "period_minutes": period, **c} for c in candles]
        write_raw(raw_dir, SOURCE, f"{tier}_candlesticks", rows, {"ticker": ticker})
    if include_trades:
        trades = client.iter_historical_trades(ticker) if historical else client.iter_trades(ticker)
        write_raw(raw_dir, SOURCE, f"{tier}_trades", trades, {"ticker": ticker})
    state.mark_done(ticker)
    logger.info("backfilled %s (%s tier)", ticker, tier)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("series", nargs="*", help="Kalshi series tickers (default: registry)")
    parser.add_argument("--no-trades", action="store_true", help="skip trade history")
    args = parser.parse_args()
    settings = get_settings()
    setup_logging(settings.pmeval_log_level)
    client = KalshiHistoricalClient(make_http_client())
    state = BackfillState(settings.pmeval_raw_dir / "_state" / "kalshi_backfill.json")
    tickers = args.series or [spec.kalshi_series_ticker for spec in SERIES_REGISTRY]
    for ticker in tickers:
        backfill_series(
            client, settings.pmeval_raw_dir, ticker, state, include_trades=not args.no_trades
        )


if __name__ == "__main__":
    main()
