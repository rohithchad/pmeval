"""The dbt seed and the Python registry describe the same series; keep them in sync."""

import csv
from pathlib import Path

from pmeval.config import SERIES_REGISTRY

SEED = Path(__file__).parent.parent / "dbt_project" / "seeds" / "series_registry.csv"


def test_seed_matches_python_registry():
    with SEED.open() as handle:
        seed_rows = {row["series_key"]: row for row in csv.DictReader(handle)}
    assert set(seed_rows) == {spec.key for spec in SERIES_REGISTRY}
    for spec in SERIES_REGISTRY:
        row = seed_rows[spec.key]
        assert row["fred_series_id"] == spec.fred_series_id
        assert row["kalshi_series_ticker"] == spec.kalshi_series_ticker
        assert row["release_name"] == spec.release_name
        assert row["release_time_et"] == spec.release_time_et
        assert (row["is_revised"] == "true") == spec.revised
