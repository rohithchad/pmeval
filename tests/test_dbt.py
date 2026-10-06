"""Runs dbt against the synthetic fixture warehouse and checks model output.

Skipped when dbt is not installed. These tests never touch the network.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import fixture_warehouse  # noqa: E402

DBT = shutil.which("dbt") or str(Path(sys.executable).parent / "dbt")
DBT_PROJECT = Path(__file__).parent.parent / "dbt_project"

pytestmark = pytest.mark.skipif(not Path(DBT).exists(), reason="dbt is not installed")


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory):
    """Build the fixture warehouse once, run `dbt build`, and return its path."""
    root = tmp_path_factory.mktemp("dbt")
    path = root / "warehouse.duckdb"
    fixture_warehouse.build(root / "raw", path)
    env = {
        **os.environ,
        "PMEVAL_WAREHOUSE_PATH": str(path),
        "DBT_TARGET_PATH": str(root / "target"),
    }
    result = subprocess.run(
        [DBT, "build", "--profiles-dir", ".", "--log-path", str(root / "logs")],
        cwd=DBT_PROJECT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1000:]
    return path


def query(path, sql):
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_markets_deduplicated_and_typed(warehouse):
    rows = query(
        warehouse,
        "select market_ticker, result, series_ticker, close_at from staging.stg_kalshi__markets "
        "order by 1",
    )
    assert [r[:3] for r in rows] == [
        ("KXU3-26JUN-T4.2", "no", "KXU3"),
        ("KXU3-26MAY-T4.0", "yes", "KXU3"),
    ]
    assert str(rows[1][3]) == "2026-06-05 12:29:00"


def test_candles_handle_both_tier_shapes_and_null_prices(warehouse):
    rows = query(
        warehouse,
        "select market_ticker, trade_close_probability from staging.stg_kalshi__candlesticks "
        "order by market_ticker, period_end_at",
    )
    # rows are ordered JUN (live shape) first, then MAY (historical shape)
    assert rows[0][1] == 0.25  # live shape (price.close_dollars)
    assert rows[2][1] == 0.6  # historical shape (price.close)
    assert rows[3][1] is None  # candle with no trades


def test_trades_deduplicated_across_reingestion(warehouse):
    assert query(warehouse, "select count(*) from staging.stg_kalshi__trades") == [(1,)]


def test_fred_first_release_dates(warehouse):
    rows = query(
        warehouse,
        "select observation_date::varchar, value, first_released_on::varchar "
        "from staging.stg_fred__observations order by observation_date",
    )
    assert rows[1] == ("2026-04-01", 4.0, "2026-05-08")
    assert len(rows) == 4


def test_release_calendar_converted_to_utc_with_dst(warehouse):
    # 08:30 America/New_York on 2026-06-05 is EDT (UTC-4), so 12:30 UTC
    rows = query(
        warehouse,
        "select release_at::varchar from staging.stg_fred__release_calendar "
        "where release_date = date '2026-06-05'",
    )
    assert rows == [("2026-06-05 12:30:00",)]


def test_fed_statement_publication_time(warehouse):
    rows = query(
        warehouse,
        "select published_at::varchar, release_time_is_assumed from staging.stg_fed__statements "
        "order by published_at",
    )
    # 2:00 p.m. EDT is 18:00 UTC; the page without a release line is assumed to be 14:00 too
    assert rows == [("2026-04-29 18:00:00", True), ("2026-06-17 18:00:00", False)]


def test_fed_meeting_dates_parsed(warehouse):
    rows = query(
        warehouse,
        "select meeting_end_date::varchar, has_statement from staging.stg_fed__meetings "
        "order by meeting_end_date",
    )
    # "30-1" with month "Apr/May" ends on May 1
    assert rows == [("2026-04-29", False), ("2026-05-01", False), ("2026-06-17", True)]
