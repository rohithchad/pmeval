"""Ingest scheduled release dates for each series from the FRED release calendar.

FRED gives the release *date*, not the time. Each row is stored with the series'
usual local release time (see SeriesSpec.release_time_et) so staging can build a
timestamp. Past dates are the realised schedule; future dates are the published schedule.

Run:  python -m pmeval.ingest.release_calendar
"""

from __future__ import annotations

import logging
from pathlib import Path

from pmeval.config import SERIES_REGISTRY, SeriesSpec, get_settings
from pmeval.ingest.fred import FredClient, make_fred_client
from pmeval.ingest.raw import write_raw
from pmeval.logging_setup import setup_logging

logger = logging.getLogger(__name__)

SOURCE = "fred"
TIMEZONE = "America/New_York"


def ingest_release_dates(client: FredClient, raw_dir: Path, spec: SeriesSpec) -> int:
    """Store the release dates for one series; returns the number of dates stored."""
    if spec.fred_release_id is None:
        return 0
    dates = client.get_release_dates(spec.fred_release_id)
    rows = [
        {
            "series_key": spec.key,
            "release_id": spec.fred_release_id,
            "release_name": spec.release_name,
            "release_date": item["date"],
            "release_time_local": spec.release_time_et,
            "timezone": TIMEZONE,
        }
        for item in dates
    ]
    write_raw(raw_dir, SOURCE, "release_calendar", rows, {"release_id": spec.fred_release_id})
    return len(rows)


def main() -> None:
    settings = get_settings()
    setup_logging(settings.pmeval_log_level)
    client = make_fred_client()
    for spec in SERIES_REGISTRY:
        count = ingest_release_dates(client, settings.pmeval_raw_dir, spec)
        logger.info("stored %d release dates for %s", count, spec.key)


if __name__ == "__main__":
    main()
