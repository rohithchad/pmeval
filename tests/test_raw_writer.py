from datetime import UTC, datetime

import pandas as pd

from pmeval.ingest.raw import write_raw


def test_partitioned_by_source_dataset_and_date(tmp_path):
    stamp = datetime(2026, 3, 4, 12, 0, tzinfo=UTC)
    path = write_raw(tmp_path, "kalshi", "markets", [{"a": 1}], {"x": 2}, ingested_at=stamp)
    assert path.parent == tmp_path / "source=kalshi" / "dataset=markets" / "ingest_date=2026-03-04"
    frame = pd.read_parquet(path)
    assert list(frame.columns) == ["payload", "request_params", "ingested_at"]
    assert frame.loc[0, "payload"] == '{"a": 1}'
    assert frame.loc[0, "request_params"] == '{"x": 2}'


def test_empty_records_write_nothing(tmp_path):
    assert write_raw(tmp_path, "kalshi", "markets", []) is None
    assert list(tmp_path.iterdir()) == []


def test_each_call_writes_a_new_file(tmp_path):
    first = write_raw(tmp_path, "s", "d", [{"a": 1}], ingested_at=datetime(2026, 1, 1, tzinfo=UTC))
    second = write_raw(
        tmp_path, "s", "d", [{"a": 1}], ingested_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)
    )
    assert first != second
