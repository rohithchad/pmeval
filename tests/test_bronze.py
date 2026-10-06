from datetime import UTC, datetime

from pmeval.ingest.raw import write_raw
from pmeval.warehouse.bronze import discover_datasets, load_bronze
from pmeval.warehouse.db import connect


def _write(raw_dir, second):
    write_raw(
        raw_dir,
        "kalshi",
        "markets",
        [{"ticker": f"T{second}"}],
        ingested_at=datetime(2026, 1, 1, 0, 0, second, tzinfo=UTC),
    )


def test_bronze_view_reads_all_files(tmp_path):
    _write(tmp_path, 1)
    _write(tmp_path, 2)
    con = connect(":memory:")
    assert load_bronze(con, tmp_path) == ["kalshi_markets"]
    count = con.execute("SELECT count(*) FROM bronze.kalshi_markets").fetchone()[0]
    assert count == 2
    columns = [c[0] for c in con.execute("DESCRIBE bronze.kalshi_markets").fetchall()]
    assert {"payload", "ingested_at", "ingest_date", "source", "dataset"} <= set(columns)


def test_loader_is_idempotent_and_sees_new_files(tmp_path):
    _write(tmp_path, 1)
    con = connect(":memory:")
    load_bronze(con, tmp_path)
    load_bronze(con, tmp_path)  # rerun must not fail or duplicate anything
    _write(tmp_path, 2)
    assert con.execute("SELECT count(*) FROM bronze.kalshi_markets").fetchone()[0] == 2


def test_empty_raw_dir_registers_nothing_but_creates_placeholders(tmp_path):
    (tmp_path / "_state").mkdir()
    assert discover_datasets(tmp_path) == []
    con = connect(":memory:")
    assert load_bronze(con, tmp_path) == []
    # placeholder views exist, are empty, and have the standard columns
    assert con.execute("SELECT count(*) FROM bronze.fred_observations").fetchone()[0] == 0
    columns = [c[0] for c in con.execute("DESCRIBE bronze.fred_observations").fetchall()]
    assert columns == [
        "payload",
        "request_params",
        "ingested_at",
        "source",
        "dataset",
        "ingest_date",
    ]


def test_placeholder_replaced_when_data_arrives(tmp_path):
    con = connect(":memory:")
    load_bronze(con, tmp_path)
    _write(tmp_path, 1)
    load_bronze(con, tmp_path)
    assert con.execute("SELECT count(*) FROM bronze.kalshi_markets").fetchone()[0] == 1
