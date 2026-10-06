import json
from pathlib import Path

import pytest

from pmeval.ingest.http import HttpClient

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
