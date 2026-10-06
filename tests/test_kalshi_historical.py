import json

import pytest
import responses

from pmeval.ingest.kalshi_historical import (
    KalshiHistoricalClient,
    backfill_market,
    backfill_series,
    is_historical,
)
from pmeval.ingest.state import ProgressState
from pmeval.ingest.timeutil import parse_iso_to_unix

BASE = "https://api.test/v2"


def test_cutoff_routing(load_fixture):
    cutoff = parse_iso_to_unix(load_fixture("kalshi/historical_cutoff.json")["market_settled_ts"])
    assert is_historical({"settlement_ts": "2026-06-01T00:00:00Z"}, cutoff)
    assert not is_historical({"settlement_ts": "2026-09-01T00:00:00Z"}, cutoff)
    assert not is_historical({"settlement_ts": None}, cutoff)


@responses.activate
def test_cutoff_endpoint(make_client, load_fixture):
    responses.add(
        responses.GET,
        f"{BASE}/historical/cutoff",
        json=load_fixture("kalshi/historical_cutoff.json"),
    )
    cutoff = KalshiHistoricalClient(make_client(BASE)).get_cutoff()
    assert "market_settled_ts" in cutoff


def _market(load_fixture):
    return load_fixture("kalshi/hist_markets_page1.json")["markets"][0]


@responses.activate
def test_historical_market_uses_historical_endpoints(make_client, load_fixture, tmp_path):
    market = _market(load_fixture)
    ticker = market["ticker"]
    responses.add(
        responses.GET,
        f"{BASE}/historical/markets/{ticker}/candlesticks",
        json=load_fixture("kalshi/hist_candlesticks.json"),
    )
    responses.add(
        responses.GET,
        f"{BASE}/historical/trades",
        json={"trades": load_fixture("kalshi/hist_trades.json")["trades"], "cursor": ""},
    )
    state = ProgressState(tmp_path / "state.json")
    backfill_market(
        KalshiHistoricalClient(make_client(BASE)), tmp_path, "KXU3", market, True, state, 60, True
    )
    datasets = {p.parent.parent.name for p in tmp_path.rglob("*.parquet")}
    assert datasets == {
        "dataset=historical_markets",
        "dataset=historical_candlesticks",
        "dataset=historical_trades",
    }
    assert state.is_done(ticker)


@responses.activate
def test_live_market_uses_live_endpoints(make_client, load_fixture, tmp_path):
    market = _market(load_fixture)
    ticker = market["ticker"]
    responses.add(
        responses.GET,
        f"{BASE}/series/KXU3/markets/{ticker}/candlesticks",
        json=load_fixture("kalshi/candlesticks_live.json"),
    )
    state = ProgressState(tmp_path / "state.json")
    backfill_market(
        KalshiHistoricalClient(make_client(BASE)), tmp_path, "KXU3", market, False, state, 60, False
    )
    datasets = {p.parent.parent.name for p in tmp_path.rglob("*.parquet")}
    assert datasets == {"dataset=live_markets", "dataset=live_candlesticks"}


@responses.activate
def test_backfill_resumes_and_skips_done_markets(make_client, load_fixture, tmp_path):
    page = load_fixture("kalshi/hist_markets_page1.json")
    page["cursor"] = ""
    ticker = page["markets"][0]["ticker"]
    responses.add(
        responses.GET,
        f"{BASE}/historical/cutoff",
        json=load_fixture("kalshi/historical_cutoff.json"),
    )
    responses.add(responses.GET, f"{BASE}/historical/markets", json=page)
    responses.add(responses.GET, f"{BASE}/markets", json={"markets": [], "cursor": ""})
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"done": [m["ticker"] for m in page["markets"]]}))
    state = ProgressState(state_path)
    client = KalshiHistoricalClient(make_client(BASE))
    backfill_series(client, tmp_path, "KXU3", state, include_trades=False)
    # everything was already done: only listing calls happened, no candlestick or trade calls
    urls = [c.request.url for c in responses.calls]
    assert not any("candlesticks" in u or "trades" in u for u in urls)
    assert state.is_done(ticker)


def test_progress_state_persists(tmp_path):
    path = tmp_path / "nested" / "s.json"
    ProgressState(path).mark_done("A")
    assert ProgressState(path).is_done("A")
    assert not ProgressState(path).is_done("B")


def test_historical_candle_period_validated(make_client):
    with pytest.raises(ValueError):
        KalshiHistoricalClient(make_client(BASE)).get_historical_candlesticks("T", 0, 1, 7)
