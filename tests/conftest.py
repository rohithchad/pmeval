import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))  # lets tests import helper modules by name

import fixture_warehouse  # noqa: E402
from dbt_helpers import dbt_available, run_dbt  # noqa: E402
from pmeval.ingest.http import HttpClient  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture():
    """Return a function that loads a recorded JSON fixture by relative path."""

    def _load(name: str):
        return json.loads((FIXTURES / name).read_text())

    return _load


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def sleeps() -> list[float]:
    """Collects the delays HttpClient would have slept, so tests run instantly."""
    return []


@pytest.fixture
def make_client(sleeps):
    """Factory for an HttpClient whose sleeping is recorded instead of performed."""

    def _make(base_url="https://api.test/v2", **kwargs):
        kwargs.setdefault("min_interval_seconds", 0)
        kwargs.setdefault("backoff_seconds", 1.0)
        return HttpClient(base_url, sleep=sleeps.append, **kwargs)

    return _make


@pytest.fixture(scope="session")
def dbt_warehouse(tmp_path_factory):
    """Synthetic raw data -> bronze -> `dbt build`; shared by every test that needs gold tables."""
    if not dbt_available():
        pytest.skip("dbt is not installed")
    root = tmp_path_factory.mktemp("dbt")
    path = root / "warehouse.duckdb"
    fixture_warehouse.build(root / "raw", path)
    result = run_dbt(root, path, "build")
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1000:]
    return path
