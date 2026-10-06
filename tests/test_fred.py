import pandas as pd
import pytest
import responses
from pydantic import SecretStr

from pmeval.ingest.fred import FredClient, MissingApiKeyError, ingest_series, make_fred_client

BASE = "https://api.test/fred"
SECRET = "super-secret-key"


def _client(make_client):
    return FredClient(make_client(BASE), SecretStr(SECRET))


@responses.activate
def test_first_release_query_parameters(make_client, load_fixture):
    responses.add(
        responses.GET,
        f"{BASE}/series/observations",
        json=load_fixture("fred/unrate_first_release.json"),
    )
    rows = _client(make_client).get_first_release_observations("UNRATE")
    url = responses.calls[0].request.url
    assert "output_type=4" in url  # initial release only
    assert "realtime_start=1776-07-04" in url and "realtime_end=9999-12-31" in url
    assert len(rows) == 3
    assert {"date", "value", "realtime_start", "realtime_end"} <= rows[0].keys()


@responses.activate
def test_pagination_advances_offset(make_client):
    page1 = {"count": 3, "observations": [{"date": "2020-01-01"}, {"date": "2020-02-01"}]}
    page2 = {"count": 3, "observations": [{"date": "2020-03-01"}]}
    responses.add(responses.GET, f"{BASE}/series/observations", json=page1)
    responses.add(responses.GET, f"{BASE}/series/observations", json=page2)
    rows = _client(make_client).get_first_release_observations("X")
    assert len(rows) == 3
    assert "offset=2" in responses.calls[1].request.url


@responses.activate
def test_unrevised_series_uses_default_realtime(make_client, load_fixture):
    responses.add(
        responses.GET,
        f"{BASE}/series/observations",
        json=load_fixture("fred/dfedtaru_unrevised.json"),
    )
    _client(make_client).get_unrevised_observations("DFEDTARU")
    assert "output_type" not in responses.calls[0].request.url


@responses.activate
def test_api_key_never_reaches_raw_files(make_client, load_fixture, tmp_path):
    responses.add(
        responses.GET,
        f"{BASE}/series/observations",
        json=load_fixture("fred/unrate_first_release.json"),
    )
    ingest_series(_client(make_client), tmp_path, "UNRATE")
    frame = pd.concat(pd.read_parquet(p) for p in tmp_path.rglob("*.parquet"))
    assert SECRET not in frame.to_json(date_format="iso")
    assert set(frame["payload"].str.contains("first_release")) == {True}


def test_missing_key_fails_clearly(monkeypatch):
    from pmeval import config

    monkeypatch.setattr(
        config, "get_settings", lambda: config.Settings(_env_file=None, fred_api_key=None)
    )
    monkeypatch.setattr("pmeval.ingest.fred.get_settings", config.get_settings)
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    with pytest.raises(MissingApiKeyError):
        make_fred_client()


@responses.activate
def test_release_dates(make_client):
    responses.add(
        responses.GET,
        f"{BASE}/release/dates",
        json={"release_dates": [{"release_id": 10, "date": "2026-10-14"}]},
    )
    assert _client(make_client).get_release_dates(10)[0]["date"] == "2026-10-14"
    assert "include_release_dates_with_no_data=true" in responses.calls[0].request.url
