"""FRED/ALFRED client that stores first-release ("as first published") observations.

Why first release: Kalshi markets settle on the number the agency publishes on
release day. Later revisions are not known at forecast time, so using them would
leak the future into features. FRED's `output_type=4` returns, for every
observation date, only its initial release; `realtime_start` on each row is the
date that value first appeared (its vintage date).

Docs: https://fred.stlouisfed.org/docs/api/fred/series_observations.html
FRED allows up to 120 requests per minute, so the default spacing is 0.6 seconds.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from pydantic import SecretStr

from pmeval.config import SERIES_REGISTRY, get_settings
from pmeval.ingest.http import HttpClient
from pmeval.ingest.raw import write_raw
from pmeval.logging_setup import setup_logging

logger = logging.getLogger(__name__)

SOURCE = "fred"
FRED_BASE_URL = "https://api.stlouisfed.org/fred"

# Real-time window covering all of history, so no vintage is cut off.
EARLIEST_REALTIME = "1776-07-04"
LATEST_REALTIME = "9999-12-31"

# output_type=4 means "Observations, Initial Release Only".
OUTPUT_INITIAL_RELEASE_ONLY = 4

PAGE_SIZE = 100000


class MissingApiKeyError(RuntimeError):
    """Raised when FRED_API_KEY is not configured."""


class FredClient:
    """Fetches first-release observations. The API key is sent but never logged or stored."""

    def __init__(self, http: HttpClient, api_key: SecretStr) -> None:
        self.http = http
        self._api_key = api_key

    def get_first_release_observations(
        self, series_id: str, observation_start: str | None = None
    ) -> list[dict]:
        """Return every observation of a series at its initial release.

        Each row has `date` (observation period), `value` (a string; "." means
        missing), `realtime_start` (first-release date) and `realtime_end`.
        """
        rows: list[dict] = []
        offset = 0
        while True:
            params = {
                "series_id": series_id,
                "api_key": self._api_key.get_secret_value(),
                "file_type": "json",
                "output_type": OUTPUT_INITIAL_RELEASE_ONLY,
                "realtime_start": EARLIEST_REALTIME,
                "realtime_end": LATEST_REALTIME,
                "limit": PAGE_SIZE,
                "offset": offset,
            }
            if observation_start:
                params["observation_start"] = observation_start
            body = self.http.get_json("/series/observations", params)
            page = body.get("observations") or []
            rows.extend(page)
            offset += len(page)
            if not page or offset >= int(body.get("count", offset)):
                return rows

    def get_unrevised_observations(self, series_id: str) -> list[dict]:
        """Return all observations of a series that is never revised (default real-time period).

        Daily series such as the Fed target rate have thousands of vintage dates, which
        exceeds the 2000 vintage-date limit of the first-release query. Because their values
        are never revised, the latest data equals the first release.
        """
        params = {
            "series_id": series_id,
            "api_key": self._api_key.get_secret_value(),
            "file_type": "json",
            "limit": PAGE_SIZE,
        }
        return self.http.get_json("/series/observations", params).get("observations") or []


def make_fred_client() -> FredClient:
    """Build a FredClient from settings, failing early with a clear message if no key is set."""
    key = get_settings().fred_api_key
    if key is None or not key.get_secret_value():
        raise MissingApiKeyError("FRED_API_KEY is not set; add it to .env")
    return FredClient(HttpClient(FRED_BASE_URL, min_interval_seconds=0.6), key)


def ingest_series(client: FredClient, raw_dir: Path, series_id: str, revised: bool = True) -> None:
    """Fetch and store observations for one FRED series.

    Revised series are stored as first releases. Never-revised series are stored from
    the latest data; the `vintage_policy` tag records which method produced each row.
    """
    if revised:
        observations = client.get_first_release_observations(series_id)
        policy = "first_release"
    else:
        observations = client.get_unrevised_observations(series_id)
        policy = "unrevised"
    # Tag each row because the API does not repeat the series id per observation.
    rows = [{"series_id": series_id, "vintage_policy": policy, **row} for row in observations]
    write_raw(raw_dir, SOURCE, "observations", rows, {"series_id": series_id})
    logger.info("stored %d first-release observations for %s", len(rows), series_id)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("series", nargs="*", help="FRED series ids (default: registry)")
    args = parser.parse_args()
    settings = get_settings()
    setup_logging(settings.pmeval_log_level)
    client = make_fred_client()
    specs = {spec.fred_series_id: spec for spec in SERIES_REGISTRY}
    for series_id in args.series or sorted(specs):
        revised = specs[series_id].revised if series_id in specs else True
        ingest_series(client, settings.pmeval_raw_dir, series_id, revised)


if __name__ == "__main__":
    main()
