"""Kalshi client for the live tier of the public market-data API.

Only unauthenticated GET endpoints are used: series, events, markets,
candlesticks and trades. There is no code for orders, portfolio or any other
trading endpoint. Docs: https://docs.kalshi.com (see docs/DECISIONS.md D4).

Kalshi splits data into a live tier and a historical tier. Markets that settled
before the cutoff returned by GET /historical/cutoff are only served by the
historical endpoints; see pmeval.ingest.kalshi_historical.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from pmeval.config import get_settings
from pmeval.ingest.http import HttpClient

logger = logging.getLogger(__name__)

# Largest page size the docs allow for markets and trades.
MAX_PAGE_SIZE = 1000

# Candlesticks are fetched in windows so a single response stays a manageable size.
CANDLE_WINDOW_SECONDS = 30 * 24 * 3600


def make_http_client(min_interval_seconds: float = 0.2) -> HttpClient:
    """Build an HttpClient pointed at the configured Kalshi base URL."""
    return HttpClient(get_settings().kalshi_base_url, min_interval_seconds=min_interval_seconds)


class KalshiClient:
    """Read-only access to Kalshi public market data (live tier)."""

    def __init__(self, http: HttpClient) -> None:
        self.http = http

    def paginate(self, path: str, items_key: str, params: dict[str, Any]) -> Iterator[dict]:
        """Yield every record from a cursor-paginated endpoint.

        Kalshi returns a `cursor` string with each page; an empty cursor means
        there are no more pages. The cursor is passed back unchanged.
        """
        query = dict(params)
        while True:
            body = self.http.get_json(path, query)
            yield from body.get(items_key) or []
            cursor = body.get("cursor")
            if not cursor:
                return
            query["cursor"] = cursor

    def get_series_list(self, category: str | None = None) -> list[dict]:
        """List series (templates for recurring events), optionally by category."""
        params = {"category": category} if category else {}
        body = self.http.get_json("/series", params)
        return body.get("series") or []

    def get_series(self, series_ticker: str) -> dict:
        """Fetch one series by ticker."""
        body = self.http.get_json(f"/series/{series_ticker}")
        return body["series"]

    def iter_events(self, series_ticker: str, status: str | None = None) -> Iterator[dict]:
        """Yield events belonging to a series."""
        params: dict[str, Any] = {"series_ticker": series_ticker, "limit": 200}
        if status:
            params["status"] = status
        yield from self.paginate("/events", "events", params)

    def iter_markets(
        self,
        series_ticker: str | None = None,
        event_ticker: str | None = None,
        status: str | None = None,
    ) -> Iterator[dict]:
        """Yield live-tier markets. Only one status filter may be given at a time."""
        params: dict[str, Any] = {"limit": MAX_PAGE_SIZE}
        if series_ticker:
            params["series_ticker"] = series_ticker
        if event_ticker:
            params["event_ticker"] = event_ticker
        if status:
            params["status"] = status
        yield from self.paginate("/markets", "markets", params)

    def get_candlesticks(
        self,
        series_ticker: str,
        market_ticker: str,
        start_ts: int,
        end_ts: int,
        period_minutes: int = 60,
    ) -> list[dict]:
        """Fetch candlesticks for [start_ts, end_ts] (Unix seconds), in windows.

        period_minutes must be 1, 60 or 1440 per the docs.
        """
        if period_minutes not in (1, 60, 1440):
            raise ValueError("period_minutes must be 1, 60 or 1440")
        path = f"/series/{series_ticker}/markets/{market_ticker}/candlesticks"
        candles: list[dict] = []
        window_start = start_ts
        while window_start <= end_ts:
            window_end = min(window_start + CANDLE_WINDOW_SECONDS, end_ts)
            body = self.http.get_json(
                path,
                {
                    "start_ts": window_start,
                    "end_ts": window_end,
                    "period_interval": period_minutes,
                },
            )
            candles.extend(body.get("candlesticks") or [])
            window_start = window_end + 1
        return candles

    def iter_trades(
        self, ticker: str, min_ts: int | None = None, max_ts: int | None = None
    ) -> Iterator[dict]:
        """Yield live-tier trades for one market."""
        params: dict[str, Any] = {"ticker": ticker, "limit": MAX_PAGE_SIZE}
        if min_ts is not None:
            params["min_ts"] = min_ts
        if max_ts is not None:
            params["max_ts"] = max_ts
        yield from self.paginate("/markets/trades", "trades", params)
